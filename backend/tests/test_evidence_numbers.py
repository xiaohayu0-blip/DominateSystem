from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.evidence_numbers import quantities
from app.models import Chunk
from app.qa import DeepSeekModel, InvalidEvidence, verified_excerpts
from app.retrieval import citation, retriever
from conftest import signup, student
from test_knowledge import ask, publish


@pytest.mark.parametrize("original,answer", [
    ("不少于三个工作日", "不少于3个工作日"), ("不少于五个工作日", "不少于5工作日"),
    ("三十%", "30%"), ("零点三分", "0.3分"), ("二十人", "20人"),
    ("一百二十五元", "125元"), ("两万元", "20000元"), ("第十五条", "第15条"),
])
def test_equivalent_chinese_and_arabic_quantities(original, answer):
    assert quantities(original) == quantities(answer)


@pytest.mark.parametrize("text", [
    "1. 政策宣讲；2. 本人申请", "1).政策宣讲\n2).本人申请", "（1）政策宣讲\n（2）本人申请",
    "评审程序如下：1.政策宣讲；2.本人申请", "评审包括1.政策宣讲，2.本人申请", "流程为：(1)政策宣讲；(2)本人申请",
])
def test_presentation_numbers_are_not_policy_quantities(text):
    assert not quantities(text)


def test_decimal_percentages_dates_and_real_amounts_are_not_removed_as_bullets():
    assert quantities("0.3分；3.5%；2026年1月2日；20人") == {
        (Decimal("0.3"), "分"), (Decimal("3.5"), "%"), (Decimal(2026), "年"),
        (Decimal(1), "月"), (Decimal(2), "日"), (Decimal(20), "人")}
    assert not quantities("时间点分别说明，重点分析。")


def count_case(original, answer, members, phrase="这两项"):
    reference = {"chunk_id": "rule", "document_id": "policy", "excerpt": original,
                 "title": "办理规定", "location": "第1条"}
    selected = {"answer": answer, "selections": [{"chunk_id": "rule", "quote": original}],
                "enumerated_counts": [{"phrase": phrase, "chunk_id": "rule", "members": members}]}
    return selected, [reference]


@pytest.mark.parametrize(("original", "answer", "members"), [
    ("智育成绩排名与综合测评成绩排名均须达到前10%。", "这两项都须达到前10%。",
     ["智育成绩排名", "综合测评成绩排名"]),
    ("申请表与成绩单均须在三个工作日内提交。", "这两项都须在3个工作日内提交。",
     ["申请表", "成绩单"]),
    ("志愿服务与社会实践均可加0.3分，且同一项目不重复计分。", "这两项都可加0.3分，且同一项目不重复计分。",
     ["志愿服务", "社会实践"]),
])
def test_derived_count_matches_named_members_and_shared_original_condition(original, answer, members):
    selected, sources = count_case(original, answer, members)
    assert verified_excerpts(selected, sources, cohort_options=False, deepseek=True)[0] == answer


@pytest.mark.parametrize("answer", [
    "这两项都须达到前30%。",
    "这两项都须低于前10%。",
    "这两项都须达到前10%。另外至少提交两项证明。",
])
def test_count_grounding_does_not_exempt_wrong_or_unrelated_quantities(answer):
    selected, sources = count_case("智育排名与综测排名均须达到前10%。",
                                   answer, ["智育排名", "综测排名"])
    with pytest.raises(InvalidEvidence):
        verified_excerpts(selected, sources, cohort_options=False, deepseek=True)


@pytest.mark.parametrize(("original", "answer"), [
    ("申请表与成绩单均须提交，且无不及格科目。", "这两项都须提交。"),
    ("申请表与成绩单均须提交。若因系统故障无法提交，可走人工审核。", "这两项都须提交。"),
    ("仅限大二同学申请。申请表与成绩单均须提交。", "这两项都须提交。"),
    ("申请表、成绩单与获奖证明均须提交。", "这两项都须提交。"),
    ("申请表须提交。成绩单须提交。", "这两项都须提交。"),
])
def test_count_grounding_keeps_scope_conditions_exceptions_and_full_enumeration(original, answer):
    selected, sources = count_case(original, answer, ["申请表", "成绩单"])
    with pytest.raises(InvalidEvidence):
        verified_excerpts(selected, sources, cohort_options=False, deepseek=True)


