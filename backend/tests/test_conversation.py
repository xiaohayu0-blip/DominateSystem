import json
from types import SimpleNamespace

import httpx
import pytest

from app import conversation as chat
from app.config import settings
from app.db import SessionLocal
from app.qa import DeepSeekModel, ModelFailure, context_for
from app.schemas import QuestionInput
from conftest import signup, student
from test_knowledge import ask, publish

real_search = chat.search_web
real_plan = chat.plan_turn


@pytest.mark.parametrize('reply', ['因病', '病假', '那就因病吧'])
def test_sick_leave_short_answer_resumes_original_question_and_cites_rule(client, monkeypatch, reply):
    signup(client, student())
    question = '请假要交哪些材料？'
    rule = '学生请事假的，应提供请假事由的证明材料。学生请病假的，应提供校医院出具的病假证明材料。学生请公假的，应当由组织单位提供证明。'
    doc_id = publish('武汉纺织大学学生考勤管理办法', rule)
    expected = '请病假需要提交校医院出具的病假证明。'
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'plan_turn', real_plan)

    def model(self, system, payload):
        if 'current_question' in payload:
            if payload['current_question'] == question:
                return {'tasks': [{'kind': 'campus', 'question': question, 'clauses': [0],
                                   'clarify_options': ['因病请假', '因事请假', '公假']}]}
            return {'tasks': [{'kind': 'general', 'question': reply, 'clauses': [0]}]}
        assert '请假要交哪些材料' in payload['question'] and '因病请假' in payload['question']
        source = next(item for item in payload['untrusted_evidence'] if item['document_id'] == doc_id)
        return {'answer': expected, 'selections': [{'chunk_id': source['chunk_id'],
                'quote': '学生请病假的，应提供校医院出具的病假证明材料。'}]}

    monkeypatch.setattr(DeepSeekModel, 'request_json', model)
    first = ask(client, question)
    assert first['answer']['items'][0]['status'] == 'needs_clarification'
    item, = ask(client, reply, previous_id=first['id'])['answer']['items']
    assert item['status'] == 'answered' and item['content'] == expected
    assert {source['document_id'] for source in item['citations']} == {doc_id}


@pytest.mark.parametrize('question,options,short,selected', [
    ('补办学生证要带什么？', ['本人办理', '委托办理'], '本人', '本人办理'),
    ('转专业要准备什么材料？', ['校内转专业', '跨校转入'], '校内', '校内转专业'),
])
def test_short_branch_reply_keeps_other_campus_topics(monkeypatch, question, options, short, selected):
    context = {'business_year': 2026, 'previous_branch': {
        'kind': 'topic_branch', 'question': question, 'options': options, 'scope': {'business_year': 2025},
    }}
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'plan_turn', lambda *args: pytest.fail('唯一匹配的选项无需再分流'))

    def policy(db, text, scope):
        assert question in text and selected in text
        assert scope['business_year'] == 2025
        return {'items': [{'question': text, 'status': 'answered', 'content': '按所选办理方式提交材料。'}]}

    monkeypatch.setattr(chat, 'policy_answer', policy)
    item, = chat.answer_turn(None, short, context)['items']
    assert item['status'] == 'answered'


def test_ambiguous_short_branch_reply_is_not_guessed(monkeypatch):
    context = {'previous_branch': {'kind': 'topic_branch', 'question': '复学怎么办理？',
               'options': ['因病休学后复学', '因病保留学籍后入学'], 'scope': {}}}
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'plan_turn', lambda db, text, ctx: [{
        'kind': 'campus', 'question': context['previous_branch']['question'],
        'clarify_options': ctx['previous_branch']['options'],
    }])
    item, = chat.answer_turn(None, '因病', context)['items']
    assert item['status'] == 'needs_clarification'


def test_retry_can_reuse_pending_choice_after_failed_reply():
    pending = {'kind': 'topic_branch', 'question': '请假要交哪些材料？',
               'options': ['因病请假', '因事请假', '公假'], 'scope': {'business_year': 2025}}
    previous = SimpleNamespace(text='因病', context={'previous_branch': pending},
                               answer={'items': [{'status': 'validation_failed'}]})
    retry = QuestionInput(request_id='branch-retry-0001', text='因病')
    assert context_for(retry, previous)['previous_branch'] == pending


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
    assert '知识库暂时没有' in answer['items'][1]['content']


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
        real_search('通知')
        assert False, 'timeout must not appear as an empty successful search'
    except ModelFailure:
        pass


def test_web_answer_uses_supported_source_numbers_and_keeps_links(monkeypatch):
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
        assert '30个' not in failed['items'][0]['content']


def test_general_answer_preserves_model_text_without_character_cutoff(monkeypatch):
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *args: {'answer': '字' * 501})
    with SessionLocal() as db:
        answer = chat.general_answer(db, '解释机会成本', {})
    assert answer['content'] == '字' * 501


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
        item = chat.web_answer(db, '官网在哪里')
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


