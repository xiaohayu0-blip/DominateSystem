import json
from types import SimpleNamespace

import httpx
import pytest

from app import conversation as chat
from app.config import settings
from app.db import SessionLocal
from app.qa import DeepSeekModel, ModelFailure, context_for
from app.schemas import QuestionInput

real_search = chat.search_web
real_plan = chat.plan_turn


def plan(monkeypatch, tasks):
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'plan_turn', lambda *args: tasks)


def test_general_chat_does_not_require_documents(monkeypatch):
    plan(monkeypatch, [{'question': '你好', 'kind': 'general'}])
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *args: {'answer': '你好呀，我是小八 😊'})
    with SessionLocal() as db:
        answer = chat.answer_turn(db, '你好', {})
    assert answer['items'][0]['status'] == 'general_answer'
    assert answer['items'][0]['citations'] == []


def test_general_information_uses_deepseek_search_sources(monkeypatch):
    question = '机会成本是什么意思？'
    url = 'https://example.org/economics/opportunity-cost'
    plan(monkeypatch, [{'question': question, 'kind': 'general'}])
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': '机会成本是放弃的最佳替代选项的价值。',
            'annotations': [{'type': 'url_citation', 'url_citation': {'url': url}}],
            'server_tool_calls': [{'calls': [{'status': 'completed', 'sources': [
                {'type': 'url', 'url': url, 'title': '经济学概念',
                 'snippet': '机会成本是放弃的最佳替代选项的价值。'}]}]}],
        }}]})

    real_client = httpx.Client
    monkeypatch.setattr(chat.httpx, 'Client', lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw))
    with SessionLocal() as db:
        item = chat.answer_turn(db, question, {})['items'][0]
    assert item['status'] == 'web_answer'
    assert '最佳替代选项' in item['content']
    assert item['web_sources'][0]['url'] == url
    assert seen[0]['messages'][-1]['content'] == question
    assert {'type': 'deepseek_web_search'} in seen[0]['tools']
    assert 'response_format' not in seen[0]


def test_deepseek_can_answer_without_tools_if_the_endpoint_rejects_them(monkeypatch):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if 'tools' in body:
            return httpx.Response(400)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                                                     'message': {'content': '通用参考答复。'}}]})

    real_client = httpx.Client
    monkeypatch.setattr(settings(), 'deepseek_model', 'deepseek-flash')
    monkeypatch.setattr(chat.httpx, 'Client', lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw))
    assert DeepSeekModel().request_web('请直接回答', '这是什么') == {'content': '通用参考答复。'}
    assert len(requests) == 2 and 'tools' not in requests[1]


def test_partial_knowledge_is_kept_with_missing_material(monkeypatch):
    plan(monkeypatch, [{'question': '申请条件', 'kind': 'campus'},
                       {'question': '今年报名截止日期', 'kind': 'campus'}])
    def policy(db, text, context):
        item = {'question': text, 'status': 'answered' if text == '申请条件' else 'missing_material',
                'content': '条件原文' if text == '申请条件' else '缺失', 'citations': []}
        return {'items': [item]}
    monkeypatch.setattr(chat, 'policy_answer', policy)
    monkeypatch.setattr(DeepSeekModel, 'request_web', lambda *a: (_ for _ in ()).throw(ModelFailure('search_failed')))
    with SessionLocal() as db:
        answer = chat.answer_turn(db, '申请条件和今年截止日期', {})
    assert answer['items'][0]['content'] == '条件原文'
    assert answer['items'][1]['status'] == 'missing_material'
    assert '暂无资料' in answer['items'][1]['content']


def test_web_answer_requires_verified_search_sources(monkeypatch):
    plan(monkeypatch, [{'question': '最新通知', 'kind': 'web'}])
    monkeypatch.setattr(DeepSeekModel, 'request_web', lambda *a: {
        'content': '我暂时无法确认最新通知。',
        'annotations': [{'type': 'url_citation', 'url_citation': {'url': 'https://example.org/made-up'}}],
        'server_tool_calls': [{'calls': [{'status': 'completed', 'sources': [
            {'url': 'https://www.wtu.edu.cn/a.htm', 'title': '通知', 'snippet': '通知于2026年11月发布。'}]}]}],
    })
    with SessionLocal() as db:
        answer = chat.answer_turn(db, '最新通知', {})
    assert answer['items'][0]['status'] == 'web_unavailable'
    assert not answer['items'][0].get('web_sources')


