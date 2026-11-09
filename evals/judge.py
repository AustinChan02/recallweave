"""Judge interface + mock judge (protocol §7.3: unjudged never enters the denominator).

Judge returns a `Judgment`; an unjudged case RAISES rather than returning a
score of 0/1 — the harness must count it separately (`judged/total`).

`JudgeName=mock` is a deterministic keyword-coverage judge for the offline
smoke run ONLY. Its scores say nothing about real LLM quality.
"""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, Field

from recallweave.adapters.llm import LLMAdapter
from recallweave.core.errors import ProviderError


class Judgment(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
    judge_label: str  # "mock:keyword-coverage" | "openai_compat:<model>" ...


class Judge(Protocol):
    async def judge(self, *, question: str, reference: str, answer: str) -> Judgment: ...


_STOP = {"的", "了", "是", "吗", "？", "，", "。", "和", "与", "在", "什么"}


def _keywords(text: str) -> list[str]:
    latin = [w for w in re.findall(r"[a-zA-Z]+|\d+", text) if w.lower() not in _STOP]
    cjk = [c for c in re.findall(r"[\u4e00-\u9fff]{1,4}", text) if c not in _STOP]
    return latin + cjk


class MockKeywordJudge:
    """Deterministic: score = (number of reference keywords present in the answer)
    / total. Unjudged when reference yields no keywords — a judge cannot grade
    with an empty rubric, and silently scoring 0/1 would be fabrication."""

    judge_label = "mock:keyword-coverage"

    async def judge(self, *, question: str, reference: str, answer: str) -> Judgment:
        keys = [k for k in _keywords(reference) if k]
        if not keys:
            raise ProviderError("mock judge: reference has no keywords — unjudgable")
        hits = sum(1 for k in keys if k.lower() in answer.lower())
        return Judgment(
            score=hits / len(keys),
            rationale=f"{hits}/{len(keys)} reference keywords present",
            judge_label=self.judge_label,
        )


class JudgeVerdict(BaseModel):
    """Strict schema the LLM judge must return (validates provider output)."""

    score: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class LLMJudge:
    """Rubric judge over any LLMAdapter (proposal: cheap model + human spot check)."""

    SYSTEM = """You grade answers to memory-QA questions with one number in [0,1]:
1.0 complete and correct; 0.5 partially correct; 0.0 wrong or contradicting.
Judge ONLY correctness of the answer vs the reference. Ignore style/language.
Output JSON object {"score": <float>, "rationale": "<short>"} and nothing else."""

    def __init__(self, adapter: LLMAdapter, schema: type[BaseModel] = JudgeVerdict) -> None:
        self.adapter = adapter
        self.schema = schema
        self.judge_label = getattr(adapter, "provider_label", "unknown-judge")

    async def judge(self, *, question: str, reference: str, answer: str) -> Judgment:
        graded = f"QUESTION: {question}\nREFERENCE: {reference}\nANSWER: {answer}\nGrade now."
        judgment = await self.adapter.chat_json(self.SYSTEM, graded, self.schema)
        obj = Judgment(
            score=float(judgment["score"]),
            rationale=str(judgment.get("rationale", "")),
            judge_label=self.judge_label,
        )
        return obj


def make_judge(name: str, llm: LLMAdapter | None = None, schema: type[BaseModel] | None = None):
    if name == "mock":
        return MockKeywordJudge()
    if name == "llm":
        if llm is None or schema is None:
            raise ProviderError("llm judge requires an adapter + judgment schema")
        return LLMJudge(llm, schema)
    raise ProviderError(f"unknown judge: {name}")