def test_deadline_retries_invalid_model_duration(client, monkeypatch):
    signup(client, student())
    original = "申请表与成绩单均须在三个工作日内提交。若系统故障，可向学院申请人工审核。"
    doc_id = publish("奖学金材料提交办法", original)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    attempts = []

    def wrong_count(_, query, candidates, *, retry=False):
        attempts.append(retry)
        if retry:
            return {"answer": "申请表和成绩单须在3个工作日内提交，系统故障可申请人工审核。",
                    "selections": [{"chunk_id": candidates[0]["chunk_id"], "quote": original}]}
        return {"answer": "这两项都须在5个工作日内提交。",
                "selections": [{"chunk_id": candidates[0]["chunk_id"], "quote": original}],
                "enumerated_counts": [{"phrase": "这两项", "chunk_id": candidates[0]["chunk_id"],
                                       "members": ["申请表", "成绩单"]}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", wrong_count)
    item, = ask(client, "奖学金申请材料要在几天内提交？")["answer"]["items"]
    assert attempts == [False, True]
    assert item["status"] == "answered" and "3个工作日内提交" in item["content"]
    assert "5个工作日" not in item["content"] and item["citations"][0]["document_id"] == doc_id


def test_deadline_keeps_exception_and_general_rule_separate(client, monkeypatch):
    signup(client, student())
    original = "申请表与成绩单均须在三个工作日内提交。若系统故障，可在五个工作日内补交。"
    publish("奖学金材料提交办法", original)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")

    def model(_, query, candidates, *, retry=False):
        chunk = candidates[0]["chunk_id"]
        if retry:
            assert "例外条件" in query
        return {"answer": ("申请表和成绩单须在3个工作日内提交。系统故障时可在5个工作日内补交。" if retry
                           else "申请表与成绩单均须在五个工作日内提交。"),
                "selections": [{"chunk_id": chunk, "quote": original}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model)
    item, = ask(client, "奖学金申请材料要在几天内提交？")["answer"]["items"]
    assert item["status"] == "answered" and "3个工作日内提交" in item["content"]
    assert "系统故障时可在5个工作日内补交" in item["content"]
    assert "均须在五个工作日内提交" not in item["content"]


@pytest.mark.parametrize("days", [3, 5])
def test_normalized_days_do_not_allow_invented_periods(days):
    with SessionLocal() as db:
        doc_id = publish("国家励志奖学金办法", "学院公示不少于三个工作日，学校公示不少于五个工作日。")
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc_id))
        from app.models import Document
        source = citation(chunk, db.get(Document, doc_id))
        correct = {"answer": f"公示不少于{days}个工作日。", "selections": [{"chunk_id": chunk.id, "quote": chunk.text}]}
        assert verified_excerpts(correct, [source], cohort_options=False, deepseek=True)[0] == correct["answer"]
        wrong = {**correct, "answer": "公示不少于7个工作日。"}
        with pytest.raises(InvalidEvidence, match="unsupported_summary_quantity"):
            verified_excerpts(wrong, [source], cohort_options=False, deepseek=True)


def test_procedure_retrieval_keeps_all_steps_and_chinese_days_pass_validation(client, monkeypatch):
    signup(client, student())
    steps = [
        "1).政策宣讲。说明国家励志奖学金政策。", "2).本人申请。提交国家励志奖学金申请。",
        "3).资格审查。小组人数不少于班级总人数的20%。", "4).班级评议。班级讨论推荐名单。",
        "5).学院初评。公示不少于三个工作日。", "6).学校审定。公示不少于五个工作日。",
    ]
    from app.models import Document
    from app.retrieval import build_chunks
    with SessionLocal() as db:
        document = Document(title="管理学院国家励志奖学金评选办法.docx", status="published")
        db.add(document)
        db.flush()
        parts = [("第23段", "国家励志奖学金的评审程序：")]
        parts += [(f"第{24 + index}段", text) for index, text in enumerate(steps)]
        parts += [("第30段", "五、申诉处理"), ("第31段", "异议可在公示期间提出。")]
        build_chunks(db, document, parts)
        db.commit()
        rows, special = retriever.search(db, "国家励志奖学金的评审程序有哪些", {"business_year": 2026})
        assert special == "" and [chunk.text for chunk, _ in rows] == steps
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    calls = []
    summary = ("评审共有6个步骤。\n1.政策宣讲；2.本人申请；3.资格审查，小组人数不少于班级总人数的20%；"
               "4.班级评议；5.学院初评，公示不少于3个工作日；6.学校审定，公示不少于5个工作日。")

    def model_answer(_, query, evidence, *, retry=False):
        calls.append(retry)
        return {"answer": summary, "selections": [{"chunk_id": source["chunk_id"], "quote": source["excerpt"]}
                                                  for source in evidence]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, "国家励志奖学金的评审程序有哪些?")["answer"]["items"][0]
    assert item["status"] == "answered" and item["content"] == summary
    assert len(item["citations"]) == 6 and calls == [False]


@pytest.mark.parametrize("original,answer,quote", [
    ("公示不少于三个工作日，公示期满无异议后报送。", "至少公示3个工作日，没有异议后报送。", "公示至少3个工作日，无异议后报送"),
    ("公示不少于三个工作日。", "公示3个工作日以上。", "公示三工作日或更久"),
    ("同一项目不重复加分。", "相同项目只计一次分。", "同项目不累加分数"),
    ("申请材料包括申请表和成绩单。", "需要交成绩单和申请表。", "成绩单、申请表"),
    ("奖励为每人每年6000元。", "每名学生一年奖励6000元。", "每生每年六千元"),
    ("可以。", "可以", "可以"),
])
def test_same_meaning_paraphrases_and_reworded_quotes_are_accepted(original, answer, quote):
    from app.models import Document
    with SessionLocal() as db:
        doc_id = publish("评选办法", original)
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc_id))
        source = citation(chunk, db.get(Document, doc_id))
        selected = {"answer": answer, "selections": [{"chunk_id": chunk.id, "quote": quote}]}
        content, valid = verified_excerpts(selected, [source], cohort_options=False, deepseek=True)
        assert content == answer and valid[0]["excerpt"] == original