@pytest.mark.parametrize('chosen,other', [('国家奖学金', '社会奖学金'), ('因病请假', '因事请假')])
def test_unselected_branch_cannot_become_a_web_followup(monkeypatch, chosen, other):
    plan(monkeypatch, [{'question': '请比较两个选项', 'kind': 'campus', 'branch_action': 'all_previous'}])
    monkeypatch.setattr(chat, 'policy_answer', lambda db, question, scope: {'items': [{
        'question': question, 'status': 'answered', 'content': f'{chosen}的相关规定。', 'citations': [],
        'missing_questions': [f'{other}的条件是什么？'],
    }]})

    def unexpected(*args, **kwargs):
        pytest.fail('未选择的分支被发去联网查询')

    monkeypatch.setattr(chat, 'fill_material_gap', unexpected)
    previous = {'kind': 'topic_branch', 'question': '这个怎么申请？', 'options': [chosen, other], 'scope': {}}
    item, = chat.answer_turn(None, chosen, {'previous_branch': previous})['items']
    assert chosen in item['content'] and other not in item['content']


def test_multiple_leave_types_keep_local_conclusion_and_search_only_missing_type(monkeypatch):
    question = '因病请假和因事请假分别需要什么材料？'
    plan(monkeypatch, [{'question': question, 'kind': 'campus', 'branch_action': 'all',
                       'branches': ['因病请假', '因事请假']}])

    def policy(db, text, scope):
        assert scope['_branch_options'] == ['因病请假', '因事请假']
        return {'items': [{'question': text, 'status': 'answered', 'content': '因病请假：提交诊断证明。',
                           'citations': [], 'missing_questions': ['因事请假要交什么材料？']}]}

    def search(db, text, *, campus_gap, brief):
        assert campus_gap and brief and text == '因事请假要交什么材料？'
        return {'status': 'web_answer', 'content': '因事请假：提交请假申请。', 'citations': [],
                'web_sources': [{'url': 'https://example.org/leave'}]}

    monkeypatch.setattr(chat, 'policy_answer', policy)
    monkeypatch.setattr(chat, 'find_web_answer', search)
    def compose(db, system, payload):
        assert '诊断证明' in payload['verified_facts'] and '请假申请' in payload['verified_facts']
        return {'answer': '因病请假交诊断证明。\n因事请假交请假申请。'}

    monkeypatch.setattr(chat, 'model_json', compose)
    item, = chat.answer_turn(None, question, {})['items']
    assert item['content'] == '因病请假交诊断证明。\n因事请假交请假申请。'
    assert item['web_sources']


@pytest.mark.parametrize('reply,chosen', [
    ('生病的那一种', ['因病请假']),
    ('都说一下', ['因病请假', '因事请假']),
    ('我想比较它们的区别', ['因病请假', '因事请假']),
])
def test_router_asks_for_branch_and_resumes_the_same_request(monkeypatch, reply, chosen):
    question = '2025年请假要交哪些材料？'
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'plan_turn', real_plan)
    def route(self, system, payload):
        task = {'question': question, 'kind': 'campus', 'clauses': [0]}
        if previous := payload['previous_branch']:
            assert previous['options'] == ['因病请假', '因事请假']
            assert payload['current_question'] == reply
            task.update(branch_action='all_previous' if len(chosen) == 2 else 'select_previous',
                        branches=[] if len(chosen) == 2 else chosen)
        else:
            task['clarify_options'] = ['因病请假', '因事请假']
        return {'tasks': [task]}
    monkeypatch.setattr(DeepSeekModel, 'request_json', route)
    data = QuestionInput(request_id='branch-followup-01', text=question)
    with SessionLocal() as db:
        answer = chat.answer_turn(db, question, context_for(data))
        item, = answer['items']
        assert item['status'] == 'needs_clarification'
        assert '因病请假' in item['content'] and '因事请假' in item['content']
        previous = SimpleNamespace(text=question, context=answer['context'], answer=answer)
        scope = context_for(QuestionInput(request_id='branch-followup-02', text=reply), previous)
        seen = []
        def policy(db, text, context):
            seen.append((text, context))
            return {'items': [{'question': text, 'status': 'answered', 'content': '申请材料见原文。', 'citations': []}]}
        monkeypatch.setattr(chat, 'policy_answer', policy)
        result, = chat.answer_turn(db, reply, scope)['items']
    queried, query_context = seen[0]
    assert result['status'] == 'answered'
    assert len(seen) == 1 and query_context['business_year'] == 2025
    assert all(branch in queried for branch in chosen) and '材料' in queried
    assert ('因事请假' in queried) == (len(chosen) == 2)


def test_router_cannot_drop_current_question_clauses(monkeypatch):
    from app.qa import InvalidEvidence
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *a: {'tasks': [
        {'question': '寝室长加几分？', 'kind': 'campus', 'clauses': [0]}]})
    with SessionLocal() as db, pytest.raises(InvalidEvidence):
        real_plan(db, '寝室长加几分？病假要交哪些材料？', {})
