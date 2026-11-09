"""Eval conditions (proposal §7.2) + offline protocol.

Honesty core: with `llm_provider=mock` there IS no real answering model. The
harness therefore measures `context-evidence recall`: whether the reference
evidence for a question lands inside the context each condition emits (the
"answer" is the emitted context itself — an echo policy). It is a PROXY for
pipeline behavior, clearly labeled in every result file; it is NOT a QA
accuracy number and must never be quoted as one. Real answering needs a
real provider key (tracked as pending work in docs/devlog.md).

Conditions (§7.2):
  C0 no-memory        : last 20 turns only, no memory layer involved.
  C1 full-history     : every active memory + every message dumped in.
  C2 naive-vector-RAG : top-k vector-only hits serialized verbatim, no compiler.
  C3 recallweave      : full pipeline (extract -> dedup -> resolve -> compiler).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from recallweave.adapters.llm import MockLLMAdapter
from recallweave.core.config import make_settings
from recallweave.core.errors import ProviderError
from recallweave.db.session import make_engine, prep_sqlite_path
from recallweave.service.service import RecallWeaveService
from recallweave.tokens import counter

_KIND_OF = {
    "preference": "preference",
    "fact": "fact",
    "conflict": "fact",
    "event": "event",
    "instruction": "instruction",
    "recency": "fact",
}

# The harness's deterministic offline conflict policy: when the pipeline routes
# a candidate to the conflict resolver, the scripted answer is NOOP (declared,
# reproducible). Result files disclose this; it is NOT measured LLM behavior.
STICKY_NOOP = {
    "op": "NOOP",
    "rationale": "offline harness: conflict resolution scripted to NOOP",
    "confidence": 0.5,
}


def _extraction_script(item: dict) -> dict:
    """Deterministic offline extraction: each user turn becomes one candidate."""
    kind = _KIND_OF.get(item.get("type", "fact"), "fact")
    memories = [
        {"kind": kind, "content": m.get("content", ""), "importance": 0.6}
        for m in item.get("turns", [])
        if m.get("role") == "user" and m.get("content")
    ]
    return {"memories": memories}


async def _build_item_service(root: Path, item: dict) -> RecallWeaveService:
    db_path = root / f"{item['qid']}-{uuid.uuid4().hex[:6]}.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    prep_sqlite_path(db_url)
    settings = make_settings(
        app_env="test",
        db_url=db_url,
        llm_provider="mock",
        embedding_provider="hash",
        embedding_dim=64,
        context_budget_default=600,
        env_file="",
    )
    engine = make_engine(db_url)
    llm = MockLLMAdapter([_extraction_script(item)], sticky=STICKY_NOOP)
    svc = RecallWeaveService(settings, engine, llm=llm)
    await svc.bootstrap()
    return svc


async def run_condition(condition: str, item: dict, item_root: Path, budget: int) -> dict:
    """Run ONE (condition, item) pair; returns {context_messages, provider, meta}."""
    user_id = uuid.uuid4()
    turns = [t for t in item.get("turns", []) if t.get("role") and t.get("content")]
    final_q = {"role": "user", "content": item["question"]}

    if condition == "C0":
        # Bare-chat lower bound: recent-20 truncation, memory layer not involved.
        context = [
            {"role": "system", "content": "You are a helpful assistant."},
            *[{"role": m["role"], "content": m["content"]} for m in turns[-20:]],
            final_q,
        ]
        return {"context_messages": context, "provider": "local-truncate", "meta": {}}

    svc = None
    try:
        svc = await _build_item_service(item_root, item)
        await svc.ingest(user_id, session_id=None, messages=turns)

        if condition == "C1":
            rows = await svc.repo.active_candidates(user_id, 2000)
            system = "\n".join(["ALL MEMORIES (C1 full-dump):"] + [f"- {m.content}" for m in rows])
            context = [
                {"role": "system", "content": system},
                *[{"role": m["role"], "content": m["content"]} for m in turns],
                final_q,
            ]
            return {
                "context_messages": context,
                "provider": "local-fulldump",
                "meta": {"memory_count": len(rows), "retrieval": "none"},
            }

        if condition == "C2":
            q_emb = (await svc.embedder.embed_many([item["question"]]))[0]
            hits = await svc.vector.search(user_id=user_id, query_embedding=q_emb, k=3)
            rows = await svc.repo.by_ids(user_id, [mid for mid, _ in hits])
            system = "\n".join(
                ["RETRIEVED (C2 naive vector-RAG, verbatim, no compiler):"]
                + [f"- {rows[mid].content}" for mid, _ in hits if mid in rows]
            )
            context = [
                {"role": "system", "content": system},
                *[{"role": m["role"], "content": m["content"]} for m in turns[-20:]],
                final_q,
            ]
            return {
                "context_messages": context,
                "provider": "local-vector-rag",
                "meta": {"hit_count": len(hits), "retrieval": "vector-only"},
            }

        if condition == "C3":
            out = await svc.compile(user_id, None, [*turns, final_q], budget=budget)
            return {
                "context_messages": out.context_messages,
                "provider": out.provider,
                "meta": {
                    "trace_count": len(out.trace),
                    "usage": out.usage.model_dump(),
                },
            }

        raise ProviderError(f"unknown condition: {condition}")
    finally:
        if svc is not None:
            await svc.aclose()


@dataclass
class ItemRecord:
    qid: str
    item_type: str
    judged: bool
    score: float | None
    rationale: str
    tokens: int
    provider: str


@dataclass
class ConditionRun:
    condition: str
    records: list[ItemRecord] = field(default_factory=list)

    def metrics(self) -> dict:
        """Unjudged cases are OUT of the denominator (protocol §7.3), and are
        reported alongside — never folded in as fake 0s or 1s."""
        judged = [r for r in self.records if r.judged]
        unjudged = [r for r in self.records if not r.judged]
        scores = [r.score for r in judged if r.score is not None]
        return {
            "accuracy": (sum(scores) / len(scores)) if scores else None,
            "judged": len(judged),
            "total": len(self.records),
            "unjudged": len(unjudged),
            "judged_fraction": (round(len(judged) / len(self.records), 6) if self.records else None),
            "tokens_total_estimate": sum(r.tokens for r in self.records),
        }


async def run_harness(
    dataset_path: Path, conditions: list[str], judge, *, budget: int, tmp_root: Path
) -> list[ConditionRun]:
    items = [
        json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    item_root = tmp_root / "items"
    item_root.mkdir(parents=True, exist_ok=True)

    runs: list[ConditionRun] = []
    for condition in conditions:
        run = ConditionRun(condition=condition)
        for item in items:
            payload = await run_condition(condition, item, item_root, budget)
            answer = "\n".join(m.get("content", "") for m in payload["context_messages"])
            tok = counter()
            tokens = sum(tok.count(m.get("content", "")) for m in payload["context_messages"])
            try:
                judgment = await judge.judge(
                    question=item["question"],
                    reference=item["reference"],
                    answer=answer,
                )
                run.records.append(
                    ItemRecord(
                        qid=item["qid"],
                        item_type=item.get("type", "fact"),
                        judged=True,
                        score=round(judgment.score, 6),
                        rationale=judgment.rationale,
                        tokens=tokens,
                        provider=payload["provider"],
                    )
                )
            except Exception as exc:  # noqa: BLE001 — unjudged, by protocol design
                run.records.append(
                    ItemRecord(
                        qid=item["qid"],
                        item_type=item.get("type", "fact"),
                        judged=False,
                        score=None,
                        rationale=f"unjudged: {type(exc).__name__}: {exc}"[:200],
                        tokens=tokens,
                        provider=payload["provider"],
                    )
                )
        runs.append(run)
    return runs
