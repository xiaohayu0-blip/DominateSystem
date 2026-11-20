import io
import uuid
import zipfile

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Chunk, Document
from app.qa import DeepSeekModel, ExtractiveMock, ModelFailure
from app.retrieval import build_chunks
from app.storage import parse_file, store
from conftest import signup, student


def publish(title="奖学金办法", content="奖学金申请材料包括申请表和成绩单，由学院人工审核。", year=None, grade=None,
            start=None, end=None, status="published"):
    with SessionLocal() as db:
        d = Document(title=title, publisher="合成演示学院", business_year=year, admission_year=grade,
                     effective_from=start, effective_to=end, status=status)
        db.add(d)
        db.flush()
        build_chunks(db, d, [("第1条", content)])
        db.commit()
        return d.id


def test_bge_indexes_new_chunks_and_reranks_within_selected_year(monkeypatch):
    from app import retrieval
    from app.config import settings

    monkeypatch.setattr(settings(), "embedding_mode", "bge")
    first_axis = [1.0, 0.0] + [0.0] * 510
    second_axis = [0.0, 1.0] + [0.0] * 510
    monkeypatch.setattr(retrieval, "embed_query", lambda text: first_axis)
    monkeypatch.setattr(retrieval, "embed_passages", lambda passages, **kwargs: [
        first_axis if "目标依据" in text else second_axis for text in passages
    ])
    other = publish("请假规则", "学生请假需要提交说明。", year=2023)
    best = publish("请假规则", "目标依据：学生请假需要提交说明。", year=2023)
    other_year = publish("请假规则", "目标依据：学生请假需要提交说明。", year=2024)
    with SessionLocal() as db:
        results, _ = retrieval.retriever.search(db, "学生请假需要提交说明", {"business_year": 2023})
        assert results[0][1].id == best
        assert {doc.id for _, doc in results} == {best, other, other_year}
        assert all(len(chunk.bge_vector) == 512 and len(chunk.vector) == 128 for chunk, _ in results)


def ask(client, text, **kwargs):
    data = {"request_id": uuid.uuid4().hex, "text": text, **kwargs}
    response = client.post("/api/questions", json=data)
    assert response.status_code == 202, response.text
    return client.get("/api/questions/" + response.json()["id"]).json()


def test_winter_break_start_and_duration_are_one_question():
    from app.qa import split_questions

    assert split_questions("什么时候放寒假?放多久?") == ["什么时候放寒假，放多久"]
    assert split_questions("什么时候放寒假?另外暑假呢?") == ["什么时候放寒假", "暑假呢"]


def test_winter_break_duration_is_derived_from_the_calendar(client):
    signup(client, student())
    with SessionLocal() as db:
        doc = Document(title="武汉纺织大学2026-2027校历.pdf", status="published")
        db.add(doc)
        db.flush()
        db.add(Chunk(document_id=doc.id, location="第1页 · 校历结构化 · 寒假",
                     text="武汉纺织大学2026-2027学年校历，寒假：2027年1月18日至2027年2月21日。"))
        db.commit()

    items = ask(client, "什么时候放寒假?放多久?")["answer"]["items"]
    assert len(items) == 1
    assert items[0]["status"] == "answered"
    assert "2027年1月18日至2027年2月21日" in items[0]["content"]
    assert "35天" in items[0]["content"]
    assert items[0]["citations"][0]["location"] == "第1页 · 校历结构化 · 寒假"


def test_verified_calendar_repairs_uploaded_ocr_without_changing_document(monkeypatch):
    from app import calendar_index
    from app.main import parse_document

    content_hash = "d55a841ed865d6e2e515eeea3a1d31238e682058d3b4885d9fa59548c02fc3ed"
    key = store.save(b"fixture", ".pdf")
    monkeypatch.setattr(calendar_index, "digest", lambda data: content_hash)
    with SessionLocal() as db:
        doc = Document(title="武汉纺织大学2026-2027校历[1].pdf", status="published",
                       storage_key=key, content_hash=content_hash)
        db.add(doc)
        db.flush()
        build_chunks(db, doc, [("第1页", "寒假 a= at +=" * 50)])
        assert calendar_index.reindex_verified_calendars(db) == 1
        assert doc.status == "published" and doc.storage_key == key
        chunks = db.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()
        assert len(chunks) == 60
        assert all("校历结构化" in chunk.location and "a=" not in chunk.text for chunk in chunks)

        monkeypatch.setattr("app.main.parse_file", lambda *a, **k: pytest.fail("verified PDF should skip OCR"))
        parse_document(db, doc)
        assert doc.parse_status == "ready"
        assert len(db.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()) == 60


