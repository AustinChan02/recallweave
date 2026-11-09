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
import time
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


ANSWER_SYSTEM = """You answer memory-QA questions for an evaluation.
Use ONLY the memory context in the system message and the transcript above —
never invent facts. Answer in the question's language, at most two sentences.
If the context lacks the answer, say you do not know."""


async def _produce_answer(
    answer_adapter, payload: dict, question: str, answer_system: str = ""
) -> tuple[str, str, float | None, dict]:
    """Real protocol: ask the answer model over the condition's emitted context.
    Echo protocol (offline proxy, no adapter): answer = emitted context text.

    Real mode sends (memory system block + answer policy, transcript, question)
    to the answer model; its response — not the context — is what gets graded.
    """
    if answer_adapter is None:
        echo = "\n".join(m.get("content", "") for m in payload["context_messages"])
        return echo, payload.get("provider", "echo"), None, {}

    cms = payload["context_messages"]
    memory_block = cms[0]["content"] if cms and cms[0].get("role") == "system" else ""
    rest = cms[1:]
    if rest and str(rest[-1].get("content", "")).strip() == str(question).strip():
        rest = rest[:-1]  # the question is passed explicitly below, not twice
    transcript = "\n".join(f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in rest)
    user = f"{transcript}\n\nQUESTION: {question}\n请仅依据上文作答。".strip()
    instructions = answer_system or ANSWER_SYSTEM
    system = f"{memory_block}\n\n{instructions}" if memory_block else instructions

    t0 = time.perf_counter()
    answer = (await answer_adapter.chat_text(system, user)).strip()
    wall_ms = (time.perf_counter() - t0) * 1000
    usage = dict(getattr(answer_adapter, "last_usage", {}) or {})
    return answer, f"answered-by:{getattr(answer_adapter, 'provider_label', 'unknown')}", wall_ms, usage


def _judge_usage(judge) -> dict:
    """Provider usage of the judge's LATEST call (OpenAICompatAdapter.last_usage)."""
    adapter = getattr(judge, "adapter", None)
    return dict(getattr(adapter, "last_usage", None) or {})


@dataclass
class ItemRecord:
    qid: str
    item_type: str
    judged: bool
    score: float | None
    rationale: str
    tokens: int
    provider: str
    answer_provider: str = "echo-context"
    answer: str = ""
    tokens_answer: int = 0
    tokens_judge: int = 0
    cost_yuan_est: float = 0.0
    wall_ms_answer: float | None = None


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
        walls = [r.wall_ms_answer for r in self.records if r.wall_ms_answer is not None]

        def pct(q: float) -> float | None:
            if not walls:
                return None
            ordered = sorted(walls)
            return round(ordered[min(len(ordered) - 1, int(q / 100 * len(ordered)))], 1)

        return {
            "accuracy": (sum(scores) / len(scores)) if scores else None,
            "judged": len(judged),
            "total": len(self.records),
            "unjudged": len(unjudged),
            "judged_fraction": (round(len(judged) / len(self.records), 6) if self.records else None),
            "tokens_context_est": sum(r.tokens for r in self.records),
            "tokens_answer": sum(r.tokens_answer for r in self.records),
            "tokens_judge": sum(r.tokens_judge for r in self.records),
            "cost_yuan_upper_est": round(sum(r.cost_yuan_est for r in self.records), 6),
            "answer_wall_ms_p50": pct(50),
            "answer_wall_ms_p95": pct(95),
        }


async def run_harness(
    dataset_path: Path,
    conditions: list[str],
    judge,
    *,
    budget: int,
    tmp_root: Path,
    answer_adapter=None,
    answer_system: str = "",
    cost_guard=None,
) -> list[ConditionRun]:
    """answer_adapter=None → offline echo protocol (honest proxy metric).

    Real mode: every condition emits its context; the ANSWER model receives
    (memory system block, transcript, question) and its response — not the
    context — is what the judge grades. cost_guard (evals.pricing.CostGuard)
    is charged for every real call and aborts when the upper-bound estimate
    exceeds its limit (write-once files stay untouched: no half-written runs).
    """
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
            question = item["question"]
            tok = counter()
            tokens_ctx = sum(tok.count(m.get("content", "")) for m in payload["context_messages"])
            try:
                answer, answer_provider, wall_ms, usage = await _produce_answer(
                    answer_adapter, payload, question, answer_system
                )
            except Exception as exc:  # noqa: BLE001 — fail-closed per item
                run.records.append(
                    ItemRecord(
                        qid=item["qid"],
                        item_type=item.get("type", "fact"),
                        judged=False,
                        score=None,
                        rationale=f"unjudged: answer step failed: {type(exc).__name__}: {exc}"[:200],
                        tokens=tokens_ctx,
                        provider=payload["provider"],
                    )
                )
                continue  # unjudged, never faked

            # Charge the ANSWER call before judging: a guarded over-budget run
            # aborts here, fail-closed, before any result file could be written.
            cost_here = 0.0
            if cost_guard is not None and usage:
                cost_here = cost_guard.charge(getattr(answer_adapter, "_model", "*"), usage)
            # provider-reported completion tokens are facts; fall back to the
            # local estimate only for adapters that expose no usage (mock).
            tokens_answer = int((usage or {}).get("completion_tokens") or 0) or tok.count(answer)

            try:
                judgment = await judge.judge(question=question, reference=item["reference"], answer=answer)
                judge_usage = _judge_usage(judge)  # read AFTER the call — no stale slot
                tokens_judge = int(judge_usage.get("prompt_tokens") or 0) + int(
                    judge_usage.get("completion_tokens") or 0
                )
                cost_judge = 0.0
                if cost_guard is not None and judge_usage:
                    cost_judge = cost_guard.charge(
                        getattr(getattr(judge, "adapter", None), "_model", "*"), judge_usage
                    )
                run.records.append(
                    ItemRecord(
                        qid=item["qid"],
                        item_type=item.get("type", "fact"),
                        judged=True,
                        score=round(judgment.score, 6),
                        rationale=judgment.rationale,
                        tokens=tokens_ctx,
                        provider=payload["provider"],
                        answer_provider=answer_provider,
                        answer=answer[:400],
                        tokens_answer=tokens_answer,
                        tokens_judge=tokens_judge,
                        cost_yuan_est=round(cost_here + cost_judge, 6),
                        wall_ms_answer=round(wall_ms, 1) if wall_ms is not None else None,
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
                        tokens=tokens_ctx,
                        provider=payload["provider"],
                        answer_provider=answer_provider,
                        answer=answer[:400],
                        tokens_answer=tokens_answer,
                        tokens_judge=0,
                        cost_yuan_est=round(cost_here, 6),
                        wall_ms_answer=round(wall_ms, 1) if wall_ms is not None else None,
                    )
                )
        runs.append(run)
    return runs
