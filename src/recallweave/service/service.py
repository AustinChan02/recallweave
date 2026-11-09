"""RecallWeaveService — the one composition point that both the API and the SDK
rely on. Domain rules live here (not in transport).

Honesty: every result carries `provider` labels so mock-mode outputs can never
masquerade as measured provider numbers.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncEngine

from ..adapters.embeddings import EmbeddingAdapter, make_embedder
from ..adapters.llm import LLMAdapter, make_llm_adapter
from ..core.config import Settings
from ..core.errors import NotFoundError
from ..core.logging import get_logger
from ..db.models import Memory
from ..db.repos import MemoryRepo, memory_to_public
from ..db.session import Database, ensure_schema
from ..db.vector_index import VectorIndex
from ..read.compiler import CompileOutput, compile_context
from ..read.fusion import mmr_select, rrf_fuse, weighted_rank
from ..read.retrievers import lexical_lane, recency_lane
from ..tokens import counter
from ..write.pipeline import IngestReport, ingest

log = get_logger(component="service")


def build_engine(settings: Settings) -> AsyncEngine:
    from ..db.session import make_engine

    return make_engine(settings.db_url)


class RecallWeaveService:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        *,
        llm: LLMAdapter | None = None,
        embedder: EmbeddingAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.db = Database(engine)
        self.llm = llm if llm is not None else make_llm_adapter(settings)
        self.embedder = embedder if embedder is not None else make_embedder(settings)
        self.vector = VectorIndex(
            engine, settings.embedding_dim, hnsw_ef_search=settings.hnsw_ef_search
        )
        self.repo = MemoryRepo(engine)

    @property
    def dialect(self) -> str:
        return self.db.engine.dialect.name

    async def bootstrap(self) -> None:
        await ensure_schema(self.db.engine)

    async def aclose(self) -> None:
        await self.db.engine.dispose()

    # ------------------------------- write -----------------------------------

    async def ingest(
        self, user_id: uuid.UUID, session_id: uuid.UUID | None, messages: list[dict]
    ) -> IngestReport:
        return await ingest(
            user_id=user_id,
            session_id=session_id,
            messages=messages,
            llm=self.llm,
            embedder=self.embedder,
            repo=self.repo,
            vector=self.vector,
            settings=self.settings,
        )

    # ------------------------------- read ------------------------------------

    async def search(self, user_id: uuid.UUID, query: str, top_k: int = 5) -> dict:
        """Hybrid retrieval. Response carries `rrf_detail` (proposal §5): the
        three召回 lanes with their per-lane ids, plus each item's lane membership."""
        settings = self.settings
        rows = await self.repo.active_candidates(user_id, settings.lexical_scan_limit)
        rows_by_id: dict[str, Memory] = {str(m.id): m for m in rows}

        q_emb = (await self.embedder.embed_many([query]))[0]
        vec_ids = [
            str(mid)
            for mid, _ in await self.vector.search(
                user_id=user_id, query_embedding=q_emb, k=settings.vector_top_m
            )
        ]
        lex_ids = lexical_lane(query, rows, settings.lexical_top_m)
        rec_ids = recency_lane(rows, settings.recency_top_m)
        fused = weighted_rank(
            rrf_fuse([vec_ids, lex_ids, rec_ids], settings.rrf_k),
            rows_by_id,
            kind_weight_fn=settings.kind_weight,
            tau_days=settings.decay_tau_days,
            importance_lo=settings.importance_lo,
            importance_span=settings.importance_span,
        )[: max(top_k * 3, top_k)]
        if not fused:
            return {
                "items": [],
                "rrf_detail": {"lanes": {"vector": [], "lexical": [], "recency": []}},
                "provider": "local-hybrid",
            }

        item_embs = await self.embedder.embed_many([rows_by_id[mid].content for mid, _ in fused])
        emb_map = {mid: emb for (mid, _), emb in zip(fused, item_embs, strict=True)}
        picked = mmr_select(fused, emb_map, k=top_k, lam=settings.mmr_lambda)
        scores = dict(fused)
        lane_members = {"vector": set(vec_ids), "lexical": set(lex_ids), "recency": set(rec_ids)}
        out = [
            {
                "memory": memory_to_public(rows_by_id[mid]),
                "score": round(scores.get(mid, 0.0), 6),
                "lanes": [lane for lane in ("vector", "lexical", "recency") if mid in lane_members[lane]],
            }
            for mid in picked
        ]
        await self._touch(user_id, [uuid.UUID(mid) for mid in picked])
        return {
            "items": out,
            "rrf_detail": {
                "rrf_k": settings.rrf_k,
                "lanes": {"vector": vec_ids, "lexical": lex_ids, "recency": rec_ids},
            },
            "provider": "local-hybrid",
        }

    async def compile(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None,
        messages: list[dict],
        budget: int | None = None,
    ) -> CompileOutput:
        out = await compile_context(
            settings=self.settings,
            tok=counter(),
            repo=self.repo,
            vector=self.vector,
            embedder=self.embedder,
            user_id=user_id,
            session_id=session_id,
            messages=messages,
            budget=budget,
        )
        touched = [t.memory_id for t in out.trace if t.included]
        await self._touch(user_id, [uuid.UUID(x) for x in touched])
        return out

    async def _touch(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
        """last_accessed stamp: read-path best effort (a failed stamp never breaks a read)."""
        if not ids:
            return
        try:
            await self.repo.touch_last_accessed(user_id, ids)
        except Exception as exc:  # noqa: BLE001
            log.warning("touch_failed", user=str(user_id), error=repr(exc))

    # ------------------------------- admin -----------------------------------

    async def get_memory(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> dict:
        m = await self.repo.get_any_status(user_id, memory_id)
        if m is None:
            raise NotFoundError(f"memory {memory_id} not found")
        return memory_to_public(m)

    async def delete_memory(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> dict:
        m = await self.repo.get_any_status(user_id, memory_id)
        if m is None:
            raise NotFoundError(f"memory {memory_id} not found")
        async with self.repo.new_session() as session:
            async with session.begin():
                # re-attach INSIDE this write session (detached mutations don't flush)
                attached = await session.get(Memory, memory_id)
                if attached is None or attached.user_id != user_id:  # defensive re-check
                    raise NotFoundError(f"memory {memory_id} not found")
                await self.repo.soft_delete(session, attached)
        return {"deleted": True, "id": str(memory_id)}

    async def stats(self, user_id: uuid.UUID) -> dict:
        return {"counts_by_status_kind": await self.repo.counts(user_id)}


def build_service(settings: Settings) -> RecallWeaveService:
    engine = build_engine(settings)
    return RecallWeaveService(settings, engine)
