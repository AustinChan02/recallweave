"""CostGuard / price-table semantics (the fail-closed budget guard, TODO P0 1.3)."""

from __future__ import annotations

import pytest

from evals.pricing import CostGuard, PriceRow, estimate_cost_yuan, load_price_table, price_for


class TestPriceTable:
    def test_default_table_has_fallback_row(self):
        rows = load_price_table()
        assert [r.model for r in rows] == ["deepseek-flash", "deepseek-v4-pro", "*"]

    def test_price_for_exact_then_fallback(self):
        table = [PriceRow("known", 3.0, 7.0), PriceRow("*", 11.0, 13.0)]
        assert price_for(table, "known") == table[0]
        assert price_for(table, "anything-else") == table[1]

    def test_price_for_without_fallback_raises(self):
        with pytest.raises(KeyError):
            price_for([PriceRow("known", 3.0, 7.0)], "unknown")


class TestEstimate:
    def test_math(self):
        assert estimate_cost_yuan([PriceRow("m", 2.0, 8.0)], "m", 1_000_000, 0) == pytest.approx(2.0)
        assert estimate_cost_yuan([PriceRow("m", 2.0, 8.0)], "m", 0, 1) == pytest.approx(8e-6)


class TestCostGuard:
    def test_accumulates_and_allows_under_cap(self):
        g = CostGuard([PriceRow("*", 1.0, 1.0)], max_yuan=1.0)
        assert g.charge("m", {"prompt_tokens": 100, "completion_tokens": 10}) == pytest.approx(
            110 / 1_000_000
        )
        assert g.tokens_prompt == 100 and g.tokens_completion == 10

    def test_aborts_when_cumulative_upper_bound_exceeds_cap(self):
        g = CostGuard([PriceRow("*", 1.0, 1.0)], max_yuan=0.0005)
        g.charge("m", {"prompt_tokens": 400, "completion_tokens": 0})  # 0.0004 ok
        with pytest.raises(SystemExit, match="COST GUARD"):
            g.charge("m", {"prompt_tokens": 400, "completion_tokens": 0})  # 0.0008 > cap

    def test_zero_tokens_costs_nothing(self):
        g = CostGuard([PriceRow("*", 1.0, 1.0)], max_yuan=0.0)
        assert g.charge("m", {}) == 0.0  # missing usage → no charge, no abort
