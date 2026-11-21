from pathlib import Path

import pytest
from sqlalchemy import select

from app import conversation
from app.calendar_index import CALENDAR_DATA, verified_calendar_parts
from app.competition_catalog import CATALOG_MARKER
from app.config import settings
from app.db import SessionLocal
from app.models import Document, MailJob
from app.retrieval import build_chunks
from conftest import signup, student


@pytest.mark.parametrize(('question', 'classified_as'), [
    ('奖学金有多少', 'campus'),
    ('帮我联网确认我们学院的奖学金有多少？', 'web'),
])
def test_campus_material_gap_checks_official_web_and_discloses_unverified_result(monkeypatch, question, classified_as):
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(conversation, 'plan_turn', lambda *args: [
        {'kind': classified_as, 'question': question}])
    monkeypatch.setattr(conversation, 'policy_answer', lambda *args: {'items': [
        {'question': question, 'status': 'missing_material', 'content': '缺少依据', 'citations': []}]})
    def official_only(db, text, *, official):
        assert official and text == question
        raise conversation.MissingEvidence()
    monkeypatch.setattr(conversation, 'direct_model_answer', official_only)
    item, = conversation.answer_turn(None, question, {})['items']
    assert item['status'] == 'missing_material'
    assert '知识库暂时没有' in item['content'] and '暂未找到能核实' in item['content']


def test_unsourced_general_information_is_not_presented_as_answer(monkeypatch):
    question = '奖学金申请书怎么写？'
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(conversation, 'plan_turn', lambda *args: [
        {'kind': 'general', 'question': question}])
    monkeypatch.setattr(conversation, 'direct_model_answer', lambda *args, **kwargs: {
        'question': question, 'status': 'general_answer', 'content': '无出处', 'citations': []})
    item, = conversation.answer_turn(None, question, {})['items']
    assert item['status'] == 'web_unavailable'
    assert '未查到可核实' in item['content']


@pytest.mark.parametrize('question', ['你是谁', '小八，你叫什么名字？', '你好！'])
def test_assistant_introduction_works_without_model_or_search(monkeypatch, question):
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    def unexpected(*args, **kwargs):
        raise AssertionError('简单自我介绍不需要模型或联网')
    monkeypatch.setattr(conversation, 'plan_turn', unexpected)
    monkeypatch.setattr(conversation, 'direct_model_answer', unexpected)
    answer = conversation.answer_turn(None, question, {})
    item, = answer['items']
    assert item['status'] == 'general_answer'
    assert '小八' in item['content'] and '(｡•̀ᴗ-)✧' in item['content']
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
def test_public_school_profile_discloses_knowledge_gap_and_searches_official_web(monkeypatch, question):
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(conversation, 'plan_turn', lambda *args: [{'kind': 'web', 'question': question}])
    monkeypatch.setattr(conversation, 'policy_answer', lambda *args, **kwargs: {'items': [
        {'question': question, 'status': 'missing_material', 'content': '暂无资料', 'citations': []}]})
    monkeypatch.setattr(conversation, 'direct_model_answer', lambda *args, **kwargs: {
        'question': question, 'status': 'general_answer', 'content': '无出处答案', 'citations': []})
    def verified_web(db, text, *, official, search_query=None):
        assert official and text == question
        return {'status': 'web_answer', 'content': '学校前身始建于1958年，2010年更名。',
                'web_sources': [{'url': 'https://www.wtu.edu.cn/xxgk/xxjj.htm'}], 'citations': []}
    monkeypatch.setattr(conversation, 'web_answer', verified_web)
    item, = conversation.answer_turn(None, question, {})['items']
    assert item['status'] == 'web_answer'
    assert item['knowledge_gap']
    assert item['content'].startswith('知识库暂时没有相关资料。这是我联网查到的内容')
    assert '1958' in item['content']


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
        answer = client.get('/api/questions/' + response.json()['id']).json()['answer']['items'][0]
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
        return response.json()['id']
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
