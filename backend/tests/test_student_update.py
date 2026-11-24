import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app import conversation
from app.calendar_index import CALENDAR_DATA, verified_calendar_parts
from app.competition_catalog import CATALOG_MARKER
from app.config import settings
from app.db import SessionLocal
from app.models import Document, MailJob
from app.retrieval import build_chunks
from conftest import signup, student, wait_for_answer
from test_knowledge import ask, publish


@pytest.mark.parametrize(('question', 'classified_as'), [
    ('奖学金有多少', 'campus'),
    ('帮我联网确认我们学院的奖学金有多少？', 'web'),
])
def test_campus_material_gap_discloses_the_absence_of_verified_search_sources(monkeypatch, question, classified_as):
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(conversation, 'plan_turn', lambda *args: [
        {'kind': classified_as, 'question': question}])
    monkeypatch.setattr(conversation, 'policy_answer', lambda *args: {'items': [
        {'question': question, 'status': 'missing_material', 'content': '缺少依据', 'citations': []}]})
    def native_search(db, text, *, campus_gap):
        assert campus_gap and text == question
        raise conversation.MissingEvidence()
    monkeypatch.setattr(conversation, 'direct_model_answer', native_search)
    item, = conversation.answer_turn(None, question, {})['items']
    assert item['status'] == 'missing_material'
    assert '知识库暂时没有' in item['content'] and '暂未找到能核实' in item['content']


def test_unsourced_general_information_keeps_model_text(monkeypatch):
    question = '奖学金申请书怎么写？'
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(conversation, 'plan_turn', lambda *args: [
        {'kind': 'general', 'question': question}])
    monkeypatch.setattr(conversation, 'direct_model_answer', lambda *args, **kwargs: {
        'question': question, 'status': 'general_answer', 'content': '无出处', 'citations': []})
    item, = conversation.answer_turn(None, question, {})['items']
    assert item['status'] == 'general_answer'
    assert item['content'] == '无出处' and not item['citations']


@pytest.mark.parametrize('question', ['你是谁', '管小智，你叫什么名字？', '管小智，你好！', '小八，你叫什么名字？', '你好！'])
def test_assistant_introduction_uses_model_without_routing_or_search(monkeypatch, question):
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    def unexpected(*args, **kwargs):
        raise AssertionError('简单自我介绍不需要分流或联网')
    monkeypatch.setattr(conversation, 'plan_turn', unexpected)
    monkeypatch.setattr(conversation, 'direct_model_answer', unexpected)
    with SessionLocal() as db:
        answer = conversation.answer_turn(db, question, {})
    item, = answer['items']
    assert item['status'] == 'general_answer'
    assert '管小智' in item['content'] and '(｡•̀ᴗ-)✧' in item['content']
    assert not answer.get('notice')


def test_light_conversation_uses_the_chat_model_without_search(monkeypatch):
    question = '陪我随便聊两句'
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(conversation, 'plan_turn', lambda *args: [{'kind': 'chat', 'question': question}])
    monkeypatch.setattr(conversation, 'model_json', lambda *args: {'answer': '好呀，今天有没有什么有意思的事？😊'})
    def unexpected(*args, **kwargs):
        raise AssertionError('闲聊不应调用联网')
    monkeypatch.setattr(conversation, 'direct_model_answer', unexpected)
    item, = conversation.answer_turn(None, question, {})['items']
    assert item['status'] == 'general_answer'
    assert item['content'].endswith('😊')


