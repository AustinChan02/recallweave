"""Routing/unit checks for the write pipeline (table-driven, proposal W2).

Determinism note: hash-embedding similarities are content-dependent, so tests
that must DETERMINISTICALLY exercise the LLM route use custom thresholds
(conflict_lo=0.001, dedup_near=0.999) — routing itself is covered purely by
TestRouteCandidate. Near-dup skip uses default thresholds with IDENTICAL
content (cosine==1.0), which is threshold-independent.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from recallweave.core.errors import ProviderError, ProviderSchemaError
from recallweave.write.conflict import Decision, Neighbor, coerce_decision, route_candidate
from recallweave.write.extraction import Extracted, ExtractedBatch
from recallweave.write.pipeline import ingest

from .conftest import make_service

_LO, _NEAR = 0.80, 0.93
# settings key that forces every similarity>0.001 candidate into the LLM route
_FORCE_LLM_ROUTE = dict(conflict_lo=0.001, dedup_near=0.999)


def _neighbor(sim: float):
    mid = uuid.uuid4()
    m = type("MStub", (), {"id": mid, "kind": "fact", "content": "old content"})()
    return mid, Neighbor(memory=m, similarity=sim)


class TestRouteCandidate:
    def test_table(self):
        cases = [
            (None, "add"),          # no neighbors -> direct ADD
            (0.5, "add"),           # below conflict window
            (_LO - 0.01, "add"),
            (_LO + 0.001, "llm"),   # inside window -> LLM decides
            (0.86, "llm"),
            (_NEAR - 0.001, "llm"),
            (_NEAR, "near_dup"),    # boundary: >= 0.93 -> skip
            (0.99, "near_dup"),
        ]
        for sim, expected in cases:
            got = route_candidate(sim, conflict_lo=_LO, dedup_near=_NEAR)
            assert got == expected, f"sim={sim} -> expected {expected}"


class TestCoercion:
    def test_update_target_not_in_window_coerced_noop(self):
        _real_id, neighbor = _neighbor(0.85)
        raw = Decision(op="UPDATE", target_memory_id=uuid.uuid4(), content="x")
        coerced = coerce_decision(raw, [neighbor])
        assert coerced.op == "NOOP"
        assert "coerced" in coerced.rationale

    def test_delete_target_not_in_window_coerced_noop(self):
        _id, neighbor = _neighbor(0.85)
        raw = Decision(op="DELETE", target_memory_id=uuid.uuid4())
        assert coerce_decision(raw, [neighbor]).op == "NOOP"

    def test_update_without_content_coerced_noop(self):
        mem_id, neighbor = _neighbor(0.85)
        raw = Decision(op="UPDATE", target_memory_id=mem_id, content="  ")
        assert coerce_decision(raw, [neighbor]).op == "NOOP"

    def test_valid_update_passes_through_same_object(self):
        mem_id, neighbor = _neighbor(0.85)
        raw = Decision(op="UPDATE", target_memory_id=mem_id, content="merged", confidence=0.7)
        assert coerce_decision(raw, [neighbor]) is raw


class TestExtractSchemaStrict:
    def test_kind_is_limited(self):
        with pytest.raises(ValidationError):
            Extracted.model_validate({"kind": "gossip", "content": "xx"})

    def test_importance_bounds(self):
        with pytest.raises(ValidationError):
            Extracted.model_validate({"kind": "fact", "content": "xxx", "importance": 1.5})

    def test_content_min_length(self):
        with pytest.raises(ValidationError):
            Extracted.model_validate({"kind": "fact", "content": "x"})

    def test_batch_caps_count(self):
        with pytest.raises(ValidationError):
            ExtractedBatch.model_validate(
                {"memories": [{"kind": "fact", "content": f"m{i}"}
                              for i in range(17)]}
            )


def _run_ingest(svc, user_id, messages: list[dict]):
    return ingest(
        user_id=user_id, session_id=None, messages=messages,
        llm=svc.llm, embedder=svc.embedder, repo=svc.repo,
        vector=svc.vector, settings=svc.settings,
    )


class TestIngest:
    @pytest.mark.asyncio
    async def test_extraction_provider_error_fails_closed(self, tmp_path):
        svc, llm, _s = make_service(tmp_path)
        await svc.bootstrap()
        llm.push(ProviderError("extraction down"))
        uid = uuid.uuid4()
        report = await _run_ingest(svc, uid, [{"role": "user", "content": "hello"}])
        assert report.extracted == 0
        assert "extraction down" in report.extraction_error
        assert await svc.repo.counts(uid) == {}
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_two_facts_added(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path)
        await svc.bootstrap()
        llm.push({"memories": [
            {"kind": "fact", "content": "用户居住在北京海淀区", "importance": 0.7},
            {"kind": "fact", "content": "用户养了一只橘猫", "importance": 0.5},
        ]})
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "我住海淀，有只橘猫。"}])
        assert report.extracted == 2
        assert [d.op for d in report.decisions] == ["ADD", "ADD"]
        counts = await svc.repo.counts(user_id)
        assert counts.get("active:fact") == 2
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_in_batch_duplicates_collapse(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path)
        await svc.bootstrap()
        llm.push({"memories": [
            {"kind": "fact", "content": "用户不吃香菜", "importance": 0.5},
            {"kind": "fact", "content": "用户不吃香菜", "importance": 0.5},
        ]})
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "我不吃香菜。"}])
        assert report.extracted == 2
        assert len(report.decisions) == 1
        assert report.skipped[0].reason == "in_batch_duplicate"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_identical_content_second_ingest_is_near_dup_skip(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path)  # default thresholds: cosine(identical)==1.0 >= 0.93
        await svc.bootstrap()
        content = "用户对花生过敏"
        for _ in range(2):
            llm.push({"memories": [{"kind": "fact", "content": content, "importance": 0.8}]})
            await _run_ingest(svc, user_id, [{"role": "user", "content": content}])
        counts = await svc.repo.counts(user_id)
        assert counts.get("active:fact") == 1  # second ingest hit near_dup and was skipped
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_noop_decision_from_resolver_is_skipped(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path, **_FORCE_LLM_ROUTE)
        await svc.bootstrap()
        llm.push({"memories": [{"kind": "fact", "content": "用户会用 Vim", "importance": 0.3}]})
        await _run_ingest(svc, user_id, [{"role": "user", "content": "我用 Vim。"}])
        llm.push({"memories": [{"kind": "fact", "content": "用户经常使用 Vim 做事", "importance": 0.3}]})
        llm.push({"op": "NOOP", "confidence": 0.9, "rationale": "paraphrase of existing"})
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "我常用 Vim。"}])
        assert any(s.reason.startswith("noop:") for s in report.skipped), report
        assert not report.decisions
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_llm_update_builds_superseded_chain(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path, **_FORCE_LLM_ROUTE)
        await svc.bootstrap()
        llm.push({"memories": [{"kind": "fact", "content": "用户不喝咖啡", "importance": 0.6}]})
        await _run_ingest(svc, user_id, [{"role": "user", "content": "我不喝咖啡。"}])
        old = next(m for m in await svc.repo.active_candidates(user_id, 10) if "咖啡" in m.content)

        llm.push({"memories": [{"kind": "fact", "content": "用户胃病后不喝咖啡，喝红茶", "importance": 0.6}]})
        llm.push({"op": "UPDATE", "target_memory_id": str(old.id),
                  "content": "用户有胃病，不喝咖啡，改喝红茶", "confidence": 0.85,
                  "rationale": "health change supersedes old habit"})
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "胃病后戒了咖啡。"}])
        assert [d.op for d in report.decisions] == ["UPDATE"]
        old_after = await svc.repo.get_any_status(user_id, old.id)
        new_id = uuid.UUID(report.decisions[0].memory_id)
        assert old_after.status == "superseded"
        assert str(old_after.superseded_by) == str(new_id)
        refreshed = await svc.repo.by_ids(user_id, [new_id])
        assert refreshed[new_id].content == "用户有胃病，不喝咖啡，改喝红茶"
        active_after = await svc.repo.active_candidates(user_id, 10)
        coffee_active = [m for m in active_after if "咖啡" in m.content]
        assert len(coffee_active) == 1 and coffee_active[0].id == new_id
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_llm_delete_closes_target(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path, **_FORCE_LLM_ROUTE)
        await svc.bootstrap()
        llm.push({"memories": [{"kind": "fact", "content": "用户租住在加州", "importance": 0.4}]})
        await _run_ingest(svc, user_id, [{"role": "user", "content": "我住在加州。"}])
        old = (await svc.repo.active_candidates(user_id, 10))[0]

        llm.push({"memories": [{"kind": "fact", "content": "用户已从加州搬离", "importance": 0.4}]})
        llm.push({"op": "DELETE", "target_memory_id": str(old.id),
                  "confidence": 0.8, "rationale": "stale residency"})
        await _run_ingest(svc, user_id, [{"role": "user", "content": "我搬离加州了。"}])
        after = await svc.repo.get_any_status(user_id, old.id)
        assert after.status == "superseded"
        assert after.superseded_by is None
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_coerced_noop_when_target_invalid(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path, **_FORCE_LLM_ROUTE)
        await svc.bootstrap()
        llm.push({"memories": [{"kind": "fact", "content": "用户会用 Vim", "importance": 0.3}]})
        await _run_ingest(svc, user_id, [{"role": "user", "content": "我用 Vim。"}])
        llm.push({"memories": [{"kind": "fact", "content": "用户经常使用 Vim 做事", "importance": 0.3}]})
        # invalid (not-in-window) target -> coerce_decision must turn it into NOOP
        llm.push({"op": "DELETE", "target_memory_id": str(uuid.uuid4()),
                  "confidence": 0.9, "rationale": "wants to delete"})
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "我常用 Vim。"}])
        noop_skips = [s for s in report.skipped if s.reason.startswith("noop:")]
        assert noop_skips and "coerced" in noop_skips[0].reason
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_provider_error_in_resolution_skips_candidate_only(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path, **_FORCE_LLM_ROUTE)
        await svc.bootstrap()
        llm.push({"memories": [{"kind": "fact", "content": "用户住北京", "importance": 0.4}]})
        await _run_ingest(svc, user_id, [{"role": "user", "content": "住北京。"}])
        llm.push({"memories": [
            {"kind": "fact", "content": "用户住北京海淀区", "importance": 0.4},
            {"kind": "fact", "content": "用户养了三只猫", "importance": 0.4},
        ]})
        llm.push(ProviderError("resolver down"))
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "海淀。猫。"}])
        reasons = [s.reason for s in report.skipped]
        assert "provider_error_in_resolution" in reasons
        assert report.extraction_error is None
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_extraction_schema_failure_fails_closed(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path)
        await svc.bootstrap()
        llm.push(ProviderSchemaError("bad payload"))
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "hi"}])
        assert report.extraction_error and "bad payload" in report.extraction_error
        assert await svc.repo.counts(user_id) == {}
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_add_route_never_calls_resolver(self, tmp_path, user_id):
        """No neighbors -> ADD; the resolver LLM call must NOT happen
        (verified by an untouched sentinel left in the mock FIFO queue)."""
        svc, llm, _s = make_service(tmp_path)
        await svc.bootstrap()
        llm.push({"memories": [{"kind": "fact", "content": "用户喜欢瑜伽", "importance": 0.5}]})
        llm.push("SENTINEL-untouched")  # only a resolver call would consume this
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "用户喜欢瑜伽。"}])
        assert report.decisions and report.decisions[0].op == "ADD"
        assert llm._outputs == ["SENTINEL-untouched"]
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_extraction_without_script_fails_closed(self, tmp_path, user_id):
        svc, llm, _s = make_service(tmp_path)
        await svc.bootstrap()
        report = await _run_ingest(svc, user_id, [{"role": "user", "content": "用户喜欢瑜伽"}])
        assert report.extraction_error is not None
        assert await svc.repo.counts(user_id) == {}
        await svc.aclose()
