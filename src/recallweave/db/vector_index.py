"""Vector index — two backends behind one small surface.

Write path atomicity lives in MemoryRepo (same session/tx as the memory row);
this module only reads (search) and offers a standalone upsert for tools/tests.

Two format rules that keep raw SQL safe across dialects (the classic trap
here: SQLAlchemy's Uuid stores uuid.hex WITHOUT dashes on SQLite, so a raw
`JOIN memories.id = <our string with dashes>` can never match):
  1. `memory_embeddings` rows are keyed by str(uuid) WITH dashes — OUR format,
     written only through upsert().
  2. reads of memories.id/user_id go through ORM-typed selects (bind/result
     processing stays consistent), and the embedding side is fetched with the
     dash-format ids we wrote. No cross-format string join anywhere.

sqlite ceiling (ADR-001): brute-force cosine for the dev/demo path; the
Candidate fetch is bounded by user isolation + status filters. Production
path is Postgres HNSW with runtime `SET LOCAL hnsw.ef_search` (proposal §4.2).
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from .models import Memory

_PG_SEARCH = text(
    """
    SELECT m.id::text AS mid, 1 - (e.embedding <=> (:qv)::vector) AS sim
    FROM memory_embeddings e
    JOIN memories m ON m.id = e.memory_id
    WHERE m.user_id = :uid AND m.status = 'active'
      AND (m.valid_to IS NULL OR m.valid_to > now())
    ORDER BY e.embedding <=> (:qv)::vector
    LIMIT :k
    """
)

_SQLITE_FETCH_EMB = text(
    "SELECT memory_id, embedding_json FROM memory_embeddings WHERE memory_id IN :ids"
).bindparams(bindparam("ids", expanding=True))


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


class VectorIndex:
    def __init__(self, engine: AsyncEngine, dim: int, *, hnsw_ef_search: int = 40) -> None:
        self.engine = engine
        self.dim = dim
        self.hnsw_ef_search = hnsw_ef_search
        self.dialect = engine.dialect.name

    async def upsert(self, session, memory_id: uuid.UUID, embedding: list[float]) -> None:
        if self.dialect == "postgresql":
            await session.execute(
                text(
                    "INSERT INTO memory_embeddings(memory_id, embedding) "
                    "VALUES (:mid, (:ev)::vector) "
                    "ON CONFLICT (memory_id) DO UPDATE SET embedding = EXCLUDED.embedding"
                ),
                {"mid": memory_id, "ev": _vector_literal(embedding)},
            )
        else:
            await session.execute(
                text(
                    "INSERT OR REPLACE INTO memory_embeddings(memory_id, embedding_json) VALUES (:mid, :ev)"
                ),
                {"mid": str(memory_id), "ev": json.dumps(embedding)},
            )

    async def search(
        self, *, user_id: uuid.UUID, query_embedding: list[float], k: int
    ) -> list[tuple[uuid.UUID, float]]:
        """Candidates restricted to: same user + active + validity window open."""
        if self.dialect == "postgresql":
            async with self.engine.begin() as conn:
                # Transaction-scoped ef_search (proposal §4.2, "ef_search 可配"):
                # asyncpg refuses bind params in SET statements ("syntax error at
                # or near $1"), so use set_config(..., is_local) — identical
                # tx-scoped semantics, no state leaks across pooled queries.
                await conn.execute(
                    text("SELECT set_config('hnsw.ef_search', :ef, true)"),
                    {"ef": str(self.hnsw_ef_search)},
                )
                rows = (
                    await conn.execute(
                        _PG_SEARCH,
                        {"uid": user_id, "qv": _vector_literal(query_embedding), "k": k},
                    )
                ).all()
            return [(uuid.UUID(r.mid), float(r.sim)) for r in rows]

        # ---- sqlite path: ORM-typed candidate fetch + dash-keyed embed fetch ----
        async with self.engine.connect() as conn:
            cand_rows = (
                await conn.execute(
                    select(Memory.id, Memory.valid_to).where(
                        Memory.user_id == user_id, Memory.status == "active"
                    )
                )
            ).all()
            ids = [(r.id if isinstance(r.id, uuid.UUID) else uuid.UUID(str(r.id))) for r in cand_rows]
            if not ids:
                return []
            emb_rows = (await conn.execute(_SQLITE_FETCH_EMB, {"ids": [str(mid) for mid in ids]})).all()
        emb_by_mid = {r.memory_id: r.embedding_json for r in emb_rows}

        now = datetime.now(UTC)
        scored: list[tuple[uuid.UUID, float]] = []
        for r in cand_rows:
            mid = r.id if isinstance(r.id, uuid.UUID) else uuid.UUID(str(r.id))
            raw = emb_by_mid.get(str(mid))
            if raw is None:  # embedding row missing -> not searchable; never crash a read
                continue
            if r.valid_to is not None:
                vt = r.valid_to
                if vt.tzinfo is None:
                    vt = vt.replace(tzinfo=UTC)
                if vt <= now:
                    continue
            sim = _cosine(query_embedding, json.loads(raw))
            scored.append((mid, sim))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    num = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        num += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return num / (math.sqrt(na) * math.sqrt(nb))
