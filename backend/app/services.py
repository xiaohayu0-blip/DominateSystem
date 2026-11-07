from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from .db import SessionLocal, now
from .models import ChildEntry, Document, EmergencyEvent, Issue, MailJob, Question, WeeklyMailItem
from .qa import answer_question


def profile(db, person):
    children = db.scalars(select(ChildEntry).where(ChildEntry.parent_id == person.id)).all()
    return {"id": person.id, "role": person.role, "name": person.name, "phone": person.phone,
            "major": person.major, "class_name": person.class_name,
            "children": [{"id": c.id, "name": c.name, "phone": c.phone, "major": c.major,
                          "class_name": c.class_name, "relationship": c.relationship} for c in children],
            "identity_verified": False}


def finish_question(question_id, factory=SessionLocal):
    with factory() as db:
        q = db.get(Question, question_id)
        if not q or q.status != "pending":
            return
        try:
            answer = answer_question(db, q.text, q.context)
        except Exception:
            answer = {"items": [{"question": q.text, "status": "system_error",
                                 "content": "服务暂时不可用，已记录技术故障。", "citations": []}],
                      "context": q.context, "model_mode": q.model_mode}
        q.answer, q.status, q.completed_at = answer, "complete", now()
        for item in answer["items"]:
            if item["status"] != "answered":
                db.add(Issue(question_id=q.id, text=item["question"], reason=item["status"]))
        # Clarification can only resolve within same registration + explicit previous question.
        previous = db.get(Question, q.context.get("previous_id", ""))
        if previous and previous.registration_id == q.registration_id and all(
                i["status"] == "answered" for i in answer["items"]):
            for issue in db.scalars(select(Issue).where(Issue.question_id == previous.id,
                                                       Issue.reason == "needs_clarification",
                                                       Issue.merged_into.is_(None))):
                issue.state = "completed"
                issue.history = issue.history + [{"at": now().isoformat(), "action": "clarified",
                                                  "question_id": q.id}]
                issue.revision += 1
        db.commit()


def issue_data(db, issue):
    members = [issue] + list(db.scalars(select(Issue).where(Issue.merged_into == issue.id)))
    mail_by_issue = dict(db.execute(select(WeeklyMailItem.issue_id, MailJob)
        .join(MailJob).where(WeeklyMailItem.issue_id.in_([item.id for item in members]))).all())
    records = []
    for item in members:
        q = db.get(Question, item.question_id)
        records.append({"issue_id": item.id, "question_id": q.id, "question": q.text,
                        "subquestion": item.text, "reason": item.reason, "snapshot": q.snapshot,
                        "context": q.context, "answer": q.answer, "created_at": q.created_at,
                        "weekly_mail": weekly_mail_data(mail_by_issue.get(item.id))})
    return {"id": issue.id, "text": issue.text, "reason": issue.reason, "state": issue.state,
            "owner": issue.owner, "evidence_ids": issue.evidence_ids, "retests": issue.retests,
            "history": issue.history, "revision": issue.revision, "created_at": issue.created_at,
            "occurrences": records, "count": len(records)}


def weekly_mail_data(job):
    if not job:
        return None
    return {"status": job.status, "week_start": job.week_start,
            "sent_at": job.sent_at, "attempts": job.attempts}


def week_bounds(day):
    zone = ZoneInfo("Asia/Shanghai")
    start_day = day - timedelta(days=day.weekday())
    local = datetime.combine(start_day, time.min, zone)
    end = local + timedelta(days=7)
    return (local.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None), local, end)


def weekly(db, day):
    start, end, local, local_end = week_bounds(day)
    roots = db.scalars(select(Issue).where(Issue.merged_into.is_(None),
        Issue.question_id.not_in(select(EmergencyEvent.question_id))).order_by(Issue.created_at.desc())).all()
    new, backlog = [], []
    for root in roots:
        data = issue_data(db, root)
        # Group appears once per requested week even when a later occurrence joins an older root.
        times = [r["created_at"] for r in data["occurrences"]]
        if any(start <= t < end for t in times):
            new.append(data)
        elif min(times) < start and root.state not in {"completed", "dismissed"}:
            backlog.append(data)
    groups = {}
    for item in new:
        groups[item["reason"]] = groups.get(item["reason"], 0) + 1
    job = db.scalar(select(MailJob).where(MailJob.week_start == local.date().isoformat()))
    from .config import settings
    return {"start": local.isoformat(), "end": local_end.isoformat(), "new": new, "backlog": backlog,
            "counts": {"new": len(new), "backlog": len(backlog), "by_reason": groups},
            "timezone": "Asia/Shanghai", "mail_enabled": settings().weekly_mail_enabled,
            "weekly_mail": weekly_mail_data(job)}


def ensure_evidence(db, ids):
    for document_id in ids:
        document = db.get(Document, document_id)
        if not document or document.status != "published":
            raise HTTPException(422, "关联依据必须是已发布资料")
