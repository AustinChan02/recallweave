"""P1.c /v1/reflect: clustering -> conflict pipeline -> bitemporal apply.

Under test (mirrors the TODO checklist 聚簇→合并链→审计字段):
  * transitive cosine clusters over active memories (hash embeddings: exact
    duplicates -> cosine 1.0; distinct contents -> separate rows);
  * NO clusters -> ZERO LLM interaction (empty queue must stay untouched);
  * scripted UPDATE merges the cluster into ONE fresh row, supersedes every
    member (bitemporal chain kept), and audits everything in meta.decision;
  * scripted NOOP / DELETE are applied exactly as reported;
  * an unscripted mock produces honest skips, NEVER fabricated merges.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from recallweave.adapters.llm import MockLLMAdapter
from recallweave.core.config import make_settings
from recallweave.db.models import Memory
from recallweave.db.session import make_engine, prep_sqlite_path
from recallweave.service.service import RecallWeaveService

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _svc(tmp_path: Path, llm) -> RecallWeaveService:
    db_url = f"sqlite+aiosqlite:///{tmp_path}/s.sqlite"
    prep_sqlite_path(db_url)
    engine = make_engine(db_url)
    settings = make_settings(
        app_env="test",
        db_url=db_url,
        llm_provider="mock",
        embedding_provider="hash",
        embedding_dim=64,
        env_file="",
    )
    return RecallWeaveService(settings, engine, llm=llm)


async def _seed(svc, uid, contents: list[str], *, importance=0.5, days_apart=10):
    """Chronologically spaced rows (index order = chronological order)."""
    maker = async_sessionmaker(svc.repo.engine, expire_on_commit=False)
    rows = [
        Memory(
            user_id=uid,
            session_id=None,
            kind="fact",
            content=c,
            importance=importance,
            status="active",
            meta={},
            created_at=_BASE + timedelta(days=i * days_apart),
        )
        for i, c in enumerate(contents)
    ]
    async with maker() as session, session.begin():
        session.add_all(rows)
    return rows


async def _active_contents(svc, uid) -> list[str]:
    return [m.content for m in await svc.repo.active_candidates(uid, 100)]


@pytest.mark.asyncio
async def test_no_clusters_means_zero_llm_calls(tmp_path):
    llm = MockLLMAdapter([])  # any call would exhaust -> skipped; absence proves 0 calls
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid = uuid.uuid4()
    contents = [
        "用户住在上海浦东新区，骑车通勤。",
        "用户最喜欢的咖啡是蓝山拿铁。",
        "用户的生日是 1990 年 4 月。",
    ]
    await _seed(svc, uid, contents)
    report = await svc.reflect(uid)
    assert report.clusters_found == 0
    assert report.decisions == [] and report.skipped == []
    assert report.scanned == 3
    assert report.provider == "mock"
    assert sorted(await _active_contents(svc, uid)) == sorted(contents)  # nothing changed
    await svc.aclose()


@pytest.mark.asyncio
async def test_update_merges_cluster_and_keeps_bitemporal_chain(tmp_path):
    llm = MockLLMAdapter([])  # queue filled below, AFTER the real ids exist
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid = uuid.uuid4()
    rows = await _seed(svc, uid, ["用户住在上海浦东。", "用户住在上海浦东。"])
    older, newer = rows[0], rows[1]
    llm.push(
        {
            "op": "UPDATE",
            "target_memory_id": str(older.id),
            "content": "用户住在上海浦东（合并自重复记忆）。",
            "confidence": 0.9,
            "rationale": "duplicate cluster merged",
        }
    )
    try:
        report = await svc.reflect(uid)
        assert report.clusters_found == 1
        assert len(report.decisions) == 1
        d = report.decisions[0]
        assert d.op == "UPDATE"
        assert d.target_memory_id == str(older.id)
        assert d.similarity is not None and d.similarity > 0.95

        active = await svc.repo.active_candidates(uid, 100)
        assert sorted(m.content for m in active) == ["用户住在上海浦东（合并自重复记忆）。"]
        assert len(active) == 1  # candidate itself was superseded too (merge semantics)
        merged = active[-1]
        assert d.memory_id == str(merged.id)
        # bitemporal chain: BOTH members superseded, pointing at the new row
        for old in (older, newer):
            row = await svc.repo.get_any_status(uid, old.id)
            assert row.status == "superseded" and str(row.superseded_by) == str(merged.id)
        # audit: provider label, cluster membership, reflect flag
        audit = merged.meta["decision"]
        assert audit["op"] == "UPDATE" and audit["provider"] == "mock"
        assert audit["reflect"] is True
        assert set(audit["cluster"]) == {str(older.id), str(newer.id)}
        assert "forgotten" not in (merged.meta or {})
    finally:
        await svc.aclose()


@pytest.mark.asyncio
async def test_noop_decision_changes_nothing_but_is_reported(tmp_path):
    llm = MockLLMAdapter([])
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid = uuid.uuid4()
    rows = await _seed(svc, uid, ["用户住在上海浦东。", "用户住在上海浦东。"])
    llm.push({"op": "NOOP", "confidence": 0.8, "rationale": "restatement", "target_memory_id": None})
    report = await svc.reflect(uid)
    assert report.clusters_found == 1
    assert report.decisions == []
    assert report.skipped and report.skipped[0].reason.startswith("noop:")
    active = await _active_contents(svc, uid)
    assert active == ["用户住在上海浦东。", "用户住在上海浦东。"]  # untouched, honestly reported
    for r in rows:
        assert (await svc.repo.get_any_status(uid, r.id)).status == "active"
    await svc.aclose()


@pytest.mark.asyncio
async def test_delete_supersedes_named_target_only(tmp_path):
    llm = MockLLMAdapter([])
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid = uuid.uuid4()
    rows = await _seed(svc, uid, ["过时的记忆。", "过时的记忆。"])
    older = rows[0]
    llm.push(
        {
            "op": "DELETE",
            "target_memory_id": str(older.id),
            "confidence": 0.7,
            "rationale": "obsolete duplicate",
        }
    )
    report = await svc.reflect(uid)
    assert [d.op for d in report.decisions] == ["DELETE"]
    assert (await svc.repo.get_any_status(uid, older.id)).status == "superseded"
    assert (await svc.repo.get_any_status(uid, older.id)).superseded_by is None  # no successor
    assert await _active_contents(svc, uid) == ["过时的记忆。"]  # the newest survives
    await svc.aclose()


@pytest.mark.asyncio
async def test_unscripted_mock_skips_and_never_fabricates(tmp_path):
    llm = MockLLMAdapter([])  # exhaustion -> ProviderError inside resolution
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid = uuid.uuid4()
    await _seed(svc, uid, ["重复记忆一。", "重复记忆一。"])
    report = await svc.reflect(uid)
    assert report.clusters_found == 1
    assert report.decisions == []
    assert report.skipped[0].reason.startswith("provider_error_in_resolution")
    assert await _active_contents(svc, uid) == ["重复记忆一。", "重复记忆一。"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_three_member_cluster_is_one_call_one_merge(tmp_path):
    llm = MockLLMAdapter([])  # exactly ONE queued decision proves ONE call
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid = uuid.uuid4()
    rows = await _seed(svc, uid, ["同一条记忆。", "同一条记忆。", "同一条记忆。"])
    llm.push(
        {
            "op": "UPDATE",
            "target_memory_id": str(rows[-1].id),  # candidate = newest; invalid for coercion
            "content": "同一条记忆（合并）。",
            "confidence": 0.9,
            "rationale": "merged",
        }
    )
    report = await svc.reflect(uid)
    # coercion: target must be a NEIGHBOR (i.e., NOT the candidate itself)
    assert report.decisions == []
    assert report.skipped[0].reason.startswith("noop:")  # coerced NOOP, original preserved
    assert len(await _active_contents(svc, uid)) == 3  # nothing changed, nothing lost
    await svc.aclose()