def test_year_grade_validity_and_citations(client):
    signup(client, student())
    older = publish(year=2025, content="奖学金材料包含往年说明和申请表。")
    current = publish(year=2026)
    expired = publish(year=2026, content="奖学金已经过期的材料。", end="2025-12-31")
    result = ask(client, "2026年度奖学金申请材料")
    answer = result["answer"]["items"][0]
    assert answer["status"] == "answered"
    assert {c["document_id"] for c in answer["citations"]} == {current, older, expired}
    assert "其他年度" in answer["content"]
    c = answer["citations"][0]
    source = client.get("/api/sources/" + c["chunk_id"])
    assert source.status_code == 200
    assert source.json()["excerpt"] == c["excerpt"]
    assert "往年说明" in ask(client, "2025年度奖学金材料")["answer"]["items"][0]["content"]
    publish(title="转专业规定", content="转专业申请须提交学习计划。", grade=2024)
    result = ask(client, "转专业申请条件")
    assert result["answer"]["items"][0]["status"] == "answered"
    assert "2024级" in result["answer"]["items"][0]["content"]
    result = ask(client, "转专业申请条件", admission_year=2024, previous_id=result["id"])
    assert result["answer"]["items"][0]["status"] == "answered"


def test_partial_missing_and_conflict(client):
    signup(client, student())
    publish()
    result = ask(client, "奖学金申请材料？校外住宿补助如何申请？")
    assert [x["status"] for x in result["answer"]["items"]] == ["answered", "missing_material"]
    publish(title="实习规定甲", content="实习申请应于5天内提交材料。")
    publish(title="实习规定乙", content="实习申请应于8天内提交材料。")
    assert ask(client, "实习申请几天提交")["answer"]["items"][0]["status"] == "document_conflict"

    publish(title="银行卡风险说明甲", content="不得出租出借银行卡。奖学金公示不少于1个工作日。")
    publish(title="银行卡风险说明乙", content="不得出租出借银行卡。奖学金公示不少于3个工作日。")
    item = ask(client, "出借银行卡有什么风险")["answer"]["items"][0]
    assert item["status"] == "answered" and item["citations"]


def test_roster_matches_class_row_without_assuming_teacher_role(admin_client):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["班级", "班主任", "班主任职称"])
    workbook.active.append(["ACCA12401", "演示老师甲", "讲师"])
    workbook.active.append(["ACCA12402", "演示老师乙", "讲师"])
    workbook.active.append(["工程造价12402", "演示老师丙", "讲师"])
    data = io.BytesIO()
    workbook.save(data)
    response = admin_client.post("/api/admin/documents/upload", files={
        "file": ("班主任表.xlsx", data.getvalue()),
    })
    assert response.json()["status"] == "published"
    signup(admin_client, student())
    item = ask(admin_client, "24级ACCA二班找哪个专业成长班主任")["answer"]["items"][0]
    assert item["status"] == "answered"
    assert "没有细分班主任角色" in item["content"]
    assert len(item["citations"]) == 1 and "ACCA12402" in item["citations"][0]["excerpt"]
    item = ask(admin_client, "工成造价12402那班班主任是谁呀")["answer"]["items"][0]
    assert item["status"] == "answered"
    assert len(item["citations"]) == 1 and "工程造价12402" in item["citations"][0]["excerpt"]


