"""
test_section12_3.py
§12.3 Python のテスト。

- 検算用の全列挙コードをテスト内に置く（本体では使わない）。
- 各エラー・警告コードの検証。
- IDのゼロ埋め保持。
- §6.2 の L と U_i の計算。
- --deterministic での workers=1 の確認。
- 終了コードの確認。
"""

from __future__ import annotations

import csv
import json
import tempfile
from fractions import Fraction
from itertools import combinations
from pathlib import Path

import pytest

from src.import_data import Participant, Rating, build_incompatible_pairs
from src.metrics import (
    compute_all_satisfactions,
    compute_candidate_metrics,
    compute_group_metrics,
    compute_individual_satisfaction,
    compute_scale_L,
)
from src.optimize import optimize
from src.validate import validate_all, validate_participants, validate_ratings, validate_constraints
from tests.conftest import PARTICIPANTS_4, RATINGS_4, GROUP_SIZES_2x2, make_data_dir


# --- 検算用の全列挙コード（テスト内のみ） ---

def _enumerate_all_splits(
    participants: list[Participant],
    group_sizes: list[int],
    incompatible_pairs: list[tuple[str, str]],
    score_map: dict[tuple[str, str], int],
) -> list[tuple[Fraction, Fraction, Fraction, list[list[str]]]]:
    """
    すべての分割を列挙し、(最低満足度, 合計満足度, 分散, グループリスト) を返す。
    分割は正規形に変換して重複を除く。
    """
    pids = [p.id for p in participants]
    sorted_sizes = sorted(group_sizes)
    n = len(pids)
    incompat_set = set(incompatible_pairs)

    results = []
    seen_splits: set[frozenset] = set()

    def _is_compatible(group: list[str]) -> bool:
        for a, b in combinations(group, 2):
            pair = (min(a, b), max(a, b))
            if pair in incompat_set:
                return False
        return True

    def _generate(remaining: list[str], sizes: list[int], current_groups: list[list[str]]):
        if not sizes:
            # 正規形に変換（人数昇順、同サイズなら最小IDの昇順）
            sorted_groups = sorted(current_groups, key=lambda g: (len(g), min(g)))
            key = frozenset(frozenset(g) for g in sorted_groups)
            if key in seen_splits:
                return
            seen_splits.add(key)

            sats = compute_all_satisfactions(sorted_groups, score_map)
            metrics = compute_candidate_metrics(sats)
            results.append((
                metrics.min_satisfaction,
                metrics.sum_satisfaction,
                metrics.variance,
                sorted_groups,
            ))
            return

        # remaining[0] をどのグループに割り当てるか試みる（対称性除去）
        # 同じサイズのグループが複数ある場合は1つだけ試す
        first = remaining[0]
        rest = remaining[1:]
        tried_sizes: set[int] = set()
        for idx, size in enumerate(sizes):
            if size in tried_sizes:
                continue
            tried_sizes.add(size)
            remaining_sizes = sizes[:idx] + sizes[idx + 1:]
            for combo in combinations(rest, size - 1):
                group = [first] + list(combo)
                if not _is_compatible(group):
                    continue
                new_remaining = [p for p in rest if p not in combo]
                _generate(new_remaining, remaining_sizes, current_groups + [group])

    _generate(pids, sorted_sizes, [])
    return results


def test_exhaustive_check_matches_optimizer():
    """
    N=4 の全分割を列挙して最適化結果と照合する。
    """
    score_map = {(r.rater_id, r.target_id): r.score for r in RATINGS_4}
    all_splits = _enumerate_all_splits(PARTICIPANTS_4, GROUP_SIZES_2x2, [], score_map)

    # 全分割を指標でソート（最低満足度降順、合計満足度降順、分散昇順）
    all_splits.sort(key=lambda x: (-x[0], -x[1], x[2]))

    candidates, _ = optimize(
        participants=PARTICIPANTS_4,
        ratings=RATINGS_4,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=[],
        top_n=10,
        time_limit=30,
        workers=1,
        seed=0,
    )

    assert len(candidates) == len(all_splits), \
        f"候補数が違います: {len(candidates)} vs {len(all_splits)}"

    for i, (cand, (exp_min, exp_sum, exp_var, _)) in enumerate(zip(candidates, all_splits)):
        metrics = compute_candidate_metrics(cand.satisfactions)
        assert metrics.min_satisfaction == exp_min, \
            f"候補{i+1}: 最低満足度 {metrics.min_satisfaction} != {exp_min}"
        assert metrics.sum_satisfaction == exp_sum, \
            f"候補{i+1}: 合計満足度 {metrics.sum_satisfaction} != {exp_sum}"
        assert metrics.variance == exp_var, \
            f"候補{i+1}: 分散 {metrics.variance} != {exp_var}"


