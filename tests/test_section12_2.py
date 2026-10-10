"""
test_section12_2.py
§12.2 同席不可のテスト。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from src.import_data import Rating, build_incompatible_pairs
from src.metrics import compute_all_satisfactions, compute_candidate_metrics
from src.optimize import optimize
from tests.conftest import PARTICIPANTS_4, RATINGS_4, GROUP_SIZES_2x2


def _make_ratings_with_incompatible(overrides: dict[tuple[str, str], bool]) -> list[Rating]:
    """
    RATINGS_4 をベースに incompatible だけ変更した Rating リストを返す。
    overrides: {(rater_id, target_id): incompatible_bool}
    """
    result = []
    for r in RATINGS_4:
        key = (r.rater_id, r.target_id)
        if key in overrides:
            result.append(Rating(r.rater_id, r.target_id, r.score, overrides[key]))
        else:
            result.append(r)
    return result


def _make_ratings_with_score_override(overrides: dict[tuple[str, str], int]) -> list[Rating]:
    """score だけ変更した Rating リストを返す"""
    result = []
    for r in RATINGS_4:
        key = (r.rater_id, r.target_id)
        if key in overrides:
            result.append(Rating(r.rater_id, r.target_id, overrides[key], r.incompatible))
        else:
            result.append(r)
    return result


# IP-01: 001,002 を true にする
def test_ip01_incompatible_pair_created():
    """IP-01: 片方向の incompatible=true で同席不可ペアが作られる"""
    ratings = _make_ratings_with_incompatible({("001", "002"): True})
    pairs = build_incompatible_pairs(ratings)
    assert ("001", "002") in pairs


def test_ip01_candidates_exclude_001_002():
    """IP-01: 001と002が同じグループの候補が存在しない"""
    ratings = _make_ratings_with_incompatible({("001", "002"): True})
    pairs = build_incompatible_pairs(ratings)
    candidates, end_reason = optimize(
        participants=PARTICIPANTS_4,
        ratings=ratings,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=pairs,
        top_n=10,
        time_limit=30,
        workers=1,
        seed=0,
    )
    assert len(candidates) == 2
    # すべての候補で001と002が別グループ
    for cand in candidates:
        for group in cand.groups:
            assert not ("001" in group and "002" in group), \
                f"001と002が同じグループに入っています: {group}"


# IP-02: 片方向だけの指定でも成立
def test_ip02_one_directional():
    """IP-02: 001->002 が true、002->001 が false でも同席不可ペアが作られる"""
    ratings = _make_ratings_with_incompatible({
        ("001", "002"): True,
        ("002", "001"): False,
    })
    pairs = build_incompatible_pairs(ratings)
    assert ("001", "002") in pairs
    # 001と002が同じグループの候補なし
    candidates, _ = optimize(
        participants=PARTICIPANTS_4,
        ratings=ratings,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=pairs,
        top_n=10,
        time_limit=30,
        workers=1,
        seed=0,
    )
    for cand in candidates:
        for group in cand.groups:
            assert not ("001" in group and "002" in group)


# IP-03: 双方向 true でも1組にまとまる
def test_ip03_bidirectional_is_one_pair():
    """IP-03: 双方向 true でも incompatible_pairs は1組だけ"""
    ratings = _make_ratings_with_incompatible({
        ("001", "002"): True,
        ("002", "001"): True,
    })
    pairs = build_incompatible_pairs(ratings)
    assert len(pairs) == 1
    assert ("001", "002") in pairs


# IP-04: すべて false なら空
def test_ip04_all_false():
    """IP-04: すべて false なら incompatible_pairs は空"""
    pairs = build_incompatible_pairs(RATINGS_4)
    assert pairs == []


# IP-05: 大文字小文字の区別なく読む
def test_ip05_case_insensitive(tmp_path):
    """IP-05: TRUE、False などを受け付ける"""
    import csv as csv_mod
    ratings_path = tmp_path / "master_ratings.csv"
    with ratings_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv_mod.writer(f)
        writer.writerow(["rater_id", "target_id", "score", "incompatible"])
        for r in RATINGS_4:
            # 001->002 を TRUE、002->001 を False にする
            if r.rater_id == "001" and r.target_id == "002":
                writer.writerow([r.rater_id, r.target_id, r.score, "TRUE"])
            elif r.rater_id == "002" and r.target_id == "001":
                writer.writerow([r.rater_id, r.target_id, r.score, "False"])
            else:
                writer.writerow([r.rater_id, r.target_id, r.score, "false"])

    from src.import_data import load_ratings
    loaded = load_ratings(ratings_path)
    pairs = build_incompatible_pairs(loaded)
    assert ("001", "002") in pairs


# IP-06: 無効な incompatible 値はエラー
def test_ip06_invalid_incompatible_value(tmp_path):
    """IP-06: 1, 0, yes, 空欄は R-07 エラー"""
    import csv as csv_mod
    from src.validate import validate_ratings

    for invalid_value in ["1", "0", "yes", ""]:
        ratings_path = tmp_path / f"master_ratings_{invalid_value or 'empty'}.csv"
        with ratings_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv_mod.writer(f)
            writer.writerow(["rater_id", "target_id", "score", "incompatible"])
            for r in RATINGS_4:
                if r.rater_id == "001" and r.target_id == "002":
                    writer.writerow([r.rater_id, r.target_id, r.score, invalid_value])
                else:
                    writer.writerow([r.rater_id, r.target_id, r.score, "false"])

        # validate.py の文字列レベル検証で R-07 が検出されること
        from src.validate import _validate_ratings_raw
        errs, _ = _validate_ratings_raw(ratings_path)
        r07_errors = [e for e in errs if "[R-07]" in e]
        assert r07_errors, f"無効な値 '{invalid_value}' で R-07 エラーが検出されませんでした"


# IP-07: score の値が異なっても同席不可の組・グループ構成・指標は変わらない
def test_ip07_score_does_not_affect_incompatible():
    """IP-07: 001,002 を true にして score=2 と score=-2 の2通りで実行する。
    グループ構成、順位、全指標（最低・合計・平均・分散）が完全に一致すること。
    """
    def make_candidates(score_val: int):
        ratings = []
        for r in RATINGS_4:
            if r.rater_id == "001" and r.target_id == "002":
                ratings.append(Rating(r.rater_id, r.target_id, score_val, True))
            else:
                ratings.append(r)
        pairs = build_incompatible_pairs(ratings)
        candidates, _ = optimize(
            participants=PARTICIPANTS_4,
            ratings=ratings,
            group_sizes=GROUP_SIZES_2x2,
            incompatible_pairs=pairs,
            top_n=10,
            time_limit=30,
            workers=1,
            seed=0,
        )
        return candidates, ratings

    candidates_pos, ratings_pos = make_candidates(2)
    candidates_neg, ratings_neg = make_candidates(-2)

    # score_map の構築（compute_candidate_metrics 呼び出し用）
    def to_score_map(ratings):
        return {(r.rater_id, r.target_id): r.score for r in ratings}

    score_map_pos = to_score_map(ratings_pos)
    score_map_neg = to_score_map(ratings_neg)

    # 1. 候補件数が同じ
    assert len(candidates_pos) == len(candidates_neg), (
        f"候補件数が異なります: score=2 → {len(candidates_pos)}, score=-2 → {len(candidates_neg)}"
    )

    # 2・3. 各順位のグループ構成と全指標が完全に一致
    for rank, (cand_pos, cand_neg) in enumerate(zip(candidates_pos, candidates_neg), start=1):
        # グループ構成
        groups_pos = frozenset(frozenset(g) for g in cand_pos.groups)
        groups_neg = frozenset(frozenset(g) for g in cand_neg.groups)
        assert groups_pos == groups_neg, (
            f"順位 {rank} のグループ構成が異なります:\n"
            f"  score=2  → {groups_pos}\n"
            f"  score=-2 → {groups_neg}"
        )

        # 指標（compute_candidate_metrics で再計算）
        sat_pos = compute_all_satisfactions(cand_pos.groups, score_map_pos)
        sat_neg = compute_all_satisfactions(cand_neg.groups, score_map_neg)
        metrics_pos = compute_candidate_metrics(sat_pos)
        metrics_neg = compute_candidate_metrics(sat_neg)

        assert metrics_pos.min_satisfaction == metrics_neg.min_satisfaction, (
            f"順位 {rank} の min_satisfaction が異なります: "
            f"{metrics_pos.min_satisfaction} vs {metrics_neg.min_satisfaction}"
        )
        assert metrics_pos.sum_satisfaction == metrics_neg.sum_satisfaction, (
            f"順位 {rank} の sum_satisfaction が異なります: "
            f"{metrics_pos.sum_satisfaction} vs {metrics_neg.sum_satisfaction}"
        )
        assert metrics_pos.mean_satisfaction == metrics_neg.mean_satisfaction, (
            f"順位 {rank} の mean_satisfaction が異なります: "
            f"{metrics_pos.mean_satisfaction} vs {metrics_neg.mean_satisfaction}"
        )
        assert metrics_pos.variance == metrics_neg.variance, (
            f"順位 {rank} の variance が異なります: "
            f"{metrics_pos.variance} vs {metrics_neg.variance}"
        )


# IP-11: 001 の全対象に incompatible=true → 解なし → 終了コード3相当
def test_ip11_no_solution():
    """IP-11: 001が誰とも同じグループになれない場合は解なし"""
    ratings = _make_ratings_with_incompatible({
        ("001", "002"): True,
        ("001", "003"): True,
        ("001", "004"): True,
    })
    pairs = build_incompatible_pairs(ratings)
    candidates, end_reason = optimize(
        participants=PARTICIPANTS_4,
        ratings=ratings,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=pairs,
        top_n=10,
        time_limit=30,
        workers=1,
        seed=0,
    )
    # 解なし: candidates が空で end_reason が INFEASIBLE
    assert len(candidates) == 0
    assert end_reason == "INFEASIBLE"


# IP-08: score の値と incompatible の判定は独立している
def test_ip08_incompatible_is_independent_from_score():
    """
    IP-08: score がどんな値でも incompatible=false なら同席不可にならない。
           また score がどんな値でも incompatible=true なら同席不可になる。
    テスト1: ("002","003") を score=-2, incompatible=False → incompatible_pairs に含まれない
    テスト2: ("002","004") を score=2, incompatible=True  → incompatible_pairs に含まれる
    """
    # テスト1: score=-2 かつ incompatible=False → 同席不可の組にならない
    # RATINGS_4 では ("002","003") はもともと score=-2, incompatible=False なのでそのまま使用
    ratings_test1 = []
    for r in RATINGS_4:
        if r.rater_id == "002" and r.target_id == "003":
            # 明示的に incompatible=False を指定（score=-2 は維持）
            ratings_test1.append(Rating(r.rater_id, r.target_id, -2, False))
        else:
            ratings_test1.append(r)

    pairs_test1 = build_incompatible_pairs(ratings_test1)
    assert ("002", "003") not in pairs_test1, \
        "score=-2 かつ incompatible=False なのに ('002','003') が同席不可になっています"

    # テスト2: score=2 かつ incompatible=True → 同席不可の組になる
    # RATINGS_4 では ("002","004") は score=0, incompatible=False なのでオーバーライドする
    ratings_test2 = []
    for r in RATINGS_4:
        if r.rater_id == "002" and r.target_id == "004":
            # score を 2 に変更し、incompatible=True に変更
            ratings_test2.append(Rating(r.rater_id, r.target_id, 2, True))
        else:
            ratings_test2.append(r)

    pairs_test2 = build_incompatible_pairs(ratings_test2)
    assert ("002", "004") in pairs_test2, \
        "score=2 かつ incompatible=True なのに ('002','004') が同席不可になっていません"


# IP-09: false → true → false のトグルで組が追加・削除される
def test_ip09_toggle_incompatible():
    """IP-09: 001,002 を false→true→false に変えると、組が追加されて除かれる"""
    # テスト1: ("001","002") が false → incompatible_pairs に含まれない
    ratings_false = _make_ratings_with_incompatible({("001", "002"): False})
    pairs_false = build_incompatible_pairs(ratings_false)
    assert ("001", "002") not in pairs_false, \
        "incompatible=false のとき ('001','002') が同席不可の組に含まれています"

    # テスト2: ("001","002") を true に変える → incompatible_pairs に含まれる
    ratings_true = _make_ratings_with_incompatible({("001", "002"): True})
    pairs_true = build_incompatible_pairs(ratings_true)
    assert ("001", "002") in pairs_true, \
        "incompatible=true にしたのに ('001','002') が同席不可の組に含まれていません"

    # テスト3: ("001","002") を false に戻す → incompatible_pairs に含まれなくなる
    ratings_back = _make_ratings_with_incompatible({("001", "002"): False})
    pairs_back = build_incompatible_pairs(ratings_back)
    assert ("001", "002") not in pairs_back, \
        "incompatible=false に戻したのに ('001','002') が同席不可の組に残っています"


# IP-10: 双方向 true → 片方を false にしても組は残る → 両方 false で組が解除される
def test_ip10_bidirectional_release():
    """IP-10: 001,002 と 002,001 を両方 true にし、片方だけ false にしても組は残る。
    両方 false にすると組が解除される。
    """
    # テスト1: 双方向 true → ("001","002") が同席不可の組に含まれる
    ratings_both_true = _make_ratings_with_incompatible({
        ("001", "002"): True,
        ("002", "001"): True,
    })
    pairs_both_true = build_incompatible_pairs(ratings_both_true)
    assert ("001", "002") in pairs_both_true, \
        "001,002 と 002,001 をともに true にしたのに ('001','002') が同席不可の組に含まれていません"

    # テスト2: ("001","002") だけ false、("002","001") は true → 組はまだ残る（片方向 true で成立）
    ratings_one_false = _make_ratings_with_incompatible({
        ("001", "002"): False,
        ("002", "001"): True,
    })
    pairs_one_false = build_incompatible_pairs(ratings_one_false)
    assert ("001", "002") in pairs_one_false, \
        "片方（002,001）が true のとき ('001','002') が同席不可の組に含まれていません"

    # テスト3: 両方 false → 組が解除される
    ratings_both_false = _make_ratings_with_incompatible({
        ("001", "002"): False,
        ("002", "001"): False,
    })
    pairs_both_false = build_incompatible_pairs(ratings_both_false)
    assert ("001", "002") not in pairs_both_false, \
        "001,002 と 002,001 をともに false にしたのに ('001','002') が同席不可の組に残っています"
