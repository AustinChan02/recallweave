"""Seed the local DB with a small demo memory set — DECLARED scripted mock.

Why this exists: the live server in mock mode has an EMPTY mock queue by
design (fail-closed honesty: a mock can never silently "answer for" a real
provider), so `POST /v1/memories:extract` on a fresh mock server is an
honest no-op. To demo the READ paths (search / compiler) offline, we seed
the write pipeline the same way the test suite and the eval harness do:
a SCRIPTED MockLLMAdapter whose outputs are declared here and labeled in
every resulting row (`provider=mock:scripted-seed`). The seeded content is
mechanics-only demo data — never quote it as a measured result.

Usage:
    uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from recallweave.adapters.llm import MockLLMAdapter  # noqa: E402
from recallweave.core.config import from_env  # noqa: E402
from recallweave.db.session import ensure_schema, make_engine, prep_sqlite_path  # noqa: E402
from recallweave.service.service import RecallWeaveService  # noqa: E402

DEV_USER = uuid.UUID("11111111-1111-1111-1111-111111111111")  # dev-key-1's user

STICKY_NOOP = {  # conflict resolver: declared offline NOOP (same as the eval harness)
    "op": "NOOP",
    "rationale": "seed script: conflict resolution scripted to NOOP",
    "confidence": 0.5,
}

TRANSCRIPT = [
    {"role": "user", "content": "我对花生过敏，千万不要给我推荐含花生的菜。"},
    {"role": "assistant", "content": "好的，已记录：花生过敏。"},
    {"role": "user", "content": "我平时喜欢吃粤菜，尤其是清蒸鱼。"},
    {"role": "user", "content": "对了，我周四晚上一般要加班，别约那个时间。"},
]

# Declared scripted extraction output (the write pipeline validates it against
# the ExtractedBatch schema exactly like a real provider payload).
SCRIPTED_EXTRACTION = {
    "memories": [
        {
            "kind": "fact",
            "content": "用户对花生过敏，饮食需避开含花生的菜。",
            "importance": 1.0,
            "span": {"message_index": 0},
        },
        {
            "kind": "preference",
            "content": "用户喜欢吃粤菜，尤其是清蒸鱼。",
            "importance": 0.6,
            "span": {"message_index": 2},
        },
        {
            "kind": "instruction",
            "content": "用户每周四晚上加班，请勿在周四晚安排活动。",
            "importance": 0.5,
            "span": {"message_index": 3},
        },
    ],
}


async def main() -> int:
    settings = from_env()
    db_url = settings.db_url
    if "sqlite" in db_url:
        prep_sqlite_path(db_url)
    engine = make_engine(db_url)
    await ensure_schema(engine)

    llm = MockLLMAdapter([SCRIPTED_EXTRACTION], sticky=STICKY_NOOP)
    svc = RecallWeaveService(settings, engine, llm=llm)
    await svc.bootstrap()

    report = await svc.ingest(DEV_USER, uuid.uuid4(), TRANSCRIPT)
    data = report.model_dump()
    print(
        f"seed_demo.py: provider={data['provider']} extracted={data['extracted']} "
        f"turns_ingested={data['turns_ingested']} db={db_url.split('///')[-1]}"
    )
    for dec in data["decisions"]:
        print(f"  [{dec['op']}] {dec['content'][:64]}")
    if data["extraction_error"]:
        print("  extraction_error:", data["extraction_error"])
        return 1
    print("seed_demo.py: scripted-mock seeding done (declared, labeled; mechanics-only).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