# --- エラーコードのテスト ---

def _write_participants_csv(path: Path, rows: list[list], header: list | None = None):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if header is None:
            writer.writerow(["id", "name"])
        else:
            writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def _write_ratings_csv(path: Path, rows: list[list]):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rater_id", "target_id", "score", "incompatible"])
        for row in rows:
            writer.writerow(row)


def _write_constraints_json(path: Path, data: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)


def _std_ratings_rows():
    return [
        [r.rater_id, r.target_id, r.score, "true" if r.incompatible else "false"]
        for r in RATINGS_4
    ]


def test_f01_missing_file(tmp_path):
    """F-01: ファイルが存在しない"""
    from src.import_data import load_all
    _, _, _, errors = load_all(tmp_path)
    codes = [e.split("]")[0].strip("[") for e in errors]
    assert "F-01" in codes


def test_p01_wrong_header(tmp_path):
    """P-01: ヘッダーが id,name でない"""
    _write_participants_csv(tmp_path / "participants.csv", [["001", "Alice"]], header=["ID", "Name"])
    from src.validate import validate_participants_header
    errs = validate_participants_header(tmp_path / "participants.csv")
    assert any("P-01" in e for e in errs)


def test_p02_invalid_id(tmp_path):
    """P-02: ID が 3 桁数字でない"""
    p = [Participant("01", "Alice"), Participant("002", "Bob"),
         Participant("003", "Carol"), Participant("004", "Dave")]
    errs = validate_participants(p)
    assert any("P-02" in e for e in errs)


def test_p03_duplicate_id(tmp_path):
    """P-03: ID が重複している"""
    p = [Participant("001", "Alice"), Participant("001", "Bob"),
         Participant("002", "Carol"), Participant("003", "Dave")]
    errs = validate_participants(p)
    assert any("P-03" in e for e in errs)


def test_p04_empty_name(tmp_path):
    """P-04: name が空"""
    p = [Participant("001", ""), Participant("002", "Bob"),
         Participant("003", "Carol"), Participant("004", "Dave")]
    errs = validate_participants(p)
    assert any("P-04" in e for e in errs)


def test_p04_name_with_spaces():
    """P-04: name に前後空白"""
    p = [Participant("001", " Alice"), Participant("002", "Bob"),
         Participant("003", "Carol"), Participant("004", "Dave")]
    errs = validate_participants(p)
    assert any("P-04" in e for e in errs)


def test_p05_duplicate_name():
    """P-05: name が重複"""
    p = [Participant("001", "Alice"), Participant("002", "Alice"),
         Participant("003", "Carol"), Participant("004", "Dave")]
    errs = validate_participants(p)
    assert any("P-05" in e for e in errs)


def test_p06_not_sorted():
    """P-06: ID 昇順でない"""
    p = [Participant("002", "Bob"), Participant("001", "Alice"),
         Participant("003", "Carol"), Participant("004", "Dave")]
    errs = validate_participants(p)
    assert any("P-06" in e for e in errs)


def test_p07_too_few_participants():
    """P-07: 参加者数 < 4"""
    p = [Participant("001", "Alice"), Participant("002", "Bob"),
         Participant("003", "Carol")]
    errs = validate_participants(p)
    assert any("P-07" in e for e in errs)


