"""Offline demo: in-process service, SQLite temp file, scripted mock LLM.

Everything printed here is produced by `provider=mock` (deterministic scripted
extraction + hash embeddings). It demonstrates MECHANICS, never real quality:
mock output must NOT be quoted as measured numbers. Real-provider runs need
RECALLWEAVE_LLM_PROVIDER=openai_compat + key.

Run: uv run python scripts/demo_offline.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recallweave.adapters.llm import MockLLMAdapter  # noqa: E402
from recallweave.core.config import make_settings  # noqa: E402
from recallweave.core.errors import ProviderError  # noqa: E402
from recallweave.db.session import make_engine, prep_sqlite_path  # noqa: E402
from recallweave.service.service import RecallWeaveService  # noqa: E402

TURNS: list[list[dict]] = [
    [
        {"role": "user", "content": "我对花生过敏，吃了花生会起疹子，记一下。"},
        {"role": "assistant", "content": "好的，已记录你的花生过敏。"},
    ],
    [
        {"role": "user", "content": "上个月我去大连出差了三天，顺便去星海广场逛了逛。"},
        {"role": "assistant", "content": "出差顺利。"},
    ],
    [
        {"role": "user", "content": "对了，后来试了花生酱也会过敏，吃了花生酱也会起疹子。"},
        {"role": "assistant", "content": "明白。"},
    ],
]

FIND_TARGET_IN = "花生"  # which stored memory the scripted UPDATE targets

SEARCH_QUERIES = ["用户的过敏情况", "出差去过哪里"]


def _print(title: str, obj) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rw-demo-"))
    db_url = f"sqlite+aiosqlite:///{tmp/'demo.sqlite'}"
    prep_sqlite_path(db_url)
    settings = make_settings(
        app_env="dev",
        db_url=db_url,
        llm_provider="mock",
        embedding_provider="hash",
        embedding_dim=64,
        context_budget_default=800,
    )
    engine = make_engine(db_url)
    llm = MockLLMAdapter([])  # FIFO: each scripted return is declared up front
    # Turn-1 extraction script (1 candidate -> ADD, no neighbors)
    llm.push(
        {"memories": [
            {"kind": "fact", "content": "用户对花生过敏，吃了花生会起疹子", "importance": 0.9,
             "span": {"message_index": 0}},
        ]}
    )
    # Turn-2 extraction script (1 event -> ADD)
    llm.push(
        {"memories": [
            {"kind": "event", "content": "用户上个月去大连出差三天，去了星海广场", "importance": 0.5},
        ]}
    )
    # Turn-3 candidate "花生酱也会起疹子" lands in the (0.80, 0.93) window of the
    # turn-1 fact (hash-cosine ≈ 0.885) — the pipeline routes it to the resolver;
    # we script the de-duplication decision right before turn-3 runs.
    # Turn-3 extraction script is pushed there too (see loop below).

    svc = RecallWeaveService(settings, engine, llm=llm)
    await svc.bootstrap()
    import uuid as uuid_mod

    user = uuid_mod.UUID(settings.dev_user)

    reports = []
    for i, turn in enumerate(TURNS, start=1):
        if i == 3:
            actives = await svc.repo.active_candidates(user, 50)
            allergy_id = next(
                (str(m.id) for m in actives if FIND_TARGET_IN in m.content and m.kind == "fact"),
                None,
            )
            assert allergy_id, "turn-1 allergy memory not found"
            llm.push({"memories": [
                {"kind": "fact", "content": "用户对花生过敏，吃了花生酱也会起疹子", "importance": 0.85},
            ]})
            llm.push({
                "op": "UPDATE", "target_memory_id": allergy_id,
                "content": "用户对花生过敏，吃花生或花生酱都会起疹子",
                "confidence": 0.9, "rationale": "同一事实的扩展表述，合并避免重复",
            })
        report = (await svc.ingest(user, None, turn)).model_dump()
        report["provider_note"] = "mock（脚本化产出，禁止作为实测引用）"
        reports.append(report)
        _print(f"ingest turn-{i} report", report)

    for q in SEARCH_QUERIES:
        _print(f"search: {q}", await svc.search(user, q, top_k=3))

    compiled = await svc.compile(
        user,
        None,
        [
            {"role": "system", "content": "ignore"},
            {"role": "user", "content": "我最近能吃海鲜吗？出差吃什么好？"},
        ],
        budget=600,
    )
    compiled = compiled.model_dump()
    compiled["provider_note"] = "compiler 是纯算法(本地)，不涉及 LLM"
    _print("compile output", compiled)

    _print("stats", await svc.stats(user))
    print("\n--- honesty footer: provider=mock for all LLM outputs above; "
          "compile/search are local algorithms. Do not quote mock numbers as measured. ---")
    await svc.aclose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except ProviderError as exc:
        print(f"DEMO FAIL (provider fail-closed): {exc}")
        raise
