"""Context Compiler contract tests: cache-stable ordering, budget split, trace."""

from __future__ import annotations

from datetime import UTC

import pytest

from recallweave.read.compiler import (
    CompileOutput,
    build_system_block,
    stable_order_key,
)

from .conftest import make_service


async def _seed(svc, user_id, memories: list[dict]):
    """Seed memories straight into the store (bypassing the mock LLM)."""
    from recallweave.db.models import Memory

    async with svc.repo.new_session() as session:
        async with session.begin():
            for spec in memories:
                m = Memory(
                    user_id=user_id,
                    session_id=None,
                    kind=spec.get("kind", "fact"),
                    content=spec["content"],
                    importance=spec.get("importance", 0.5),
                    meta={"span": spec.get("span")},
                    status="active",
                )
                session.add(m)
                await session.flush()
                emb = (await svc.embedder.embed_many([m.content]))[0]
                await svc.vector.upsert(session, m.id, emb)
    return await svc.repo.active_candidates(user_id, 200)


@pytest.mark.asyncio
async def test_profile_hash_order_beats_score_order(tmp_path, user_id):
    svc, _llm, _settings = make_service(tmp_path)
    await svc.bootstrap()
    await _seed(
        svc,
        user_id,
        [
            {"content": "用户居住在北京", "kind": "fact", "importance": 0.9},
            {"content": "用户说普通话", "kind": "fact", "importance": 0.9},
        ],
    )
    out = await svc.compile(user_id, None, messages=[{"role": "user", "content": "普通话"}])
    texts = [m["content"] for m in out.context_messages if m["role"] == "system"]
    assert len(texts) == 1
    system = texts[0]
    hash_order = sorted(["用户居住在北京", "用户说普通话"], key=stable_order_key)
    line_positions = [system.index(f"- {c}") for c in hash_order]
    assert line_positions == sorted(line_positions)
    await svc.aclose()


