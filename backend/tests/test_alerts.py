from datetime import timedelta

import pytest
from sqlalchemy import select

from app.alerts import detect_danger, retention_preview, run_one
from app.config import settings
from app.db import SessionLocal, now
from app.models import EmergencyEvent, Issue, MailAttempt, MailJob, Question
from conftest import signup, parent, student


@pytest.mark.parametrize("text,expected", [
    ("我不想活了", True), ("孩子说他想自杀", True), ("我现在有生命危险，救命", True),
    ("我没有自杀想法", False), ("预防轻生的制度是什么", False), ("新闻中提到想自杀", False),
])
def test_detector_examples(text, expected):
    assert detect_danger(text) == expected


def alert(client, request_id="emergency-test-00001"):
    return client.post("/api/questions", json={"request_id": request_id, "text": "演示用例：孩子说他不想活了"})


def test_event_skips_knowledge_and_keeps_parent_snapshot(client, monkeypatch):
    signup(client, parent())
    seen = []
    from app import services
    def examine(db, text, context):
        seen.append(text)
    monkeypatch.setattr(services, "answer_question", examine)
    response = alert(client)
    assert response.status_code == 202 and response.json()["alert"]
    assert not seen
    with SessionLocal() as db:
        q = db.get(Question, response.json()["id"])
        assert q.status == "complete" and q.answer["items"] == []
        assert db.scalar(select(Issue).where(Issue.question_id == q.id)) is None
        assert q.snapshot["role"] == "parent" and len(q.snapshot["children"]) == 2
    assert run_one()
    with SessionLocal() as db:
        assert db.scalar(select(MailJob)).status == "simulated_sent"
        assert db.scalar(select(EmergencyEvent)).state == "open"


def test_retry_restart_lease_and_idempotency(client, monkeypatch):
    signup(client, student())
    assert alert(client).status_code == 202
    assert alert(client).status_code == 202
    monkeypatch.setattr(settings(), "mock_mail_failures", 1)
    assert run_one()
    with SessionLocal() as db:
        job = db.scalar(select(MailJob))
        assert job.status == "retry"
        job.next_attempt = now() - timedelta(seconds=1)
        db.commit()
    # New factory call represents a new worker process reading the same persistent DB.
    assert run_one()
    assert not run_one()
    with SessionLocal() as db:
        assert len(list(db.scalars(select(EmergencyEvent)))) == 1
        job = db.scalar(select(MailJob))
        assert job.status == "simulated_sent" and job.attempts == 2
        assert len(list(db.scalars(select(MailAttempt)))) == 2
    assert alert(client, "emergency-test-00002").status_code == 202
    with SessionLocal() as db:
        assert len(list(db.scalars(select(EmergencyEvent)))) == 2


def test_crashed_mock_recovers_but_smtp_uncertain_not_resent(client):
    signup(client, student())
    alert(client)
    with SessionLocal() as db:
        job = db.scalar(select(MailJob))
        job.status, job.lease_until = "sending", now() - timedelta(seconds=1)
        db.commit()
    assert run_one()
    with SessionLocal() as db:
        assert db.scalar(select(MailJob)).status == "simulated_sent"
        job = db.scalar(select(MailJob))
        job.status, job.mode, job.lease_until = "sending", "smtp", now() - timedelta(seconds=1)
        db.commit()
    assert not run_one()
    with SessionLocal() as db:
        assert db.scalar(select(MailJob)).status == "uncertain"


def test_smtp_missing_config_visible_and_no_external_mail(client):
    signup(client, student())
    alert(client)
    with SessionLocal() as db:
        db.scalar(select(MailJob)).mode = "smtp"
        db.commit()
    assert run_one()
    with SessionLocal() as db:
        assert db.scalar(select(MailJob)).status == "unconfigured"


def test_retention_only_preview_preserves_unresolved_and_questions(client):
    signup(client, student())
    alert(client)
    run_one()
    with SessionLocal() as db:
        job = db.scalar(select(MailJob))
        job.sent_at = now() - timedelta(days=8)
        db.commit()
        data = retention_preview(db)
        assert not data["deletion_enabled"] and len(data["candidates"]) == 1
        assert job.body and db.scalar(select(Question)) and db.scalar(select(EmergencyEvent)).state == "open"
        job.status = "failed"
        db.commit()
        assert not retention_preview(db)["candidates"]


def test_admin_disposition_and_success_cannot_resend(admin_client):
    c = admin_client
    signup(c, student())
    alert(c)
    run_one()
    event = c.get("/api/admin/events").json()[0]
    assert c.post('/api/admin/events/' + event["id"] + '/retry').status_code == 409
    assert c.patch('/api/admin/events/' + event["id"], json={"state": "closed", "note": "仅合成演示，人工处置记录"}).status_code == 200
    assert c.get("/api/admin/events").json()[0]["history"][-1]["state"] == "closed"
