"""Additive vocabulary storage; existing identities and evidence remain untouched."""
from sqlalchemy import JSON, Integer, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, uid


class VocabularyEntry(Base):
    __tablename__ = 'vocabulary_entries'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(20), default='draft')
    draft: Mapped[dict] = mapped_column(JSON)
    published: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    versions: Mapped[list] = mapped_column(JSON, default=list)
    history: Mapped[list] = mapped_column(JSON, default=list)


class VocabularyEpoch(Base):
    __tablename__ = 'vocabulary_epoch'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class VocabularyMember(Base):
    __tablename__ = 'vocabulary_members'
    registration_id: Mapped[str] = mapped_column(ForeignKey('registrations.id'), primary_key=True)