def test_r01_missing_required_column(tmp_path):
    """R-01: 必須列がない"""
    path = tmp_path / "master_ratings.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rater_id", "target_id", "score"])  # incompatible がない
        writer.writerow(["001", "002", "1"])
    from src.validate import validate_ratings_header
    errs = validate_ratings_header(path)
    assert any("R-01" in e for e in errs)


def test_r02_unknown_rater(tmp_path):
    """R-02: rater_id が参加者に存在しない"""
    ratings = [Rating("099", "002", 1, False)] + [r for r in RATINGS_4 if r.rater_id != "001"]
    errs = validate_ratings(ratings, PARTICIPANTS_4, None)
    assert any("R-02" in e for e in errs)


def test_r03_self_rating():
    """R-03: rater_id == target_id"""
    ratings = [Rating("001", "001", 1, False)] + [r for r in RATINGS_4 if r.rater_id != "001"]
    errs = validate_ratings(ratings, PARTICIPANTS_4, None)
    assert any("R-03" in e for e in errs)


def test_r04_invalid_score(tmp_path):
    """R-04: score が +2 などの無効な表記"""
    path = tmp_path / "master_ratings.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rater_id", "target_id", "score", "incompatible"])
        writer.writerow(["001", "002", "+2", "false"])
    from src.validate import _validate_ratings_raw
    errs, _ = _validate_ratings_raw(path)
    assert any("R-04" in e for e in errs)


def test_r05_duplicate_pair():
    """R-05: 同じ (rater_id, target_id) が複数"""
    ratings = list(RATINGS_4) + [Rating("001", "002", 1, False)]
    errs = validate_ratings(ratings, PARTICIPANTS_4, None)
    assert any("R-05" in e for e in errs)


def test_r06_missing_target(tmp_path):
    """R-06: ある回答者の行が不足している"""
    ratings = [r for r in RATINGS_4 if not (r.rater_id == "001" and r.target_id == "004")]
    errs = validate_ratings(ratings, PARTICIPANTS_4, None)
    assert any("R-06" in e for e in errs)


def test_r07_invalid_incompatible(tmp_path):
    """R-07: incompatible が yes などの無効な値"""
    path = tmp_path / "master_ratings.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rater_id", "target_id", "score", "incompatible"])
        writer.writerow(["001", "002", "1", "yes"])
    from src.validate import _validate_ratings_raw
    errs, _ = _validate_ratings_raw(path)
    assert any("R-07" in e for e in errs)


def test_c01_missing_group_sizes():
    """C-01: group_sizes がない"""
    errs = validate_constraints({}, PARTICIPANTS_4)
    assert any("C-01" in e for e in errs)


def test_c02_invalid_group_sizes():
    """C-02: group_sizes が整数でない"""
    errs = validate_constraints({"group_sizes": [2, "a"]}, PARTICIPANTS_4)
    assert any("C-02" in e for e in errs)


def test_c02_size_less_than_2():
    """C-02: group_sizes に 2 未満の要素"""
    errs = validate_constraints({"group_sizes": [1, 3]}, PARTICIPANTS_4)
    assert any("C-02" in e for e in errs)


def test_c03_size_mismatch():
    """C-03: group_sizes の合計が N と不一致"""
    errs = validate_constraints({"group_sizes": [2, 3]}, PARTICIPANTS_4)  # 5 != 4
    assert any("C-03" in e for e in errs)


def test_c04_extra_keys():
    """C-04: group_sizes 以外のキーがある"""
    errs = validate_constraints({"group_sizes": [2, 2], "incompatible_pairs": []}, PARTICIPANTS_4)
    assert any("C-04" in e for e in errs)


def test_w01_all_same_score():
    """W-01: 全員に同じ評価点"""
    ratings = [Rating(r.rater_id, r.target_id, 0, False) for r in RATINGS_4]
    _, warnings = validate_all(PARTICIPANTS_4, ratings, {"group_sizes": [2, 2]}, [])
    assert any("W-01" in w for w in warnings)