@pytest.mark.parametrize("answer", ["公示最多3个工作日。", "公示低于3个工作日。"])
def test_reversed_condition_is_not_a_same_meaning_paraphrase(answer):
    from app.models import Document
    with SessionLocal() as db:
        doc_id = publish("评选办法", "公示不少于三个工作日。")
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc_id))
        source = citation(chunk, db.get(Document, doc_id))
        selected = {"answer": answer, "selections": [{"chunk_id": chunk.id, "quote": chunk.text}]}
        with pytest.raises(InvalidEvidence, match="reversed_summary_condition"):
            verified_excerpts(selected, [source], cohort_options=False, deepseek=True)


@pytest.mark.parametrize("index", [0, 1])
def test_real_deepseek_failed_outputs_pass_with_actual_sources(index):
    import json
    from pathlib import Path

    fixture = json.loads((Path(__file__).parent / "fixtures" / "real-scholarship-procedure.json").read_text(encoding="utf-8"))
    record = fixture["records"][index]
    assert record["validation"] == "InvalidEvidence:unsupported_summary_quantity"
    content, sources = verified_excerpts(record["selected"], fixture["sources"], cohort_options=False, deepseek=True)
    assert content == record["selected"]["answer"] and len(content) <= 500
    assert len(sources) == 6 and "不少于3个工作日" in content and "不少于5个工作日" in content


@pytest.mark.parametrize("reference", [
    "武汉纺织大学学生手册（2026版）", "学生手册（2026版）", "武汉纺织大学学生手册（2026版）.pdf",
])
def test_actual_document_version_in_reference_is_not_a_policy_quantity(reference):
    # Actual source title and passage; the answer is constructed to isolate metadata validation.
    source = {"chunk_id": "handbook", "document_id": "handbook", "location": "第169页",
              "title": "武汉纺织大学学生手册（2026版）.pdf",
              "excerpt": "校、院级学生组织干事及其他班委、寝室长，加0-5分/学期，由学院负责认定加分。"}
    answer = f"根据《{reference}》，寝室长加0—5分/学期，由学院负责认定加分。"
    selected = {"answer": answer, "selections": [{"chunk_id": source["chunk_id"], "quote": source["excerpt"]}]}
    assert verified_excerpts(selected, [source], cohort_options=False, deepseek=True)[0] == answer


