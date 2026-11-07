import logging
import unicodedata
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, text, update

from .db import SessionLocal, now
from .models import Admin, ChildEntry, Document, EmergencyEvent, Issue, MailJob, Question, WeeklyMailItem
from .qa import answer_question
from .retrieval import retriever
from .security import audit


logger = logging.getLogger("dominate")


def profile(db, person):
    children = db.scalars(select(ChildEntry).where(ChildEntry.parent_id == person.id)).all()
    return {"id": person.id, "role": person.role, "name": person.name, "phone": person.phone,
            "major": person.major, "class_name": person.class_name,
            "children": [{"id": c.id, "name": c.name, "phone": c.phone, "major": c.major,
                          "class_name": c.class_name, "relationship": c.relationship} for c in children],
            "identity_verified": False}


def issue_group_key(issue_text, reason, context):
    if issue_text == context.get("followup_input"):
        issue_text = context.get("followup_question") or issue_text
    normalized = unicodedata.normalize("NFKC", issue_text).casefold()
    normalized = " ".join(normalized.split()).rstrip(" ?。!.,，")
    return normalized, reason


def open_issue_roots():
    return select(Issue, Question.context).join(Question, Issue.question_id == Question.id).where(
        Issue.merged_into.is_(None), Issue.state.not_in(("completed", "dismissed")),
        Issue.reason != "needs_clarification",
        Issue.question_id.not_in(select(EmergencyEvent.question_id)),
    ).order_by(Issue.created_at, Issue.id)


def lock_issue_grouping(db):
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(74831904)"))
    elif dialect == "sqlite":
        # Take the write lock before reading existing groups.
        db.execute(text("BEGIN IMMEDIATE"))


def group_existing_issues(db):
    lock_issue_grouping(db)
    roots = {}
    grouped = 0
    for issue, context in db.execute(open_issue_roots()).all():
        # Preserve separately worked records and any existing manual groups.
        if issue.state != "open" or issue.owner or issue.history or issue.retests or issue.evidence_ids:
            continue
        key = issue_group_key(issue.text, issue.reason, context)
        root = roots.setdefault(key, issue)
        if root.id != issue.id:
            for child in db.scalars(select(Issue).where(Issue.merged_into == issue.id)):
                child.merged_into = root.id
            issue.merged_into = root.id
            root.revision += 1
            grouped += 1
    return grouped


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
        failed_items = [item for item in answer["items"] if item["status"] != "answered"]
        if failed_items:
            # End the retrieval transaction before acquiring the grouping lock.
            db.rollback()
            lock_issue_grouping(db)
            q = db.get(Question, question_id, populate_existing=True)
            if not q or q.status != "pending":
                return
            roots = {}
            for issue, context in db.execute(open_issue_roots()).all():
                roots.setdefault(issue_group_key(issue.text, issue.reason, context), issue)
            for item in failed_items:
                key = issue_group_key(item["question"], item["status"], q.context)
                root = roots.get(key) if item["status"] != "needs_clarification" else None
                issue = Issue(question_id=q.id, text=item["question"], reason=item["status"],
                              merged_into=root.id if root else None)
                db.add(issue)
                if root is None:
                    db.flush()
                    roots[key] = issue
                else:
                    root.revision += 1
        q.answer, q.status, q.completed_at = answer, "complete", now()
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


def retest_issue(db, issue, actor_name, required_document_id=None):
    members = db.execute(select(Issue, Question.context).join(Question).where(
        (Issue.id == issue.id) | (Issue.merged_into == issue.id)
    )).all()
    if required_document_id is not None:
        for member, context in members:
            query = member.text
            if query == context.get("followup_input"):
                query = context.get("followup_question") or query
            evidence, _ = retriever.search(db, query, context)
            if not any(document.id == required_document_id for _, document in evidence):
                return None

    results = []
    passed = True
    evidence_ids = set()
    for member, context in members:
        result = answer_question(db, member.text, context)
        results.append({"issue_id": member.id, "answer": result})
        items = result["items"]
        for item in items:
            cited = {citation["document_id"] for citation in item["citations"]}
            evidence_ids.update(cited)
            passed &= item["status"] == "answered" and (
                required_document_id is None or required_document_id in cited)
        passed &= bool(items)
        if required_document_id is not None and not passed:
            return None
    return {"at": now().isoformat(), "by": actor_name, "passed": passed,
            "results": results, "evidence_ids": sorted(evidence_ids)}


def resolve_issues_after_publish(document_id, admin_id, factory=SessionLocal):
    with factory() as db:
        issue_ids = list(db.scalars(open_issue_roots().with_only_columns(Issue.id).where(
            Issue.reason.in_(("missing_material", "document_conflict"))
        )))
    for issue_id in issue_ids:
        try:
            with factory() as db:
                issue = db.get(Issue, issue_id)
                actor = db.get(Admin, admin_id)
                if not issue or not actor or issue.merged_into or issue.state in {"completed", "dismissed"}:
                    continue
                revision = issue.revision
                retest = retest_issue(db, issue, actor.username, required_document_id=document_id)
                if retest is None:
                    continue
                retests = issue.retests + [retest]
                history = issue.history + [{"at": now().isoformat(), "by": actor.username,
                                            "state": "completed", "action": "auto_resolved",
                                            "document_id": document_id}]
                # End model/retrieval reads before locking and checking the current state.
                db.rollback()
                lock_issue_grouping(db)
                published = db.scalar(select(Document.id).where(
                    Document.id == document_id, Document.status == "published"
                ).with_for_update())
                if not published:
                    return
                changed = db.execute(update(Issue).where(
                    Issue.id == issue_id, Issue.revision == revision, Issue.merged_into.is_(None),
                    Issue.state.not_in(("completed", "dismissed")),
                ).values(state="completed", retests=retests, history=history,
                         evidence_ids=retest["evidence_ids"], revision=revision + 1))
                if changed.rowcount == 1:
                    audit(db, actor, "issue_auto_resolved", issue_id, document_id=document_id)
                    db.commit()
        except Exception:
            logger.exception("Automatic issue retest failed for issue %s", issue_id)


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
            "total_issue_groups": len(roots),
            "counts": {"new": len(new), "backlog": len(backlog), "by_reason": groups},
            "timezone": "Asia/Shanghai", "mail_enabled": settings().weekly_mail_enabled,
            "weekly_mail": weekly_mail_data(job)}


def ensure_evidence(db, ids):
    for document_id in ids:
        document = db.get(Document, document_id)
        if not document or document.status != "published":
            raise HTTPException(422, "关联依据必须是已发布资料")
