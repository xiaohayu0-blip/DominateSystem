"""Regressions found by the 2026-11-17 live oral-question corpus."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.evidence_numbers import quantities
from app.models import Chunk, Document
from app.policy_context import search_policy_evidence
from app.qa import InvalidEvidence, answer_question, verified_excerpts
from app.retrieval import build_chunks

CONTEXT = {"business_year": 2026, "selected_year": None, "admission_year": None}


def source(text, title="管理学院寝室长管理办法.pdf", location="第1页"):
    return {"chunk_id": "rule", "document_id": "document", "title": title,
            "location": location, "excerpt": text}


def verify(answer, original, *, question=""):
    evidence = source(original)
    selected = {"answer": answer, "selections": [{"chunk_id": "rule", "quote": original}]}
    options = {"question": question} if question else {}
    return verified_excerpts(selected, [evidence], cohort_options=False, deepseek=True, **options)


@pytest.mark.parametrize("original,answer", [
    ("任期一般为六个月。", "任期一般为6个月。"),
    ("每学期接受一次考核。", "每学期考核1次。"),
    ("每人每年6000元。", "每名学生每学年6000元。"),
    ("周三下午两点到五点。", "周三14:00到17:00。"),
    ("同一学年不能兼得。", "同一年不能兼得。"),
    ("周三下午两点到五点。", "周三下午2点到5点，5点结束。"),
])
def test_equivalent_periods_and_clock_times(original, answer):
    assert quantities(original) == quantities(answer)


def test_selected_line_keeps_other_facts_in_the_same_displayed_source():
    evidence = source("本科生从2024级开始，需要半年相关经历。\n管理学院2025年6月2日印发。")
    answer = "2024级本科生需要半年相关经历，这份办法于2025年6月2日印发。"
    selected = {"answer": answer, "selections": [{"chunk_id": "rule", "start_line": 1, "end_line": 1}]}
    assert verified_excerpts(selected, [evidence], cohort_options=False, deepseek=True)[0] == answer


def test_explicit_scenario_word_count_and_computed_gap_are_grounded_separately():
    answer = "你写了1499字，材料要求不少于1500字，所以至少还差1字。"
    assert verify(answer, "个人事迹材料不少于1500字。",
                  question="攀登事迹写了1499字，最低多少字")[0] == answer


def test_scenario_rank_does_not_become_the_policy_threshold():
    answer = "你排25%，文件一处要求前15%，另一处要求前30%，需要学院确认口径。"
    assert verify(answer, "基本条件前15%；评定要求前30%。", question="我排25%，按15还是30")[0] == answer


def test_student_group_size_is_a_scenario_not_a_new_score_rule():
    answer = "你们2人寝室，人数不足4人，需按比例加分。"
    assert verify(answer, "寝室人数不足4人时按比例加分。", question="我们寝室2人怎么算")[0] == answer


def test_rejecting_a_question_proposal_does_not_assert_it_as_policy():
    answer = "节日标记不等于连放3天，具体放假安排以通知为准。"
    assert verify(answer, "节假日根据国家有关规定安排放假。",
                  question="校历标红是不是连放3天")[0] == answer


@pytest.mark.parametrize("answer", ["奖励每年50000元。", "你能拿50000元。", "学校要求写1499字。"])
def test_scenario_numbers_cannot_be_promoted_to_policy(answer):
    with pytest.raises(InvalidEvidence):
        verify(answer, "奖励每年6000元，事迹不少于1500字。",
               question="我想拿50000元，事迹写了1499字")


def test_true_reversed_condition_remains_invalid():
    with pytest.raises(InvalidEvidence, match="reversed_summary_condition"):
        verify("公示最多3个工作日。", "公示不少于3个工作日。")


@pytest.mark.parametrize("answer,original,question", [
    ("每学年6000元，不是一个月6000元。", "每人每年6000元。", "一学年6000还是一个月6000"),
    ("大一还评不了，要到大二。", "二年级以上（含二年级）。", "大一能评吗，还是大二才行"),
    ("没有只垫一次的例外，不参与刷单。", "不参与刷单、代付款、返现。", "刷单我只垫一次行吗"),
    ("你写的1499字还差一点，需不少于1500字。", "事迹不少于1500字。", "我写了1499字够吗"),
])
def test_rejected_input_proposals_and_non_time_idioms(answer, original, question):
    assert verify(answer, original, question=question)[0] == answer


def test_point_idiom_is_not_a_clock_time():
    assert not quantities("还差一点，别急。")


def test_pdf_spaces_do_not_reverse_a_limit():
    assert verify("累计不超过2年。", "累计不 超过 2 年。")[0] == "累计不超过2年。"


def test_source_post_counter_is_not_lost_in_chinese_notation():
    assert quantities("只能申请一个岗位") == quantities("只能申请1个岗位")


def test_source_term_need_not_be_repeated_when_no_other_term_is_claimed():
    evidence = source("第二学期2月22日开始上课，共18周。",
                      title="武汉纺织大学2026-2027校历.pdf",
                      location="第2页 · 校历结构化 · 第二学期教学周")
    answer = "2月22日开始上课。"
    selected = {"answer": answer, "selections": [{"chunk_id": "rule", "quote": evidence["excerpt"]}]}
    assert verified_excerpts(selected, [evidence], cohort_options=False, deepseek=True)[0] == answer


def publish_parts(db, title, parts, original_name=""):
    doc = Document(title=title, status="published", original_name=original_name)
    db.add(doc)
    db.flush()
    build_chunks(db, doc, parts)
    db.flush()
    return doc


@pytest.mark.parametrize("query,title,text", [
    ("国励一年6000还是每月6000", "管理学院国家励志奖学金评选办法.docx", "国家励志奖学金每人每年6000元。"),
    ("舍涨半年到期能继续做吗", "管理学院寝室长管理办法.pdf", "寝室长任期一般为六个月，可连选连任。"),
    ("刷单说本金马上退，我就先垫一次，也在禁止范围里嘛", "管理学院安全责任事项提醒告知书.docx",
     "增强网络安全意识，不参与任何刷单、代付款、返现、非法团购、免费领奖、非法网络交友等活动，注意辨别网络安全环境。"),
])
def test_oral_aliases_and_specific_safety_terms_retrieve_the_rule(query, title, text):
    with SessionLocal() as db:
        doc = publish_parts(db, title, [("第1段", text)])
        rows, _ = search_policy_evidence(db, query, CONTEXT)
        assert rows and rows[0][1].id == doc.id


@pytest.mark.parametrize("query", ["会计的老师是谁啊我忘记班号了", "我就记得是二班，帮我查辅导员"])
def test_incomplete_roster_lookup_requests_identity_instead_of_claiming_no_material(query):
    with SessionLocal() as db:
        publish_parts(db, "管理学院各年级班主任信息统计表.xlsx", [
            ("工作表 Sheet1 第1行", "| 年级 | 辅导员 | 班级 | 班主任 | 职称 |"),
            ("工作表 Sheet1 第2行", "| 2026级 | 测试辅导员 | 会计12601 | 测试班主任 | 讲师 |"),
        ], original_name="班主任信息.xlsx")
        result = answer_question(db, query, CONTEXT)["items"][0]
        assert result["status"] == "needs_clarification"
        assert "班号" in result["content"]


@pytest.mark.parametrize("query", ["那个还差两天我能不能补一下啊", "我能评上吗，帮看看呗"])
def test_unresolved_policy_question_asks_for_the_missing_subject(query):
    with SessionLocal() as db:
        result = answer_question(db, query, CONTEXT)["items"][0]
        assert result["status"] == "needs_clarification" and "事项" in result["content"]


def test_award_scope_is_retrieved_with_its_independent_condition_paragraph():
    with SessionLocal() as db:
        doc = publish_parts(db, "管理学院国家励志奖学金评选办法.docx", [
            ("第1段", "一、评定范围"),
            ("第2段", "1.管理学院二年级以上（含二年级）全日制学生中品学兼优的家庭经济困难学生。"),
            ("第3段", "2.国家励志奖学金申请条件。"),
            ("第4段", "国家励志奖学金成绩优秀，排名前15%。"),
        ] + [(f"第{i}段", "国家励志奖学金大二成绩优秀、申请评定条件和程序说明。") for i in range(5, 16)])
        rows, _ = search_policy_evidence(db, "励志奖是不是大二才开始能评", CONTEXT)
        assert any(d.id == doc.id and "二年级以上" in chunk.text for chunk, d in rows)


def test_calendar_first_week_uses_a_verified_start_and_program_derived_end():
    from app.qa import calendar_period
    with SessionLocal() as db:
        doc = publish_parts(db, "武汉纺织大学2026-2027校历.pdf", [
            ("第2页 · 校历结构化 · 第二学期教学周",
             "2026-2027学年第二学期校历。2月22日开始上课，共18周。"
             "第二学期教学周覆盖2027年2月22日至2027年6月27日。"),
        ])
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc.id))
        chunk.location = "第2页 · 校历结构化 · 第二学期教学周"
        db.flush()
        result = calendar_period(db, "2027年春季第1周是2月22到2月28，对吧", CONTEXT)
        assert result and "2027年2月28日" in result["derived_facts"]
        assert (Decimal(28), "日") in quantities(result["derived_facts"])


def test_roster_model_must_use_requested_column_and_retry_a_different_teacher(monkeypatch):
    from app.config import settings
    from app.qa import DeepSeekModel

    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    attempts = []

    def model_answer(self, question, evidence, *, retry=False):
        record = evidence[0]
        assert record["roster_field"] == "班主任" and record["roster_value"] == "测试班主任"
        attempts.append(retry)
        name = "测试班主任" if retry else "测试辅导员"
        return {"answer": f"班主任是{name}。", "selections": [
            {"chunk_id": record["chunk_id"], "quote": record["excerpt"]}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    with SessionLocal() as db:
        publish_parts(db, "管理学院班主任统计表.xlsx", [
            ("工作表 Sheet1 第1行", "| 年级 | 辅导员 | 班级 | 班主任 | 班主任职称 |"),
            ("工作表 Sheet1 第2行", "| 2026级 | 测试辅导员 | 会计12601 | 测试班主任 | 讲师 |"),
        ], original_name="班主任统计表.xlsx")
        item = answer_question(db, "会计12601的班主任是谁", CONTEXT)["items"][0]
        assert attempts == [False, True]
        assert item["status"] == "answered" and "测试班主任" in item["content"]

