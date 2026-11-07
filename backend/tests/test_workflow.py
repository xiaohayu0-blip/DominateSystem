from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Issue, Question
from app.services import group_existing_issues, week_bounds, weekly
from conftest import signup, student
from test_knowledge import ask


def test_publish_automatically_retests_and_closes_related_issues(admin_client):
    c = admin_client
    signup(c, student())
    question = ask(c, "校外住宿补助如何申请")
    original = question["answer"]
    issue = c.get("/api/admin/issues").json()[0]
    ask(c, "图书馆遗失物品怎么领取")
    payload = {"revision": issue["revision"], "state": "completed", "owner": "演示老师", "note": "已处理", "evidence_ids": []}
    assert c.patch('/api/admin/issues/' + issue["id"], json=payload).status_code == 422
    d = c.post("/api/admin/documents/formal", json={
        "title": "住宿补助正式答复", "publisher": "演示学院", "confirmed_by": "演示老师",
        "confirmed_on": date.today().isoformat(), "basis": "合成演示制度第2条，适用所有年级",
        "content": "校外住宿补助申请需提交住宿证明及申请表，结果由学院人工审核。"}).json()
    assert d["status"] == "draft"
    assert c.post(f'/api/admin/documents/{d["id"]}/publish').status_code == 200
    resolved = c.get(f'/api/admin/issues/{issue["id"]}').json()
    assert resolved["state"] == "completed"
    assert resolved["retests"][-1]["passed"]
    assert resolved["evidence_ids"] == [d["id"]]
    assert resolved["history"][-1]["action"] == "auto_resolved"
    unrelated = next(item for item in c.get("/api/admin/issues").json() if item["id"] != issue["id"])
    assert unrelated["state"] == "open"
    assert not unrelated["retests"]
    payload["evidence_ids"] = [d["id"]]
    assert c.patch('/api/admin/issues/' + issue["id"], json=payload).status_code == 409
    report = c.get("/api/admin/weekly").json()
    assert any(item["id"] == issue["id"] and item["state"] == "completed" for item in report["new"])
    assert c.get("/api/questions/" + question["id"]).json()["answer"] == original
    assert c.get("/api/admin/audit").status_code == 200


def test_publication_leaves_issue_open_when_only_one_year_has_new_evidence(admin_client):
    c = admin_client
    signup(c, student())
    for year in (2023, 2024):
        ask(c, "校外住宿补助如何申请", business_year=year)
    doc = c.post("/api/admin/documents/formal", json={
        "title": "校外住宿补助申请办法", "publisher": "演示学院",
        "confirmed_by": "演示老师", "confirmed_on": date.today().isoformat(),
        "basis": "仅适用2023年度", "business_year": 2023,
        "content": "校外住宿补助申请需提交住宿证明及申请表。"}).json()
    assert c.post(f'/api/admin/documents/{doc["id"]}/publish').status_code == 200
    issue, = c.get("/api/admin/issues").json()
    assert issue["count"] == 2
    assert issue["state"] == "open"


def test_merge_preserves_all_people_and_weekly_no_duplicate(admin_client):
    c = admin_client
    for name, question in [("虚构甲", "校外住宿补助如何申请"), ("虚构乙", "如何申请校外住宿补助")]:
        signup(c, student(name=name))
        ask(c, question)
    before = c.get("/api/admin/issues").json()
    root, source = before
    merged = c.post(f'/api/admin/issues/{root["id"]}/merge', json={"source_ids": [source["id"]], "note": "已核对相同年度、事项和条件"})
    assert merged.status_code == 200
    assert {o["snapshot"]["name"] for o in merged.json()["occurrences"]} == {"虚构甲", "虚构乙"}
    assert c.get("/api/admin/weekly").json()["counts"]["new"] == 1
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Issue)))) == 2
        assert len(list(db.scalars(select(Question)))) == 2


