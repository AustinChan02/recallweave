"""Cache-stable A/B (proposal §7.4): v1 drift ordering vs v2 content-addressed.

What IS measurable offline (mock providers):
  * prefix STABILITY — identical queries/rounds must emit identical stable
    blocks for v2 (content-hash ordered), while v1's score-ordered output can
    drift when wall-clock recency changes between rounds.

What is NOT measurable offline and therefore always null here:
  * cache hit tokens / TTFT — need a REAL provider showing usage stats. Never
    simulated, never fabricated (honesty note in every output).

Usage:
  uv run python bench/cache_ab.py            # print report
  uv run python bench/cache_ab.py --write    # also write-once to bench/results/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from latency import write_once_json  # same bench helpers

from recallweave.adapters.llm import MockLLMAdapter
from recallweave.core.config import make_settings
from recallweave.db.session import make_engine, prep_sqlite_path
from recallweave.service.service import RecallWeaveService

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


def main() -> None:
    report = asyncio.run(main_async())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if args.write:
        path = write_once_json(Path(__file__).resolve().parent / "results", "cache-ab", report)
        print(f"written: {path}")


if __name__ == "__main__":
    main()
