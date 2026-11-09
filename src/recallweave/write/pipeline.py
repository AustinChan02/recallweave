"""Write pipeline orchestrator: extract -> dedup gate -> resolve -> apply.

Durability rule (proposal §4.1 stage 4): every applied ops set commits as ONE
transaction — a mid-batch DB failure rolls the whole ingest back (no half
memories). LLM/provider failures skip that single candidate and are reported
(fail-closed): they never abort the batch, and they never write half-state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from ..adapters.embeddings import EmbeddingAdapter
from ..adapters.llm import LLMAdapter
from ..core.errors import ProviderError
from ..core.logging import get_logger
from ..db.models import Memory
from ..db.repos import MemoryRepo
from ..db.vector_index import VectorIndex
from .conflict import Decision, Neighbor, resolve_with_llm, route_candidate
from .extraction import Extracted, extract_memories

log = get_logger(component="write.pipeline")


class DecisionRecord(BaseModel):
    op: str
    memory_id: str | None = None
    target_memory_id: str | None = None
    content: str
    similarity: float | None = None
    confidence: float
    rationale: str


class SkippedRecord(BaseModel):
    content: str = Field(max_length=140)
    reason: str
    similarity: float | None = None


class IngestReport(BaseModel):
    user_id: str
    session_id: str | None
    provider: str
    extracted: int
    decisions: list[DecisionRecord] = Field(default_factory=list)
    skipped: list[SkippedRecord] = Field(default_factory=list)
    extraction_error: str | None = None


def _provider_label(llm: LLMAdapter) -> str:
    return getattr(llm, "provider_label", type(llm).__name__)


def _add_decision(cand: Extracted) -> Decision:
    return Decision(
        op="ADD",
        confidence=1.0,
        rationale="no neighbor inside conflict window",
        content=cand.content,
    )


def _message_ref(span: dict | None) -> str | None:
    if not span:
        return None
    idx = span.get("message_index")
    return f"msg:{idx}" if idx is not None else None


def _record(
    decision: Decision,
    fallback_content: str,
    max_sim: float | None,
    memory_id: uuid.UUID | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        op=decision.op,
        memory_id=str(memory_id) if memory_id else None,
        target_memory_id=str(decision.target_memory_id) if decision.target_memory_id else None,
        content=decision.content or fallback_content,
        similarity=max_sim,
        confidence=decision.confidence,
        rationale=decision.rationale or "",
    )


async def ingest(
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    messages: list[dict],
    llm: LLMAdapter,
    embedder: EmbeddingAdapter,
    repo: MemoryRepo,
    vector: VectorIndex,
    settings,
) -> IngestReport:
    provider = _provider_label(llm)

    # 1) extract (recent k turns only) — provider failure fails closed, zero writes
    try:
        candidates: list[Extracted] = await extract_memories(
            llm, list(messages), settings.extract_recent_turns
        )
    except ProviderError as exc:
        log.warning("ingest.extract_failed", user=str(user_id), error=str(exc))
        return IngestReport(
            user_id=str(user_id),
            session_id=str(session_id) if session_id else None,
            provider=provider,
            extracted=0,
            extraction_error=str(exc),
        )
    log.debug(
        "ingest.extracted",
        user=str(user_id),
        n=len(candidates),
        provider=provider,
        at=datetime.now(UTC).isoformat(),
    )

    # in-batch exact duplicates collapse (same normalized content)
    seen: set[str] = set()
    batch: list[Extracted] = []
    report = IngestReport(
        user_id=str(user_id),
        session_id=str(session_id) if session_id else None,
        provider=provider,
        extracted=len(candidates),
    )
    for cand in candidates:
        key = " ".join(cand.content.lower().split())
        if key in seen:
            report.skipped.append(SkippedRecord(content=cand.content[:140], reason="in_batch_duplicate"))
            continue
        seen.add(key)
        batch.append(cand)

    # 2) embeddings in ONE batch call (embedding store has its own memo cache)
    embeddings = await embedder.embed_many([c.content for c in batch]) if batch else []

    async with repo.new_session() as session:
        async with session.begin():
            for cand, emb in zip(batch, embeddings, strict=True):
                sims = await vector.search(user_id=user_id, query_embedding=emb, k=settings.neighbor_top_n)
                neighbor_rows = await repo.by_ids(user_id, [mid for mid, _ in sims])
                neighbors = [
                    Neighbor(memory=neighbor_rows[mid], similarity=sim)
                    for mid, sim in sims
                    if mid in neighbor_rows
                ]
                neighbors.sort(key=lambda n: n.similarity, reverse=True)
                max_sim: float | None = neighbors[0].similarity if neighbors else None

                route = route_candidate(
                    max_sim, conflict_lo=settings.conflict_lo, dedup_near=settings.dedup_near
                )
                if route == "near_dup":
                    report.skipped.append(
                        SkippedRecord(
                            content=cand.content[:140],
                            reason="near_dup_skip",
                            similarity=max_sim,
                        )
                    )
                    log.debug("ingest.near_dup", user=str(user_id), sim=max_sim)
                    continue

                if route == "add":
                    decision = _add_decision(cand)
                else:
                    try:
                        decision = await resolve_with_llm(llm, cand, neighbors)
                    except ProviderError as exc:
                        log.warning("ingest.resolve_failed", error=str(exc))
                        report.skipped.append(
                            SkippedRecord(
                                content=cand.content[:140],
                                reason="provider_error_in_resolution",
                                similarity=max_sim,
                            )
                        )
                        continue

                if decision.op == "ADD":
                    memory = Memory(
                        user_id=user_id,
                        session_id=session_id,
                        kind=cand.kind,
                        content=cand.content,
                        importance=cand.importance,
                        meta={
                            "span": cand.span,
                            "decision": {
                                "op": "ADD",
                                "similarity": max_sim,
                                "confidence": decision.confidence,
                                "rationale": decision.rationale,
                                "provider": provider,
                            },
                        },
                    )
                    await MemoryRepo.insert_with_embedding(
                        session,
                        memory,
                        emb,
                        vector,
                        sources=[{"message_ref": _message_ref(cand.span), "span": cand.span}],
                    )
                    report.decisions.append(_record(decision, cand.content, max_sim, memory.id))

                elif decision.op in ("UPDATE", "DELETE"):
                    # Re-attach the target INSIDE this write session: neighbors were
                    # fetched through a different session (detached copies); mutating
                    # them would never reach the DB — the flush would see nothing.
                    target = await session.get(Memory, decision.target_memory_id)
                    if target is None:  # coercion should have prevented this
                        report.skipped.append(
                            SkippedRecord(content=cand.content[:140], reason="coercion_race")
                        )
                        continue
                    if decision.op == "UPDATE":
                        merged = decision.content or cand.content
                        memory = Memory(
                            user_id=user_id,
                            session_id=session_id,
                            kind=target.kind,
                            content=merged,
                            importance=cand.importance,
                            meta={
                                "span": cand.span,
                                "replaces": str(target.id),
                                "decision": {
                                    "op": "UPDATE",
                                    "similarity": max_sim,
                                    "confidence": decision.confidence,
                                    "rationale": decision.rationale,
                                    "provider": provider,
                                },
                            },
                        )
                        merged_emb = (await embedder.embed_many([merged]))[0]
                        await MemoryRepo.insert_with_embedding(
                            session,
                            memory,
                            merged_emb,
                            vector,
                            sources=[{"message_ref": _message_ref(cand.span), "span": cand.span}],
                        )
                        await MemoryRepo.mark_superseded(
                            session, target, new_id=memory.id, reason=decision.rationale
                        )
                        report.decisions.append(_record(decision, cand.content, max_sim, memory.id))
                    else:  # DELETE
                        await MemoryRepo.mark_superseded(
                            session, target, new_id=None, reason=decision.rationale
                        )
                        report.decisions.append(_record(decision, cand.content, max_sim))
                        log.debug("ingest.deleted", target=str(target.id), user=str(user_id))

                else:  # NOOP from resolver
                    report.skipped.append(
                        SkippedRecord(
                            content=cand.content[:140],
                            reason=f"noop:{decision.rationale[:60]}",
                            similarity=max_sim,
                        )
                    )

    return report
