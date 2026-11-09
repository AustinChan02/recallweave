"""Full user-journey e2e: ingest(s) -> conflict chain -> search -> compile.
Also: /stats, get/delete via the service layer (transport tested separately)."""

from __future__ import annotations

import uuid

import pytest

from .conftest import make_service


@pytest.mark.asyncio
async def test_full_journey(tmp_path, user_id):
    svc, llm, _s = make_service(
        tmp_path, conflict_lo=0.001, dedup_near=0.999, context_budget_default=900
    )
    await svc.bootstrap()

    # ---- turn 1: two ADD facts (no neighbors) ------------------------------
    llm.push({"memories": [
        {"kind": "fact", "content": "用户是后端工程师，主要写 Java", "importance": 0.7},
        {"kind": "fact", "content": "用户在学粤语", "importance": 0.4},
    ]})
    r1 = await svc.ingest(user_id, None, [
        {"role": "user", "content": "我是后端工程师，写 Java；最近在学粤语。"}
    ])
    assert [d.op for d in r1.decisions] == ["ADD", "ADD"]
    fact_id = next(d.memory_id for d in r1.decisions if "Java" in d.content)

    # ---- turn 2: UPDATE the Java fact (conflict chain) ---------------------
    java_mem = await svc.repo.get_any_status(user_id, uuid.UUID(fact_id))
    llm.push({"memories": [
        {"kind": "fact", "content": "用户改做风控引擎项目", "importance": 0.7},
    ]})
    llm.push({
        "op": "UPDATE", "target_memory_id": str(java_mem.id),
        "content": "用户是后端工程师，正在开发风控引擎系统", "confidence": 0.88,
        "rationale": "新业务方向并入职业事实",
    })
    r2 = await svc.ingest(user_id, None, [
        {"role": "user", "content": "我最近在做一个风控引擎的项目。"}
    ])
    assert [d.op for d in r2.decisions] == ["UPDATE"]
    old = await svc.repo.get_any_status(user_id, java_mem.id)
    assert old.status == "superseded" and old.superseded_by is not None

    # ---- read: search() returns the LATEST version ----------------------------------
    got = await svc.search(user_id, "用户做什么工作", top_k=3)
    assert got["provider"] == "local-hybrid"
    assert got["items"] and got["items"][0]["memory"]["id"] == str(old.superseded_by)
    for item in got["items"]:
        assert item["memory"]["status"] == "active"
        assert set(item["lanes"]) <= {"vector", "lexical", "recency"}

    # ---- read: compile() hits budget + trace + stability --------------------
    out = await svc.compile(user_id, None, [
        {"role": "user", "content": "用户做什么工作？"},
    ])
    sys_text = out.context_messages[0]["content"]
    assert "风控引擎" in sys_text          # newest version wins
    assert out.usage.total_tokens <= out.usage.budget + 60
    slots = {t.budget_slot for t in out.trace}
    assert slots <= {"profile", "episodic"}

    # ---- admin: stats + retrieve + soft delete ------------------------------
    stats = await svc.stats(user_id)
    assert stats["counts_by_status_kind"]["active:fact"] >= 1
    one = await svc.get_memory(user_id, uuid.UUID(str(old.superseded_by)))
    assert one["content"].startswith("用户是后端工程师")
    deleted = await svc.delete_memory(user_id, uuid.UUID(str(old.superseded_by)))
    assert deleted["deleted"] is True
    hard_gone = await svc.search(user_id, "风控引擎", top_k=3)
    assert all(i["memory"]["id"] != str(old.superseded_by) for i in hard_gone["items"])
    await svc.aclose()


@pytest.mark.asyncio
async def test_user_isolation(tmp_path, user_id):
    svc, llm, _s = make_service(tmp_path)
    await svc.bootstrap()
    other = uuid.uuid4()
    llm.push({"memories": [{"kind": "fact", "content": "A 用户住在北京", "importance": 0.5}]})
    await svc.ingest(user_id, None, [{"role": "user", "content": "A 用户住北京。"}])
    llm.push({"memories": [{"kind": "fact", "content": "B 用户住在成都", "importance": 0.5}]})
    await svc.ingest(other, None, [{"role": "user", "content": "B 用户住成都。"}])
    got_a = await svc.search(user_id, "用户住在哪里", top_k=5)
    got_b = await svc.search(other, "用户住在哪里", top_k=5)
    contents_a = {i["memory"]["content"] for i in got_a["items"]}
    contents_b = {i["memory"]["content"] for i in got_b["items"]}
    assert contents_a and contents_b
    assert not (contents_a & contents_b)
    await svc.aclose()
