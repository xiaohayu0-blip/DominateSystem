import json
from pathlib import Path

import httpx
import pytest

from app import qa
from app import conversation as chat
from app.qa import DeepSeekModel, InvalidEvidence, verified_excerpts

real_group_questions = DeepSeekModel.group_questions
real_plan_turn = chat.plan_turn
real_model_json = chat.model_json


def assert_markdown_system_prompt(system):
    sections = system.split('\n\n')
    assert len(sections) > 1, 'DeepSeek system prompt should contain Markdown sections'
    for section in sections:
        heading, _, paragraph = section.partition('\n')
        assert heading.startswith('## ') and heading[3:].strip()
        assert paragraph.strip() and '\n' not in paragraph


def test_live_adapter_requests_friendly_style_without_weakening_policy_constraints(monkeypatch):
    seen = []
    real_client = httpx.Client

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"selections": []}'}}]})

    monkeypatch.setattr(qa.httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    DeepSeekModel().select_evidence('寝室长能加几分', [])
    system = seen[0]['messages'][0]['content']
    assert_markdown_system_prompt(system)
    assert '轻松自然' in system and '少量贴切幽默' in system and '颜文字' in system
    assert '严肃事项保持认真' in system and '不使用玩笑或颜文字' in system
    assert '不得削弱或遗漏' in system and '不得编造资料没有的事实' in system
    assert 'selections 必须保留原文' in system and '能用一句话说清就只写一句' in system


def test_deepseek_system_prompts_keep_markdown_in_http_payloads(monkeypatch):
    systems = []
    real_client = httpx.Client
    monkeypatch.setattr(DeepSeekModel, 'group_questions', real_group_questions)
    monkeypatch.setattr(chat, 'plan_turn', real_plan_turn)
    monkeypatch.setattr(chat, 'model_json', real_model_json)

    def handler(request):
        messages = json.loads(request.content)['messages']
        assert messages[0]['role'] == 'system'
        systems.append(messages[0]['content'])
        content = json.dumps({'answer': '答复', 'tasks': [
            {'question': '你好', 'kind': 'chat', 'clauses': [0]},
        ], 'groups': [[0]]})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})

    monkeypatch.setattr(qa.httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(chat, 'model_budget_available', lambda db: True)
    monkeypatch.setattr(chat.settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'prepare_turn', lambda *args: {'items': [
        {'question': '你好', 'status': 'answered', 'content': '事实', '_compose': True},
    ]})
    question = '全校各学院团委书记是谁？'
    DeepSeekModel().select_evidence(question, [], retry=True)
    DeepSeekModel().group_questions(question, [question])
    chat.plan_turn(None, '你好', {})
    chat.direct_model_answer(None, question, campus_gap=True, brief=True, campus_public='person')
    chat.general_answer(None, '你好', {})
    chat.answer_with_web_sources(None, question, [{'id': 'web-0'}],
                                 campus_gap=True, brief=True, campus_public='person')
    chat.answer_turn(None, '你好', {})

    assert len(systems) == 7
    for system in systems:
        assert_markdown_system_prompt(system)
    assert '\n\n## 重试要求\n' in systems[0]
    for system in (systems[3], systems[5]):
        assert '\n\n## 公开校园人员查询\n' in system
        assert '\n\n## 全校各学院查询\n' in system


def test_friendly_dorm_answer_preserves_scores_exceptions_and_original_sources():
    source = json.loads((Path(__file__).parent / 'fixtures/real-dorm-leader.json').read_text(encoding='utf-8'))['sources'][0]
    answer = ('能加，不过先别急着把1分记满，得看实际工作表现 (｡•̀ᴗ-)✧\n'
              '本科生可在综合测评中加0.5—1分，能与其他任职加分累计。寝室不足4人时，'
              '根据表现按相应比例加分；研究生则参照研究生奖学金评定细则。')
    selected = {'answer': answer, 'selections': [{'chunk_id': source['chunk_id'], 'quote': source['excerpt']}]}
    content, citations = verified_excerpts(selected, [source], cohort_options=False, deepseek=True)
    assert content == answer and citations == [source]
    assert citations[0]['excerpt'] == source['excerpt']
    with pytest.raises(InvalidEvidence, match='unsupported_summary_quantity'):
        verified_excerpts({**selected, 'answer': answer.replace('0.5—1分', '1.5分')},
                          [source], cohort_options=False, deepseek=True)