def test_search_filters_other_schools_and_forged_official_domains(monkeypatch):
    rss = '''<rss><channel><item><title>通知</title><link>https://em.wtu.edu.cn/info/a.htm</link>
    <description>申请需要成绩单。</description></item><item><title>外校</title>
    <link>https://other.edu.cn/a</link><description>申请无需材料。</description></item>
    <item><title>假官网</title><link>https://wtu.edu.cn.evil.com/a</link>
    <description>假规则</description></item></channel></rss>'''
    real = httpx.Client
    monkeypatch.setattr(chat.httpx, 'Client', lambda **kw: real(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=rss)), **kw))
    sources = real_search('申请', official=True)
    assert len(sources) == 1 and sources[0]['url'].startswith('https://em.wtu.edu.cn/')
    assert sources[0]['published_at'] is None and sources[0]['retrieved_at']


def test_new_topic_resets_prior_scope_and_followups_keep_anchor():
    previous = SimpleNamespace(text='2024年度奖学金如何申请', context={
        'business_year': 2024, 'selected_year': 2024, 'admission_year': 2022},
        answer={'items': [{'question': '2024年度奖学金如何申请', 'content': '请提交申请表。'}]})
    def data(text):
        return QuestionInput(request_id='conversation-test-01', text=text)
    fresh = context_for(data('解释一下机器学习'), previous)
    assert fresh['selected_year'] is None and fresh['admission_year'] is None
    follow = context_for(data('那需要哪些材料'), previous)
    assert '奖学金' in follow['followup_question'] and '哪些材料' in follow['followup_question']
    assert follow['selected_year'] == 2024
    grade = context_for(data('大二可以吗'), previous)
    assert '奖学金' in grade['followup_question'] and '大二' in grade['followup_question']


def test_search_timeout_is_a_controlled_failure(monkeypatch):
    real = httpx.Client
    def fail(request):
        raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(chat.httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(fail), **kw))
    try:
        real_search('通知', official=True)
        assert False, 'timeout must not appear as an empty successful search'
    except ModelFailure:
        pass


def test_official_search_falls_back_to_school_pages(monkeypatch):
    real = httpx.Client
    def handler(request):
        if request.url.host == 'www.bing.com':
            return httpx.Response(200, text='<rss><channel/></rss>')
        return httpx.Response(200, headers={'content-type': 'text/html'}, text='<html><title>学院通知</title><body>'
                              '<a href="/info/1/2.htm">奖学金申请通知</a>'
                              '<article>奖学金申请需要成绩单。</article></body></html>')
    monkeypatch.setattr(chat.httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    sources = real_search('奖学金申请', official=True)
    assert sources and all(chat.safe_url(s['url'], True) for s in sources)
    assert any('成绩单' in s['excerpt'] for s in sources)


def test_web_answer_keeps_real_links_and_rejects_wrong_numbers(monkeypatch):
    plan(monkeypatch, [{'question': '最新发布', 'kind': 'web'}])
    url = 'https://example.org/news'
    message = {
        'content': '开放3个申请名额，仅限大二学生。',
        'annotations': [{'type': 'url_citation', 'url_citation': {'url': url}}],
        'server_tool_calls': [{'calls': [{'status': 'completed', 'sources': [
            {'url': url, 'title': '官方发布', 'snippet': '开放3个申请名额，仅限大二学生。'}]}]}],
    }
    monkeypatch.setattr(DeepSeekModel, 'request_web', lambda *a: message)
    with SessionLocal() as db:
        answer = chat.answer_turn(db, '最新发布', {})
        assert answer['items'][0]['status'] == 'web_answer'
        assert answer['items'][0]['web_sources'][0]['url'] == url
        message['content'] = '开放30个申请名额，仅限大二学生。'
        failed = chat.answer_turn(db, '最新发布', {})
        assert failed['items'][0]['status'] == 'web_unavailable'


def test_general_answer_rewrites_over_limit_and_does_not_truncate(monkeypatch):
    answers = iter([{'answer': '字' * 501}, {'answer': '机会成本是放弃的最佳替代选择的价值 😊'}])
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *a: next(answers))
    with SessionLocal() as db:
        answer = chat.general_answer(db, '解释机会成本', {})
    assert answer['content'].endswith('😊')


def test_history_is_bounded_and_scrubbed():
    previous = None
    for i in range(8):
        data = QuestionInput(request_id='conversation-history-01', text=f'讲解机会成本{i}')
        context = context_for(data, previous)
        previous = SimpleNamespace(text='我的手机13800000001', context=context,
                                   answer={'items': [{'content': '可以继续讲解 😊'}]})
    assert len(context['history']) == 6
    assert '13800000001' not in str(context['history'])