def test_roster_recognizes_shorthand_and_preserves_merged_counselor(admin_client, monkeypatch):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["辅导员", "班级", "班主任"])
    sheet.append(["辅导老师甲", "数据管理12502", "班主任甲"])
    sheet.append([None, "数据管理12501", "班主任乙"])
    sheet.append(["辅导老师乙", "数字管理12503", "班主任丙"])
    sheet.append(["辅导老师乙", "数据管理12503", "班主任丁"])
    sheet.merge_cells("A2:A3")
    data = io.BytesIO()
    workbook.save(data)
    assert admin_client.post("/api/admin/documents/upload", files={
        "file": ("班主任表.xlsx", data.getvalue()),
    }).json()["status"] == "published"
    signup(admin_client, student())
    monkeypatch.setattr(settings(), "model_mode", "deepseek")

    def model_answer(_, question, evidence, *, retry=False):
        assert evidence[0]["roster_field"] == "辅导员"
        assert evidence[0]["roster_value"] == "辅导老师甲"
        return {"answer": "这个班的辅导员是辅导老师甲。",
                "selections": [{"chunk_id": evidence[0]["chunk_id"],
                                "quote": "辅导老师甲 | 数据管理12501"}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    for question in ("数管12501的导员是谁", "数理12501的导员是谁"):
        item = ask(admin_client, question)["answer"]["items"][0]
        assert item["status"] == "answered" and "辅导老师甲" in item["content"]
    item = ask(admin_client, "数理12503的导员是谁")["answer"]["items"][0]
    assert item["status"] == "needs_clarification" and not item["citations"]


def test_unspecified_rank_percentage_is_not_blocked_before_retrieval(client):
    signup(client, student())
    publish("国家奖学金办法", "国家奖学金要求排名前10%。")
    item = ask(client, "班上40个人我排第4，这卡在10%算不算")["answer"]["items"][0]
    assert item["status"] == "missing_material" and not item["citations"]


def test_points_without_cohort_show_scoped_evidence_instead_of_scholarships(client):
    signup(client, student())
    publish("奖学金评定办法", "校级奖学金 | 综测前30% | 每人每年1000元")
    older = publish("综测办法2023版", "数学建模竞赛获省级三等奖的，综测加0.3分。", grade=2023)
    newer = publish("综测办法2024版", "数学建模竞赛获省级三等奖的，综测加0.5分。", grade=2024)
    item = ask(client, "数学建模竞赛省级三等奖综测加几分？")["answer"]["items"][0]
    assert item["status"] == "answered"
    assert {c["document_id"] for c in item["citations"]} == {older, newer}
    assert "2023级" in item["content"] and "2024级" in item["content"]
    question = "数学建模竞赛省级三等奖综测加几分？"
    latest = ask(client, question, business_year=2024, admission_year=2024)
    scoped = ask(client, question, business_year=2023, previous_id=latest["id"])["answer"]["items"][0]
    assert scoped["status"] == "answered"
    assert {c["document_id"] for c in scoped["citations"]} == {older, newer}
    assert "2023级" in scoped["content"] and "2024级" in scoped["content"]
    older_question = ask(client, question, business_year=2023)
    followed = ask(client, "那2024年呢？", business_year=2023, previous_id=older_question["id"])["answer"]["items"][0]
    assert followed["question"] == "那2024年呢"
    assert {c["document_id"] for c in followed["citations"]} == {older, newer}
    assert "2023级" in followed["content"] and "2024级" in followed["content"]
    short = ask(client, "那24年呢。", business_year=2023, previous_id=older_question["id"])["answer"]
    assert short["context"]["business_year"] == 2024
    assert {c["document_id"] for c in short["items"][0]["citations"]} == {older, newer}
    independent = ask(client, "那2024年呢？")["answer"]["items"][0]
    assert independent["status"] == "missing_material"
    assert ask(client, "数学建模竞赛能认定多少学分？")["answer"]["items"][0]["status"] == "missing_material"


def test_model_error_and_invalid_reference_not_missing(client, monkeypatch):
    signup(client, student())
    publish()
    def fail(*_):
        raise ModelFailure("timeout")
    monkeypatch.setattr(ExtractiveMock, "select_evidence", fail)
    assert ask(client, "奖学金材料")["answer"]["items"][0]["status"] == "system_error"
    monkeypatch.setattr(ExtractiveMock, "select_evidence", lambda *_: {"selections": [{"chunk_id": "fake", "quote": "随意编造"}]})
    answer = ask(client, "奖学金材料")["answer"]["items"][0]
    assert answer["status"] == "validation_failed" and not answer["citations"]


@pytest.mark.parametrize("question", [
    "专业排名第一,德育分加多少分?", "专业第一能加多少德育分？", "学习成绩排第一，德育加分怎么算？",
])
def test_retry_abstention_is_missing_material_not_validation_failure(client, monkeypatch, caplog, question):
    signup(client, student())
    quote = "德育素质评价成绩=德育素质积分（15 分）-扣分项。"
    publish("专业排名与德育素质评价说明", quote, year=2026)
    publish("国家奖学金评审打分表", "专业排名第一：50分。", year=2026, status="disabled")
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    calls = []

    def model_answer(_, query, evidence, *, retry=False):
        calls.append(retry)
        assert evidence and all("打分表" not in source["title"] for source in evidence)
        if retry:
            assert "unsupported_summary_quantity" in query
            return {"answer": "现有资料没有该加分条款。", "selections": []}
        return {"answer": "专业排名第一加0分。", "selections": [
            {"chunk_id": evidence[0]["chunk_id"], "quote": quote}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, question, business_year=2026)["answer"]["items"][0]
    assert calls == [False, True]
    assert item["status"] == "missing_material" and not item["citations"]
    assert "未通过校验" not in item["content"] and "0分" not in item["content"]
    assert "evidence_validation_retry reason=unsupported_summary_quantity" in caplog.text
    assert "evidence_validation_failed" not in caplog.text
    assert question not in caplog.text


@pytest.mark.parametrize("retry_valid", [False, True])
def test_retry_validation_preserves_number_checks_and_logs_reason(client, monkeypatch, caplog, retry_valid):
    signup(client, student())
    quote = "德育素质积分基准为15分。"
    publish("德育评分办法", quote)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")

    def model_answer(_, query, evidence, *, retry=False):
        return {"answer": "德育素质积分基准为15分。" if retry and retry_valid else "德育素质积分基准为50分。",
                "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": quote}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, "德育素质积分基准多少分？")["answer"]["items"][0]
    assert item["status"] == ("answered" if retry_valid else "validation_failed")
    if retry_valid:
        assert "15分" in item["content"] and item["citations"]
    else:
        assert not item["citations"]
        assert "evidence_validation_failed type=InvalidEvidence reason=unsupported_summary_quantity" in caplog.text


def test_deepseek_summarizes_verified_excerpts_without_inventing_numbers(client, monkeypatch):
    signup(client, student())
    quote = "数学建模竞赛获省级三等奖的，综测加0.3分。"
    original = f"第三章 加分项目。{quote}同一项目不重复加分。"
    publish("综合素质测评办法", original, year=2023)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")

    def model_answer(_, question, evidence, *, retry=False):
        return {"answer": summary if not retry or not recover_on_retry else correct_summary,
                "selections": [{"chunk_id": evidence[0]["chunk_id"],
                                "quote": quote if retry and recover_on_retry else selected_quote}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    question = "数学建模竞赛省级三等奖综测加几分？"
    selected_quote = quote
    correct_summary = "按这条规定，获得该奖可以计入综测，分值是0.3分。"
    summary = correct_summary
    recover_on_retry = False
    item = ask(client, question, business_year=2023)["answer"]["items"][0]
    assert item["content"] == correct_summary
    assert item["citations"][0]["excerpt"] == original

    selected_quote = quote.replace("0.3", "0.5")
    item = ask(client, question, business_year=2023)["answer"]["items"][0]
    assert item["status"] == "answered" and item["content"] == correct_summary
    assert item["citations"][0]["excerpt"] == original

    selected_quote = quote
    summary = correct_summary.replace("0.3", "0.5")
    item = ask(client, question, business_year=2023)["answer"]["items"][0]
    assert item["status"] == "validation_failed" and not item["citations"]

    recover_on_retry = True
    item = ask(client, question, business_year=2023)["answer"]["items"][0]
    assert item["status"] == "answered" and item["content"] == correct_summary and item["citations"]


def test_assessment_distinguishes_question_percentage_from_policy_threshold(client, monkeypatch):
    signup(client, student())
    rule = "大四年级就业率低于85%，该单项考核为不合格。"
    doc_id = publish("班主任管理办法", "大四年级就业率85%到88%，该单项考核为合格。\n" + rule)
    with SessionLocal() as db:
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc_id))
        chunk.location = "第5页 · 班主任考核结构化 · 大四 · 就业率"
        db.commit()
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    summary = "如果大四就业率低于25%，该单项评为不合格。表里的界线是低于85%。"

    def model_answer(_, question, evidence, *, retry=False):
        assert "该单项为「不合格」" in question
        assert evidence[0]["excerpt"] == rule
        return {"answer": summary, "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": rule}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    question = "大四的就业率低于25%，班主任考核评为什么？"
    item = ask(client, question)["answer"]["items"][0]
    assert item["status"] == "answered" and item["content"] == summary and item["citations"]
    summary = summary.replace("不合格", "优秀")
    item = ask(client, question)["answer"]["items"][0]
    assert item["status"] == "validation_failed" and not item["citations"]


def test_competition_score_uses_two_verified_tables(client, monkeypatch):
    from app.competition import BASE_LOCATION, TEAM_LOCATION
    from app.retrieval import vector

    signup(client, student())
    base = "其他A类国家级一等奖或同级奖：45分。"
    team = "6人团队：0.325、0.225、0.150、0.125、0.100、0.075。"
    with SessionLocal() as db:
        document = Document(title="学生手册", business_year=2026, status="published")
        db.add(document)
        db.flush()
        db.add_all(Chunk(document_id=document.id, location=location, text=text, vector=vector(text))
                   for location, text in [(BASE_LOCATION, base), (TEAM_LOCATION, team)])
        db.commit()
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    summary = "如果第一名认定为一等奖或同级奖，按表分配，参考分为45分×0.075＝3.375分。"
    omit_team = False

    def model_answer(_, question, evidence, *, retry=False):
        assert "45分 × 0.075 = 3.375分" in question
        selections = [{"chunk_id": source["chunk_id"], "quote": quote}
                      for source, quote in zip(evidence, (base, team), strict=True)]
        return {"answer": summary, "selections": selections[:1] if omit_team else selections}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    question = "2026年国家级a类赛事第一名,团队里面第六名,加多少分"
    item = ask(client, question)["answer"]["items"][0]
    assert item["status"] == "answered" and item["content"] == summary and len(item["citations"]) == 2
    omit_team = True
    item = ask(client, question)["answer"]["items"][0]
    assert item["status"] == "validation_failed"
    omit_team = False
    summary = summary.replace("3.375分", "4分")
    item = ask(client, question)["answer"]["items"][0]
    assert item["status"] == "validation_failed"


def test_competition_second_place_can_follow_up_with_team_size(client, monkeypatch):
    from app.competition import BASE_LOCATION, TEAM_LOCATION
    from app.retrieval import vector

    signup(client, student())
    base = "其他A类国家级二等奖或同级奖：35分。"
    three = "3人团队：0.500、0.300、0.200。"
    six = "6人团队：0.325、0.225、0.150、0.125、0.100、0.075。"
    with SessionLocal() as db:
        document = Document(title="学生手册", business_year=2026, status="published")
        db.add(document)
        db.flush()
        db.add_all(Chunk(document_id=document.id, location=location, text=text, vector=vector(text))
                   for location, text in [(BASE_LOCATION, base), (TEAM_LOCATION, three + "\n" + six)])
        db.commit()
    monkeypatch.setattr(settings(), "model_mode", "deepseek")

    def model_answer(_, question, evidence, *, retry=False):
        assert "35分 × 0.15 = 5.25分" in question
        assert "第二名被认定为二等奖" in question
        return {"answer": "若证书认定为二等奖且按表分配，第三位的参考加分为5.25分。",
                "selections": [{"chunk_id": source["chunk_id"], "quote": quote}
                               for source, quote in zip(evidence, (base, six), strict=True)]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    q = ask(client, "2026年国家级a类赛事第二名,团队里面第三名,加多少分")
    item = q["answer"]["items"][0]
    assert item["status"] == "needs_clarification" and "一共几个人" in item["content"]
    follow = ask(client, "六个人", previous_id=q["id"])["answer"]["items"][0]
    assert follow["status"] == "answered" and "5.25分" in follow["content"]
    fourth = ask(client, "2026年国家级A类竞赛第四名，6人团队第3位加多少分")["answer"]["items"][0]
    assert fourth["status"] == "needs_clarification" and "证书" in fourth["content"]


def test_calendar_term_uses_source_heading_for_colloquial_query(client, monkeypatch):
    signup(client, student())
    text = "2026-2027学年第一学期校历。按周次表整理，第一学期教学周覆盖2026年9月7日至2027年1月17日。"
    doc_id = publish("校历", text)
    with SessionLocal() as db:
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc_id))
        chunk.location = "第1页 · 校历结构化 · 第一学期教学周"
        db.commit()
    monkeypatch.setattr(settings(), "model_mode", "deepseek")

    def model_answer(_, question, evidence, *, retry=False):
        title = "第一学期" if retry else "秋季学期"
        return {"answer": f"按周次表整理，{title}教学周为2026年9月7日至2027年1月17日。",
                "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": text}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, "2026年秋季学期教学周是什么时候")["answer"]["items"][0]
    assert item["status"] == "answered" and "第一学期" in item["content"] and "秋季学期" not in item["content"]


