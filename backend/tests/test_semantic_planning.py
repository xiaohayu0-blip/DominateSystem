import json

import httpx
import pytest

from app import qa
from app.config import settings
from app.db import SessionLocal
from app.question_planning import grouped_questions, question_clauses
from app.qa import DeepSeekModel, ModelFailure, planned_questions
from app.retrieval import retriever
from conftest import signup, student
from test_knowledge import ask, publish


@pytest.mark.parametrize("plan", [
    None, {}, {"groups": []}, {"groups": [[0]]}, {"groups": [[0, 0, 1, 2]]},
    {"groups": [[0, 1], [1, 2]]}, {"groups": [[0, 1, 3]]}, {"groups": [[0, 1, -1]]},
    {"groups": [[True, 1, 2]]}, {"groups": [[0, "1", 2]]}, {"groups": [[1, 0, 2]]},
    {"groups": [[1], [0, 2]]}, {"groups": [[], [0, 1, 2]]},
])
def test_planner_cannot_drop_repeat_invent_or_reorder_clauses(plan):
    with pytest.raises(ValueError):
        grouped_questions(["原句甲", "原句乙", "原句丙"], plan)


def test_interleaved_topics_can_rejoin_without_inventing_text():
    clauses = question_clauses("国家奖学金要什么材料？病假要怎么请？刚才那个奖学金什么时候截止？")
    assert grouped_questions(clauses, {"groups": [[0, 2], [1]]}) == [
        "国家奖学金要什么材料？刚才那个奖学金什么时候截止？", "病假要怎么请？"]


@pytest.mark.parametrize("text", [
    "家庭月收入1,000元，能申请补助吗？但有兼职收入，另外这个收入也要算吗？",
    "我要申请困难补助\n需要盖章吗\n复印件可以不",
])
def test_clause_reconstruction_preserves_numbers_conditions_and_punctuation(text):
    clauses = question_clauses(text)
    assert grouped_questions(clauses, {"groups": [list(range(len(clauses)))]}) == [text]
    if "1,000元" in text:
        assert "1,000元" in clauses[0]


@pytest.mark.parametrize("text", [
    "申请国家奖学金，综合测评排名需要满足什么要求？德育成绩呢？",
    "办理休学要交哪些材料？家长签字这块怎么弄？",
    "竞赛团队加分怎么算？第二作者这边是什么口径？",
    "申请宿舍调整需要什么？审核通过后的入住手续呢？",
    "申请资助要满足哪些条件？家庭收入这一项怎么认定？",
])
def test_complete_input_is_given_to_semantic_planner_and_each_group_keeps_context(text, monkeypatch):
    seen = []

    def classify(_, full_text, clauses):
        seen.append((full_text, clauses))
        return {"groups": [list(range(len(clauses)))]}

    monkeypatch.setattr(DeepSeekModel, "group_questions", classify)
    with SessionLocal() as db:
        result = planned_questions(db, text, DeepSeekModel())
    assert len(result) == 1 and seen[0][0] == text
    assert all(clause in result[0] for clause in seen[0][1])


@pytest.mark.parametrize("failure", ["unavailable", "invalid", "missing", "duplicate"])
def test_failed_planning_preserves_full_original_input(failure, monkeypatch):
    text = "申请国家奖学金，综测排名有要求吗？德育成绩呢？"

    def classify(*_):
        if failure == "unavailable":
            raise ModelFailure("model_unavailable")
        return {"invalid": None, "missing": {"groups": [[0]]},
                "duplicate": {"groups": [[0, 1], [1, 2]]}}[failure]

    monkeypatch.setattr(DeepSeekModel, "group_questions", classify)
    with SessionLocal() as db:
        assert planned_questions(db, text, DeepSeekModel()) == [text]