def test_partial_single_policy_answer_keeps_citations_and_records_only_gap(monkeypatch):
    monkeypatch.setattr(DeepSeekModel, 'request_web', lambda *a: {'content': '今年的截止时间暂时无法确认。'})
    plan(monkeypatch, [{'question': '申请条件和截止时间', 'kind': 'campus'}])
    cited = {'document_id': 'real-id', 'excerpt': '需要申请表。'}
    monkeypatch.setattr(chat, 'policy_answer', lambda *a: {'items': [{
        'question': '申请条件和截止时间', 'status': 'answered', 'content': '需要申请表。',
        'citations': [cited], 'missing_questions': ['今年申请截止时间']}]})
    with SessionLocal() as db:
        answer = chat.answer_turn(db, '申请条件和截止时间', {})
    assert answer['items'][0]['citations'] == [cited]
    assert answer['items'][1]['question'] == '今年申请截止时间'


def test_planner_receives_history_and_rejects_unknown_routes(monkeypatch):
    seen = []
    def model(self, system, payload):
        seen.append(payload)
        return {'tasks': [{'question': '大二能申请奖学金吗', 'kind': 'campus', 'clauses': [0]}]}
    monkeypatch.setattr(DeepSeekModel, 'request_json', model)
    with SessionLocal() as db:
        tasks = real_plan(db, '大二可以吗', {'history': [{'role': 'user', 'content': '奖学金'}]})
    assert tasks[0]['kind'] == 'campus' and seen[0]['recent_conversation'][0]['content'] == '奖学金'


def test_web_address_from_transport_metadata_is_valid_evidence(monkeypatch):
    source = {'id': 'official-0', 'title': '管理学院', 'url': 'https://em.wtu.edu.cn/',
              'excerpt': '武汉纺织大学管理学院。', 'published_at': None, 'retrieved_at': '2026-11-17'}
    monkeypatch.setattr(chat, 'search_web', lambda *a, **kw: [source])
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *a: {
        'answer': '官网地址是 https://em.wtu.edu.cn/ 😊', 'source_ids': ['official-0']})
    with SessionLocal() as db:
        item = chat.web_answer(db, '官网在哪里', official=True)
    assert item['status'] == 'web_answer' and item['web_sources'] == [source]


def test_resolved_followup_year_is_used_for_policy_scope(monkeypatch):
    plan(monkeypatch, [{'question': '2024年度奖学金申请需要哪些材料', 'kind': 'campus'}])
    seen = []
    def policy(db, text, context):
        seen.append(context)
        return {'items': [{'question': text, 'status': 'answered', 'content': '条件', 'citations': []}]}
    monkeypatch.setattr(chat, 'policy_answer', policy)
    with SessionLocal() as db:
        chat.answer_turn(db, '这项申请材料还有要求吗', {'business_year': 2026, 'selected_year': None})
    assert seen[0]['business_year'] == 2024 and seen[0]['selected_year'] == 2024


@pytest.mark.parametrize('first,followup', [
    ('当寝室长能加综测分吗？', '能加多少啊？'),
    ('奖学金能申请吗？', '那需要哪些材料？'),
    ('病假可以请吗？', '具体怎么办理？'),
    ('补贴能领取吗？', '什么时候发放？'),
])
def test_router_rejoins_subjectless_followup_before_answering(monkeypatch, first, followup):
    text = first + followup
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *a: {'tasks': [
        {'question': first, 'kind': 'campus', 'clauses': [0]},
        {'question': first + followup, 'kind': 'campus', 'clauses': [1]}]})
    with SessionLocal() as db:
        tasks = real_plan(db, text, {})
    assert len(tasks) == 1
    assert first in tasks[0]['question'] and followup in tasks[0]['question']


def test_router_keeps_named_independent_topics_separate(monkeypatch):
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *a: {'tasks': [
        {'question': '寝室长加几分？', 'kind': 'campus', 'clauses': [0]},
        {'question': '病假要交哪些材料？', 'kind': 'campus', 'clauses': [1]}]})
    with SessionLocal() as db:
        tasks = real_plan(db, '寝室长加几分？病假要交哪些材料？', {})
    assert len(tasks) == 2


def test_router_cannot_drop_current_question_clauses(monkeypatch):
    from app.qa import InvalidEvidence
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *a: {'tasks': [
        {'question': '寝室长加几分？', 'kind': 'campus', 'clauses': [0]}]})
    with SessionLocal() as db, pytest.raises(InvalidEvidence):
        real_plan(db, '寝室长加几分？病假要交哪些材料？', {})