def test_w02_too_many_participants():
    """W-02: 参加者数 > 40"""
    pids = [f"{i:03d}" for i in range(1, 42)]
    participants = [Participant(pid, f"Person{pid}") for pid in pids]
    _, warnings = validate_all(participants, None, None, [])
    assert any("W-02" in w for w in warnings)


# --- ID ゼロ埋めの保持 ---

def test_zero_padded_ids_preserved():
    """IDのゼロ埋めが読み込みから出力まで保たれる"""
    score_map = {(r.rater_id, r.target_id): r.score for r in RATINGS_4}
    candidates, _ = optimize(
        participants=PARTICIPANTS_4,
        ratings=RATINGS_4,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=[],
        top_n=1,
        time_limit=30,
        workers=1,
        seed=0,
    )
    for cand in candidates:
        for group in cand.groups:
            for pid in group:
                assert len(pid) == 3, f"ID '{pid}' のゼロ埋めが失われています"
                assert pid.isdigit(), f"ID '{pid}' が数字のみでありません"


# --- §6.2 の L と U_i の例 ---

def test_scale_L_4_5_5_5():
    """[4,5,5,5] のとき L = lcm(3,4) = 12"""
    assert compute_scale_L(sorted([4, 5, 5, 5])) == 12


def test_scale_L_individual_satisfaction():
    """
    §6.2 の例:
    4人グループで合計スコア=2 → u=2/3, U=8
    5人グループで合計スコア=2 → u=1/2, U=6
    """
    # 4人グループ (size=4): L / (4-1) = 12/3 = 4、score合計=2 → U = 4*2 = 8
    L = 12
    # U = w * Σscore = (L/(s-1)) * sum = (12/3)*2 = 8
    assert (L // (4 - 1)) * 2 == 8

    # 5人グループ (size=5): w = 12/4 = 3、score合計=2 → U = 3*2 = 6
    assert (L // (5 - 1)) * 2 == 6

    # 満足度の Fraction 計算
    four_person_group = ["001", "002", "003", "004"]
    score_map_4 = {
        ("001", "002"): 2,
        ("001", "003"): 0,
        ("001", "004"): 0,
        ("002", "001"): 0,
        ("002", "003"): 0,
        ("002", "004"): 0,
        ("003", "001"): 0,
        ("003", "002"): 0,
        ("003", "004"): 0,
        ("004", "001"): 0,
        ("004", "002"): 0,
        ("004", "003"): 0,
    }
    sat = compute_individual_satisfaction("001", four_person_group, score_map_4)
    assert sat == Fraction(2, 3), f"001 の満足度が違います: {sat}"


# --- --deterministic で workers=1 になること ---

def test_deterministic_forces_workers_1(monkeypatch):
    """--deterministic を指定すると workers が 1 になる"""
    from src import optimize as opt_module

    actual_workers = []
    original_solve_stage = opt_module._solve_stage

    def mock_solve_stage(model, time_limit, workers, seed, hint_vars=None, hint_values=None):
        actual_workers.append(workers)
        return original_solve_stage(model, time_limit, workers, seed, hint_vars, hint_values)

    monkeypatch.setattr(opt_module, "_solve_stage", mock_solve_stage)

    optimize(
        participants=PARTICIPANTS_4,
        ratings=RATINGS_4,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=[],
        top_n=1,
        time_limit=30,
        workers=4,   # workers=4 を指定しても
        seed=0,
        deterministic=True,  # deterministic なら 1 になる
    )

    assert all(w == 1 for w in actual_workers), \
        f"deterministic なのに workers={actual_workers} になっています"


# --- 終了コードのテスト ---

def test_exit_code_0(tmp_path):
    """正常終了は終了コード 0"""
    from main import main
    data_dir = make_data_dir(PARTICIPANTS_4, RATINGS_4, GROUP_SIZES_2x2)
    code = main([
        "--data-dir", str(data_dir),
        "--output-dir", str(tmp_path / "output"),
        "--top-n", "1",
        "--time-limit", "30",
        "--workers", "1",
    ])
    assert code == 0


def test_exit_code_1():
    """引数エラーは終了コード 1"""
    from main import main
    code = main(["--unknown-arg", "x"])
    assert code == 1


def test_exit_code_2(tmp_path):
    """入力検証エラーは終了コード 2"""
    from main import main
    # 不正な participants.csv を作る
    _write_participants_csv(tmp_path / "participants.csv", [["01", "Alice"]])
    _write_ratings_csv(tmp_path / "master_ratings.csv", _std_ratings_rows())
    _write_constraints_json(tmp_path / "constraints.json", {"group_sizes": [2, 2]})
    code = main([
        "--data-dir", str(tmp_path),
        "--output-dir", str(tmp_path / "output"),
        "--top-n", "1",
        "--time-limit", "30",
    ])
    assert code == 2


def test_exit_code_3(tmp_path):
    """解なしは終了コード 3"""
    from main import main
    # 001 が全員と同席不可 → 解なし
    ratings_with_incompatible = [
        Rating("001", "002", 2, True),
        Rating("001", "003", 0, True),
        Rating("001", "004", -1, True),
        Rating("002", "001", 1, False),
        Rating("002", "003", -2, False),
        Rating("002", "004", 0, False),
        Rating("003", "001", 0, False),
        Rating("003", "002", -1, False),
        Rating("003", "004", 2, False),
        Rating("004", "001", -2, False),
        Rating("004", "002", 1, False),
        Rating("004", "003", 2, False),
    ]
    data_dir = make_data_dir(PARTICIPANTS_4, ratings_with_incompatible, GROUP_SIZES_2x2)
    code = main([
        "--data-dir", str(data_dir),
        "--output-dir", str(tmp_path / "output"),
        "--top-n", "1",
        "--time-limit", "30",
        "--workers", "1",
    ])
    assert code == 3


# --- §8.8 目的値照合ロジックのテスト ---

from src.optimize import _verify_objective


def test_verify_objective_match():
    """段階1・2・3で値が一致するとき例外が発生しないこと"""
    # 段階1（最低満足度）
    _verify_objective(1, 5, 5)      # 例外なし

    # 段階2（合計満足度）
    _verify_objective(2, 100, 100)  # 例外なし

    # 段階3（分散）
    _verify_objective(3, 0, 0)      # 例外なし


def test_verify_objective_mismatch_stage1():
    """段階1で値が不一致のとき ValueError が発生すること"""
    with pytest.raises(ValueError, match="段階1"):
        _verify_objective(1, 5, 6)


def test_verify_objective_mismatch_stage2():
    """段階2で値が不一致のとき ValueError が発生すること"""
    with pytest.raises(ValueError, match="段階2"):
        _verify_objective(2, 100, 99)


def test_verify_objective_mismatch_stage3():
    """段階3で値が不一致のとき ValueError が発生すること"""
    with pytest.raises(ValueError, match="段階3"):
        _verify_objective(3, 50, 51)


# --- §9.3 end_reason のテスト ---

def test_end_reason_time_limit():
    """
    time_limit=0.001 秒（極端に短い）で optimize() を呼んだとき、
    end_reason が有効な値であることを確認する。

    UNKNOWN になるかは環境依存のため、以下のすべての値を許容する:
    - 候補が得られた場合: "no_solution_in_time_limit", "no_more_solutions", "top_n_reached"
    - 候補が0件の場合: "UNKNOWN", "INFEASIBLE"（最初から解が得られなかった）
    """
    # 有効な end_reason の全候補
    valid_end_reasons = {
        "no_solution_in_time_limit",
        "no_more_solutions",
        "top_n_reached",
        "UNKNOWN",
        "INFEASIBLE",
        "MODEL_INVALID",
    }

    candidates, end_reason = optimize(
        participants=PARTICIPANTS_4,
        ratings=RATINGS_4,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=[],
        top_n=10,
        time_limit=0.001,
        workers=1,
        seed=0,
    )

    assert end_reason in valid_end_reasons, \
        f"end_reason が無効な値です: {end_reason!r}（有効値: {valid_end_reasons}）"

    if candidates:
        # 候補が得られていた場合は、end_reason の意味が一致しているか確認
        # （空でないなら "top_n_reached" か "no_solution_in_time_limit" か "no_more_solutions" のはず）
        assert end_reason in {"no_solution_in_time_limit", "no_more_solutions", "top_n_reached"}, \
            f"候補が存在するのに end_reason が {end_reason!r} になっています"
    else:
        # 候補が空の場合は最初から解が得られなかった可能性がある
        assert end_reason in {"UNKNOWN", "INFEASIBLE", "MODEL_INVALID",
                              "no_solution_in_time_limit", "no_more_solutions"}, \
            f"候補なしのとき end_reason が {end_reason!r} になっています"


# --- N=6 全列挙検算テスト ---

def test_n6_exhaustive_check():
    """
    N=6（[3,3]）の全分割を列挙し、optimize() の上位候補と照合する。
    乱数シード42固定で評価点と同席不可を生成する。
    """
    import random
    random.seed(42)

    # 参加者の生成
    pids_n6 = [f"{i:03d}" for i in range(1, 7)]
    participants_n6 = [Participant(pid, f"Person{pid}") for pid in pids_n6]
    group_sizes_n6 = [3, 3]

    # 全ペア（自己評価を除く）に対してスコアを -2〜+2 の範囲でランダム生成
    score_map_n6: dict[tuple[str, str], int] = {}
    for rater in pids_n6:
        for target in pids_n6:
            if rater != target:
                score_map_n6[(rater, target)] = random.randint(-2, 2)

    # 一部の組を同席不可に設定（ランダムに2組選ぶ）
    all_pairs = [
        (min(a, b), max(a, b))
        for a in pids_n6
        for b in pids_n6
        if a < b
    ]
    incompat_pairs_n6 = random.sample(all_pairs, 2)

    # Rating オブジェクトのリストを作成
    ratings_n6 = [
        Rating(rater, target, score_map_n6[(rater, target)], False)
        for rater in pids_n6
        for target in pids_n6
        if rater != target
    ]

    # optimize() を実行
    candidates, _ = optimize(
        participants=participants_n6,
        ratings=ratings_n6,
        group_sizes=group_sizes_n6,
        incompatible_pairs=incompat_pairs_n6,
        top_n=20,
        time_limit=30,
        workers=1,
        seed=0,
    )

    # _enumerate_all_splits() で全分割を列挙
    all_splits = _enumerate_all_splits(
        participants_n6,
        group_sizes_n6,
        incompat_pairs_n6,
        score_map_n6,
    )

    # 辞書式順序でソート（最低満足度降順 → 合計満足度降順 → 分散昇順）
    all_splits.sort(key=lambda x: (-x[0], -x[1], x[2]))

    # 候補件数の確認（top_n=20 または全分割件数のうち少ない方）
    expected_count = min(len(all_splits), 20)
    assert len(candidates) == expected_count, \
        f"候補数が違います: {len(candidates)} vs {expected_count}"

    # 各順位の指標を照合
    for i, (cand, (exp_min, exp_sum, exp_var, _)) in enumerate(zip(candidates, all_splits)):
        metrics = compute_candidate_metrics(cand.satisfactions)
        assert metrics.min_satisfaction == exp_min, \
            f"候補{i + 1}: 最低満足度 {metrics.min_satisfaction} != {exp_min}"
        assert metrics.sum_satisfaction == exp_sum, \
            f"候補{i + 1}: 合計満足度 {metrics.sum_satisfaction} != {exp_sum}"
        assert metrics.variance == exp_var, \
            f"候補{i + 1}: 分散 {metrics.variance} != {exp_var}"


# --- N=7 全列挙検算テスト ---

def test_n7_exhaustive_check():
    """
    N=7（[3,4]）の全分割を列挙し、optimize() の上位候補と照合する。
    乱数シード99固定で評価点と同席不可を生成する。
    """
    import random
    random.seed(99)

    # 参加者の生成（"001"〜"007"）
    pids_n7 = [f"{i:03d}" for i in range(1, 8)]
    participants_n7 = [Participant(pid, f"Person{pid}") for pid in pids_n7]
    group_sizes_n7 = [3, 4]

    # 全ペア（自己評価を除く42組）に対してスコアを -2〜+2 の範囲でランダム生成
    score_map_n7: dict[tuple[str, str], int] = {}
    for rater in pids_n7:
        for target in pids_n7:
            if rater != target:
                score_map_n7[(rater, target)] = random.randint(-2, 2)

    # 一部の組を同席不可に設定（ランダムに2組選ぶ）
    all_pairs = [
        (min(a, b), max(a, b))
        for a in pids_n7
        for b in pids_n7
        if a < b
    ]
    incompat_pairs_n7 = random.sample(all_pairs, 2)

    # Rating オブジェクトのリストを作成
    ratings_n7 = [
        Rating(rater, target, score_map_n7[(rater, target)], False)
        for rater in pids_n7
        for target in pids_n7
        if rater != target
    ]

    # optimize() を実行
    candidates, _ = optimize(
        participants=participants_n7,
        ratings=ratings_n7,
        group_sizes=group_sizes_n7,
        incompatible_pairs=incompat_pairs_n7,
        top_n=20,
        time_limit=30,
        workers=1,
        seed=0,
    )

    # _enumerate_all_splits() で全分割を列挙
    all_splits = _enumerate_all_splits(
        participants_n7,
        group_sizes_n7,
        incompat_pairs_n7,
        score_map_n7,
    )

    # 辞書式順序でソート（最低満足度降順 → 合計満足度降順 → 分散昇順）
    all_splits.sort(key=lambda x: (-x[0], -x[1], x[2]))

    # 候補件数の確認（top_n=20 または全分割件数のうち少ない方）
    expected_count = min(len(all_splits), 20)
    assert len(candidates) == expected_count, \
        f"候補数が違います: {len(candidates)} vs {expected_count}"

    # 各順位の指標を照合
    for i, (cand, (exp_min, exp_sum, exp_var, _)) in enumerate(zip(candidates, all_splits)):
        metrics = compute_candidate_metrics(cand.satisfactions)
        assert metrics.min_satisfaction == exp_min, \
            f"候補{i + 1}: 最低満足度 {metrics.min_satisfaction} != {exp_min}"
        assert metrics.sum_satisfaction == exp_sum, \
            f"候補{i + 1}: 合計満足度 {metrics.sum_satisfaction} != {exp_sum}"
        assert metrics.variance == exp_var, \
            f"候補{i + 1}: 分散 {metrics.variance} != {exp_var}"


# --- §12.3 極端な time_limit での FEASIBLE テスト（タスク 5.1） ---

def test_short_time_limit_no_optimal():
    """
    §12.3: time_limit が極端に短いとき、候補ステータスが "OPTIMAL" にならないこと。

    - time_limit=0.001 秒で optimize() を呼ぶ
    - 候補が1件以上得られた場合: すべての候補の status が "OPTIMAL" でないことを確認
    - 候補が0件の場合: end_reason が有効な終了理由であることを確認（終了コード3相当）
    """
    # 有効な終了理由の一覧
    valid_end_reasons = {
        "INFEASIBLE",
        "UNKNOWN",
        "no_more_solutions",
        "no_solution_in_time_limit",
        "top_n_reached",
        "MODEL_INVALID",
    }

    candidates, end_reason = optimize(
        participants=PARTICIPANTS_4,
        ratings=RATINGS_4,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=[],
        top_n=10,
        time_limit=0.001,
        workers=1,
        seed=0,
    )

    if candidates:
        # 候補が得られた場合: FEASIBLE 等であり OPTIMAL ではないこと
        for i, cand in enumerate(candidates):
            assert cand.status != "OPTIMAL", (
                f"候補{i + 1} のステータスが 'OPTIMAL' になっています。"
                f"time_limit=0.001 秒では OPTIMAL は不正です。"
            )
    else:
        # 候補が0件の場合: 有効な終了理由であることを確認（終了コード3相当）
        assert end_reason in valid_end_reasons, (
            f"候補なし・end_reason が無効な値です: {end_reason!r}"
            f"（有効値: {valid_end_reasons}）"
        )
