import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk, Document
from app.qa import context_for
from app.ranking import ranking_answer
from app.retrieval import build_chunks
from app.schemas import QuestionInput


CONTEXT = {"business_year": 2026, "selected_year": None, "admission_year": None}


def source(db, **metadata):
    document = Document(title="管理学院本科生国家奖学金评审打分表.xlsx", status="published", **metadata)
    db.add(document)
    db.flush()
    build_chunks(db, document, [
        ("工作表 Sheet1 第4行", "1 | 智育排名（50分） | 专业排名第一加50分，第二名45分，按名次依次递减5分。"),
        ("工作表 Sheet1 第5行", "2 | 综测排名（30分） | 专业排名第一加30分，第二名25分，按名次依次递减5分。"),
    ])
    db.flush()
    return document


@pytest.mark.parametrize("query,metric,points", [
    ("专业第一，智育加几分？", "智育", "50"),
    ("专业排名第2名智育得多少分", "智育", "45"),
    ("国奖专业排名第三智育多少分", "智育", "40"),
    ("专业第十一名智育多少分", "智育", "0"),
    ("专业第一，综测多少分", "综测", "30"),
    ("专业第二名综测多少分", "综测", "25"),
    ("专业第三名综测多少分", "综测", "20"),
    ("专业第七名综测多少分", "综测", "0"),
])
def test_rank_scores_follow_source_and_keep_metric(query, metric, points):
    with SessionLocal() as db:
        doc = source(db)
        result = ranking_answer(db, query, CONTEXT)
        assert result["status"] == "answered"
        assert f"{metric}排名分为{points}分" in result["content"]
        assert "国家奖学金评审" in result["content"] and "未注明适用年度" in result["content"]
        assert len(result["citations"]) == 1 and result["citations"][0]["document_id"] == doc.id
        assert metric in result["citations"][0]["excerpt"]


@pytest.mark.parametrize("query", ["专业排名第一,德育分加多少分?", "专业第一加几分", "专业排名第一智育综测各多少分"])
def test_rank_metric_is_clarified_instead_of_silently_corrected(query):
    with SessionLocal() as db:
        source(db)
        result = ranking_answer(db, query, CONTEXT)
        assert result["status"] == "needs_clarification" and result["clarification_kind"] == "ranking_metric"
        assert "50分" in result["content"] and "30分" in result["content"]
        assert len(result["citations"]) == 2


@pytest.mark.parametrize("query", ["专业第十二名智育多少分", "专业第八名综测多少分", "专业第0名智育多少分"])
def test_rank_boundaries_do_not_assume_negative_scores_or_zero_floor(query):
    with SessionLocal() as db:
        source(db)
        assert ranking_answer(db, query, CONTEXT)["status"] == "needs_clarification"


def test_rank_rules_respect_publication_scope_and_consistency():
    with SessionLocal() as db:
        doc = source(db, business_year=2025)
        query = "专业排名第一智育多少分"
        prior = ranking_answer(db, query, CONTEXT)
        assert prior["status"] == "answered" and "2025年度" in prior["content"]
        doc.business_year = 2026
        doc.status = "disabled"
        db.flush()
        assert ranking_answer(db, query, CONTEXT) is None
        doc.status = "published"
        doc.effective_to = "2025-12-31"
        db.flush()
        expired = ranking_answer(db, query, CONTEXT)
        assert expired["status"] == "answered" and "2025-12-31" in expired["content"]
        doc.effective_to = None
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc.id, Chunk.text.contains("智育")))
        chunk.text = chunk.text.replace("第二名45分", "第二名44分")
        db.flush()
        assert ranking_answer(db, query, CONTEXT) is None
        assert ranking_answer(db, "国家级A类竞赛第一名综测多少分", CONTEXT) is None


@pytest.mark.parametrize("query", [
    "校级奖学金专业第一智育多少分", "攀登奖学金专业第一综测多少分",
    "专业第一百名智育多少分", "专业第1.5名综测多少分",
])
def test_ranking_does_not_mix_other_awards_or_partial_ranks(query):
    with SessionLocal() as db:
        source(db)
        assert ranking_answer(db, query, CONTEXT) is None


def test_ranking_scores_are_read_from_current_source_values():
    with SessionLocal() as db:
        doc = source(db)
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc.id, Chunk.text.contains("智育")))
        chunk.text = chunk.text.replace("50分", "60分").replace("45分", "53分").replace("5分", "7分")
        db.flush()
        result = ranking_answer(db, "专业第三名智育多少分", CONTEXT)
        assert result["status"] == "answered" and "智育排名分为46分" in result["content"]


def test_rank_clarification_can_be_answered_by_short_followup():
    from types import SimpleNamespace

    with SessionLocal() as db:
        source(db)
        original = "专业排名第一,德育分加多少分?"
        item = ranking_answer(db, original, CONTEXT)
        previous = SimpleNamespace(text=original, context=CONTEXT, answer={"items": [item]})
        data = QuestionInput(request_id="ranking-followup-1234", text="智育")
        context = context_for(data, previous)
        result = ranking_answer(db, context["followup_question"], context)
        assert result["status"] == "answered" and "智育排名分为50分" in result["content"]
