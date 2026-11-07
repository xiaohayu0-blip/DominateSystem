from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, now, uid


class Registration(Base):
    __tablename__ = "registrations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    role: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(11), index=True)
    major: Mapped[str] = mapped_column(String(100), default="")
    class_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserAccount(Base):
    __tablename__ = "user_accounts"
    registration_id: Mapped[str] = mapped_column(ForeignKey("registrations.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class ChildEntry(Base):
    __tablename__ = "child_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    parent_id: Mapped[str] = mapped_column(ForeignKey("registrations.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(11))
    major: Mapped[str] = mapped_column(String(100))
    class_name: Mapped[str] = mapped_column(String(100))
    relationship: Mapped[str] = mapped_column(String(80))


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    registration_id: Mapped[str | None] = mapped_column(ForeignKey("registrations.id"))
    admin_id: Mapped[str | None] = mapped_column(ForeignKey("admins.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200))
    publisher: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(100), default="1")
    business_year: Mapped[int | None] = mapped_column(Integer)
    admission_year: Mapped[int | None] = mapped_column(Integer)
    effective_from: Mapped[str | None] = mapped_column(String(10))
    effective_to: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    parse_status: Mapped[str] = mapped_column(String(20), default="ready")
    parse_note: Mapped[str] = mapped_column(Text, default="")
    storage_key: Mapped[str | None] = mapped_column(String(100))
    original_name: Mapped[str] = mapped_column(String(255), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    confirmed_by: Mapped[str] = mapped_column(String(100), default="")
    confirmed_on: Mapped[str | None] = mapped_column(String(10))
    basis: Mapped[str] = mapped_column(Text, default="")
    material_mode: Mapped[str] = mapped_column(String(20), default="synthetic")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    location: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    vector: Mapped[list] = mapped_column(JSON, default=list)
    bge_vector: Mapped[list | None] = mapped_column(JSON(none_as_null=True))


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("registration_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    registration_id: Mapped[str] = mapped_column(ForeignKey("registrations.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    answer: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    model_mode: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Issue(Base):
    __tablename__ = "issues"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(30), default="open")
    owner: Mapped[str] = mapped_column(String(100), default="")
    merged_into: Mapped[str | None] = mapped_column(ForeignKey("issues.id"), index=True)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    retests: Mapped[list] = mapped_column(JSON, default=list)
    history: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class EmergencyEvent(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="open")
    detector: Mapped[str] = mapped_column(String(80), default="rules-v1")
    history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class MailJob(Base):
    __tablename__ = "mail_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), unique=True)
    week_start: Mapped[str | None] = mapped_column(String(10), unique=True)
    mode: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    body: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime, default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str] = mapped_column(String(60), default="")


class WeeklyMailItem(Base):
    __tablename__ = "weekly_mail_items"
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("mail_jobs.id"), index=True)


class MailAttempt(Base):
    __tablename__ = "mail_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("mail_jobs.id"), index=True)
    result: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    admin_id: Mapped[str | None] = mapped_column(ForeignKey("admins.id"))
    action: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
