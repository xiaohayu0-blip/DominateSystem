import pytest

from app.config import settings
from app.db import SessionLocal
from app.qa import DeepSeekModel
from app.retrieval import retriever
from conftest import signup, student
from test_knowledge import ask, publish


@pytest.mark.parametrize("question", [
    "你每天最多调用多少次模型", "本系统的并发限制是多少", "上传文件最大多少MB", "OCR超时时长是多少",
])
def test_internal_limits_have_fixed_reply_without_retrieval(client, monkeypatch, question):
    signup(client, student())
    monkeypatch.setattr(retriever, "search", lambda *_: pytest.fail("technical-limit question must stop here"))
    item = ask(client, question)["answer"]["items"][0]
    assert item["content"] == "这类问题不能回答" and item["citations"] == []


@pytest.mark.parametrize("question,quote", [
    ("全班每个人奖学金评分明细记录是什么", "全班每个人奖学金评分明细记录：虚构甲为优秀，虚构乙为合格。"),
    ("今晚宿管几点查寝", "今晚宿管查寝时间安排：以楼栋公告为准。"),
    ("帮我审批奖学金", "奖学金审批条件：提交申请表和成绩单，由学院审查。"),
    ("老师的联系方式", "老师的联系方式：可通过学院公示的办公邮箱联系。"),
    ("老师的个人微信是什么", "老师的个人微信联系方式见学院发布的联系表。"),
])
def test_supplied_documents_can_answer_previously_blocked_topics(client, monkeypatch, question, quote):
    signup(client, student())
    publish(question, quote)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    monkeypatch.setattr(DeepSeekModel, "select_evidence", lambda _, query, evidence, **kwargs: {
        "answer": quote, "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": quote}]})
    item = ask(client, question)["answer"]["items"][0]
    assert item["status"] == "answered" and item["content"] == quote and item["citations"]


def test_upload_requirements_in_a_document_are_not_internal_limits(client):
    signup(client, student())
    publish("奖学金上传材料要求", "奖学金申请附件大小应按学院通知要求提交。")
    item = ask(client, "奖学金申请附件大小要求是什么")["answer"]["items"][0]
    assert item["status"] == "answered" and item["content"] != "这类问题不能回答"


@pytest.mark.parametrize("year,grade,start,end", [
    (2024, None, None, None), (None, 2023, None, None), (None, None, None, "2024-12-31"),
    (None, None, "2027-01-01", None),
])
def test_other_scopes_remain_retrievable(year, grade, start, end):
    doc_id = publish("奖学金申请材料", "奖学金申请需要申请表。", year=year, grade=grade, start=start, end=end)
    with SessionLocal() as db:
        rows, _ = retriever.search(db, "奖学金申请材料", {"business_year": 2026, "admission_year": 2024})
        assert doc_id in {doc.id for _, doc in rows}


@pytest.mark.parametrize("year", [None, 2024])
def test_model_reply_is_preserved_with_sources(client, monkeypatch, year):
    signup(client, student())
    quote = "奖学金申请需要申请表，按学院通知办理。"
    publish("奖学金申请材料", quote, year=year)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    calls = []

    def model_answer(_, query, evidence, *, retry=False):
        calls.append(retry)
        return {"answer": quote * 30,
                "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": quote}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, "奖学金申请材料")["answer"]["items"][0]
    assert calls == [False] and item["status"] in {"answered", "historical_reference"}
    assert item["content"] == quote * 30


def test_long_evidence_and_original_links_are_allowed_with_short_answer(client, monkeypatch):
    signup(client, student())
    quote = "心理中心预约入口https://example.edu/booking，请按页面填写。"
    original = "心理咨询预约说明。" * 45 + quote
    publish("心理咨询预约", original)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    monkeypatch.setattr(DeepSeekModel, "select_evidence", lambda _, query, evidence, **kwargs: {
        "answer": quote, "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": original}]})
    item = ask(client, "心理咨询怎么预约")["answer"]["items"][0]
    assert item["status"] == "answered" and item["content"] == quote


@pytest.mark.parametrize("length", [500, 501])
def test_exact_length_boundary_without_model_retry(client, monkeypatch, length):
    signup(client, student())
    quote = "奖学金申请需要申请表，同一项目不重复计分。"
    publish("奖学金申请", quote)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    calls = []

    def model_answer(_, query, evidence, *, retry=False):
        calls.append(retry)
        return {"answer": quote if retry else (quote + "请按学院通知办理。" * 80)[:length],
                "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": quote}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, "奖学金申请")["answer"]["items"][0]
    assert calls == [False]
    assert item["status"] == "answered" and len(item["content"]) == length
    assert "同一项目不重复计分" in item["content"]


def test_long_scholarship_citation_keeps_a_verified_short_answer():
    from app.qa import cited_answer

    quote = '学业奖学金一等奖1800元，二等奖1200元，三等奖800元。'
    source = {'chunk_id': 'award', 'document_id': 'policy', 'title': '本科生奖学金评定管理办法',
              'location': '第3页', 'excerpt': quote + '其他评定细则。' * 100}
    answer, citations = cited_answer(
        {'answer': quote, 'selections': [{'chunk_id': 'award', 'quote': quote}]},
        [source], question='学业奖学金各等级金额是多少？')
    assert answer == quote and citations == [source]
