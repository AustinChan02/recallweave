"""P1.b §4.5 forgetting = archival, NOT deletion: boundaries, recoverability.

Semantics under test:
  * BOTH boundaries are strict `<` — exactly-at-tau and exactly-at-importance
    rows are KEPT (forgetting errs on retention);
  * superseded/deleted rows are never archived (chain + explicit deletion are
    the only paths out of 'active');
  * archived rows keep every column and can be restored; the audit trail is
    additive meta (never a rewrite of history);
  * archived rows drop out of retrieval and show up in /v1/stats.
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

_LATE = datetime(2026, 1, 1, tzinfo=UTC)  # explicit last_accessed for exact boundaries


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


async def _raw(svc, user_id, *, importance, last_accessed=_LATE, created_at=None, status="active"):
    """Insert a Memory row with EXPLICIT access times (exact boundary control)."""
    m = Memory(
        user_id=user_id,
        session_id=None,
        kind="fact",
        content=f"row-{uuid.uuid4().hex[:8]}",
        importance=importance,
        status=status,
        last_accessed=last_accessed,
        created_at=created_at,
        meta={},
    )
    maker = async_sessionmaker(svc.repo.engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        session.add(m)
    return m.id


async def _row(svc, user_id, mid):
    return await svc.repo.get_any_status(user_id, mid)


class TestArchiveBoundaries:
    @pytest.mark.asyncio
    async def test_tau_boundary_is_strict(self, tmp_path):
        llm = MockLLMAdapter([])
        svc = _svc(tmp_path, llm)
        await svc.bootstrap()
        uid = uuid.uuid4()
        mid = await _raw(svc, uid, importance=0.1)
        assert await _row(svc, uid, mid) is not None

        # exactly at tau: kept (strict <)
        kept = await svc.repo.archive_stale(now=_LATE + timedelta(days=30), user_id=uid)
        assert kept == []
        assert (await _row(svc, uid, mid)).status == "active"

        # one second past tau: archived
        archived = await svc.repo.archive_stale(now=_LATE + timedelta(days=30, seconds=1), user_id=uid)
        assert archived == [mid]
        assert (await _row(svc, uid, mid)).status == "archived"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_importance_boundary_is_strict(self, tmp_path):
        llm = MockLLMAdapter([])
        svc = _svc(tmp_path, llm)
        await svc.bootstrap()
        uid = uuid.uuid4()
        at_cut = await _raw(svc, uid, importance=0.3)
        below = await _raw(svc, uid, importance=0.299)
        high = await _raw(svc, uid, importance=0.9)
        out = await svc.repo.archive_stale(now=_LATE + timedelta(days=30, seconds=1), user_id=uid)
        assert out == [below]
        assert (await _row(svc, uid, at_cut)).status == "active"
        assert (await _row(svc, uid, high)).status == "active"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_only_active_rows_qualify(self, tmp_path):
        llm = MockLLMAdapter([])
        svc = _svc(tmp_path, llm)
        await svc.bootstrap()
        uid = uuid.uuid4()
        sup = await _raw(svc, uid, importance=0.1, status="superseded")
        gone = await _raw(svc, uid, importance=0.1, status="deleted")
        act = await _raw(svc, uid, importance=0.1)
        out = await svc.repo.archive_stale(now=_LATE + timedelta(days=60), user_id=uid)
        assert out == [act]
        assert (await _row(svc, uid, sup)).status == "superseded"
        assert (await _row(svc, uid, gone)).status == "deleted"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_never_accessed_ages_from_created_at(self, tmp_path):
        llm = MockLLMAdapter([])
        svc = _svc(tmp_path, llm)
        await svc.bootstrap()
        uid = uuid.uuid4()
        old = await _raw(
            svc, uid, importance=0.299, last_accessed=None, created_at=_LATE - timedelta(days=31)
        )
        fresh = await _raw(
            svc, uid, importance=0.299, last_accessed=None, created_at=_LATE - timedelta(days=5)
        )
        out = await svc.repo.archive_stale(now=_LATE, user_id=uid)
        assert out == [old]
        assert (await _row(svc, uid, fresh)).status == "active"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_audit_trail_in_meta(self, tmp_path):
        llm = MockLLMAdapter([])
        svc = _svc(tmp_path, llm)
        await svc.bootstrap()
        uid = uuid.uuid4()
        mid = await _raw(svc, uid, importance=0.1)
        awaited = await svc.repo.archive_stale(now=_LATE + timedelta(days=40), user_id=uid)
        assert awaited == [mid]
        row = await _row(svc, uid, mid)
        assert row.meta["forgotten"]["reason"] == "stale+low-importance"
        assert row.meta["forgotten"]["tau_days"] == 30.0  # the JOB's param, not elapsed time
        assert row.meta["forgotten"]["importance_lt"] == 0.3
        await svc.aclose()


class TestRestore:
    @pytest.mark.asyncio
    async def test_restore_brings_the_row_back_active(self, tmp_path):
        llm = MockLLMAdapter([])
        svc = _svc(tmp_path, llm)
        await svc.bootstrap()
        uid = uuid.uuid4()
        mid = await _raw(svc, uid, importance=0.1)
        await svc.repo.archive_stale(now=_LATE + timedelta(days=40), user_id=uid)
        assert (await _row(svc, uid, mid)).status == "archived"

        assert await svc.repo.restore(uid, mid) is True
        row = await _row(svc, uid, mid)
        assert row.status == "active"
        assert "restored_at" in row.meta and "forgotten" in row.meta  # additive audit
        # retrievable again (compare by id — different sessions, different objects)
        assert [m.id for m in await svc.repo.active_candidates(uid, 10)] == [mid]

        # second restore refused: not archived anymore
        assert await svc.repo.restore(uid, mid) is False
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_restore_refuses_non_archived_and_unknown(self, tmp_path):
        llm = MockLLMAdapter([])
        svc = _svc(tmp_path, llm)
        await svc.bootstrap()
        uid = uuid.uuid4()
        active = await _raw(svc, uid, importance=0.1)
        sup = await _raw(svc, uid, importance=0.1, status="superseded")
        gone = await _raw(svc, uid, importance=0.1, status="deleted")
        assert await svc.repo.restore(uid, active) is False
        assert await svc.repo.restore(uid, sup) is False
        assert await svc.repo.restore(uid, gone) is False
        assert await svc.repo.restore(uid, uuid.uuid4()) is False  # unknown id
        await svc.aclose()


class TestLayeredNotDeleted:
    @pytest.mark.asyncio
    async def test_archival_hides_from_retrieval_without_deleting(self, tmp_path):
        llm = MockLLMAdapter(
            [{"memories": [{"kind": "fact", "content": "我喜欢 Java。", "importance": 0.1}]}],
        )
        svc = _svc(tmp_path, llm)
        await svc.bootstrap()
        uid, sid = uuid.uuid4(), uuid.uuid4()
        await svc.ingest(uid, sid, [{"role": "user", "content": "我喜欢 Java。"}])
        rows = await svc.repo.active_candidates(uid, 10)
        assert [r.content for r in rows] == ["我喜欢 Java。"]

        archived = await svc.repo.archive_stale(now=datetime.now(UTC) + timedelta(days=31))
        assert len(archived) == 1

        # ① 分层，不是删除：row itself is fully intact
        row = await svc.repo.get_any_status(uid, archived[0])
        assert row is not None and row.status == "archived" and row.content == "我喜欢 Java。"
        # ② retrieval no longer sees it
        assert await svc.repo.active_candidates(uid, 10) == []
        out = await svc.compile(uid, sid, [{"role": "user", "content": "我喜欢什么语言？"}])
        assert "我喜欢 Java。" not in out.context_messages[0]["content"]
        # ③ /v1/stats surfaces the archived tier
        stats = await svc.stats(uid)
        assert stats["counts_by_status_kind"].get("archived") == 1
        assert stats["archived_total"] == 1
        # ④ restore path closes the loop
        assert await svc.repo.restore(uid, archived[0]) is True
        after = await svc.stats(uid)
        assert "archived" not in after["counts_by_status_kind"]
        assert after["archived_total"] == 0
        await svc.aclose()
