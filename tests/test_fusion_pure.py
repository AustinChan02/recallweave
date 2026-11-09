"""Pure-math tests: fusion (RRF/decay/weights/MMR), lanes, token counter."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

from recallweave.db.models import Memory
from recallweave.read.fusion import (
    final_score,
    mmr_select,
    recency_factor,
    rrf_fuse,
    weighted_rank,
)
from recallweave.read.retrievers import lexical_lane, lexical_scores, recency_lane
from recallweave.tokens import counter


def _mem(kind="fact", importance=0.5, age_days=0.0, content="c", mid=None):
    now = datetime.now(UTC)
    return Memory(
        id=mid or uuid.uuid4(),
        user_id=uuid.uuid4(),
        session_id=None,
        kind=kind,
        content=content,
        importance=importance,
        status="active",
        valid_from=now,
        valid_to=None,
        superseded_by=None,
        meta={},
        created_at=now - timedelta(days=age_days),
        last_accessed=None,
    )


class TestRRF:
    def test_single_lane_ranks(self):
        lanes = [["a", "b", "c"]]
        out = rrf_fuse(lanes, k=60)
        assert math.isclose(out["a"], 1 / 61)
        assert math.isclose(out["b"], 1 / 62)
        assert math.isclose(out["c"], 1 / 63)

    def test_multi_lane_sums(self):
        lanes = [["a", "b"], ["b", "a"]]
        out = rrf_fuse(lanes, k=60)
        assert math.isclose(out["a"], 1 / 61 + 1 / 62)
        assert math.isclose(out["b"], 1 / 62 + 1 / 61)

    def test_overlap_beats_single_rank(self):
        lanes = [["a"], ["a"], ["a"], ["x"]]
        out = rrf_fuse(lanes, k=60)
        assert out["a"] > out["x"] * 3 - 1e-9

    def test_k_changes_head_vs_i_tail(self):
        small = rrf_fuse([["a", "z", "z2", "z3"]], k=1)
        big = rrf_fuse([["a", "z", "z2", "z3"]], k=100)
        assert (small["a"] - small["z3"]) > (big["a"] - big["z3"])


class TestSignedDecayAndWeights:
    def test_decay_bounds(self):
        assert recency_factor(0.0, 21.0) == 1.0
        assert math.isclose(recency_factor(21.0, 21.0), math.exp(-1.0))
        assert recency_factor(100.0, 21.0) < 0.01

    def test_final_score_linear_parts(self):
        s = final_score(
            0.5, kind_weight=1.2, recency=1.0,
            importance=1.0, importance_lo=0.7, importance_span=0.6,
        )
        assert math.isclose(s, 0.5 * 1.2 * 1.3)

    def test_weighted_rank_ordering(self):
        rows = {
            "new_pref": _mem(kind="preference", importance=0.9),
            "old_fact": _mem(kind="fact", importance=0.2, age_days=60),
        }
        out = weighted_rank(
            {"new_pref": 0.01, "old_fact": 0.01}, rows,
            kind_weight_fn=lambda k: 1.1 if k == "preference" else 1.0,
            tau_days=21.0, importance_lo=0.7, importance_span=0.6,
        )
        ids = [mid for mid, _ in out]
        assert ids[0] == "new_pref"
        assert out[0][1] > out[1][1] * 5

    def test_recency_uses_last_accessed_when_present(self):
        m = _mem()
        m.last_accessed = datetime.now(UTC) - timedelta(days=50)
        m.created_at = datetime.now(UTC)
        rows = {"m": m}
        out = weighted_rank(
            {"m": 1.0}, rows, kind_weight_fn=lambda k: 1.0,
            tau_days=21.0, importance_lo=0.7, importance_span=0.6,
        )
        val = out[0][1]
        expected = 1.0 * 1.0 * math.exp(-50.0 / 21.0) * 1.0
        assert math.isclose(val, expected, rel_tol=1e-6)


class TestMMR:
    def test_dissimilar_first_redundant_surpressed(self):
        emb = {
            "target": [1.0, 0.0],
            "dup": [0.99, -0.14],   # very similar to target in behavior below
            "diverse": [0.0, 1.0],
            "dup2": [1.0, 0.0],
        }
        ranked = [("target", 1.0), ("dup", 0.99), ("dup2", 0.98), ("diverse", 0.5)]
        picked = mmr_select(ranked, emb, k=2, lam=0.5)
        assert picked[0] == "target"
        assert picked[1] == "diverse"  # dup/dup2 suppressed by redundancy

    def test_missing_embeddings_floor(self):
        ranked = [("no_emb", 1.0), ("no_emb2", 0.9)]
        picked = mmr_select(ranked, {}, k=1, lam=0.7)
        assert picked == ["no_emb"]

    def test_empty_ranked(self):
        assert mmr_select([], {}, k=3, lam=0.7) == []


class TestLanes:
    def test_lexical_scores_prefers_more_overlap(self):
        rows = [_mem(content="用户喜欢喝无糖可乐"), _mem(content="用户喜欢养猫")]
        out = lexical_scores("喜欢无糖可乐", rows)
        assert out[str(rows[0].id)] > out[str(rows[1].id)]

    def test_lexical_empty_query(self):
        assert lexical_scores("!!!", [_mem(content="用户喜欢猫")]) == {}

    def test_lexical_lane_order(self):
        a, b = _mem(content="用户喜欢喝无糖可乐"), _mem(content="用户喜欢养猫")
        assert lexical_lane("喜欢无糖可乐", [a, b], top_m=1) == [str(a.id)]

    def test_recency_lane(self):
        newer = _mem(age_days=0.0, mid=uuid.uuid4())
        older = _mem(age_days=30.0, mid=uuid.uuid4())
        lane = recency_lane([older, newer], top_m=2)
        assert lane[0] == str(newer.id)


class TestTokenCounter:
    def test_counts_real_encoding(self):
        tok = counter()
        n = tok.count("hello world")
        assert n > 0

    def test_cjk_upper_bound_without_encoding(self):
        c = counter()
        if c._enc is not None:  # contract: fallback subset doubles as sanity val
            c._enc = None
            assert c.count("今天天气不错") >= 6  # 6 CJK chars -> >=6 tokens fallback

    def test_count_messages_adds_overhead(self):
        tok = counter()
        msgs = [{"role": "user", "content": "hi"}]
        assert tok.count_messages(msgs) == tok.count("hi") + 4