def test_single_clause_and_large_input_are_not_truncated_or_sent_to_planner(monkeypatch):
    monkeypatch.setattr(DeepSeekModel, "group_questions", lambda *_: pytest.fail("no planner call expected"))
    with SessionLocal() as db:
        for text in ["德育成绩是什么", "？".join(f"问题{index}" for index in range(40))]:
            assert planned_questions(db, text, DeepSeekModel()) == [text]


def test_planning_respects_daily_budget_and_concurrency(monkeypatch):
    monkeypatch.setattr(DeepSeekModel, "group_questions", lambda *_: pytest.fail("planning limit bypassed"))
    text = "奖学金条件？德育成绩呢？"
    monkeypatch.setattr(settings(), "model_daily_limit", 0)
    with SessionLocal() as db:
        assert planned_questions(db, text, DeepSeekModel()) == [text]
        monkeypatch.setattr(settings(), "model_daily_limit", 500)
        monkeypatch.setattr(qa, "semaphore", type("Busy", (), {"acquire": lambda self, **kw: False})())
        assert planned_questions(db, text, DeepSeekModel()) == [text]


def test_semantic_adapter_uses_complete_scrubbed_input_and_json_indices(monkeypatch):
    # Exercise the real HTTP adapter below the mocked grouping method.
    seen = []
    client = httpx.Client

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"groups":[[0,1]]}'}}]})

    monkeypatch.setattr(qa.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    payload = {"full_question": qa.scrub("手机号13800000001，奖学金条件？德育呢？"),
               "clauses": [{"index": 0, "text": "奖学金条件"}, {"index": 1, "text": "德育呢"}]}
    assert DeepSeekModel().request_json("按完整语义分组", payload) == {"groups": [[0, 1]]}
    assert "13800000001" not in json.dumps(seen)
    assert seen[0]["response_format"] == {"type": "json_object"}


def test_scholarship_facets_reach_retrieval_and_answer_as_one_matter(client, monkeypatch):
    monkeypatch.setattr(settings(), "embedding_mode", "hash")
    signup(client, student())
    quote = "合成测试国家奖学金：综合测评排名须符合学院要求，德育成绩须达到合格标准。"
    publish("合成测试国家奖学金申请条件", quote)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    plans, queries = [], []

    def classify(_, text, clauses):
        plans.append((text, clauses))
        return {"groups": [list(range(len(clauses)))]}

    search = retriever.search

    def record_search(db, query, context):
        queries.append(query)
        return search(db, query, context)

    def answer(_, query, evidence, **kwargs):
        assert "国家奖学金" in query and "德育成绩" in query
        return {"answer": quote, "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": quote}]}

    monkeypatch.setattr(DeepSeekModel, "group_questions", classify)
    monkeypatch.setattr(retriever, "search", record_search)
    monkeypatch.setattr(DeepSeekModel, "select_evidence", answer)
    original = "申请国家奖学金，综合测评排名需要满足什么要求？德育成绩呢？"
    assert len(question_clauses(original)) == 3
    items = ask(client, original)["answer"]["items"]
    assert len(plans) == 1 and plans[0][0] == original
    assert len(items) == 1 and items[0]["status"] == "answered"
    assert all("国家奖学金" in query and "德育成绩" in query for query in queries)


def test_independent_matters_still_have_separate_retrieval_and_status(client, monkeypatch):
    monkeypatch.setattr(settings(), "embedding_mode", "hash")
    signup(client, student())
    quote = "合成测试国家奖学金申请需要申请表。"
    publish("合成测试国家奖学金材料", quote)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    monkeypatch.setattr(DeepSeekModel, "group_questions", lambda _, text, clauses: {"groups": [[0], [1]]})
    monkeypatch.setattr(DeepSeekModel, "select_evidence", lambda _, query, evidence, **kwargs: {
        "answer": quote, "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": quote}]})
    items = ask(client, "国家奖学金要什么材料？出国签证怎么办？")["answer"]["items"]
    assert [item["status"] for item in items] == ["answered", "missing_material"]
    assert "签证" not in items[0]["question"] and "奖学金" not in items[1]["question"]
