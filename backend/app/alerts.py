import json
import re
import smtplib
import ssl
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from email.message import EmailMessage
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import SessionLocal, now
from .models import EmergencyEvent, Issue, MailAttempt, MailJob, Question, WeeklyMailItem
from .schemas import scrub

HELP_TEXT = "如果你或这位同学可能马上受到伤害，请立即联系身边可信任的人或当地紧急救助。不要等待系统通知。识别可能误报或漏报，邮件不能代替紧急求助，也不保证收件人及时阅读。"


def detect_danger(text: str, previous: str = "") -> bool:
    # Transparent, deliberately limited detector. Context is included for continuation cues.
    direct = r"想死|不想活|想自杀|要自杀|准备自杀|正在自残|正在自伤|活不下去|结束生命|生命危险|救命"
    for sentence in re.split(r"[。！？!?\n]", text):
        if re.search(r"没有.{0,3}(自杀|自伤|轻生)|不想自杀|新闻|制度|预防|科普|论文", sentence):
            continue
        if re.search(direct, sentence):
            return True
    if previous and re.search(direct, previous) and re.search(r"现在就|马上|已经开始|撑不住", text):
        return True
    return False


def create_alert(db, question: Question):
    event = EmergencyEvent(question_id=question.id)
    db.add(event)
    db.flush()
    body = json.dumps({"event_id": event.id, "time_utc": now().isoformat(),
                       "text": question.text, "self_reported_profile": question.snapshot,
                       "notice": "资料由用户自行填写，未核验身份；家长孩子信息不代表已核实关系。"},
                      ensure_ascii=False, indent=2)
    db.add(MailJob(event_id=event.id, mode=settings().mail_mode, body=body))
    return event


class MailSender(Protocol):
    def send(self, job: MailJob): ...


class MockSender:
    def send(self, job: MailJob):
        if job.attempts <= settings().mock_mail_failures:
            raise ConnectionError("simulated_failure")


class SmtpSender:
    def send(self, job: MailJob):
        cfg = settings()
        message = EmailMessage()
        message["Subject"] = (f"【上周新增问题】{job.week_start}" if job.week_start
                              else f"【紧急事件】{job.event_id}")
        message["From"] = cfg.smtp_from
        message["To"] = cfg.weekly_recipient if job.week_start else cfg.alert_recipient
        message["Message-ID"] = f"<{job.id}@dominate.invalid>"
        message.set_content(job.body)
        if cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=15,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(message)


def smtp_ready(weekly=False):
    cfg = settings()
    recipient_ok = (re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", cfg.weekly_recipient)
                    if weekly else cfg.alert_recipient.endswith("@qq.com"))
    return bool(cfg.allow_real_email and cfg.smtp_host and cfg.smtp_user and cfg.smtp_password
                and cfg.smtp_from and recipient_ok)


def enqueue_weekly_mail(factory=SessionLocal, at=None):
    cfg = settings()
    if not cfg.weekly_mail_enabled:
        return
    stamp = at or now()
    zone = ZoneInfo("Asia/Shanghai")
    local = stamp.replace(tzinfo=timezone.utc).astimezone(zone)
    monday = local.date() - timedelta(days=local.weekday())
    if local < datetime.combine(monday, clock_time(9), zone):
        return
    start = monday - timedelta(days=7)
    begin_utc = datetime.combine(start, clock_time.min, zone).astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = datetime.combine(monday, clock_time.min, zone).astimezone(timezone.utc).replace(tzinfo=None)
    with factory() as db:
        if db.scalar(select(MailJob.id).where(MailJob.week_start == start.isoformat())):
            return
        issues = list(db.scalars(select(Issue).where(
            Issue.created_at >= begin_utc, Issue.created_at < end_utc,
            Issue.question_id.not_in(select(EmergencyEvent.question_id)),
        ).order_by(Issue.created_at, Issue.id)))
        if not issues:
            return
        lines = [f"北京时间 {start} 至 {monday}（不含）新增问题，共 {len(issues)} 条。",
                 "处理状态请以工作台实时记录为准。"]
        lines.extend(f"{item.id} | {item.reason} | {scrub(item.text)}" for item in issues)
        job = MailJob(week_start=start.isoformat(), mode=cfg.mail_mode,
                      body="\n".join(lines), next_attempt=stamp)
        db.add(job)
        try:
            db.flush()
            db.add_all(WeeklyMailItem(issue_id=item.id, job_id=job.id) for item in issues)
            db.commit()
        except IntegrityError:
            db.rollback()
            if not db.scalar(select(MailJob.id).where(MailJob.week_start == start.isoformat())):
                raise