@pytest.mark.asyncio
async def test_system_prefix_bytes_stable_across_message_sets(tmp_path, user_id):
    """Iron rule #1: the same memory SET emits identical prefix bytes, whatever
    the volatile message history happens to be."""
    svc, _llm, _settings = make_service(tmp_path)
    await svc.bootstrap()
    await _seed(svc, user_id, [{"content": "用户居住在北京", "kind": "fact"}])

    # same seed content identical; message sets differ between calls
    m1 = [{"role": "user", "content": "请问我在哪个城市？"}]
    m2 = [
        {"role": "user", "content": "history noise one"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "请问我在哪个城市？"},
    ]
    out1 = await svc.compile(user_id, None, messages=m1)
    out2 = await svc.compile(user_id, None, messages=m2)
    assert out1.context_messages[0]["content"] == out2.context_messages[0]["content"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_working_tail_is_untouched_and_last(tmp_path, user_id):
    svc, _llm, _settings = make_service(tmp_path)
    await svc.bootstrap()
    await _seed(svc, user_id, [{"content": "用户居住在北京", "kind": "fact"}])
    msgs = [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "第二句"},
        {"role": "user", "content": "第三句：现在的问题是什么？"},
    ]
    out = await svc.compile(user_id, None, messages=msgs)
    cm = out.context_messages
    assert cm[0]["role"] == "system"
    tail = cm[1:]
    assert [t["content"] for t in tail] == [m["content"] for m in msgs]
    assert out.usage.caps["working"] == int(out.usage.budget * _settings.budget_working_pct)
    assert out.usage.working_tokens == out.usage.layers["working"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_working_tail_trimmed_to_its_share(tmp_path, user_id):
    svc, _llm, _settings = make_service(
        tmp_path,
        budget_working_pct=0.1,
        budget_profile_pct=0.3,
        budget_episodic_pct=0.5,
        context_budget_default=100,
    )
    await svc.bootstrap()
    msgs = [{"role": "user", "content": f"turn #{i} padding " + "x" * 40} for i in range(20)]
    out = await svc.compile(user_id, None, messages=msgs)
    assert out.usage.working_tokens <= out.usage.caps["working"] + 20  # soft cap tolerance
    assert out.context_messages[1]["content"].startswith("turn #")
    await svc.aclose()


@pytest.mark.asyncio
async def test_budget_slots_exhausted_trace_reason(tmp_path, user_id):
    svc, _llm, settings = make_service(
        tmp_path,
        context_budget_default=120,
        budget_system_pct=0.10,
        budget_profile_pct=0.02,
        budget_episodic_pct=0.48,
        budget_working_pct=0.40,
    )
    await svc.bootstrap()
    long_fact = "用户的职业是网络安全工程师，工作时间很长，很晚睡觉。" * 6
    await _seed(svc, user_id, [{"content": long_fact, "kind": "fact", "importance": 0.9}])
    out = await svc.compile(user_id, None, messages=[{"role": "user", "content": "我的职业？"}])
    drop = [t for t in out.trace if not t.included]
    assert drop and drop[0].reason == "budget_exhausted"
    capa = out.usage.caps
    assert capa["profile"] <= int(out.usage.budget * 0.02)
    await svc.aclose()


@pytest.mark.asyncio
async def test_trace_fields_match_proposal(tmp_path, user_id):
    svc, _llm, _settings = make_service(tmp_path)
    await svc.bootstrap()
    await _seed(
        svc,
        user_id,
        [{"content": "用户居住在北京", "kind": "fact", "span": {"message_index": 4}}],
    )
    out = await svc.compile(user_id, None, messages=[{"role": "user", "content": "哪个城市？"}])
    assert out.trace, "trace must not be empty"
    t = out.trace[0]
    for field in ("memory_id", "kind", "score", "rank", "budget_slot", "tokens", "included", "source_ref"):
        assert hasattr(t, field), field
    assert t.source_ref == "msg:4"
    assert t.budget_slot == "profile"
    assert t.rank == 1
    await svc.aclose()


@pytest.mark.asyncio
async def test_events_newest_first_and_episodic_slot(tmp_path, user_id):
    from datetime import datetime, timedelta

    from recallweave.db.models import Memory

    svc, _llm, _settings = make_service(tmp_path)
    await svc.bootstrap()
    now = datetime.now(UTC)
    m_old = Memory(
        user_id=user_id,
        kind="event",
        content="用户上周去了大连",
        importance=0.5,
        status="active",
        created_at=now - timedelta(days=5),
    )
    m_new = Memory(
        user_id=user_id,
        kind="event",
        content="用户昨天参观了故宫",
        importance=0.5,
        status="active",
        created_at=now - timedelta(days=1),
    )
    async with svc.repo.new_session() as session:
        async with session.begin():
            for m in (m_old, m_new):
                session.add(m)
                emb = (await svc.embedder.embed_many([m.content]))[0]
                await svc.vector.upsert(session, m.id, emb)
    out = await svc.compile(user_id, None, [{"role": "user", "content": "最近去过哪？"}])
    slots = {t.budget_slot for t in out.trace if t.kind == "event"}
    assert slots == {"episodic"}
    system = out.context_messages[0]["content"]
    assert system.index("用户昨天参观了故宫") < system.index("用户上周去了大连")
    await svc.aclose()


@pytest.mark.asyncio
async def test_output_is_pydantic_and_total_math(tmp_path, user_id):
    svc, _llm, _settings = make_service(tmp_path)
    await svc.bootstrap()
    await _seed(svc, user_id, [{"content": "用户居住在北京", "kind": "fact"}])
    out = await svc.compile(user_id, None, [{"role": "user", "content": "城市?"}])
    assert isinstance(out, CompileOutput)
    u = out.usage
    assert u.system_tokens == (
        u.layers["persona"] + u.layers["headers"] + u.layers["profile"] + u.layers["episodic"]
    )
    assert u.total_tokens == u.working_tokens + u.system_tokens
    assert sum(c for c in u.caps.values()) <= u.budget + 4  # int rounding tolerance
    assert out.provider == "local-compiler"
    await svc.aclose()


def test_build_system_block_shapes():
    s = build_system_block("PERSONA", ["p1"], ["e1"])
    assert s.startswith("PERSONA")
    assert "PROFILE" in s and "- p1" in s and "RECENT EVENTS" in s and "- e1" in s
    minimal = build_system_block("PERSONA", [], [])
    assert minimal == "PERSONA"
    parts = __import__("recallweave.read.compiler", fromlist=["_system_parts"])._system_parts(
        "P", ["x"], ["y"]
    )
    assert [slot for slot, _text in parts] == [
        "persona",
        "headers",
        "profile",
        "headers",
        "episodic",
    ]
