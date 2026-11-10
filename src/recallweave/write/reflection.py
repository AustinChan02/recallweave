"""P1.c reflection (§5 `/v1/reflect` + §4.1 conflict machinery reuse).

Behavior, stated as code:
  * near-duplicate clustering over the newest ACTIVE memories: transitive
    components where pairwise cosine > `settings.reflect_threshold` (0.95);
    clustering is pure Python (O(n²) within the scan cap) — no LLM involved;
  * ZERO clusters → ZERO LLM calls;
  * per cluster ONE call through the SAME conflict pipeline as ingest
    (`resolve_with_llm`: candidate = newest member, neighbors = the older
    members), with the unchanged fail-closed coercion — a hallucinated target
    can never touch the DB;
  * application is ONE transaction for the whole reflection run (same
    durability rule as ingest, §4.1 stage 4): UPDATE merges the cluster into
    one fresh row (bitemporal supersede chain preserved via mark_superseded),
    DELETE supersedes the named target with no successor;
  * every applied decision re-embeds ONLY the merged content and records the
    audit under `meta.decision` (`reflect: true`, full cluster member list);
  * honest failure surface: provider errors produce `skipped` entries with
    reasons — never fabricated merges, never a silently shortened memory set.

This is a MAINTENANCE job over stored memories: it does not extract anything
and it never deletes rows physically (status transitions only).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from ..adapters.embeddings import EmbeddingAdapter
from ..adapters.llm import LLMAdapter
from ..core.errors import ProviderError
from ..core.logging import get_logger
from ..db.models import Memory
from ..db.repos import MemoryRepo, aware
from ..db.vector_index import VectorIndex, cosine
from .conflict import Neighbor, resolve_with_llm
from .extraction import Extracted
from .pipeline import DecisionRecord, SkippedRecord, _provider_label, _record

log = get_logger(component="write.reflection")


class ReflectionReport(BaseModel):
    user_id: str
    provider: str
    threshold: float
    scanned: int
    clusters_found: int
    decisions: list[DecisionRecord] = Field(default_factory=list)
    skipped: list[SkippedRecord] = Field(default_factory=list)


def _clusters(rows: list[Memory], embeddings: list[list[float]], threshold: float) -> list[list[int]]:
    """Transitive union-find over pairs sim > threshold (strict: sim == threshold
    pairs are NOT merged — clustering errs on precision). Deterministic: rows
    are ordered chronologically, pairs iterated (i<j) in that order."""
    parent = list(range(len(rows)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            sim = cosine(embeddings[i], embeddings[j])
            if sim > threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)

    buckets: dict[int, list[int]] = {}
    for idx in range(len(rows)):
        buckets.setdefault(find(idx), []).append(idx)
    multi = [members for members in buckets.values() if len(members) > 1]
    multi.sort(key=lambda m: m[0])  # discovery order = stable
    return multi


async def reflect(
    *,
    user_id: uuid.UUID,
    llm: LLMAdapter,
    embedder: EmbeddingAdapter,
    repo: MemoryRepo,
    vector: VectorIndex,
    settings,
) -> ReflectionReport:
    provider = _provider_label(llm)
    threshold = settings.reflect_threshold
    report = ReflectionReport(
        user_id=str(user_id), provider=provider, threshold=threshold, scanned=0, clusters_found=0
    )

    rows = await repo.active_candidates(user_id, settings.reflect_max_scan)
    rows.sort(key=lambda m: (aware(m.created_at), m.id))  # chronological ▸ stable ids
    report.scanned = len(rows)
    if len(rows) < 2:
        return report

    embeddings = await embedder.embed_many([m.content for m in rows])
    clusters = _clusters(rows, embeddings, threshold)
    report.clusters_found = len(clusters)
    if not clusters:
        return report

    # ---- decide EVERY cluster BEFORE opening the write transaction -----------
    # (fail-closed: a mid-run provider failure never reaches row state)
    resolutions: list[tuple[list[int], object]] = []
    for members in clusters:
        newest_idx = members[-1]  # rows are chronological; candidate = newest
        newest = rows[newest_idx]
        neighbours = sorted(
            (
                Neighbor(memory=rows[j], similarity=cosine(embeddings[newest_idx], embeddings[j]))
                for j in members[:-1]
            ),
            key=lambda n: n.similarity,
            reverse=True,
        )
        candidate = Extracted(
            kind=newest.kind,
            content=newest.content,
            importance=newest.importance,
            span=(newest.meta or {}).get("span"),
        )
        try:
            decision = await resolve_with_llm(llm, candidate, neighbours)
        except ProviderError as exc:
            report.skipped.append(
                SkippedRecord(
                    content=newest.content[:140],
                    reason=f"provider_error_in_resolution:{exc}"[:140],
                    similarity=neighbours[0].similarity if neighbours else None,
                )
            )
            continue
        resolutions.append((members, decision))
        if decision.op == "NOOP":
            report.skipped.append(
                SkippedRecord(
                    content=newest.content[:140],
                    reason=f"noop:{decision.rationale[:100]}",
                    similarity=neighbours[0].similarity if neighbours else None,
                )
            )

    # ---- apply: ONE transaction for the whole run ---------------------------
    if not resolutions:
        return report

    async with repo.new_session() as session:
        async with session.begin():
            for members, decision in resolutions:
                cluster_rows = [rows[i] for i in members]
                newest = cluster_rows[-1]
                pair_sim = (
                    cosine(
                        embeddings[members[0]],
                        embeddings[members[1]],
                    )
                    if len(members) >= 2
                    else None
                )
                if decision.op == "UPDATE":
                    target = await session.get(Memory, decision.target_memory_id)
                    if target is None:  # coercion should have prevented this
                        report.skipped.append(
                            SkippedRecord(content=newest.content[:140], reason="coercion_race")
                        )
                        continue
                    if target.id not in [m.id for m in cluster_rows]:
                        # a decision re-read race; honest skip, no partial state
                        report.skipped.append(
                            SkippedRecord(content=newest.content[:140], reason="target_not_in_cluster")
                        )
                        continue
                    merged = decision.content or newest.content
                    memory = Memory(
                        user_id=user_id,
                        session_id=target.session_id,
                        kind=target.kind,
                        content=merged,
                        importance=max(m.importance for m in cluster_rows),
                        meta={
                            "replaces": str(target.id),
                            "decision": {
                                "op": "UPDATE",
                                "similarity": pair_sim,
                                "confidence": decision.confidence,
                                "rationale": decision.rationale,
                                "provider": provider,
                                "reflect": True,
                                "cluster": [str(m.id) for m in cluster_rows],
                            },
                        },
                    )
                    merged_emb = (await embedder.embed_many([merged]))[0]
                    await MemoryRepo.insert_with_embedding(session, memory, merged_emb, vector)
                    await MemoryRepo.mark_superseded(
                        session, target, new_id=memory.id, reason=decision.rationale
                    )
                    # EVERY remaining member leaves the active set (the candidate
                    # itself included). Rows came from a DIFFERENT (closed) session
                    # — detached copies; re-attach INSIDE this tx or the flush
                    # sees nothing (the same detached-instance bug pipeline.py
                    # documents for its neighbors).
                    for m in cluster_rows:
                        if m.id == target.id:
                            continue  # superseded above via the attached object
                        member = await session.get(Memory, m.id)
                        if member is not None:
                            await MemoryRepo.mark_superseded(
                                session, member, new_id=memory.id, reason="reflected cluster merge"
                            )
                    report.decisions.append(_record(decision, merged, pair_sim, memory.id))
                elif decision.op == "DELETE":
                    target = await session.get(Memory, decision.target_memory_id)
                    if target is None or target.id not in [m.id for m in cluster_rows]:
                        report.skipped.append(
                            SkippedRecord(content=newest.content[:140], reason="coercion_race")
                        )
                        continue
                    await MemoryRepo.mark_superseded(session, target, new_id=None, reason=decision.rationale)
                    report.decisions.append(_record(decision, newest.content, pair_sim))
                    log.debug(
                        "reflect.deleted",
                        target=str(target.id),
                        user=str(user_id),
                        provider=provider,
                    )

    return report