def run_one(factory=SessionLocal) -> bool:
    """Claim through conditional UPDATE; lease recovery survives crashes on SQLite/PostgreSQL."""
    with factory() as db:
        stamp = now()
        # Real SMTP has an ambiguous crash window after DATA. Never blindly resend an expired real lease.
        db.execute(update(MailJob).where(MailJob.mode == "smtp", MailJob.status == "sending",
                                         MailJob.lease_until < stamp).values(status="uncertain", error_code="delivery_uncertain"))
        eligible = or_(and_(MailJob.status.in_(["pending", "retry"]), MailJob.next_attempt <= stamp),
                       and_(MailJob.mode == "mock", MailJob.status == "sending", MailJob.lease_until < stamp))
        job_id = db.scalar(select(MailJob.id).where(eligible).order_by(MailJob.next_attempt).limit(1))
        if not job_id:
            db.commit()
            return False
        claimed = db.execute(update(MailJob).where(MailJob.id == job_id, eligible).values(
            status="sending", lease_until=stamp + timedelta(seconds=60), attempts=MailJob.attempts + 1))
        db.commit()
        if claimed.rowcount != 1:
            return True
        job = db.get(MailJob, job_id)
        if job.mode == "smtp" and not smtp_ready(weekly=bool(job.week_start)):
            job.status, job.error_code = "unconfigured", "smtp_not_authorized_or_configured"
        else:
            try:
                (MockSender() if job.mode == "mock" else SmtpSender()).send(job)
                job.status = "simulated_sent" if job.mode == "mock" else "submitted"
                job.sent_at, job.error_code = now(), ""
            except (ConnectionError, smtplib.SMTPConnectError, smtplib.SMTPAuthenticationError,
                    smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
                job.status = "retry" if job.attempts < settings().mail_max_attempts else "failed"
                job.error_code = "transport_failed"
                job.next_attempt = now() + timedelta(seconds=min(300, 2**job.attempts))
            except (OSError, smtplib.SMTPException):
                job.status = "uncertain" if job.mode == "smtp" else "failed"
                job.error_code = "delivery_uncertain" if job.mode == "smtp" else "transport_failed"
        job.lease_until = None
        db.add(MailAttempt(job_id=job.id, result=job.status))
        db.commit()
        return True


def retention_preview(db, at=None):
    cutoff = (at or now()) - timedelta(days=7)
    jobs = db.scalars(select(MailJob).where(MailJob.event_id.is_not(None),
                                           MailJob.status.in_(["submitted", "simulated_sent"]),
                                           MailJob.sent_at <= cutoff)).all()
    return {"mode": "preview_only", "deletion_enabled": False,
            "candidates": [{"job_id": j.id, "event_id": j.event_id, "channel": j.mode,
                            "proposed_fields": ["mail_jobs.body", "mail_attempts"]} for j in jobs],
            "preserved": ["registrations", "child_entries", "questions", "issues", "events", "audit"],
            "notice": "用户已要求仅预览，系统不会删除任何记录。"}


def worker():
    next_weekly_check = 0
    while True:
        try:
            if time.monotonic() >= next_weekly_check:
                next_weekly_check = time.monotonic() + 15
                enqueue_weekly_mail()
            if not run_one():
                time.sleep(1)
        except Exception:
            # No payloads or secrets in logs. Process supervisor monitors worker liveness.
            print("mail_worker_iteration_failed", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    worker()
