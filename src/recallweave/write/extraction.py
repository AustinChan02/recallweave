"""Stage 1: fact extraction (LLM, strict JSON, fail-closed).

Prompt extracts candidate memories from the recent transcript ONLY (proposal
budget: last k turns). A parse/schema failure retries at the adapter; if it
still fails the batch is skipped and reported — never half-committed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..adapters.llm import LLMAdapter

Kind = Literal["fact", "preference", "event", "instruction"]


class Extracted(BaseModel):
    kind: Kind
    content: str = Field(min_length=2, max_length=1000)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    span: dict | None = None


class ExtractedBatch(BaseModel):
    memories: list[Extracted] = Field(default_factory=list, max_length=16)


EXTRACT_SYSTEM = """\
You are the memory-extraction engine of a long-term-memory service.
From the transcript below, extract concise user-level facts worth remembering:
- kind must be one of: fact (stable facts/state), preference (likes/dislikes/rules),
  event (dated happenings), instruction (standing orders for the assistant).
- Prefer short, self-contained sentences in the user's own language.
- importance in [0,1]: 1.0 = life-critical (health/allergies/legals), 0.5 = routine.
- span may reference the source message index as {"message_index": <int>}.
- Cache-stability advisory: when a fact embeds a time, prefer an absolute anchor
  ("2024-05 起", "每周一") over relative wording ("昨天", "三天前"); the context
  compiler never emits wall-clock relative phrases (ADR-003 rule #2).
- Do NOT invent content; skip opinions about abstract topics with low signal.
- Do NOT extract the assistant's own statements.
Output: JSON object {"memories": [...]} and nothing else."""
# (docs/13 §6 default: extraction always runs at temperature 0 via the adapter.)


def transcript_block(messages: list[dict], k: int) -> str:
    recent = [m for m in messages if m.get("content")][-k:]
    lines = [f"- [{i}] {m.get('role', 'user')}: {m.get('content')}" for i, m in enumerate(recent)]
    return "\n".join(lines) or ""


def history_marker() -> str:
    return "TRANSCRIPT:"


def extract_user_prompt(messages: list[dict], k: int) -> str:
    return f"{history_marker()}\n{transcript_block(messages, k)}\n\nExtract memories now. JSON only."


async def extract_memories(llm: LLMAdapter, messages: list[dict], k: int) -> list[Extracted]:
    obj = await llm.chat_json(EXTRACT_SYSTEM, extract_user_prompt(messages, k), ExtractedBatch)
    return [Extracted.model_validate(m) for m in obj["memories"]]
