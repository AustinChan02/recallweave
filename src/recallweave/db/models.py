"""ORM models (dialect-agnostic). pgvector lives OUTSIDE these models in a
dialect-specific table — see vector_index.py + migrations/001_init_vector.sql.

Bi-temporal-lite (ADR-004): UPDATE/DELETE never overwrite; they close the old
row's validity window and point `superseded_by` at the successor when one exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # fact|preference|event|instruction
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # active | superseded | archived | deleted
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # rationale/span/confidence land here; keys are additive, never reinterpreted.
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_accessed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_mem_user_status", "user_id", "status"),)


class MemSource(Base):
    __tablename__ = "mem_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    message_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "msg:12"
    span: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SessionTurnStat(Base):
    """Per-(user, session) raw-turn counter (P1.a §4.4 trigger bookkeeping).

    Turns are counted on ingest; summaries derive from STORED MEMORIES (the
    bitemporal representation), raw transcripts are never persisted for this —
    amendment A8 in the proposal §14 ledger.
    """

    __tablename__ = "session_turn_stats"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SessionSummary(Base):
    """Append-only rolling session summary (P1.a §4.4). One row per trigger.

    (session_id, seq) is the natural stable order the compiler relies on — no
    rescore, no reordering: an append of a NEW seq never changes old bytes.
    """

    __tablename__ = "session_summaries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    turns_covered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_est: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ssum_session_seq", "session_id", "seq", unique=True),
        Index("ix_ssum_user_created", "user_id", "created_at"),
    )
