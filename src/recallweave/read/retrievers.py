"""Lexical + recency lanes.

ADR-002: the lexical channel is computed in Python (DB-agnostic) because Chinese
requires a parser/extension (zhparser) we deliberately do not operate in the
default image; the upgrade path to ParadeDB pg_search (BM25) is P2.
Tokenizer is the SHARED one from adapters.embeddings — a second tokenizer here
would be the classic drift trap (score semantics diverging between retrieval
and dedup).
"""

from __future__ import annotations

from ..adapters.embeddings import tokenize_for_lexical
from ..db.models import Memory


def lexical_scores(query: str, rows: list[Memory]) -> dict[str, float]:
    q_tokens = set(tokenize_for_lexical(query.lower()))
    if not q_tokens:
        return {}
    out: dict[str, float] = {}
    qn = float(len(q_tokens))
    for m in rows:
        d_tokens = set(tokenize_for_lexical(m.content.lower()))
        if not d_tokens:
            continue
        inter = len(q_tokens & d_tokens)
        if inter == 0:
            continue
        out[str(m.id)] = inter / ((qn * float(len(d_tokens))) ** 0.5)
    return out


def lexical_lane(query: str, rows: list[Memory], top_m: int) -> list[str]:
    scored = lexical_scores(query, rows)
    return [mid for mid, _ in sorted(scored.items(), key=lambda t: t[1], reverse=True)[:top_m]]


def recency_lane(rows: list[Memory], top_m: int) -> list[str]:
    def anchor_key(m: Memory) -> float:
        anchor = m.last_accessed or m.created_at
        return anchor.timestamp() if anchor else 0.0

    ordered = sorted(rows, key=anchor_key, reverse=True)
    return [str(m.id) for m in ordered[:top_m]]
