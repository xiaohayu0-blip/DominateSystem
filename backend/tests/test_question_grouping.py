import pytest

from app.qa import DeepSeekModel, split_questions
from app.config import settings
from app.retrieval import retriever
from conftest import signup, student
from test_knowledge import ask, publish


@pytest.mark.parametrize("question", [
    "当寝室长能加综测分吗？能加多少啊？",
    "当寝室长能加综测分吗? 能加多少啊?",
    "当寝室长能加综测分吗；可以加几分呢",
    "当寝室长能加综测分吗\n那最多能加多少分呀？",
    "奖学金可以申请吗？需要哪些材料？什么时候截止？",
    "助学金能申请吗？怎么领？",
    "竞赛获奖能加分吗？怎么计算？有没有上限？",
    "今年有哪些节假日？分别从什么时候放？",
])
def test_subjectless_followups_share_one_question(question):
    assert len(split_questions(question)) == 1
    assert "，" in split_questions(question)[0]


@pytest.mark.parametrize("question,expected", [
    ("当寝室长能加综测分吗？能加多少啊？", ["当寝室长能加综测分吗，能加多少啊"]),
    ("奖学金申请材料？校外住宿补助如何申请？", ["奖学金申请材料", "校外住宿补助如何申请"]),
    ("寝室长加多少分？班长加多少分？", ["寝室长加多少分", "班长加多少分"]),
    ("奖学金怎么申请？助学金需要哪些材料？", ["奖学金怎么申请", "助学金需要哪些材料"]),
    ("奖学金怎么申请？另外能加多少分？", ["奖学金怎么申请", "能加多少分"]),
    ("申请条件以及需要哪些材料", ["申请条件", "需要哪些材料"]),
    ("能加多少啊？", ["能加多少啊"]),
    ("？？\n", []),
])
def test_grouping_keeps_independent_topics_and_explicit_boundaries(question, expected):
    assert split_questions(question) == expected


def test_dependent_followup_reaches_retrieval_and_model_with_its_subject(client, monkeypatch):
    monkeypatch.setattr(settings(), "embedding_mode", "hash")
    signup(client, student())
    # Synthetic rule only: this is not a statement of the college's actual scoring policy.
    quote = "合成测试规则：寝室长考核合格，综合测评加2分。"
    publish("合成测试寝室长综合测评规则", quote)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    queries = []
    search = retriever.search

    def recording_search(db, query, context):
        queries.append(query)
        return search(db, query, context)

    def model_answer(_, query, evidence, **kwargs):
        assert "寝室长" in query and "能加多少" in query
        return {"answer": "寝室长考核合格，综合测评加2分。", "selections": [
            {"chunk_id": evidence[0]["chunk_id"], "quote": quote}]}

    monkeypatch.setattr(retriever, "search", recording_search)
    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    items = ask(client, "当寝室长能加综测分吗？能加多少啊？")["answer"]["items"]
    assert len(items) == 1 and items[0]["status"] == "answered"
    assert "2分" in items[0]["content"] and items[0]["citations"]
    assert queries and all("寝室长" in query and "能加多少" in query for query in queries)
