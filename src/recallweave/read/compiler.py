"""Context Compiler — the signature module. Pure algorithm, ZERO LLM calls.

Assembly (proposal §4.3): one system message built in a FIXED segment order,
then the working tail (original request messages) appended untouched:

    [persona (system, stable)] -> [PROFILE: hash-ordered semantic facts]
    -> [EPISODIC: event memories, newest-first, stable for a fixed set]

Cache-stable iron rules (ADR-003):
  #1 profile items are ordered by content-hash — a stable memory set always
     emits identical bytes, so provider prefix-cache hits survive.
  #2 injected items carry ONLY their text (no timestamps/relative times), and
     volatile parts (the working tail) sit strictly AFTER the stable block.

Budget (arguments are percentages of the TOTAL budget, defaults 10/30/40/20):
    persona = system% of budget; profile = profile%; episodic = episodic%;
    working = working% of budget. The working tail is trimmed from the OLDEST
    side until it fits its share (single-message overflow is tolerated as a
    soft cap so the user's current turn is never dropped entirely). Selection
    fills profile/episodic greedily by fused score, but OUTPUT ORDER follows
    rule #1 regardless of score order.

Trace (proposal §4.3): every considered memory becomes
    {memory_id, kind, score, rank, budget_slot, tokens, included, source_ref}
so a debugger can answer "why did the agent say this".

P1 cuts (recorded in devlog): the episodic layer consumes event-kind memories
as a stand-in for session summaries while 4.4 is not implemented.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC

from pydantic import BaseModel, Field

from ..adapters.embeddings import EmbeddingAdapter
from ..db.models import Memory
from ..db.repos import MemoryRepo
from ..db.vector_index import VectorIndex
from .fusion import rrf_fuse, weighted_rank
from .retrievers import lexical_lane, recency_lane

PROFILE_KINDS = ("fact", "preference", "instruction")
EVENT_KINDS = ("event",)
_TRACE_LIMIT = 60  # trace only the top-N scored candidates (documented cap)


class TraceItem(BaseModel):
    memory_id: str
    kind: str
    score: float
    rank: int | None = None
    budget_slot: str
    tokens: int
    included: bool
    reason: str | None = None
    source_ref: str | None = None


class UsageInfo(BaseModel):
    budget: int
    working_tokens: int
    system_tokens: int
    total_tokens: int
    layers: dict  # {"persona": u, "profile": u, "episodic": u, "working": u}
    caps: dict  # {"persona": c, "profile": c, "episodic": c, "working": c}


class CompileOutput(BaseModel):
    context_messages: list[dict]
    trace: list[TraceItem] = Field(default_factory=list)
    usage: UsageInfo
    provider: str = "local-compiler"


def stable_order_key(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()[:12]


def partition_rows(rows: list[Memory]) -> tuple[list[Memory], list[Memory]]:
    semantic = [m for m in rows if m.kind in PROFILE_KINDS]
    events = [m for m in rows if m.kind in EVENT_KINDS]
    return semantic, events


def build_system_block(
    persona_line: str,
    profile_contents: list[str],
    event_contents: list[str],
) -> str:
    return "\n".join(text for _slot, text in _system_parts(persona_line, profile_contents, event_contents))


def _system_parts(
    persona_line: str,
    profile_contents: list[str],
    event_contents: list[str],
) -> list[tuple[str, str]]:
    """(budget_slot, rendered_line) pairs — byte-exact building blocks so the
    compiler can account for EVERY line it emits."""
    parts: list[tuple[str, str]] = [("persona", persona_line)]
    if profile_contents:
        parts.append(("headers", "PROFILE (sorted for cache-prefix stability):"))
        parts.extend(("profile", f"- {c}") for c in profile_contents)
    if event_contents:
        parts.append(("headers", "RECENT EVENTS:"))
        parts.extend(("episodic", f"- {c}") for c in event_contents)
    return parts


def _score_of(ranked: list[tuple[str, float]], mid: str) -> float:
    for m, s in ranked:
        if m == mid:
            return s
    return 0.0


def _rank_of(ranked: list[tuple[str, float]], mid: str) -> int | None:
    for i, (m, _s) in enumerate(ranked, start=1):
        if m == mid:
            return i
    return None


def _source_ref(m: Memory) -> str | None:
    span = (m.meta or {}).get("span") or {}
    idx = span.get("message_index")
    return f"msg:{idx}" if idx is not None else None


def _event_order_key_factory(rows_by_id: dict[str, Memory]):
    """Events: newest-first, ties broken by content (stable for a fixed set)."""

    def key(content: str) -> tuple[float, str]:
        newest = 0.0
        for m in rows_by_id.values():
            if m.content == content and m.created_at is not None:
                created = m.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                newest = max(newest, created.timestamp())
        return (-newest, content)

    return key


def _trim_working_tail(messages: list[dict], tok, cap: int, max_turns: int) -> list[dict]:
    """Newest-first accumulation within working cap; drop OLDEST first.

    Soft cap rule (ADR-003): a single oversized message is kept even when it
    exceeds the cap — the user's current turn is never silently truncated.
    """
    if not messages:
        return []
    tail = list(messages[-max_turns:])
    while len(tail) > 1 and tok.count_messages(tail) > cap:
        tail.pop(0)
    return tail


async def compile_context(
    *,
    settings,
    tok,
    repo: MemoryRepo,
    vector: VectorIndex,
    embedder: EmbeddingAdapter,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    messages: list[dict],
    budget: int | None = None,
) -> CompileOutput:
    budget = budget if budget is not None else settings.context_budget_default
    caps = {
        "persona": int(budget * settings.budget_system_pct),
        "profile": int(budget * settings.budget_profile_pct),
        "episodic": int(budget * settings.budget_episodic_pct),
        "working": int(budget * settings.budget_working_pct),
    }

    # --- working layer: original request messages, oldest-side trimmed ---------
    working_tail = _trim_working_tail(messages, tok, caps["working"], settings.working_turns)
    working_tokens = tok.count_messages(working_tail)

    # --- retrieval + fusion (semantic kinds vs event kinds share one pass) -----
    rows = await repo.active_candidates(user_id, settings.lexical_scan_limit)
    rows_by_id: dict[str, Memory] = {str(m.id): m for m in rows}

    query = ""
    for m in reversed(messages):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            query = str(m["content"]).strip()
            break

    vec_ids: list[str] = []
    if query:
        q_emb = (await embedder.embed_many([query]))[0]
        vec_ids = [
            str(mid)
            for mid, _ in await vector.search(user_id=user_id, query_embedding=q_emb, k=settings.vector_top_m)
        ]
    lex_ids = lexical_lane(query, rows, settings.lexical_top_m) if query else []
    rec_ids = recency_lane(rows, settings.recency_top_m)

    fused = rrf_fuse([vec_ids, lex_ids, rec_ids], settings.rrf_k)
    ranked = weighted_rank(
        fused,
        rows_by_id,
        kind_weight_fn=settings.kind_weight,
        tau_days=settings.decay_tau_days,
        importance_lo=settings.importance_lo,
        importance_span=settings.importance_span,
    )

    semantic, events = partition_rows([rows_by_id[mid] for mid, _ in ranked if mid in rows_by_id])

    # --- budgeted selection (greedy by fused score), stable OUTPUT order ------
    traces: list[TraceItem] = []

    def fill(items: list[Memory], slot: str) -> list[str]:
        chosen: list[str] = []
        used = 0
        cap = caps[slot]
        for m in items:  # items arrive final-score ordered
            t = tok.count(f"- {m.content}")  # trace reports the RENDERED line cost
            included = cap > 0 and used + t <= cap
            if included:
                used += t
                chosen.append(m.content)
            traces.append(
                TraceItem(
                    memory_id=str(m.id),
                    kind=m.kind,
                    score=round(_score_of(ranked, str(m.id)), 6),
                    rank=_rank_of(ranked, str(m.id)),
                    budget_slot=slot,
                    tokens=t,
                    included=included,
                    reason=None if included else "budget_exhausted",
                    source_ref=_source_ref(m),
                )
            )
        return chosen

    profile_out = sorted(fill(semantic, "profile"), key=stable_order_key)
    event_out = sorted(fill(events, "episodic"), key=_event_order_key_factory(rows_by_id))

    tagged_parts = _system_parts(settings.persona_line, profile_out, event_out)
    system_text = "\n".join(text for _slot, text in tagged_parts)
    persona_tokens = tok.count(settings.persona_line)
    headers_tokens = sum(tok.count(t) for slot, t in tagged_parts if slot == "headers")

    used_profile = sum(tok.count(f"- {m.content}") for m in semantic if m.content in profile_out)
    used_episodic = sum(tok.count(f"- {m.content}") for m in events if m.content in event_out)
    system_tokens = tok.count(system_text)
    usage = UsageInfo(
        budget=budget,
        working_tokens=working_tokens,
        system_tokens=system_tokens,
        total_tokens=working_tokens + system_tokens,
        layers={
            "persona": persona_tokens,
            "headers": headers_tokens,
            "profile": used_profile,
            "episodic": used_episodic,
            "working": working_tokens,
        },
        caps=caps,
    )
    trace = traces[:_TRACE_LIMIT]

    context_messages = [{"role": "system", "content": system_text}] + [
        {"role": m.get("role", "user"), "content": m.get("content", "")} for m in working_tail
    ]
    return CompileOutput(context_messages=context_messages, trace=trace, usage=usage)
