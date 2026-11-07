from datetime import datetime

from sqlalchemy import select

from app import alerts
from app.db import SessionLocal
from app.models import Issue, MailJob
from conftest import signup, student
from test_knowledge import ask


def test_monday_weekly_mail_tracks_delivery_separately_from_handling(admin_client, monkeypatch):
    signup(admin_client, student())
    issue_question = ask(admin_client, "不存在的演示制度怎么办？")
    due = datetime(2026, 11, 9, 1)
    with SessionLocal() as db:
        issue = db.scalar(select(Issue).where(Issue.question_id == issue_question["id"]))
        issue.created_at = datetime(2026, 11, 4, 5)
        issue_id = issue.id
        db.commit()
    alerts.enqueue_weekly_mail(at=datetime(2026, 11, 9, 0, 59))
    with SessionLocal() as db:
        assert db.scalar(select(MailJob)) is None
    alerts.enqueue_weekly_mail(at=due)
    alerts.enqueue_weekly_mail(at=due)
    with SessionLocal() as db:
        jobs = list(db.scalars(select(MailJob)))
        assert len(jobs) == 1 and jobs[0].week_start == "2026-11-02"
    monkeypatch.setattr(alerts, "now", lambda: due)
    assert alerts.run_one()
    result = admin_client.get("/api/admin/issues/" + issue_id).json()
    assert result["state"] == "open"
    assert result["occurrences"][0]["weekly_mail"]["status"] == "simulated_sent"
    report = admin_client.get("/api/admin/weekly?day=2026-11-04").json()
    assert report["weekly_mail"]["status"] == "simulated_sent"