def test_identical_issues_group_automatically_and_preserve_occurrences(admin_client):
    c = admin_client
    for name, suffix, year in [("虚构甲", "？", 2023), ("虚构乙", "。", 2026)]:
        signup(c, student(name=name))
        ask(c, "校外住宿补助如何申请" + suffix, business_year=year)
    group, = c.get("/api/admin/issues").json()
    assert group["count"] == 2
    assert c.get("/api/admin/weekly").json()["counts"]["new"] == 1

    with SessionLocal() as db:
        child = db.scalar(select(Issue).where(Issue.merged_into == group["id"]))
        child.merged_into = None
        db.commit()
        assert group_existing_issues(db) == 1
        db.commit()
        assert group_existing_issues(db) == 0
        db.commit()
    group, = c.get("/api/admin/issues").json()
    assert {o["snapshot"]["name"] for o in group["occurrences"]} == {"虚构甲", "虚构乙"}
    assert {o["context"]["business_year"] for o in group["occurrences"]} == {2023, 2026}
    retest = c.post(f'/api/admin/issues/{group["id"]}/retest').json()["retests"][-1]
    assert {r["answer"]["context"]["business_year"] for r in retest["results"]} == {2023, 2026}


def test_week_boundary_backlog_and_history(admin_client):
    c = admin_client
    signup(c, student())
    q = ask(c, "校外住宿补助")
    monday_start, end, _, _ = week_bounds(date(2026, 11, 2))
    assert monday_start == datetime(2026, 11, 1, 16)
    assert end == datetime(2026, 11, 8, 16)
    with SessionLocal() as db:
        issue = db.scalar(select(Issue))
        issue.created_at = monday_start - timedelta(seconds=1)
        db.get(Question, q["id"]).created_at = issue.created_at
        db.commit()
        report = weekly(db, date(2026, 11, 2))
        assert report["counts"] == {"new": 0, "backlog": 1, "by_reason": {}}
        assert report["total_issue_groups"] == 1
        issue.state = "completed"
        db.commit()
        report = weekly(db, date(2026, 11, 2))
        assert report["counts"]["backlog"] == 0
        assert report["total_issue_groups"] == 1
        assert weekly(db, date(2026, 10, 26))["counts"]["new"] == 1
        issue.created_at = monday_start
        db.get(Question, q["id"]).created_at = monday_start
        db.commit()
        assert weekly(db, date(2026, 11, 2))["counts"]["new"] == 1


def test_idempotent_requests_and_cross_context_access(client):
    first = signup(client, student()).json()
    data = {"request_id": "same-request-00001", "text": "住宿补助"}
    a = client.post("/api/questions", json=data).json()
    b = client.post("/api/questions", json=data).json()
    assert a["id"] == b["id"]
    assert client.post("/api/questions", json={**data, "business_year": 2025}).status_code == 409
    assert client.post("/api/questions", json={**data, "text": "另一个问题"}).status_code == 409
    signup(client, student(name="演示乙"))
    assert client.post("/api/questions", json={**data, "previous_id": a["id"]}).status_code == 404
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Question).where(Question.registration_id == first["id"])))) == 1


def test_clarification_resolves_only_own_previous(client):
    from test_knowledge import publish
    signup(client, student())
    publish(title="转专业说明", content="转专业申请须提交学习计划，由学院审核。", grade=2024)
    prior = ask(client, "转专业申请条件")
    assert prior["answer"]["items"][0]["status"] == "needs_clarification"
    answer = ask(client, "转专业申请条件", admission_year=2024, previous_id=prior["id"])
    assert answer["answer"]["items"][0]["status"] == "answered"
    with SessionLocal() as db:
        old = db.scalar(select(Issue).where(Issue.question_id == prior["id"]))
        assert old.state == "completed"


def test_merge_rejects_different_years_and_technical_closure(admin_client):
    c = admin_client
    signup(c, student())
    ask(c, "住宿补助", business_year=2025)
    ask(c, "如何申请住宿补助", business_year=2026)
    one, two = c.get("/api/admin/issues").json()
    assert c.post(f'/api/admin/issues/{one["id"]}/merge', json={"source_ids": [two["id"]], "note": "不同年度"}).status_code == 422
    with SessionLocal() as db:
        db.get(Issue, one["id"]).reason = "system_error"
        db.commit()
    assert c.patch('/api/admin/issues/' + one["id"], json={"revision": one["revision"], "state": "completed", "note": "技术服务已恢复"}).status_code == 200