def test_calendar_holiday_overview_keeps_linked_date_question(client, monkeypatch):
    from app.qa import CALENDAR_OVERVIEW_LOCATION

    signup(client, student())
    excerpt = ("2026-2027学年校历假期总览。\n寒假：2027年1月18日至2027年2月21日。\n"
               "暑假：2027年7月19日至2027年8月29日。\n节日标记不代表放假或调休区间。")
    doc_id = publish("校历", excerpt)
    with SessionLocal() as db:
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc_id))
        chunk.location = CALENDAR_OVERVIEW_LOCATION
        db.commit()
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    answer = "寒假从2027年1月18日到2027年2月21日，暑假从2027年7月19日到2027年8月29日。节日调休另看通知。"

    def model_answer(_, question, evidence, *, retry=False):
        assert "26年到27年" in question and "分别是从几月几号" in question
        return {"answer": answer, "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": excerpt}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    result = ask(client, "26年到27年有哪些假可以放?分别是从几月几号休到几月几号?")["answer"]["items"]
    assert len(result) == 1 and result[0]["status"] == "answered" and result[0]["content"] == answer
    missing = ask(client, "27年到28年有哪些假可以放？")["answer"]["items"][0]
    assert missing["status"] == "missing_material" and not missing["citations"]


def test_calendar_single_year_lists_festival_marks(client, monkeypatch):
    from app.qa import CALENDAR_YEAR_LOCATION_PREFIX

    signup(client, student())
    excerpt = ("2026年覆盖范围：2026年9月7日至2026年12月31日。\n"
               "校历节日标记：中秋节2026年9月25日；国庆节2026年10月1日。")
    doc_id = publish("校历", excerpt)
    with SessionLocal() as db:
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc_id))
        chunk.location = "第1页" + CALENDAR_YEAR_LOCATION_PREFIX + "2026"
        db.commit()
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    calls = []

    def model_answer(_, question, evidence, *, retry=False):
        calls.append(retry)
        answer = ("中秋节是9月25日，国庆节是10月1日。" if retry else
                  "校历覆盖9月7日至12月31日。")
        return {"answer": answer, "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": excerpt}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, "26年有哪些假可以放？分别从几月几号休到几月几号？")["answer"]["items"][0]
    assert item["status"] == "answered" and "中秋节" in item["content"] and "国庆节" in item["content"]
    assert calls == [False, True]


