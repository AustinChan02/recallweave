"""Real-mode harness wiring, exercised OFFLINE (mock/fake adapters only).

The answer step and judge-usage accounting are tested here without any network:
MockLLMAdapter answers, fake adapters simulate OpenAI-compatible usage payloads.
The cost-guard abort path is `TestCostGuard` (test_eval_pricing.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.conditions import ANSWER_SYSTEM, run_harness
from evals.judge import LLMJudge, MockKeywordJudge
from evals.pricing import CostGuard, PriceRow
from recallweave.adapters.llm import MockLLMAdapter

_ITEMS = [
    {
        "qid": "q1",
        "type": "fact",
        "turns": [{"role": "user", "content": "我叫张三，住在杭州。"}],
        "question": "用户住在哪里？",
        "reference": "用户住在杭州。",
    },
    {
        "qid": "q2",
        "type": "preference",
        "turns": [{"role": "user", "content": "我最喜欢蓝山咖啡。"}],
        "question": "用户最喜欢什么咖啡？",
        "reference": "用户最喜欢蓝山咖啡。",
    },
]


def _dataset(tmp_path: Path) -> Path:
    ds = tmp_path / "ds.jsonl"
    ds.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in _ITEMS) + "\n", encoding="utf-8")
    return ds


class FakeAnswerAdapter:
    """OpenAICompatAdapter stand-in: fixed answer + usage payload for the guard."""

    provider_label = "openai_compat:fake-model"
    _model = "fake-model"
    last_usage = {"prompt_tokens": 100, "completion_tokens": 20}

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def chat_text(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return "用户住在杭州。"

    async def aclose(self) -> None: ...  # pragma: no cover


class FakeJudgeAdapter(MockLLMAdapter):
    """Adapter backing the judge: usage payload, no fake provider semantics."""

    _model = "judge-model"

    def __init__(self) -> None:
        super().__init__([], sticky='{"score": 0.5, "rationale": "ok"}')
        self.last_usage = {"prompt_tokens": 6, "completion_tokens": 3}


class TestRealModeWiring:
    @pytest.mark.asyncio
    async def test_mock_answer_adapter_goes_through_answer_step(self, tmp_path):
        runs = await run_harness(
            _dataset(tmp_path),
            ["C0"],
            MockKeywordJudge(),
            budget=600,
            tmp_root=tmp_path,
            answer_adapter=MockLLMAdapter([], sticky="住在杭州。"),
        )
        recs = runs[0].records
        assert len(recs) == 2
        for r in recs:
            assert r.judged and r.score is not None
            assert r.answer == "住在杭州。"
            assert r.answer_provider == "answered-by:mock"
            assert r.tokens_answer > 0  # local estimate for adapters without usage
            assert r.tokens_judge == 0 and r.cost_yuan_est == 0.0  # no real calls
            assert r.wall_ms_answer is not None
        m = runs[0].metrics()
        assert 0 < m["accuracy"] <= 1
        assert m["answer_wall_ms_p50"] is not None and m["answer_wall_ms_p95"] is not None

    @pytest.mark.asyncio
    async def test_answer_receives_memory_block_plus_policy_and_question_once(self, tmp_path):
        fake = FakeAnswerAdapter()
        await run_harness(
            _dataset(tmp_path),
            ["C0"],
            MockKeywordJudge(),
            budget=600,
            tmp_root=tmp_path,
            answer_adapter=fake,
        )
        assert len(fake.calls) == 2  # one answer call per (condition, item)
        systems, users = zip(*fake.calls, strict=True)
        assert all(ANSWER_SYSTEM in s for s in systems)
        q1_user, q2_user = users
        assert "QUESTION: 用户住在哪里？" in q1_user
        assert "QUESTION: 用户最喜欢什么咖啡？" in q2_user
        # the trailing question must appear exactly once (not duplicated from context)
        assert q1_user.count("QUESTION: 用户住在哪里？") == 1
        assert q2_user.count("QUESTION: 用户最喜欢什么咖啡？") == 1

    @pytest.mark.asyncio
    async def test_answer_usage_is_charged_to_the_guard(self, tmp_path):
        guard = CostGuard([PriceRow("*", 1.0, 1.0)], max_yuan=100.0)
        runs = await run_harness(
            _dataset(tmp_path),
            ["C0"],
            MockKeywordJudge(),
            budget=600,
            tmp_root=tmp_path,
            answer_adapter=FakeAnswerAdapter(),
            cost_guard=guard,
        )
        assert guard.tokens_prompt == 200 and guard.tokens_completion == 40  # 2 items charged
        for r in runs[0].records:
            assert r.cost_yuan_est > 0
            assert r.tokens_answer == 20  # provider-reported completion tokens are facts

    @pytest.mark.asyncio
    async def test_judge_usage_recorded_when_adapter_exposes_it(self, tmp_path):
        j = LLMJudge(FakeJudgeAdapter())
        runs = await run_harness(
            _dataset(tmp_path),
            ["C0"],
            j,
            budget=600,
            tmp_root=tmp_path,
            answer_adapter=MockLLMAdapter([], sticky="住在杭州。"),
        )
        for r in runs[0].records:
            assert r.judged and r.score == 0.5
            assert r.tokens_judge == 9  # 6 prompt + 3 completion, read AFTER the call

    @pytest.mark.asyncio
    async def test_echo_protocol_unchanged_when_no_answer_adapter(self, tmp_path):
        runs = await run_harness(
            _dataset(tmp_path), ["C0"], MockKeywordJudge(), budget=600, tmp_root=tmp_path
        )
        for r in runs[0].records:
            assert r.judged
            assert r.answer_provider == "local-truncate"  # payload provider labels the echo
            assert r.answer.startswith("You are a helpful assistant.")  # answer == context
            assert r.tokens_answer > 0 and r.tokens_judge == 0

    @pytest.mark.asyncio
    async def test_answer_failure_yields_unjudged_not_zero(self, tmp_path):
        class Boom:
            provider_label = "openai_compat:boom"

            async def chat_text(self, system, user):
                raise RuntimeError("network down")

        runs = await run_harness(
            _dataset(tmp_path),
            ["C0"],
            MockKeywordJudge(),
            budget=600,
            tmp_root=tmp_path,
            answer_adapter=Boom(),
        )
        assert all(
            (not r.judged) and r.score is None and "answer step failed" in r.rationale
            for r in runs[0].records
        )
        assert runs[0].metrics()["accuracy"] is None  # 0/2 judged → nothing fabricated

    @pytest.mark.asyncio
    async def test_guard_abort_leaves_no_records_committed(self, tmp_path):
        guard = CostGuard([PriceRow("*", 1000.0, 1000.0)], max_yuan=0.000001)  # absurd prices
        with pytest.raises(SystemExit, match="COST GUARD"):
            await run_harness(
                _dataset(tmp_path),
                ["C0"],
                MockKeywordJudge(),
                budget=600,
                tmp_root=tmp_path,
                answer_adapter=FakeAnswerAdapter(),
                cost_guard=guard,
            )
