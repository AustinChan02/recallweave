"""MemoryRepo — the only ORM access layer.

Transaction rule: write-path operations (insert with embedding + supersedes)
run inside ONE session/tx owned by the caller (pipeline); read path opens its
own short sessions. Expiry is disabled (expire_on_commit=False) so post-commit
attribute access survives — the classic rollback-expiry bug is pre-empted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .models import Memory, MemSource
from .vector_index import VectorIndex

_KINDS = ("fact", "preference", "event", "instruction")
_STATUSES = ("active", "superseded", "archived", "deleted")


def aware(dt: datetime) -> datetime:
    """sqlite returns naive UTC-ish datetimes; pg returns aware. Normalize once."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def memory_to_public(m: Memory) -> dict:
    return {
        "id": str(m.id),
        "user_id": str(m.user_id),
        "session_id": str(m.session_id) if m.session_id else None,
        "kind": m.kind,
        "content": m.content,
        "importance": m.importance,
        "status": m.status,
        "valid_from": m.valid_from.isoformat() if m.valid_from else None,
        "valid_to": m.valid_to.isoformat() if m.valid_to else None,
        "superseded_by": str(m.superseded_by) if m.superseded_by else None,
        "meta": m.meta or {},
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "last_accessed": m.last_accessed.isoformat() if m.last_accessed else None,
    }


class MemoryRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._maker = async_sessionmaker(engine, expire_on_commit=False)

    def new_session(self) -> AsyncSession:
        return self._maker()

    # ---------------- write path (caller-owned session/tx) ----------------

    @staticmethod
    async def insert_with_embedding(
        session: AsyncSession,
        memory: Memory,
        embedding: list[float],
        vector: VectorIndex,
        sources: list[dict] | None = None,
    ) -> None:
        session.add(memory)
        await session.flush()  # id needed for the embedding row
        await vector.upsert(session, memory.id, embedding)
        for src in sources or []:
            session.add(
                MemSource(
                    memory_id=memory.id,
                    message_ref=src.get("message_ref"),
                    span=src.get("span"),
                )
            )

    @staticmethod
    async def mark_superseded(
        session: AsyncSession, memory: Memory, new_id: uuid.UUID | None, reason: str
    ) -> None:
        memory.status = "superseded"
        memory.valid_to = datetime.now(UTC)
        if new_id is not None:
            memory.superseded_by = new_id
        meta = dict(memory.meta or {})
        meta.setdefault("close_reason", []).append({"reason": reason, "at": memory.valid_to.isoformat()})
        memory.meta = meta

    @staticmethod
    async def soft_delete(session: AsyncSession, memory: Memory, reason: str = "user_deleted") -> None:
        memory.status = "deleted"
        memory.valid_to = datetime.now(UTC)
        meta = dict(memory.meta or {})
        meta["delete_reason"] = reason
        memory.meta = meta

    # ---------------- read path (short-lived own sessions) ----------------

    async def get_any_status(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> Memory | None:
        async with self._maker() as session:
            res = await session.execute(
                select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
            )
            return res.scalar_one_or_none()

    async def by_ids(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, Memory]:
        if not ids:
            return {}
        async with self._maker() as session:
            res = await session.execute(
                select(Memory).where(Memory.user_id == user_id, Memory.id.in_(ids))
            )
            return {m.id: m for m in res.scalars().all()}

    async def active_candidates(
        self, user_id: uuid.UUID, limit: int, kinds: list[str] | None = None
    ) -> list[Memory]:
        now = datetime.now(UTC)
        stmt = (
            select(Memory)
            .where(Memory.user_id == user_id, Memory.status == "active")
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        async with self._maker() as session:
            rows = (await session.execute(stmt)).scalars().all()
        out = []
        for m in rows:
            if m.valid_to is not None and aware(m.valid_to) <= now:
                continue
            if kinds is not None and m.kind not in kinds:
                continue
            out.append(m)
        return out

    async def touch_last_accessed(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
        if not ids:
            return
        now = datetime.now(UTC)
        async with self._maker() as session:
            async with session.begin():
                for mid in ids:  # bounded (<= top_k); ORM update keeps binds type-safe
                    await session.execute(
                        update(Memory)
                        .where(Memory.user_id == user_id, Memory.id == mid)
                        .values(last_accessed=now)
                    )

    async def counts(self, user_id: uuid.UUID) -> dict[str, int]:
        async with self._maker() as session:
            res = await session.execute(
                select(Memory.status, Memory.kind, func.count())
                .where(Memory.user_id == user_id)
                .group_by(Memory.status, Memory.kind)
            )
        out: dict[str, int] = {}
        for status, kind, cnt in res.all():
            out[f"{status}:{kind}"] = int(cnt)
            out[status] = out.get(status, 0) + int(cnt)
        return out