def test_competition_catalog_distinguishes_main_and_special_tracks(client, monkeypatch):
    from app.competition_catalog import CATALOG_MARKER
    from app.retrieval import vector

    signup(client, student())
    with SessionLocal() as db:
        doc = Document(title="2026版学生手册", business_year=2026, status="published")
        db.add(doc)
        db.flush()
        for index, (name, grade) in enumerate((
            ("挑战杯全国大学生课外学术科技作品竞赛", "A+"),
            ("挑战杯全国大学生课外学术科技作品竞赛（专项赛）", "A"),
        ), 1):
            text = f"2025年学科竞赛目录：{name}，等级：{grade}类。"
            db.add(Chunk(document_id=doc.id, location=f"第385页{CATALOG_MARKER}2025年序号{index}",
                         text=text, vector=vector(text)))
        db.commit()
    monkeypatch.setattr(settings(), "model_mode", "deepseek")

    def model_answer(_, question, evidence, *, retry=False):
        answer = ("按2025年目录，主赛是A+类，专项赛是A类。" if len(evidence) == 2
                  else "按2025年目录，该专项赛是A类。")
        return {"answer": answer, "selections": [{"chunk_id": c["chunk_id"], "quote": c["excerpt"]}
                                                  for c in evidence]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, "挑战杯是哪类赛事")["answer"]["items"][0]
    assert item["status"] == "answered" and "A+类" in item["content"] and len(item["citations"]) == 2
    item = ask(client, "挑战杯专项赛属于哪类")["answer"]["items"][0]
    assert item["status"] == "answered" and "A+类" not in item["content"] and len(item["citations"]) == 1
    item = ask(client, "2026年挑战杯是哪类赛事")["answer"]["items"][0]
    assert item["status"] == "answered" and item["citations"]
    assert "未找到2026年" in item["content"] and "2025年目录" in item["content"]