@pytest.mark.parametrize('question', [
    '武汉纺织大学的成立时间是什么时候',
    '我们学校是哪一年建校的？',
    '武汉纺织大学的校史是什么？',
])
def test_public_school_profile_uses_native_search_and_accepts_non_school_sources(monkeypatch, question):
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(conversation, 'plan_turn', lambda *args: [{'kind': 'web', 'question': question}])
    monkeypatch.setattr(conversation, 'policy_answer', lambda *args, **kwargs: {'items': [
        {'question': question, 'status': 'missing_material', 'content': '暂无资料', 'citations': []}]})
    monkeypatch.setattr(settings(), 'deepseek_model', 'deepseek-flash')
    url = 'https://example.org/university-history'

    def handler(request):
        payload = json.loads(request.content)
        assert {'type': 'deepseek_web_search'} in payload['tools']
        assert payload['messages'][-1]['content'] == question
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': '武汉纺织大学前身始建于1958年。',
            'annotations': [{'type': 'url_citation', 'url_citation': {'url': url}}],
            'server_tool_calls': [{'calls': [{'status': 'completed', 'sources': [
                {'url': url, 'title': '大学校史', 'snippet': '武汉纺织大学前身始建于1958年。'},
            ]}]}],
        }}]})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    with SessionLocal() as db:
        item, = conversation.answer_turn(db, question, {})['items']
    assert item['web_sources'][0]['url'] == url
    assert item['status'] == 'web_answer'
    assert item['knowledge_gap']
    assert item['content'] == '武汉纺织大学前身始建于1958年。'
    assert '1958' in item['content']


@pytest.mark.parametrize('question', ['奖学金有多少', '奖学金多少钱？', '奖学金能拿多少？'])
def test_unspecified_scholarship_amount_asks_which_award(client, question):
    signup(client, student())
    item, = ask(client, question)['answer']['items']
    assert item['status'] == 'needs_clarification'
    assert '国家奖学金' in item['content'] and '学业奖学金' in item['content']


def test_award_choice_is_understood_as_an_amount_followup(client):
    signup(client, student())
    doc_id = publish('国家励志奖学金评选办法', '国家励志奖学金的奖励标准为每人每年6000元。', year=2026)
    initial = ask(client, '2026年奖学金有多少')
    item, = ask(client, '国励', previous_id=initial['id'])['answer']['items']
    assert item['status'] == 'answered' and '6000元' in item['content']
    assert item['citations'][0]['document_id'] == doc_id


@pytest.mark.parametrize("followup", ["都说一下", "每个都讲一下", "我想横向对比一下", "都要"])
def test_all_award_amounts_share_one_answer_and_keep_the_selected_year(client, monkeypatch, followup):
    signup(client, student())
    ids = {
        publish('国家奖学金评选办法', '国家奖学金的奖励标准为每人每年10000元。', year=2025),
        publish('国家励志奖学金评选办法', '国家励志奖学金的奖励标准为每人每年6000元。', year=2025),
        publish('本科生奖学金评定管理办法', '一等奖：1500元，二等奖：1000元，三等奖：500元。', year=2025),
    }
    publish('国家奖学金评选办法', '国家奖学金的奖励标准为每人每年12000元。', year=2026)
    initial = ask(client, '2025年奖学金多少钱？')
    searched = []
    if followup in {'我想横向对比一下', '都要'}:
        monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
        def plan(db, text, context):
            assert text == followup and context['previous_branch']['kind'] == 'scholarship_amount'
            return [{'question': text, 'kind': 'campus',
                     'branch_action': 'none' if followup == '都要' else 'all_previous'}]
        monkeypatch.setattr(conversation, 'plan_turn', plan)
        def amount(db, question, *, campus_gap):
            assert campus_gap and '攀登奖学金' in question
            searched.append(question)
            return {'status': 'web_answer', 'content': '每学年7500元。', 'citations': [],
                    'web_sources': [{'url': 'https://example.org/award'}]}
        monkeypatch.setattr(conversation, 'find_web_answer', amount)
    item, = ask(client, followup, previous_id=initial['id'])['answer']['items']
    assert item['status'] == 'answered' and len(item['content']) <= 500
    assert all(value in item['content'] for value in ('10000元', '6000元', '学业奖学金', '攀登奖学金'))
    assert '12000元' not in item['content']
    assert {source['document_id'] for source in item['citations']} == ids
    assert len(item['content'].splitlines()) == 4
    if followup in {'我想横向对比一下', '都要'}:
        assert len(searched) == 1 and '7500元' in item['content'] and item['web_sources']


@pytest.mark.parametrize('question', ['桑麻奖学金有多少钱', '国家奖学金有多少钱，怎么申请？', '研究生国家奖学金有多少'])
def test_specific_award_path_does_not_intercept_other_awards_or_mixed_questions(question):
    from app.scholarship import scholarship_request
    assert scholarship_request(question) is None


