"""Fusion + selection math (pure functions, side-effect free — table-driven tests).

final = RRF(lanes) * kind_weight * recency * importance_factor
    recency           = exp(-age_days / tau)  (age = now - coalesce(last_accessed, created_at))
    importance_factor = importance_lo + importance * importance_span

MMR (used by the /v1/memories:search endpoint): λ*relevance - (1-λ)*max_cos_sim(item, selected).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from ..db.models import Memory


def rrf_fuse(lanes: list[list[str]], k: int) -> dict[str, float]:
    """`lanes` are ids ordered best-first; score = Σ 1/(k + rank) over lanes."""
    out: dict[str, float] = {}
    for lane in lanes:
        for rank, mid in enumerate(lane, start=1):
            out[mid] = out.get(mid, 0.0) + 1.0 / (k + rank)
    return out


def age_days(m: Memory, now: datetime) -> float:
    anchor = m.last_accessed or m.created_at
    if anchor is None:  # unflushed instance in tests
        return 0.0
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    return max(0.0, (now - anchor).total_seconds() / 86400.0)


def recency_factor(age_d: float, tau_days: float) -> float:
    return math.exp(-age_d / tau_days)


def final_score(
    rrf_score: float,
    *,
    kind_weight: float,
    recency: float,
    importance: float,
    importance_lo: float,
    importance_span: float,
) -> float:
    return rrf_score * kind_weight * recency * (importance_lo + importance_span * importance)


def weighted_rank(
    rrf_scores: dict[str, float],
    rows: dict[str, Memory],
    *,
    kind_weight_fn,
    tau_days: float,
    importance_lo: float,
    importance_span: float,
) -> list[tuple[str, float]]:
    now = datetime.now(UTC)
    scored: list[tuple[str, float]] = []
    for mid, s in rrf_scores.items():
        m = rows.get(mid)
        if m is None:
            continue
        scored.append(
            (
                mid,
                final_score(
                    s,
                    kind_weight=kind_weight_fn(m.kind),
                    recency=recency_factor(age_days(m, now), tau_days),
                    importance=m.importance,
                    importance_lo=importance_lo,
                    importance_span=importance_span,
                ),
            )
        )
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # both L2-normalized → cos


def mmr_select(
    ranked: list[tuple[str, float]],
    embeddings: dict[str, list[float]],
    *,
    k: int,
    lam: float,
) -> list[str]:
    """Greedy MMR over the ranked list. Missing embeddings -> similarity 0 (diverse floor)."""
    if not ranked:
        return []
    scores = [t[1] for t in ranked]
    norm = max(scores) or 1.0
    rel = {mid: s / norm for mid, s in ranked}
    selected: list[str] = []
    candidates = [mid for mid, _ in ranked]
    while candidates and len(selected) < k:
        best = None
        best_val = -1e9
        for mid in candidates:
            div = 0.0
            if selected:
                emb = embeddings.get(mid)
                sims = [
                    _dot(emb, embeddings.get(s, []))
                    if emb and embeddings.get(s)
                    else 0.0
                    for s in selected
                ]
                div = max(sims) if sims else 0.0
            val = lam * rel[mid] - (1.0 - lam) * div
            if val > best_val:
                best_val = val
                best = mid
        selected.append(best)
        candidates.remove(best)
    return selected
