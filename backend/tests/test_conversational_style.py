import json
from pathlib import Path

import httpx
import pytest

from app import qa
from app.qa import DeepSeekModel, InvalidEvidence, verified_excerpts


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
    assert '轻松自然' in system and '少量贴切幽默' in system and '颜文字' in system
    assert '严肃事项保持认真' in system and '不使用玩笑或颜文字' in system
    assert '不得削弱或遗漏' in system and '不得编造资料没有的事实' in system
    assert 'selections 必须保留原文' in system and '500字符' in system


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