def test_national_scholarship_amount_alias_falls_back_to_native_search(client, monkeypatch):
    signup(client, student())
    publish('国家奖学金评审打分表', '智育排名30分，综合测评排名20分。', year=2026)
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    def money_result(db, question, *, campus_gap):
        assert campus_gap and '本科生国家奖学金' in question and '奖励金额' in question
        return {'status': 'web_answer', 'content': '本科生国家奖学金标准为每生每学年10000元。',
                'citations': [], 'web_sources': [{'url': 'https://welcome.wtu.edu.cn/info/1021/2031.htm'}]}
    monkeypatch.setattr(conversation, 'find_web_answer', money_result)
    item, = ask(client, '国家级奖学金有多少钱')['answer']['items']
    assert item['status'] == 'web_answer' and '10000元' in item['content']
    assert item['knowledge_gap'] and item['web_sources']


def test_national_scholarship_dual_ranking_requirements_quote_complete_rule(client, monkeypatch):
    signup(client, student())
    rule = ('（六）上一学年度智育成绩排名与综合测评成绩排名均在班级或专业前10%（含10%）且无不及格科目。'
            '如在其他方面表现非常突出，智育成绩排名与综合测评成绩排名均没有进入班级和专业前10%，'
            '但达到前30%（含30%）的学生且无不及格科目，也可申请国家奖学金，但需提交详细的证明材料，'
            '证明材料须经学院审核盖章确认。')
    doc_id = publish('管理学院本科生国家奖学金评选办法（2026年修订版）', rule, year=2026)
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    def unexpected(*args, **kwargs):
        raise AssertionError('完整排名条款可直接引用')
    monkeypatch.setattr(conversation, 'plan_turn', unexpected)
    monkeypatch.setattr(conversation, 'find_web_answer', unexpected)
    item, = ask(client, '申请国家奖学金，智育成绩和综合测评排名需要满足什么要求？', business_year=2026)['answer']['items']
    assert item['status'] == 'answered' and '前10%' in item['content'] and '前30%' in item['content']
    assert '无不及格' in item['content'] and '审核盖章' in item['content']
    assert item['citations'][0]['document_id'] == doc_id


def test_policy_amount_shows_original_model_reply_without_retry(client, monkeypatch):
    from app.qa import DeepSeekModel

    signup(client, student())
    rule = '特殊课程补贴为每人每年600元。休学期间不发放。'
    doc_id = publish('特殊课程补贴规定', rule)
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    calls = []

    def single_reply(_, query, evidence, *, retry=False):
        calls.append(retry)
        return {'answer': '每人每年600元，休学时停发。' if retry else '管小智查到啦，补贴为800元。',
                'selections': [{'chunk_id': evidence[0]['chunk_id'], 'quote': '模型改写过的摘录'}]}

    monkeypatch.setattr(DeepSeekModel, 'select_evidence', single_reply)
    item, = ask(client, '特殊课程补贴有多少钱？')['answer']['items']
    assert calls == [False] and item['status'] == 'answered'
    assert item['content'] == '管小智查到啦，补贴为800元。'
    assert item['citations'][0]['document_id'] == doc_id


def test_explanation_with_a_natural_count_is_not_rejected(client, monkeypatch):
    from app.qa import DeepSeekModel

    signup(client, student())
    rule = '申诉可提交申请表与说明函。'
    publish('学生申诉材料办法', rule)
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    answer = '管小智找到啦，申诉可交两项材料：申请表和说明函 😊'
    calls = []

    def single_reply(_, query, evidence, *, retry=False):
        calls.append(retry)
        return {'answer': answer, 'selections': [{'chunk_id': evidence[0]['chunk_id'], 'quote': rule}]}

    monkeypatch.setattr(DeepSeekModel, 'select_evidence', single_reply)
    item, = ask(client, '学生申诉需要准备哪些材料？')['answer']['items']
    assert calls == [False] and item['status'] == 'answered'
    assert item['content'] == answer and item['citations']


