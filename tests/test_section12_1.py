"""
test_section12_1.py
§12.1 基準となる小さな例のテスト。

N=4、group_sizes=[2,2]、incompatible なし の標準入力で
3候補が期待どおりに出ることを確認する。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from src.import_data import build_incompatible_pairs
from src.metrics import (
    compute_all_satisfactions,
    compute_candidate_metrics,
    compute_scale_L,
)
from src.optimize import optimize
from tests.conftest import PARTICIPANTS_4, RATINGS_4, GROUP_SIZES_2x2


def test_scale_L_2x2():
    """[2,2] のとき L = lcm(1, 1) = 1"""
    assert compute_scale_L(sorted(GROUP_SIZES_2x2)) == 1


def test_no_incompatible_pairs():
    """incompatible がすべて false のとき incompatible_pairs は空"""
    pairs = build_incompatible_pairs(RATINGS_4)
    assert pairs == []


def _run_optimize_4():
    """§12.1 の最適化を実行して候補リストを返す"""
    candidates, end_reason = optimize(
        participants=PARTICIPANTS_4,
        ratings=RATINGS_4,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=[],
        top_n=10,
        time_limit=30,
        workers=1,
        seed=0,
        deterministic=True,
    )
    return candidates, end_reason


def test_three_candidates():
    """top_n=10 で実行したとき、候補は3件で終わる"""
    candidates, end_reason = _run_optimize_4()
    assert len(candidates) == 3
    assert end_reason == "no_more_solutions"


def test_candidate1_groups():
    """
    候補1 は {001,002} {003,004} の分割であること。
    各人の満足度: 001=2, 002=1, 003=2, 004=2
    最低=1, 合計=7
    """
    candidates, _ = _run_optimize_4()
    cand1 = candidates[0]
    sats = cand1.satisfactions

    # 満足度の確認
    assert sats["001"] == Fraction(2), f"001の満足度が違います: {sats['001']}"
    assert sats["002"] == Fraction(1), f"002の満足度が違います: {sats['002']}"
    assert sats["003"] == Fraction(2), f"003の満足度が違います: {sats['003']}"
    assert sats["004"] == Fraction(2), f"004の満足度が違います: {sats['004']}"

    metrics = compute_candidate_metrics(sats)
    assert metrics.min_satisfaction == Fraction(1)
    assert metrics.sum_satisfaction == Fraction(7)
    assert metrics.variance == Fraction(3, 16)


def test_candidate2_groups():
    """
    候補2 は {001,003} {002,004} の分割であること。
    各人の満足度: 001=0, 002=0, 003=0, 004=1
    最低=0, 合計=1
    """
    candidates, _ = _run_optimize_4()
    cand2 = candidates[1]
    sats = cand2.satisfactions

    assert sats["001"] == Fraction(0)
    assert sats["002"] == Fraction(0)
    assert sats["003"] == Fraction(0)
    assert sats["004"] == Fraction(1)

    metrics = compute_candidate_metrics(sats)
    assert metrics.min_satisfaction == Fraction(0)
    assert metrics.sum_satisfaction == Fraction(1)
    assert metrics.variance == Fraction(3, 16)


def test_candidate3_groups():
    """
    候補3 は {001,004} {002,003} の分割であること。
    各人の満足度: 001=-1, 002=-2, 003=-1, 004=-2
    最低=-2, 合計=-6
    """
    candidates, _ = _run_optimize_4()
    cand3 = candidates[2]
    sats = cand3.satisfactions

    assert sats["001"] == Fraction(-1)
    assert sats["002"] == Fraction(-2)
    assert sats["003"] == Fraction(-1)
    assert sats["004"] == Fraction(-2)

    metrics = compute_candidate_metrics(sats)
    assert metrics.min_satisfaction == Fraction(-2)
    assert metrics.sum_satisfaction == Fraction(-6)
    assert metrics.variance == Fraction(1, 4)


def test_candidate_order():
    """候補は (最低満足度降順, 合計満足度降順, 分散昇順) の辞書式順になっている"""
    candidates, _ = _run_optimize_4()
    mins = [compute_candidate_metrics(c.satisfactions).min_satisfaction for c in candidates]
    sums = [compute_candidate_metrics(c.satisfactions).sum_satisfaction for c in candidates]
    variances = [compute_candidate_metrics(c.satisfactions).variance for c in candidates]

    # 最低満足度は降順
    assert mins[0] >= mins[1] >= mins[2]

    # 最低満足度が等しいなら合計満足度は降順（候補1と候補2は最低満足度が異なるので確認は候補2-3のみ）
    # 候補2と候補3は最低満足度が異なるので単純比較
    assert mins[0] > mins[1]
    assert mins[1] > mins[2]
