import pytest

from app import conversation
from app.db import SessionLocal
from app.models import Document
from app.policy_context import search_policy_evidence
from app.qa import answer_question
from app.retrieval import build_chunks

CONTEXT = {"business_year": 2026, "admission_year": None}


def publish(db, title, parts, **scope):
    doc = Document(title=title, original_name=title, status="published", **scope)
    db.add(doc)
    db.flush()
    build_chunks(db, doc, parts, structured=True)
    return doc


@pytest.mark.parametrize(("question", "expected"), [
    ("会计12603班班主任是谁，啥职称", ("周老师", "教授")),
    ("2026级会计班的辅导员是谁", ("张老师",)),
    ("表里有哪个班主任没写职称", ("麻老师", "智能财务12401")),
])
def test_lookup_reads_all_requested_fields_before_asking_for_more(question, expected):
    with SessionLocal() as db:
        publish(db, "班主任与辅导员信息.xlsx", [
            ("工作表 名单 第1行", "| 年级 | 辅导员 | 班级 | 班主任 | 职称 |"),
            ("工作表 名单 第2行", "| 2026级 | 张老师 | 会计12601 | 李老师 | 讲师 |"),
            ("工作表 名单 第3行", "| 2026级 | 张老师 | 会计12603 | 周老师 | 教授 |"),
            ("工作表 名单 第4行", "| 2024级 | 王老师 | 智能财务12401 | 麻老师 | |"),
        ])
        item = answer_question(db, question, CONTEXT)["items"][0]
        assert item["status"] == "answered"
        assert all(value in item["content"] for value in expected)
        assert item["citations"]


def test_scoped_roster_with_two_different_people_still_asks_for_class():
    with SessionLocal() as db:
        publish(db, "辅导员表.xlsx", [
            ("工作表 名单 第1行", "| 班级 | 辅导员 | 班主任 |"),
            ("工作表 名单 第2行", "| 会计12601 | 张老师 | 王老师 |"),
            ("工作表 名单 第3行", "| 会计12602 | 李老师 | 赵老师 |"),
        ])
        item = answer_question(db, "2026级会计班的辅导员是谁", CONTEXT)["items"][0]
        assert item["status"] == "needs_clarification"


def test_no_blank_title_in_requested_major_does_not_include_another_major():
    with SessionLocal() as db:
        publish(db, "班主任表.xlsx", [
            ("工作表 名单 第1行", "| 班级 | 班主任 | 职称 |"),
            ("工作表 名单 第2行", "| 会计12602 | 李老师 | 讲师 |"),
            ("工作表 名单 第3行", "| 财管12602 | 周老师 | |"),
        ])
        item = answer_question(db, "会计班哪些班主任没填职称？", CONTEXT)["items"][0]
        assert item["status"] == "answered" and "均已填写" in item["content"]
        assert all("财管" not in source["excerpt"] for source in item["citations"])


