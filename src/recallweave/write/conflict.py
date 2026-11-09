"""Stage 2+3: dedup gate & LLM conflict resolution (ADD/UPDATE/DELETE/NOOP).

Threshold routing (proposal §4.1):
    max_similarity >= dedup_near       -> NOOP (near-duplicate, skipped)
    no neighbor / <= conflict_lo       -> ADD outright (no LLM call)
    conflict_lo < sim < dedup_near     -> LLM decision on the candidate+neighbors

Coercion rule (fail-closed): an UPDATE/DELETE that does not name a valid
neighbor inside the window is coerced to NOOP with the rationale preserved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ..adapters.llm import LLMAdapter
from ..db.models import Memory
from .extraction import Extracted

Op = Literal["ADD", "UPDATE", "DELETE", "NOOP"]


@dataclass
class Neighbor:
    memory: Memory
    similarity: float


class Decision(BaseModel):
    op: Op
    target_memory_id: uuid.UUID | None = None
    content: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""


RESOLVE_SYSTEM = """\
You are the conflict-resolution engine of a long-term-memory service.
You receive ONE candidate memory and its nearest existing memories.
Decide exactly one operation:
- ADD: candidate states new information.               -> content stays unchanged.
- UPDATE: candidate REWRITES a target memory.          -> set target_memory_id to that
  neighbor's id and provide the merged `content`.
- DELETE: an existing memory is now FALSE/obsolete.    -> set target_memory_id; do not write content.
- NOOP: restatement/paraphrase of an existing memory.  -> prefer this over a risky rewrite.
Rules:
- If information changed over time, DELETE/UPDATE the STALE memory and the new
  fact must remain explicit and time-consistent; never leave both as active truth.
- target_memory_id, when set, MUST be one of the provided neighbor ids.
- Output JSON object {"op", "target_memory_id", "content", "confidence", "rationale"}
  and nothing else."""


def route_candidate(max_sim: float | None, *, conflict_lo: float, dedup_near: float) -> str:
    if max_sim is not None and max_sim >= dedup_near:
        return "near_dup"
    if max_sim is None or max_sim < conflict_lo:
        return "add"
    return "llm"


def coerce_decision(raw: Decision, neighbors: list[Neighbor]) -> Decision:
    """Fail-closed coercion: UPDATE/DELETE must reference a real neighbor."""
    if raw.op in ("UPDATE", "DELETE"):
        valid_ids = {n.memory.id for n in neighbors}
        if raw.target_memory_id not in valid_ids:
            return Decision(
                op="NOOP",
                confidence=raw.confidence,
                rationale=f"coerced: target {raw.target_memory_id} not in window "
                f"| original: {raw.op} {raw.rationale}",
            )
        if raw.op == "UPDATE" and (raw.content or "").strip() == "":
            return Decision(
                op="NOOP",
                confidence=raw.confidence,
                rationale=f"coerced: UPDATE without content | original: {raw.rationale}",
            )
    return raw


async def resolve_with_llm(llm: LLMAdapter, candidate: Extracted, neighbors: list[Neighbor]) -> Decision:
    if not neighbors:
        return Decision(op="ADD", rationale="no neighbors in window", confidence=1.0)
    neighbor_lines = "\n".join(
        f'- id={n.memory.id} sim={n.similarity:.3f} kind={n.memory.kind} content="{n.memory.content}"'
        for n in neighbors
    )
    candidate_obj = {
        "kind": candidate.kind,
        "content": candidate.content,
        "importance": candidate.importance,
        "span": candidate.span,
    }
    user = (
        "CANDIDATE:\n"
        f"{candidate_obj}\n\n"
        f"NEIGHBORS (nearest first):\n{neighbor_lines}\n\n"
        "Decide the operation. JSON only."
    )
    raw = Decision.model_validate(await llm.chat_json(RESOLVE_SYSTEM, user, Decision))
    return coerce_decision(raw, neighbors)