@pytest.mark.parametrize('reference', ['学生手册（2026版）', '按学生手册（2026版）', '武汉纺织大学学生手册（2026版）'])
def test_natural_unbracketed_document_reference_keeps_real_version(reference):
    source = {'chunk_id': 'handbook', 'document_id': 'handbook', 'location': '第169页',
              'title': '武汉纺织大学学生手册（2026版）.pdf',
              'excerpt': '寝室长加0-5分/学期，由学院负责认定加分。'}
    answer = f'能加。{reference}里，寝室长由学院认定加0—5分/学期。'
    selected = {'answer': answer, 'selections': [{'chunk_id': 'handbook', 'quote': source['excerpt']}]}
    assert verified_excerpts(selected, [source], cohort_options=False, deepseek=True)[0] == answer


@pytest.mark.parametrize('answer', [
    '学生手册（2027版）里，寝室长加0—5分/学期。',
    '学生手册（2026版）里，寝室长加0—7分/学期。',
    '学生手册（2026版）里，另外加2026分。',
    '其他大学学生手册（2026版）里，寝室长加0—5分/学期。',
])
def test_unbracketed_document_reference_cannot_hide_wrong_year_or_score(answer):
    source = {'chunk_id': 'handbook', 'document_id': 'handbook', 'location': '第169页',
              'title': '武汉纺织大学学生手册（2026版）.pdf',
              'excerpt': '寝室长加0-5分/学期，由学院负责认定加分。'}
    selected = {'answer': answer, 'selections': [{'chunk_id': 'handbook', 'quote': source['excerpt']}]}
    with pytest.raises(InvalidEvidence, match='unsupported_summary_quantity'):
        verified_excerpts(selected, [source], cohort_options=False, deepseek=True)


@pytest.mark.parametrize("answer", [
    "根据《武汉纺织大学学生手册（2027版）》，寝室长加0—5分/学期。",
    "根据《学生手册（2026版）》，寝室长加0—7分/学期。",
    "根据《学生手册（2026版）》，寝室长加0—5元/学期。",
    "根据《学生手册（2026版）》，另加2026分。",
    "根据《学生手册（2026版）》，另需2026项材料。",
])
def test_document_references_do_not_allow_wrong_versions_or_invented_policy_numbers(answer):
    source = {"chunk_id": "handbook", "document_id": "handbook", "location": "第169页",
              "title": "武汉纺织大学学生手册（2026版）.pdf",
              "excerpt": "寝室长加0-5分/学期，由学院负责认定加分。"}
    selected = {"answer": answer, "selections": [{"chunk_id": source["chunk_id"], "quote": source["excerpt"]}]}
    with pytest.raises(InvalidEvidence, match="unsupported_summary_quantity"):
        verified_excerpts(selected, [source], cohort_options=False, deepseek=True)


@pytest.mark.parametrize("index", range(4))
def test_real_dorm_leader_outputs_keep_score_range_and_conditions(index):
    import json
    from pathlib import Path

    fixture = json.loads((Path(__file__).parent / "fixtures" / "real-dorm-leader.json").read_text(encoding="utf-8"))
    selected = fixture["records"][index]["selected"]
    content, sources = verified_excerpts(selected, fixture["sources"], cohort_options=False, deepseek=True)
    assert content == selected["answer"] and len(sources) == 2
    assert "0.5—1分" in content and "不足4人" in content and "累计加分" in content


def test_direct_source_keeps_number_outside_the_selected_excerpt(client, monkeypatch):
    signup(client, student())
    quote = "合成测试寝室长规则：按工作表现认定加分。考核合格加2分。"
    publish("合成测试寝室长加分", quote)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    calls = []

    def model_answer(_, query, evidence, *, retry=False):
        calls.append(retry)
        return {"answer": "考核合格加2分。", "selections": [{"chunk_id": evidence[0]["chunk_id"],
                "quote": quote if retry else "合成测试寝室长规则：按工作表现认定加分。"}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    item = ask(client, "寝室长考核合格加几分")["answer"]["items"][0]
    assert calls == [False, True] and item["status"] == "answered"
    assert "2分" in item["content"] and quote in item["citations"][0]["excerpt"]