def test_current_year_overrides_inherited_year_and_model_can_abstain(client, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    signup(client, student())
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    previous = ask(client, "校外住宿补助", business_year=current_year - 1)
    publish("奖学金评定办法", "申请校级奖学金要求综合素质测评成绩位于专业前30%。", year=current_year)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    monkeypatch.setattr(DeepSeekModel, "select_evidence",
                        lambda *_: {"answer": "资料未说明应交材料，无法回答。", "selections": []})
    answer = ask(client, "今年申请奖学金要交什么", previous_id=previous["id"])["answer"]
    assert answer["context"]["business_year"] == current_year
    assert answer["items"][0]["status"] == "missing_material"
    assert "联网检索" in answer["items"][0]["content"]


def test_historical_reference_does_not_open_an_issue(client, monkeypatch):
    signup(client, student())
    historic = publish("应征入伍指南", "应征入伍基本条件\n男性身高160cm以上。", year=2024)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    monkeypatch.setattr(DeepSeekModel, "select_evidence", lambda _, query, evidence: {
        "answer": "男性的身高要求为160cm以上。",
        "selections": [{"chunk_id": evidence[0]["chunk_id"], "start_line": 2, "end_line": 2}],
    })
    result = ask(client, "2026年征兵入伍的条件是什么")["answer"]
    assert result["context"]["business_year"] == 2026
    item = result["items"][0]
    assert item["status"] == "historical_reference"
    assert "暂无2026年" in item["content"] and "2024年资料" in item["content"]
    assert item["content"].endswith("男性的身高要求为160cm以上。")
    assert {c["document_id"] for c in item["citations"]} == {historic}
    from app.models import Issue
    with SessionLocal() as db:
        assert db.scalar(select(Issue)) is None
    latest = ask(client, "2024年征兵入伍的条件是什么")["answer"]["items"][0]
    assert latest["status"] == "answered" and latest["content"] == "男性的身高要求为160cm以上。"


@pytest.mark.parametrize("question", [
    "入伍的条件是啥", "参军要满足哪些要求", "当兵需要什么条件", "报名参军有什么门槛",
])
def test_colloquial_enlistment_questions_retrieve_eligibility(client, question):
    signup(client, student())
    doc = publish("大学生应征入伍指南", "应征入伍基本条件\n男性身高160cm以上。", year=2024)
    item = ask(client, question, business_year=2024)["answer"]["items"][0]
    assert item["status"] == "answered"
    assert {citation["document_id"] for citation in item["citations"]} == {doc}


def test_draft_disable_and_history_source(client):
    signup(client, student())
    doc_id = publish(status="draft")
    assert ask(client, "奖学金材料")["answer"]["items"][0]["status"] == "missing_material"
    with SessionLocal() as db:
        db.get(Document, doc_id).status = "published"
        db.commit()
    cited = ask(client, "奖学金材料")["answer"]["items"][0]["citations"][0]
    with SessionLocal() as db:
        db.get(Document, doc_id).status = "disabled"
        db.commit()
    assert ask(client, "奖学金材料")["answer"]["items"][0]["status"] == "missing_material"
    assert client.get("/api/sources/" + cited["chunk_id"]).json()["source_status"] == "disabled"


def test_upload_validation_retry_and_file_permission(admin_client):
    c = admin_client
    assert c.post("/api/admin/documents/upload", files={"file": ("x.exe", b"invalid")}).status_code == 422
    assert c.post("/api/admin/documents/upload", files={"file": ("x.pdf", b"invalid")}).status_code == 422
    damaged = c.post("/api/admin/documents/upload", files={"file": ("x.pdf", b"%PDF-1.7\ninvalid")}).json()
    assert damaged["parse_status"] == "failed"
    assert c.post(f'/api/admin/documents/{damaged["id"]}/retry').json()["parse_status"] == "failed"
    uploaded = c.post("/api/admin/documents/upload", files={"file": ("../../escape.csv", "奖学金,申请表\n".encode())}).json()
    assert uploaded["original_name"] == "escape.csv" and uploaded["parse_status"] == "ready"
    assert uploaded["status"] == "published" and "storage_key" not in uploaded
    assert uploaded["publisher"] == "" and uploaded["business_year"] is None
    c.post("/api/admin/logout")
    assert c.get(f'/api/admin/documents/{uploaded["id"]}/file').status_code == 401
    with pytest.raises(ValueError):
        store.path("../escape")


def test_pptx_and_zip_upload_publish_locatable_documents(admin_client, monkeypatch):
    presentation = io.BytesIO()
    with zipfile.ZipFile(presentation, "w") as deck:
        deck.writestr("ppt/presentation.xml", """
            <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                <p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>
            </p:presentation>""")
        deck.writestr("ppt/_rels/presentation.xml.rels", """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                    Target="slides/slide2.xml"/>
            </Relationships>""")
        deck.writestr("ppt/slides/slide2.xml", """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
                <p:sp><p:txBody><a:p><a:r><a:t>奖学金申请表</a:t></a:r></a:p></p:txBody></p:sp>
            </p:sld>""")
    c = admin_client
    response = c.post("/api/admin/documents/upload", files={"file": ("2024年说明.pptx", presentation.getvalue())})
    assert response.status_code == 200
    doc = response.json()
    assert doc["parse_status"] == "ready" and doc["status"] == "published"
    assert doc["business_year"] == 2024 and doc["publisher"] == ""
    assert doc["chunks"][0]["location"].startswith("第1页幻灯片")
    assert doc["chunks"][0]["text"] == "奖学金申请表"

    batch = io.BytesIO()
    with zipfile.ZipFile(batch, "w") as archive:
        archive.writestr("制度/说明.pptx", presentation.getvalue())
        archive.writestr("资料.csv", "奖学金,成绩单")
        archive.writestr("readme.txt", "ignored")
    response = c.post("/api/admin/documents/upload", files={"file": ("批量.zip", batch.getvalue())})
    assert response.status_code == 200
    result = response.json()
    assert len(result["documents"]) == 2 and result["skipped"] == 1
    assert all(doc["status"] == "published" for doc in result["documents"])

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("正常.csv", "制度,正文")
        archive.writestr("../越界.csv", "制度,正文")
    assert c.post("/api/admin/documents/upload", files={"file": ("越界.zip", unsafe.getvalue())}).status_code == 422
    assert len(c.get("/api/admin/documents").json()) == 3

    illustrated = io.BytesIO()
    with zipfile.ZipFile(presentation) as source, zipfile.ZipFile(illustrated, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "ppt/slides/slide2.xml":
                data = data.replace(b"</p:sld>", b"<a:blip/></p:sld>")
            target.writestr(name, data)
    upload = {"file": ("含图说明.pptx", illustrated.getvalue())}
    draft = c.post("/api/admin/documents/upload", files=upload).json()
    assert draft["status"] == "draft" and draft["parse_status"] == "failed" and "尚未解析" in draft["parse_note"]

    from app import storage

    def recognize(path, pages, slide_count, deadline):
        assert path.suffix == ".pptx" and pages == [1] and slide_count == 1
        return [("第1页幻灯片（OCR）", "申请需提交成绩单")]

    monkeypatch.setattr(settings(), "ocr_enabled", True)
    monkeypatch.setattr(storage, "presentation_ocr", recognize)
    result = c.post(f'/api/admin/documents/{draft["id"]}/retry').json()
    assert result["status"] == "published" and "OCR" in result["parse_note"]
    assert any("（OCR）" in chunk["location"] and "成绩单" in chunk["text"] for chunk in result["chunks"])
    assert c.post("/api/admin/documents/upload", files=upload).json()["status"] == "published"

    def fail_ocr(path, pages, slide_count, deadline):
        raise ValueError("OCR 处理超时")

    monkeypatch.setattr(storage, "presentation_ocr", fail_ocr)
    mixed_batch = io.BytesIO()
    with zipfile.ZipFile(mixed_batch, "w") as archive:
        archive.writestr("含图说明.pptx", illustrated.getvalue())
        archive.writestr("资料.csv", "奖学金,成绩单")
    result = c.post("/api/admin/documents/upload", files={
        "file": ("批量.zip", mixed_batch.getvalue()),
    }).json()
    assert [(doc["status"], doc["parse_status"]) for doc in result["documents"]] == [
        ("draft", "failed"), ("published", "ready"),
    ]


@pytest.mark.parametrize("format", ["docx", "xlsx", "csv", "pdf"])
def test_actual_parsers(format):
    buffer = io.BytesIO()
    if format == "docx":
        from docx import Document as Word
        d = Word()
        d.add_paragraph("奖学金申请材料")
        d.add_table(rows=1, cols=1).cell(0, 0).text = "成绩单"
        d.save(buffer)
    elif format == "xlsx":
        from openpyxl import Workbook
        d = Workbook()
        d.active.title = "制度表"
        d.active.append(["申请材料", "成绩单"])
        d.save(buffer)
    elif format == "csv":
        buffer.write("申请材料,成绩单\n".encode())
    else:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=500, height=500)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 50 400 Td (Scholarship application form and transcript.) Tj ET")
        page[NameObject("/Contents")] = stream
        writer.write(buffer)
    key = store.save(buffer.getvalue(), "." + format)
    parts, _ = parse_file(store.path(key))
    assert parts and all(location for location, _ in parts)
    assert any(text.strip() for _, text in parts)


@pytest.mark.parametrize("format", ["docx", "xlsx", "pdf"])
def test_upload_extracts_text_and_ocr_before_publishing(format, admin_client, monkeypatch):
    from PIL import Image
    from app import storage

    image = Image.new("RGB", (100, 50), "white")
    png = io.BytesIO()
    image.save(png, format="PNG")
    png.seek(0)
    buffer = io.BytesIO()
    if format == "docx":
        from docx import Document
        document = Document()
        document.add_paragraph("Original text")
        document.add_picture(png)
        document.save(buffer)
    elif format == "xlsx":
        from openpyxl import Workbook
        from openpyxl.drawing.image import Image as WorksheetImage
        workbook = Workbook()
        workbook.active.append(["Original text"])
        workbook.active.add_image(WorksheetImage(png), "A2")
        workbook.save(buffer)
    else:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        image_pdf = io.BytesIO()
        image.save(image_pdf, format="PDF")
        writer = PdfWriter(clone_from=image_pdf)
        page = writer.pages[0]
        page["/Resources"][NameObject("/Font")] = DictionaryObject({
            NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }),
        })
        stream = DecodedStreamObject()
        stream.set_data(page.get_contents().get_data() + b"\nBT /F1 12 Tf 2 2 Td (Original text) Tj ET")
        page.replace_contents(stream)
        writer.write(buffer)

    monkeypatch.setattr(settings(), "ocr_enabled", True)
    monkeypatch.setattr(storage, "ocr_image", lambda path, *, deadline: "Recognized text")
    monkeypatch.setattr(storage, "ocr_page", lambda path, page, *, deadline: "Recognized text")
    response = admin_client.post("/api/admin/documents/upload", files={
        "file": (f"illustrated.{format}", buffer.getvalue()),
    })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "published"
    assert any("Original text" in chunk["text"] for chunk in result["chunks"])
    assert any("OCR" in chunk["location"] and "Recognized text" in chunk["text"]
               for chunk in result["chunks"])


def test_deepseek_adapter_mock_transport(monkeypatch):
    import httpx
    import app.qa as qa
    seen = []
    real_client = httpx.Client
    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"selections": []}'}}]})
    monkeypatch.setattr(qa.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert DeepSeekModel().select_evidence("我的手机号13800000001", []) == {"selections": []}
    assert "13800000001" not in seen[0]


def test_unknown_source_and_scope(client):
    signup(client, student())
    publish()
    with SessionLocal() as db:
        chunk_id = db.scalar(select(Chunk.id))
    assert client.get("/api/sources/" + chunk_id).status_code == 404
    item = ask(client, "帮我审批奖学金")["answer"]["items"][0]
    assert item["status"] == "answered" and item["citations"]
