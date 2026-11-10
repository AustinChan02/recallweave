"""P1.a session summaries (§4.4): trigger thresholds, scripted-vs-mock
semantics, append-only seq, compiler episodic takeover.

Honesty rule under test: a mock adapter that produces NO text must never
fabricate a summary (ProviderError → summarized=False with a note).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from recallweave.adapters.llm import MockLLMAdapter
from recallweave.core.config import make_settings
from recallweave.db.session import make_engine, prep_sqlite_path
from recallweave.read.compiler import stable_order_key
from recallweave.service.service import RecallWeaveService


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


def _turns(n: int) -> list[dict]:
    return [{"role": "user", "content": f"第{i}轮：我提到一个事实 {i}。"} for i in range(n)]


@pytest.mark.asyncio
async def test_zero_turns_and_below_threshold_are_clean_noops(tmp_path):
    llm = MockLLMAdapter([])  # prove NO llm access happens: empty queue must stay unused
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid: uuid.UUID = uuid.uuid4()
    out = await svc.summarize_session(uid, uuid.uuid4())  # unknown session
    assert out == {
        "session_id": out["session_id"],
        "summarized": False,
        "turns": 0,
        "turns_covered": 0,
        "provider": "mock",
        "note": "no turns ingested for this session",
    }
    await svc.ingest(uid, None, _turns(3))  # session-less ingest bumps nothing usable
    await svc.aclose()


@pytest.mark.asyncio
async def test_trigger_threshold_first_summary_and_append_only(tmp_path):
    """A scripted mock supplies the summary TEXT on chat_text (disclosed as
    provider=mock — scripted, not measured provider quality)."""
    llm = MockLLMAdapter(
        [{"memories": [{"kind": "fact", "content": "用户正在筹备搬家。", "importance": 0.6}]}],
        sticky="2026-11 起：用户在筹备搬家，偏好周末处理。",
    )
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid, sid = uuid.uuid4(), uuid.uuid4()
    await svc.ingest(uid, sid, _turns(23))
    out = await svc.summarize_session(uid, sid)  # 23 turns ≥ 20 → trigger
    assert out["summarized"] is True and out["seq"] == 1
    assert out["turns_covered"] == 23
    assert out["provider"] == "mock"

    again = await svc.summarize_session(uid, sid)  # 0 new turns → no-op
    assert again["summarized"] is False and "below trigger" in again["note"]

    forced = await svc.summarize_session(uid, sid, force=True)  # append-only
    assert forced["summarized"] is True and forced["seq"] == 2
    assert [r.seq for r in await svc.sum_repo.recent_summaries(uid)] == [1, 2]
    await svc.aclose()


@pytest.mark.asyncio
async def test_unscripted_mock_never_fabricates_summary(tmp_path):
    llm = MockLLMAdapter(
        [{"memories": [{"kind": "fact", "content": "用户喜欢 Java。", "importance": 0.6}]}],
    )  # queue holds ONLY the extraction script; chat_text raises → caught
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid, sid = uuid.uuid4(), uuid.uuid4()
    await svc.ingest(uid, sid, [{"role": "user", "content": "我喜欢 Java。"}])
    await svc.sum_repo.bump_turns(uid, sid, 21)
    out = await svc.summarize_session(uid, sid)
    assert out["summarized"] is False
    assert "never fabricated" in out["note"]
    assert out["provider"] == "mock"
    await svc.aclose()


@pytest.mark.asyncio
async def test_compiler_episodic_takes_summaries_chronologically(tmp_path):
    llm = MockLLMAdapter(
        [{"memories": [{"kind": "event", "content": "2026-05-01 我参加了毕业典礼。", "importance": 0.6}]}],
        sticky="s",
    )
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid, sid = uuid.uuid4(), uuid.uuid4()
    # inserted chronologically: first the seq-1 summary, then the seq-2 one
    await svc.sum_repo.add_summary(
        uid, sid, content="S1 先发生", token_est=4, provider="mock", turns_covered=20
    )
    await svc.sum_repo.add_summary(
        uid, sid, content="S2 后发生", token_est=4, provider="mock", turns_covered=40
    )
    await svc.ingest(uid, sid, [{"role": "user", "content": "说个事件。"}])
    out = await svc.compile(uid, sid, [{"role": "user", "content": "我最近怎么样？"}])
    system = out.context_messages[0]["content"]
    assert system.find("S1 先发生") < system.find("S2 后发生")
    assert "RECENT SESSION SUMMARIES" in system
    assert "RECENT EVENTS:" not in system
    kinds = {t.kind for t in out.trace}
    assert "summary" in kinds
    summary_traces = [t for t in out.trace if t.kind == "summary"]
    assert {t.memory_id for t in summary_traces} == {
        f"summary:{stable_order_key('S1 先发生')}",
        f"summary:{stable_order_key('S2 后发生')}",
    }
    assert any(t.reason == "slot_consumed_by_summaries" for t in out.trace)
    await svc.aclose()


@pytest.mark.asyncio
async def test_compiler_without_summaries_falls_back_to_events(tmp_path):
    llm = MockLLMAdapter(
        [{"memories": [{"kind": "event", "content": "2026-05-01 我参加了毕业典礼。", "importance": 0.6}]}],
        sticky="s",
    )
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid, sid = uuid.uuid4(), uuid.uuid4()
    await svc.ingest(uid, sid, [{"role": "user", "content": "说个事件。"}])
    out = await svc.compile(uid, sid, [{"role": "user", "content": "我最近怎么样？"}])
    system = out.context_messages[0]["content"]
    assert "RECENT EVENTS:" in system  # pre-P1.a behavior preserved (eval compat)
    assert "RECENT SESSION SUMMARIES" not in system
    await svc.aclose()


@pytest.mark.asyncio
async def test_ingest_reports_turn_counts(tmp_path):
    llm = MockLLMAdapter(
        [{"memories": [{"kind": "fact", "content": "事实甲。", "importance": 0.6}]}],
        sticky="s",
    )
    svc = _svc(tmp_path, llm)
    await svc.bootstrap()
    uid, sid = uuid.uuid4(), uuid.uuid4()
    report = await svc.ingest(uid, sid, [*_turns(2), {"role": "assistant", "content": "好的。"}])
    assert report.turns_ingested == 3
    # empty-content rows are not turns
    report2 = await svc.ingest(uid, sid, [{"role": "user", "content": "   "}])
    assert report2.turns_ingested == 0
    await svc.aclose()
