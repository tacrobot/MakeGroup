"""
metrics.py
指標計算モジュール。
仕様書 §6 の定義をそのまま実装する。
- 浮動小数点を使わず、fractions.Fraction で厳密計算を行う。
- 表示時のみ小数第2位に四捨五入（ROUND_HALF_UP）する。
- 最適化モジュールとは独立している。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from math import gcd, lcm
from functools import reduce
from typing import NamedTuple


class GroupMetrics(NamedTuple):
    """グループ1つの指標"""
    group_no: int
    size: int
    mean_satisfaction: Fraction  # グループ所属者の満足度の平均
    members: list[str]           # IDのリスト（昇順）


class CandidateMetrics(NamedTuple):
    """候補1件の全指標"""
    min_satisfaction: Fraction   # 最低満足度
    sum_satisfaction: Fraction   # 合計満足度
    mean_satisfaction: Fraction  # 平均満足度
    variance: Fraction           # 満足度の母分散


def compute_scale_L(group_sizes: list[int]) -> int:
    """
    §6.2 の L = lcm{ (s - 1) | s は group_sizes の各要素 } を計算する。
    """
    values = [s - 1 for s in group_sizes]
    return reduce(lcm, values)


def compute_individual_satisfaction(
    participant_id: str,
    group: list[str],
    score_map: dict[tuple[str, str], int],
) -> Fraction:
    """
    参加者 participant_id の個人満足度 u_i を計算する。
    group: 所属グループのメンバーIDリスト
    score_map: {(rater_id, target_id): score}

    u_i = ( Σ score[i][j] ) / ( |G(i)| - 1 )
          ただし j ∈ G(i) かつ j ≠ i
    同席不可の組は別グループになるため、そもそも group に含まれない。
    """
    others = [pid for pid in group if pid != participant_id]
    if not others:
        return Fraction(0)
    total_score = sum(
        score_map.get((participant_id, target_id), 0)
        for target_id in others
    )
    return Fraction(total_score, len(others))


def compute_all_satisfactions(
    groups: list[list[str]],
    score_map: dict[tuple[str, str], int],
) -> dict[str, Fraction]:
    """
    全参加者の個人満足度を計算する。
    groups: グループごとのメンバーIDリスト
    戻り値: {participant_id: satisfaction}
    """
    satisfactions: dict[str, Fraction] = {}
    for group in groups:
        for pid in group:
            satisfactions[pid] = compute_individual_satisfaction(pid, group, score_map)
    return satisfactions


def compute_candidate_metrics(
    satisfactions: dict[str, Fraction],
) -> CandidateMetrics:
    """
    候補全体の指標を計算する。
    """
    n = len(satisfactions)
    values = list(satisfactions.values())

    min_sat = min(values)
    sum_sat = sum(values, Fraction(0))
    mean_sat = sum_sat / n

    # 母分散: (1/N) Σ (u_i - mean)^2
    variance = sum((v - mean_sat) ** 2 for v in values) / n

    return CandidateMetrics(
        min_satisfaction=min_sat,
        sum_satisfaction=sum_sat,
        mean_satisfaction=mean_sat,
        variance=variance,
    )


def compute_group_metrics(
    groups: list[list[str]],
    group_sizes: list[int],
    satisfactions: dict[str, Fraction],
) -> list[GroupMetrics]:
    """
    グループごとの指標を計算し、番号付けする。
    グループ番号は §8.2 の正規形に従う:
    人数の昇順、人数が同じなら所属者の最小IDの昇順で 1 から番号を付ける。
    """
    # (人数, 最小ID, メンバーリスト) でソート
    groups_with_key = []
    for g in groups:
        members = sorted(g)
        size = len(members)
        min_id = members[0] if members else ""
        groups_with_key.append((size, min_id, members))

    groups_with_key.sort(key=lambda x: (x[0], x[1]))

    result: list[GroupMetrics] = []
    for i, (size, min_id, members) in enumerate(groups_with_key, start=1):
        mean_sat = sum(
            (satisfactions.get(pid, Fraction(0)) for pid in members),
            Fraction(0),
        ) / size
        result.append(GroupMetrics(
            group_no=i,
            size=size,
            mean_satisfaction=mean_sat,
            members=members,
        ))
    return result


def fraction_to_display(f: Fraction, decimal_places: int = 2) -> str:
    """
    Fraction を表示用の小数文字列に変換する（ROUND_HALF_UP）。
    """
    # Decimal で厳密に四捨五入
    d = Decimal(f.numerator) / Decimal(f.denominator)
    quantize_str = "0." + "0" * decimal_places
    rounded = d.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
    return str(rounded)


def fraction_to_float6(f: Fraction) -> float:
    """
    Fraction を小数第6位に丸めた float に変換する（JSON用）。
    """
    d = Decimal(f.numerator) / Decimal(f.denominator)
    rounded = d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return float(rounded)


def fraction_to_exact_str(f: Fraction) -> str:
    """
    Fraction を約分した分数文字列に変換する。
    整数のときは整数文字列を返す（例: "3"）。
    """
    if f.denominator == 1:
        return str(f.numerator)
    return f"{f.numerator}/{f.denominator}"
