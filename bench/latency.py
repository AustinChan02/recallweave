"""Latency bench: compile()/search() p50/p95 across synthetic memory sizes.

Honest scope label travels with the output: in-process sqlite + hash
embeddings, single-threaded client — this measures the pure-algorithm pipeline
on a laptop, NOT a production pg+HNSW deployment. Mock numbers are never to be
quoted as provider-measured results.

Usage:
  uv run python bench/latency.py            # print only
  uv run python bench/latency.py --write    # ALSO write-once to bench/results/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from recallweave.adapters.llm import MockLLMAdapter
from recallweave.core.config import make_settings
from recallweave.db.session import make_engine, prep_sqlite_path
from recallweave.service.service import RecallWeaveService

SIZES = [10, 100, 1000]
REPS = 30
QUERY = "用户住在哪里，最近做了什么"
SCOPE_NOTE = (
    "scope: in-process sqlite + hash embeddings, single-threaded client; "
    "excludes network/OS jitter; NOT comparable to prod pg+HNSW numbers."
)


def _percentiles(samples: list[float]) -> tuple[float, float]:
    ordered = sorted(samples)

    def at(q: float) -> float:
        idx = min(len(ordered) - 1, int(q / 100 * len(ordered)))
        return ordered[idx]

    return at(50), at(95)


_job_contents = [
    "实施了风控规则热更新，灰度了百分之三十流量",
    "修复了一个 GraphQL 的 N+1 查询问题",
    "评审同事的存储层迁移方案",
    "用户在内部工具里写了 Lua 脚本",
    "维护计算机房的值班文档",
    "用户把一批日志导出到数据仓库",
    "用户参与复盘了当晚的告警，确定了限流阈值",
    "给新同事讲了 Redis 的过期策略",
]


async def _build_service(seed_count: int) -> tuple[RecallWeaveService, uuid.UUID, Path]:
    tmp = Path(datetime.now().strftime("data/bench-%H%M%S%f") + f"-{uuid.uuid4().hex[:6]}.sqlite")
    db_url = f"sqlite+aiosqlite:///{tmp}"
    prep_sqlite_path(db_url)
    settings = make_settings(
        app_env="test",
        db_url=db_url,
        llm_provider="mock",
        embedding_provider="hash",
        embedding_dim=64,
        env_file="",
    )
    svc = RecallWeaveService(settings, make_engine(db_url), llm=MockLLMAdapter([]))
    await svc.bootstrap()
    uid = uuid.uuid4()
    contents = [_job_contents[i % len(_job_contents)] + f" 版本{i}" for i in range(seed_count)]
    emb = await svc.embedder.embed_many(contents)
    from recallweave.db.models import Memory

    async with svc.repo.new_session() as session:
        async with session.begin():
            for text, e in zip(contents, emb, strict=True):
                m = Memory(user_id=uid, kind="fact", content=text, importance=0.5)
                session.add(m)
                await session.flush()
                await svc.vector.upsert(session, m.id, e)
    return svc, uid, tmp


async def _bench_condition() -> list[dict]:
    out: list[dict] = []
    for size in SIZES:
        svc, uid, tmp_path = await _build_service(size)
        try:
            samples_compile: list[float] = []
            samples_search: list[float] = []
            for _ in range(REPS):
                t0 = time.perf_counter()
                await svc.compile(uid, None, [{"role": "user", "content": QUERY}])
                samples_compile.append((time.perf_counter() - t0) * 1000)
                t0 = time.perf_counter()
                await svc.search(uid, QUERY, top_k=5)
                samples_search.append((time.perf_counter() - t0) * 1000)
            c50, c95 = _percentiles(samples_compile)
            s50, s95 = _percentiles(samples_search)
            out.append(
                {
                    "memory_count": size,
                    "compile_ms": {"p50": round(c50, 3), "p95": round(c95, 3), "n": len(samples_compile)},
                    "search_ms": {"p50": round(s50, 3), "p95": round(s95, 3), "n": len(samples_search)},
                }
            )
        finally:
            await svc.aclose()
            tmp_path.unlink(missing_ok=True)  # bench hygiene: no stray dbs
    return out


def write_once_json(directory: Path, prefix: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    existing = len(list(directory.glob(f"{prefix}-*.json")))
    final = prefix + "-" + payload["generated_at"][:10] + f"-{existing + 1}" + ".json"
    target = directory / final
    if target.exists():
        raise SystemExit(f"WRITE-ONCE VIOLATION: bench result exists: {target}")
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> None:
    started = datetime.now(UTC)
    rows = asyncio.run(_bench_condition())
    result = {
        "schema": "rw-bench-latency/1",
        "provider": {"embedding": "hash", "db": "sqlite", "note": SCOPE_NOTE},
        "generated_at": started.isoformat(),
        "rows": rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="also persist write-once to bench/results/")
    args = ap.parse_args()
    if args.write:
        path = write_once_json(Path(__file__).resolve().parent / "results", "latency", result)
        print(f"written: {path}")


if __name__ == "__main__":
    main()
