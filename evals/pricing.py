"""Cost estimation for the eval budget guard (configs/prices.toml).

Design: (a) tokens come from the PROVIDER's usage payload (facts);
(b) prices are upper-bound placeholders until replaced with console values —
the guard safe-fails EARLY, it never under-reports spend;
(c) running the guard past its limit raises immediately (fail-closed).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PriceRow:
    model: str
    input_yuan_per_m: float
    output_yuan_per_m: float


def load_price_table(path: Path | str | None = None) -> list[PriceRow]:
    src = Path(path) if path else None
    if src is None:
        repo_root = Path(__file__).resolve().parents[1]
        src = repo_root / "configs" / "prices.toml"
    data = tomllib.loads(src.read_text(encoding="utf-8"))
    return [
        PriceRow(
            model=row["model"],
            input_yuan_per_m=float(row["input_yuan_per_m"]),
            output_yuan_per_m=float(row["output_yuan_per_m"]),
        )
        for row in data.get("models", [])
    ]


def price_for(table: list[PriceRow], model: str) -> PriceRow:
    for row in table:
        if row.model == model:
            return row
    for row in table:
        if row.model == "*":
            return row
    raise KeyError(f"no price row for {model!r} and no fallback('*') in the table")


def estimate_cost_yuan(
    table: list[PriceRow],
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    row = price_for(table, model)
    return (prompt_tokens * row.input_yuan_per_m + completion_tokens * row.output_yuan_per_m) / 1_000_000


class CostGuard:
    """Cumulative upper-bound spend tracker; aborts a run when exceeded."""

    def __init__(self, table: list[PriceRow], max_yuan: float) -> None:
        self.table = table
        self.max_yuan = max_yuan
        self.spent_yuan = 0.0
        self.tokens_prompt = 0
        self.tokens_completion = 0

    def charge(self, model: str, usage: dict) -> float:
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        self.tokens_prompt += pt
        self.tokens_completion += ct
        cost = estimate_cost_yuan(self.table, model, pt, ct)
        self.spent_yuan += cost
        if self.spent_yuan > self.max_yuan:
            raise SystemExit(
                f"COST GUARD: cumulative upper-bound estimate {self.spent_yuan:.4f}¥ "
                f"> max {self.max_yuan}¥ — aborting BEFORE writing any result file. "
                "Check usage on the provider console; raise --max-cost-yuan only if intended."
            )
        return cost