def test_examples_can_be_answered_and_are_withdrawn_when_source_is_disabled(admin_client, monkeypatch):
    client = admin_client
    assert signup(client, student()).status_code == 200
    assert client.get('/api/question-examples').json() == []
    content_hash = next(Path(CALENDAR_DATA).glob('*.json')).stem
    with SessionLocal() as db:
        calendar = Document(title='校历', content_hash=content_hash, status='published', parse_status='ready')
        scholarship = Document(title='国家奖学金评审打分表', status='published', parse_status='ready')
        contest = Document(title='学科竞赛目录', status='published', parse_status='ready')
        db.add_all([calendar, scholarship, contest])
        db.flush()
        build_chunks(db, calendar, verified_calendar_parts(content_hash), structured=True)
        build_chunks(db, scholarship, [('第1页', '智育排名（30分）：专业排名第一加30分，第二名29分，按名次依次递减1分。')],
                     structured=True)
        build_chunks(db, contest, [('第1页' + CATALOG_MARKER + '2025年序号2',
                                    '2025年学科竞赛目录：挑战杯全国大学生课外学术科技作品竞赛（专项赛），等级：A类。')],
                     structured=True)
        document_ids = [calendar.id, scholarship.id, contest.id]
        db.commit()
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    examples = client.get('/api/question-examples').json()
    assert len(examples) == 3
    for index, (question, expected) in enumerate(zip(examples, ['1月18日', '28分', 'A类'], strict=True)):
        response = client.post('/api/questions', json={'request_id': f'calendar-example-{index:02d}', 'text': question})
        answer = wait_for_answer(client, response.json()['id'])['answer']['items'][0]
        assert answer['status'] == 'answered' and expected in answer['content']
        assert answer['citations'][0]['document_id'] == document_ids[index]
    assert client.post(f'/api/admin/documents/{document_ids[1]}/disable').status_code == 200
    assert client.get('/api/question-examples').json() == [examples[0], examples[2]]
    assert client.post(f'/api/admin/documents/{document_ids[1]}/publish').status_code == 200
    assert client.get('/api/question-examples').json() == examples


def test_alert_review_context_and_legacy_preview(admin_client):
    client = admin_client
    assert signup(client, student()).status_code == 200
    def question(index, text, *, session=None, previous=None):
        response = client.post('/api/questions', json={
            'request_id': f'alert-thread-{index:05d}', 'text': text,
            'conversation_id': session, 'previous_id': previous,
        })
        assert response.status_code == 202
        question_id = response.json()['id']
        wait_for_answer(client, question_id)
        return question_id
    session = 'a' * 32
    first = question(1, '学习压力很大', session=session)
    question(2, '这条来自其他会话', session='b' * 32)
    trigger = question(3, '我不想活了', session=session)
    last = question(4, '已经联系到可信任的人', session=session)
    event, = client.get('/api/admin/events').json()
    assert event['review'] == 'pending'
    assert event['mail']['body'].startswith('管理学院危机预警')
    assert '13800000001' not in event['mail']['body']
    detail_url = f"/api/admin/events/{event['id']}/conversation"
    detail = client.get(detail_url).json()
    assert detail['complete']
    assert [turn['id'] for turn in detail['turns']] == [first, trigger, last]
    assert all(turn['replies'] for turn in detail['turns'])
    assert client.patch(f"/api/admin/events/{event['id']}", json={
        'state': 'closed', 'note': '核对了会话', 'review': 'false_alarm'
    }).status_code == 200
    assert client.get('/api/admin/events').json()[0]['review'] == 'false_alarm'
    legacy_first = question(5, '旧版会话的前文')
    legacy_trigger = question(6, '我不想活了', previous=legacy_first)
    old, = [item for item in client.get('/api/admin/events').json() if item['question_id'] == legacy_trigger]
    with SessionLocal() as db:
        job = db.scalar(select(MailJob).where(MailJob.event_id == old['id']))
        job.body = '{"old": "legacy"}'
        db.commit()
    old, = [item for item in client.get('/api/admin/events').json() if item['id'] == old['id']]
    assert old['mail']['legacy_preview'] and old['mail']['body'].startswith('管理学院危机预警')
    old_detail = client.get(f"/api/admin/events/{old['id']}/conversation").json()
    assert not old_detail['complete']
    assert [turn['id'] for turn in old_detail['turns']] == [legacy_first, legacy_trigger]
    assert client.post('/api/admin/logout').status_code == 200
    assert client.get(detail_url).status_code == 401