def test_one_model_generation_receives_name_and_title_for_the_same_row(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    calls = []
    body = "会计12603班的班主任是周老师，职称是教授。"

    def generate(db, system, payload):
        calls.append(payload)
        return {"answer": body}

    monkeypatch.setattr(conversation, "model_json", generate)
    with SessionLocal() as db:
        publish(db, "班主任表.xlsx", [
            ("工作表 名单 第1行", "| 班级 | 班主任 | 班主任职称 |"),
            ("工作表 名单 第2行", "| 会计12603 | 周老师 | 教授 |"),
            ("工作表 名单 第3行", "| 会计12601 | 李老师 | 讲师 |"),
        ])
        item = conversation.answer_turn(db, "会计12603班班主任是谁，啥职称", CONTEXT)["items"][0]
    assert len(calls) == 1
    assert all(value in calls[0]["verified_facts"] for value in ("周老师", "教授"))
    assert "李老师" not in calls[0]["verified_facts"]
    assert item["content"] == body and len(item["citations"]) == 1


def test_request_for_four_days_includes_both_sides_of_page_break():
    with SessionLocal() as db:
        publish(db, "学生请假管理办法.pdf", [
            ("第64页 · 字符1", "第十一条 请假审批权限。请假3天以上、一周以内，由学院主管教学负责人批准。"),
            ("第65页 · 字符1", "请假一周以上、两周以内，由学院院长批准。第十二条 逾期按旷课处理。"),
            ("第66页 · 字符1", "第十五条 返校第一个工作日内到学院销假。"),
        ])
        evidence, _ = search_policy_evidence(db, "我下周要做手术，请四天假找谁签字？回来要不要销假？", CONTEXT)
        joined = " ".join(chunk.text for chunk, _ in evidence)
        assert "学院主管教学负责人" in joined and "学院院长" in joined and "第十五条" in joined


@pytest.mark.parametrize(("question", "title", "content"), [
    ("找人替考会怎么样", "学生手册.pdf", "请他人代替自己考试或代替他人考试的，给予开除学籍处分。"),
    ("书弄丢了怎么赔", "图书馆借阅规则.pdf", "丢失书刊应当赔偿，专业图书按原书价6至8倍赔偿。"),
])
def test_colloquial_question_finds_formal_clause(question, title, content):
    with SessionLocal() as db:
        doc = publish(db, title, [("第2页", content)])
        evidence, _ = search_policy_evidence(db, question, CONTEXT)
        assert evidence and evidence[0][1].id == doc.id


def test_award_amount_can_come_from_the_student_handbook():
    with SessionLocal() as db:
        publish(db, "学生手册.pdf", [("第218页", "国家奖学金的奖励标准为每生每年10000元。国家励志奖学金每生每年6000元。")],
                business_year=2026)
        item = answer_question(db, "国家奖学金多少钱一年", CONTEXT)["items"][0]
        assert item["status"] == "answered"
        assert "10000元" in item["content"] and "6000元" not in item["content"]


def test_exact_campus_grant_amount_is_retrieved_alongside_general_brochure():
    with SessionLocal() as db:
        publish(db, "2026年征兵宣传.pptx", [("第10页", "在校退役士兵学生可获国家助学金，每生3300元。")],
                business_year=2026)
        school = publish(db, "学生手册.pdf", [
            ("第228页", "第七条 全日制在校退役士兵学生资助标准为每生每年3700元。"),
        ], business_year=2026)
        evidence, _ = search_policy_evidence(db, "退役士兵有助学金吗，多少钱", CONTEXT)
        assert evidence[0][1].id == school.id and "3700元" in evidence[0][0].text


def test_unspecified_competition_category_does_not_use_the_national_level():
    from app.competition import SCORE_LOCATION_MARKER

    with SessionLocal() as db:
        publish(db, "学生综合评价办法.pdf", [
            ("第163页" + SCORE_LOCATION_MARKER + "国家级", "A+类学科竞赛国家级二等奖或同级奖：40分。"),
            ("第164页" + SCORE_LOCATION_MARKER + "省市区级", "A+类学科竞赛省市区级二等奖或同级奖：20分。"),
        ], business_year=2026)
        evidence, _ = search_policy_evidence(db, "省级竞赛二等奖能加综测分吗？", CONTEXT)
        assert any("20分" in chunk.text for chunk, _ in evidence)
        assert all("国家级二等奖" not in chunk.text for chunk, _ in evidence)


def test_campus_web_evidence_is_scoped_before_one_generation(monkeypatch):
    question = "本校本科生学业奖学金和国奖能一起拿吗？"
    sources = [
        {"id": "grad", "url": "https://gs.wtu.edu.cn/grad", "title": "研究生学业奖学金评定通知", "excerpt": "可以兼得。"},
        {"id": "other", "url": "https://other.edu.cn/award", "title": "外校本科生学业奖学金评定", "excerpt": "允许兼得。"},
        {"id": "foreign_region", "url": "https://www.liaoning.gov.cn/award", "title": "辽宁本科奖学金评定", "excerpt": "允许兼得。"},
        {"id": "school", "url": "https://student.wtu.edu.cn/award", "title": "本科生奖学金评定办法", "excerpt": "同一学年不可兼得。"},
    ]
    monkeypatch.setattr(conversation, "search_web", lambda query: sources)
    calls = []

    def generate(db, system, payload):
        calls.append(payload)
        return {"answer": "同一学年不能同时领取。", "source_ids": ["school"]}

    monkeypatch.setattr(conversation, "model_json", generate)
    item = conversation.find_web_answer(None, question, campus_gap=True)
    assert len(calls) == 1
    assert calls[0]["untrusted_search_snippets"] == [sources[3]]
    assert item["content"] == "同一学年不能同时领取。"
    assert item["web_sources"] == [sources[3]]


def test_wrong_campus_or_audience_does_not_get_sent_to_model(monkeypatch):
    monkeypatch.setattr(conversation, "search_web", lambda query: [
        {"id": "other", "url": "https://other.edu.cn/exam", "title": "外校学生考试规定", "excerpt": "考试违规处理。"},
    ])
    monkeypatch.setattr(conversation, "model_json", lambda *a: pytest.fail("没有本校依据不得调用回答"))
    with pytest.raises(conversation.MissingEvidence):
        conversation.find_web_answer(None, "本校考试违规按什么规定处分？", campus_gap=True)


def test_school_policy_checks_local_clause_even_if_planner_suggests_web(monkeypatch):
    from app.config import settings
    from app.qa import DeepSeekModel

    question = "找人替考会怎么样"
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    monkeypatch.setattr(conversation, "plan_turn", lambda *a: [{"question": question, "kind": "web"}])
    monkeypatch.setattr(conversation, "find_web_answer", lambda *a, **kw: pytest.fail("已有校内原文"))
    seen = []

    def select(self, query, evidence):
        source = evidence[0]
        seen.append(source["document_id"])
        return {"answer": "按学校规定，会被开除学籍。", "selections": [
            {"chunk_id": source["chunk_id"], "quote": source["excerpt"]}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", select)
    with SessionLocal() as db:
        source = publish(db, "学生手册.pdf", [("第128页", "请他人代替自己考试，给予开除学籍处分。")])
        item = conversation.answer_turn(db, question, CONTEXT)["items"][0]
    assert seen == [source.id]
    assert item["content"] == "按学校规定，会被开除学籍。"


def test_current_effective_window_is_passed_as_applicable_and_history_stays_available(monkeypatch):
    from app.config import settings
    from app.qa import DeepSeekModel

    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    calls = []

    def select(self, question, evidence):
        calls.append((question, evidence))
        return {"answer": "答复原文", "selections": [
            {"chunk_id": evidence[0]["chunk_id"], "quote": evidence[0]["excerpt"]}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", select)
    with SessionLocal() as db:
        publish(db, "本科学费补偿办法旧版.pdf", [("第1页", "学费补偿上限为16000元。")],
                business_year=2024, effective_to="2024-12-31")
        publish(db, "本科学费补偿办法新版.pdf", [("第1页", "学费补偿上限为20000元。")],
                business_year=2025, effective_from="2025-01-01")
        question = "本科生学费补偿最多多少？"
        current = answer_question(db, question, {**CONTEXT, "_planned_task": True})["items"][0]
        history = answer_question(db, question, {**CONTEXT, "business_year": 2024,
                                                "selected_year": 2024, "_planned_task": True})["items"][0]
    assert "20000元" in current["citations"][0]["excerpt"]
    assert "16000元" in history["citations"][0]["excerpt"]
    assert calls[0][1][0]["policy_applicability"] == "applicable"
    assert all(item["content"] == "答复原文" for item in (current, history))
