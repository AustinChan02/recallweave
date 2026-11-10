"""Cache-stable A/B (proposal §7.4): v1 drift ordering vs v2 content-addressed.

What IS measurable offline (mock providers):
  * prefix STABILITY — identical queries/rounds must emit identical stable
    blocks for v2 (content-hash ordered), while v1's score-ordered output can
    drift when wall-clock recency changes between rounds.

What is NOT measurable offline and therefore always null here:
  * cache hit tokens / TTFT — need a REAL provider showing usage stats. Never
    simulated, never fabricated (honesty note in every output).

Usage:
  uv run python bench/cache_ab.py            # print report (offline stability half)
  uv run python bench/cache_ab.py --write    # also write-once to bench/results/
  uv run python bench/cache_ab.py --provider # online half: send BOTH orderings to the
                                             # REAL provider and capture cache-hit
                                             # tokens + wall time per call (fail-closed)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from latency import write_once_json  # same bench helpers

from recallweave.adapters.llm import MockLLMAdapter
from recallweave.core.config import make_settings
from recallweave.db.session import make_engine, prep_sqlite_path
from recallweave.service.service import RecallWeaveService

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))  # bench/ script runs with bench/ as sys.path[0]

from evals.pricing import CostGuard, load_price_table  # noqa: E402
from recallweave.adapters.llm import OpenAICompatAdapter  # noqa: E402
from recallweave.core.config import from_env  # noqa: E402

ROUNDS = 6
QUESTION = "用户怎么安排睡眠？"


def _service(tmp: Path) -> RecallWeaveService:
    db_url = f"sqlite+aiosqlite:///{tmp / 'ab.sqlite'}"
    prep_sqlite_path(db_url)
    settings = make_settings(
        app_env="test",
        db_url=db_url,
        llm_provider="mock",
        embedding_provider="hash",
        embedding_dim=64,
        env_file="",
        decay_tau_days=21.0,
    )
    return RecallWeaveService(settings, make_engine(db_url), llm=MockLLMAdapter([]))


async def _seed(svc, uid) -> None:
    from recallweave.db.models import Memory

    contents = [
        c
        for c in [
            "用户坚持每晚 23 点睡觉，7 点起床",
            "用户周末睡懒觉到中午",
            "用户睡前不喝咖啡",
            "用户的午睡习惯是 20 分钟",
            "用户有过失眠经历,后缓解",
            "用户收藏了睡眠呼吸练习的音频",
        ]
    ]
    emb = await svc.embedder.embed_many(contents)
    async with svc.repo.new_session() as session:
        async with session.begin():
            for text, e in zip(contents, emb, strict=True):
                m = Memory(user_id=uid, kind="fact", content=text, importance=0.6)
                session.add(m)
                await session.flush()
                await svc.vector.upsert(session, m.id, e)


def _v1_reorder(compiled, round_no: int) -> str:
    """v1 (drift simulation): re-sort the profile items by a score that churns
    every round (wall-clock recency would), rotating the list by round_no.
    Same memory SET, different prefix bytes each round — the anti-pattern."""
    sys_text = compiled.context_messages[0]["content"]
    lines = sys_text.splitlines()
    profile_start = next(i for i, ln in enumerate(lines) if ln.startswith("PROFILE"))
    profile_end = next((i for i, ln in enumerate(lines) if ln.startswith("RECENT")), len(lines))
    items = [ln[2:] for ln in lines[profile_start + 1 : profile_end]]
    if items:
        rot = round_no % len(items)
        drifted = items[rot:] + items[:rot]
        return "\n".join(lines[: profile_start + 1] + [f"- {t}" for t in drifted] + lines[profile_end:])
    return sys_text


async def main_async() -> dict:
    started = datetime.now(UTC)
    tmp = Path("data") / f"cache-ab-{started.strftime('%H%M%S%f')}"
    svc = _service(tmp)
    await svc.bootstrap()
    uid = uuid.uuid4()
    await _seed(svc, uid)

    v2_hashes: list[str] = []
    v1_hashes: list[str] = []
    for round_no in range(ROUNDS):
        compiled = await svc.compile(uid, None, [{"role": "user", "content": QUESTION}])
        v2_hashes.append(str(_stable_hash(compiled.context_messages[0]["content"])))
        v1_hashes.append(str(_stable_hash(_v1_reorder(compiled, round_no))))
        await asyncio.sleep(0)  # yield; wall-clock recency (last_accessed) updates

    v2_stable = len(set(v2_hashes)) == 1
    v1_drifted = len(set(v1_hashes)) > 1
    await svc.aclose()
    for stray in tmp.glob("*.sqlite"):
        stray.unlink(missing_ok=True)  # bench hygiene
    tmp.rmdir() if tmp.is_dir() and not any(tmp.iterdir()) else None
    return {
        "schema": "rw-bench-cache-ab/1",
        "generated_at": started.isoformat(),
        "rounds": ROUNDS,
        "question": QUESTION,
        "v2_content_addressed": {"unique_prefixes": len(set(v2_hashes)), "stable": v2_stable},
        "v1_score_drift": {"unique_prefixes": len(set(v1_hashes)), "drifted": v1_drifted},
        "provider": {
            "embedding": "hash",
            "db": "sqlite",
            "labels": ["local-compiler"],
        },
        "uncomputed": {
            "cache_hit_tokens": None,
            "ttft_ms": None,
            "note": (
                "cache-hit/TTFT deltas need a REAL provider's usage or timing data; "
                "mock mode cannot measure them. This script proves the STABILITY "
                "precondition only (the honest half of the experiment)."
            ),
        },
    }


def _stable_hash(text: str) -> int:
    import hashlib

    return int.from_bytes(hashlib.sha1(text.encode()).digest()[:8], "big")


def _cache_hit_tokens(usage: dict) -> tuple[int | None, str | None]:
    """Provider-reported cache-hit tokens (usage facts, never derived): the
    first match among known field names wins. Absent field -> (None, None):
    the cache half is then UNMEASURED, never guessed."""
    for key in ("prompt_cache_hit_tokens", "cached_tokens", "cache_read_input_tokens"):
        if key in usage:
            return int(usage.get(key) or 0), key
    return None, None


def _aggregate(rows: list[dict]) -> dict:
    prompt_total = sum(r["prompt_tokens"] for r in rows)
    hits = [r["cache_hit_tokens"] for r in rows if r["cache_hit_tokens"] is not None]
    hit_total = sum(hits) if hits else None
    walls = [r["wall_ms"] for r in rows]
    return {
        "unique_prefixes": len({r["prefix_sha1"] for r in rows}),
        "prompt_tokens_total": prompt_total,
        "cache_hit_tokens_total": hit_total,
        "hit_rate_tokens": (
            round(hit_total / prompt_total, 4) if hit_total is not None and prompt_total else None
        ),
        "walls_ms": walls,
        "mean_wall_ms": round(sum(walls) / len(walls), 1) if walls else None,
        "cache_field_seen": bool(hits),
    }


async def provider_async(args: argparse.Namespace) -> dict:
    """Online half of §7.4: SAME memory set, TWO compiler orderings per round —
    v2 = the compiler's stable content-addressed ordering, v1 = the drift
    simulation — both sent to the REAL provider as system messages.

    Everything reported is a provider-REPORTED fact (usage payload) or a
    wall-clock time. Fail-closed: refuses without openai_compat + key; charges
    every call to a CostGuard at conservative upper-bound prices and aborts
    BEFORE any write when the cap is hit; never runs with < 2 rounds because
    the first call can only miss (the comparison IS the delta).
    """
    started = datetime.now(UTC)
    settings = from_env()
    if settings.llm_provider != "openai_compat":
        raise SystemExit(
            "REFUSAL: --provider needs a real provider "
            "(RECALLWEAVE_LLM_PROVIDER=openai_compat + key in .env); "
            f"got llm_provider={settings.llm_provider!r}."
        )
    rounds = int(args.rounds)
    if rounds < 2:
        raise SystemExit("REFUSAL: cache-hit semantics need >= 2 rounds.")
    guard = CostGuard(load_price_table(), args.max_cost_yuan)
    adapter = OpenAICompatAdapter(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
    manifest: dict[str, list[dict]] = {"v2": [], "v1": []}
    svc = None
    try:
        with tempfile.TemporaryDirectory(prefix="rw-cache-ab-") as td:
            svc = _service(Path(td))
            await svc.bootstrap()
            uid = uuid.uuid4()
            await _seed(svc, uid)
            for round_no in range(rounds):
                compiled = await svc.compile(uid, None, [{"role": "user", "content": QUESTION}])
                blocks = {
                    "v2": compiled.context_messages[0]["content"],
                    "v1": _v1_reorder(compiled, round_no),
                }
                for kind, system_text in blocks.items():
                    t0 = time.perf_counter()
                    await adapter.chat_text(system_text, QUESTION)
                    wall_ms = (time.perf_counter() - t0) * 1000
                    usage = dict(getattr(adapter, "last_usage", {}) or {})
                    guard.charge(settings.llm_model, usage)
                    hit_tokens, hit_field = _cache_hit_tokens(usage)
                    manifest[kind].append(
                        {
                            "round": round_no,
                            "prefix_sha1": f"{_stable_hash(system_text):016x}",
                            "wall_ms": round(wall_ms, 1),
                            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                            "cache_hit_tokens": hit_tokens,
                            "hit_field": hit_field,
                        }
                    )
                await asyncio.sleep(0)  # yield; wall-clock recency (last_accessed) updates
    finally:
        if svc is not None:
            await svc.aclose()
        await adapter.aclose()

    v2_agg, v1_agg = _aggregate(manifest["v2"]), _aggregate(manifest["v1"])
    hit_fields = sorted({r["hit_field"] for rows in manifest.values() for r in rows if r["hit_field"]})
    return {
        "schema": "rw-bench-cache-ab-online/1",
        "generated_at": started.isoformat(),
        "rounds": rounds,
        "question": QUESTION,
        "experiment": (
            "same memory set compiled twice per round: v2 = stable content-addressed "
            "ordering; v1 = score-drift reorder simulation. Both blocks go to the REAL "
            "provider; usage and wall time are recorded per call."
        ),
        "stability": {
            "v2_content_addressed": {
                "unique_prefixes": v2_agg["unique_prefixes"],
                "stable": v2_agg["unique_prefixes"] == 1,
            },
            "v1_score_drift": {
                "unique_prefixes": v1_agg["unique_prefixes"],
                "drifted": v1_agg["unique_prefixes"] > 1,
            },
        },
        "usage": {
            "v2_content_addressed": {**v2_agg, "per_round": manifest["v2"]},
            "v1_score_drift": {**v1_agg, "per_round": manifest["v1"]},
            "cache_field": hit_fields[0] if len(hit_fields) == 1 else hit_fields,
            "note": (
                "prompt/cache tokens are provider-reported usage facts. A MISSING cache "
                "field means the provider did not report one — that half stays "
                "unmeasured, never invented."
            ),
        },
        "provider": {
            "llm": settings.llm_provider,
            "model": settings.llm_model,
            "embedding": "hash",
            "db": "sqlite",
            "compiler": "local (no LLM in the compile path)",
            "labels": [f"openai_compat:{settings.llm_model}"],
        },
        "budget_guard": {
            "max_cost_yuan": args.max_cost_yuan,
            "spent_yuan_upper_est": round(guard.spent_yuan, 6),
            "tokens_prompt": guard.tokens_prompt,
            "tokens_completion": guard.tokens_completion,
            "price_table": "configs/prices.toml (UPPER-BOUND placeholders)",
        },
        "uncomputed": {
            "ttft_ms": None,
            "cost_yuan": None,
            "note": (
                "the adapter is non-streaming: wall_ms above is FULL-response latency, "
                "not first-token — TTFT needs a streaming client or the provider console; "
                "cost_yuan needs the console price check. Never fabricated."
            ),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument(
        "--provider",
        action="store_true",
        help="online half: send both orderings to the real provider, capture usage/wall (fail-closed)",
    )
    ap.add_argument(
        "--rounds", type=int, default=ROUNDS, help="rounds per version (provider mode needs >= 2)"
    )
    ap.add_argument(
        "--max-cost-yuan",
        type=float,
        default=5.0,
        help="CostGuard cap on cumulative upper-bound estimate (fail-closed abort)",
    )
    args = ap.parse_args()
    report = asyncio.run(provider_async(args) if args.provider else main_async())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.write:
        prefix = "cache-ab-online" if args.provider else "cache-ab"
        path = write_once_json(Path(__file__).resolve().parent / "results", prefix, report)
        print(f"written: {path}")


if __name__ == "__main__":
    main()
