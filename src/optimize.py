"""
optimize.py
CP-SAT による最適化モジュール。
仕様書 §8 に定義された3段階辞書式最適化と上位候補列挙を実装する。

3段階:
  段階1: 最低満足度 m を最大化
  段階2: m* を保ったまま合計満足度 T を最大化
  段階3: T* を保ったまま満足度の分散（Q = Σ U_i^2 の最小化）を最小化

上位候補の列挙: §8.5 の除外制約を使ったイテレーション
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import NamedTuple

from ortools.sat.python import cp_model

from src.import_data import Participant, Rating
from src.metrics import compute_all_satisfactions, compute_scale_L


@dataclass
class SolveResult:
    """ソルバー1回の結果"""
    status: str           # OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN / MODEL_INVALID / SKIPPED
    objective: int | None
    elapsed: float        # 秒


@dataclass
class CandidateResult:
    """候補1件の結果"""
    rank: int
    groups: list[list[str]]           # グループごとのメンバーIDリスト
    satisfactions: dict[str, Fraction]
    status: str                        # OPTIMAL / FEASIBLE
    stage_status: dict[str, str]       # {"min": ..., "sum": ..., "variance": ...}
    solve_results: list[SolveResult]   # 各段階のソルバー結果
    scale_L: int


def _status_str(status: int) -> str:
    """CP-SAT のステータス番号を文字列に変換する"""
    return {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.UNKNOWN: "UNKNOWN",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
    }.get(status, f"UNKNOWN({status})")


def _verify_objective(stage: int, solver_value: int, recalc_value: int) -> None:
    """OPTIMAL 時にソルバーの目的値と再計算値が一致するか照合する。

    一致しない場合は ValueError を raise する（呼び出し元で終了コード4として処理）。

    Args:
        stage:        段階番号（1=最低満足度, 2=合計満足度, 3=分散）
        solver_value: ソルバーが返した目的値
        recalc_value: metrics.py による再計算値
    """
    if solver_value != recalc_value:
        raise ValueError(
            f"段階{stage} の目的値不一致: ソルバー={solver_value}, 再計算={recalc_value}"
        )


def _build_base_model(
    n: int,
    k: int,
    sorted_sizes: list[int],
    incompatible_pairs: list[tuple[str, str]],
    score_map: dict[tuple[str, str], int],
    pid_list: list[str],
    pid_to_idx: dict[str, int],
    scale_L: int,
    w: list[int],
) -> tuple[cp_model.CpModel, dict, dict, dict]:
    """
    CP-SAT の基本モデルを構築する。
    戻り値: (model, x_vars, p_vars, U_vars)
    """
    model = cp_model.CpModel()

    # x[i, g]: 参加者 i がグループ g に所属するか（0/1）
    x: dict[tuple[int, int], cp_model.IntVar] = {}
    for i in range(n):
        for g in range(k):
            x[(i, g)] = model.new_bool_var(f"x_{i}_{g}")

    # 制約1: すべての参加者がちょうど1つのグループに所属
    for i in range(n):
        model.add(sum(x[(i, g)] for g in range(k)) == 1)

    # 制約2: 各グループの人数
    for g in range(k):
        model.add(sum(x[(i, g)] for i in range(n)) == sorted_sizes[g])

    # 制約3: 同席不可の組
    for pid_a, pid_b in incompatible_pairs:
        if pid_a in pid_to_idx and pid_b in pid_to_idx:
            ia = pid_to_idx[pid_a]
            ib = pid_to_idx[pid_b]
            for g in range(k):
                model.add(x[(ia, g)] + x[(ib, g)] <= 1)

    # 対称性の除去: 人数が同じで隣り合うグループ g-1 と g について
    # i 番目の参加者が g に所属するなら、i より小さい番号の参加者が少なくとも1人 g-1 に所属する
    for g in range(1, k):
        if sorted_sizes[g] == sorted_sizes[g - 1]:
            for i in range(n):
                model.add(
                    x[(i, g)] <= sum(x[(kk, g - 1)] for kk in range(i))
                )

    # p[i, j, g]: i と j がともにグループ g に所属する（i < j）
    p: dict[tuple[int, int, int], cp_model.IntVar] = {}
    for i in range(n):
        for j in range(i + 1, n):
            for g in range(k):
                p_var = model.new_bool_var(f"p_{i}_{j}_{g}")
                p[(i, j, g)] = p_var
                model.add(p_var <= x[(i, g)])
                model.add(p_var <= x[(j, g)])
                model.add(p_var >= x[(i, g)] + x[(j, g)] - 1)

    # U_i の上下限
    u_min = -2 * scale_L
    u_max = 2 * scale_L

    # U_i = Σ_g w_g × Σ_{j≠i} score[i][j] × p[min(i,j), max(i,j), g]
    U: dict[int, cp_model.IntVar] = {}
    for i in range(n):
        terms = []
        for g in range(k):
            for j in range(n):
                if i == j:
                    continue
                s = score_map.get((pid_list[i], pid_list[j]), 0)
                if s == 0:
                    continue
                mi, mj = (i, j) if i < j else (j, i)
                terms.append(w[g] * s * p[(mi, mj, g)])

        u_var = model.new_int_var(u_min, u_max, f"U_{i}")
        U[i] = u_var
        if terms:
            model.add(u_var == sum(terms))
        else:
            model.add(u_var == 0)

    return model, x, p, U


def _run_solver(
    model: cp_model.CpModel,
    time_limit: float,
    workers: int,
    seed: int,
) -> tuple[cp_model.CpSolver, int]:
    """CP-SAT ソルバーを実行して (solver, status_int) を返す"""
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = seed
    status = solver.solve(model)
    return solver, status


def _solve_stage(
    model: cp_model.CpModel,
    time_limit: float,
    workers: int,
    seed: int,
    hint_vars: list | None = None,
    hint_values: list | None = None,
) -> tuple[cp_model.CpSolver, int]:
    """ソルバーを実行する（hint_vars/hint_values は model.add_hint で既に設定済みを前提）"""
    return _run_solver(model, time_limit, workers, seed)


def _extract_groups(
    solver: cp_model.CpSolver,
    x: dict,
    pid_list: list[str],
    n: int,
    k: int,
) -> list[list[str]]:
    """ソルバーの解からグループ割り当てを取り出す"""
    groups: list[list[str]] = [[] for _ in range(k)]
    for i in range(n):
        for g in range(k):
            if solver.value(x[(i, g)]) == 1:
                groups[g].append(pid_list[i])
                break
    return groups


def _add_hint_from_groups(
    model: cp_model.CpModel,
    x: dict,
    groups: list[list[str]],
    pid_to_idx: dict[str, int],
    n: int,
    k: int,
) -> None:
    """現在のグループ割り当てをヒントとして設定する"""
    pid_group: dict[str, int] = {}
    for g_idx, members in enumerate(groups):
        for pid in members:
            pid_group[pid] = g_idx

    for pid, i in pid_to_idx.items():
        g_assigned = pid_group.get(pid, 0)
        for g in range(k):
            model.add_hint(x[(i, g)], 1 if g == g_assigned else 0)


def _groups_to_exclusion(
    groups: list[list[str]],
    pid_to_idx: dict[str, int],
) -> list[tuple[int, int]]:
    """グループ割り当てを除外制約用の (i, g) リストに変換する"""
    result = []
    for g_idx, members in enumerate(groups):
        for pid in members:
            result.append((pid_to_idx[pid], g_idx))
    return result


def optimize(
    participants: list[Participant],
    ratings: list[Rating],
    group_sizes: list[int],
    incompatible_pairs: list[tuple[str, str]],
    top_n: int = 10,
    time_limit: float = 60.0,
    workers: int = 8,
    seed: int = 0,
    deterministic: bool = False,
    progress_callback=None,
) -> tuple[list[CandidateResult], str]:
    """
    上位 top_n 件の候補を列挙する。
    progress_callback(rank, stage, status_str) を指定すると進捗を通知する。
    戻り値: (candidates, end_reason)
    end_reason:
      "top_n_reached" / "no_more_solutions"
      または候補1で解なし時の "INFEASIBLE" / "UNKNOWN" / "MODEL_INVALID"
    """
    if deterministic:
        workers = 1

    sorted_sizes = sorted(group_sizes)
    scale_L = compute_scale_L(sorted_sizes)
    n = len(participants)
    k = len(sorted_sizes)

    # 参加者の ID リスト（ID 昇順）
    pid_list = [p.id for p in participants]
    pid_to_idx: dict[str, int] = {p.id: i for i, p in enumerate(participants)}

    # score_map: {(rater_id, target_id): score}
    score_map: dict[tuple[str, str], int] = {
        (r.rater_id, r.target_id): r.score for r in ratings
    }

    # w_g = L / (s_g - 1)
    w = [scale_L // (sorted_sizes[g] - 1) for g in range(k)]

    candidates: list[CandidateResult] = []
    excluded_assignments: list[list[tuple[int, int]]] = []
    end_reason = "no_more_solutions"

    for rank in range(1, top_n + 1):
        # ============================
        # 基本モデルの構築
        # ============================
        model, x, p, U = _build_base_model(
            n, k, sorted_sizes, incompatible_pairs,
            score_map, pid_list, pid_to_idx, scale_L, w,
        )

        # 除外制約: 既存候補の分割を除外
        for excl in excluded_assignments:
            model.add(sum(x[(i, g)] for (i, g) in excl) <= n - 1)

        # ============================
        # 段階1: 最低満足度 m を最大化
        # ============================
        m_var = model.new_int_var(-2 * scale_L, 2 * scale_L, "m")
        for i in range(n):
            model.add(m_var <= U[i])
        model.maximize(m_var)

        if progress_callback:
            progress_callback(rank, 1, "実行中")

        t0 = time.time()
        solver1, s1_int = _run_solver(model, time_limit, workers, seed)
        elapsed1 = time.time() - t0
        s1 = _status_str(s1_int)

        if progress_callback:
            progress_callback(rank, 1, s1)

        if s1_int not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if rank == 1:
                return [], s1
            else:
                # UNKNOWN（時間切れ）と INFEASIBLE（解なし）で終了理由を区別する
                if s1_int == cp_model.UNKNOWN:
                    end_reason = "no_solution_in_time_limit"
                else:
                    end_reason = "no_more_solutions"
                break

        m_star = int(round(solver1.objective_value))
        groups1 = _extract_groups(solver1, x, pid_list, n, k)

        # 段階1 OPTIMAL 時: m* を再計算値と照合する
        if s1 == "OPTIMAL":
            sat1 = compute_all_satisfactions(groups1, score_map)
            m_recalc = int(scale_L * min(u_i for u_i in sat1.values()))
            _verify_objective(1, m_star, m_recalc)

        # ============================
        # 段階2: 合計満足度 T を最大化
        # ============================
        # 段階1の解をヒントとして設定
        _add_hint_from_groups(model, x, groups1, pid_to_idx, n, k)

        # m >= m* の制約を追加（目的関数を変更）
        model.add(m_var >= m_star)

        T_var = model.new_int_var(-2 * scale_L * n, 2 * scale_L * n, "T")
        model.add(T_var == sum(U[i] for i in range(n)))
        model.maximize(T_var)

        if progress_callback:
            progress_callback(rank, 2, "実行中")

        t0 = time.time()
        solver2, s2_int = _run_solver(model, time_limit, workers, seed)
        elapsed2 = time.time() - t0
        s2 = _status_str(s2_int)

        if progress_callback:
            progress_callback(rank, 2, s2)

        if s2_int not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # 段階2で解なし → 段階1の解を候補として採用
            groups_final = groups1
            satisfactions = compute_all_satisfactions(groups_final, score_map)
            excl = _groups_to_exclusion(groups_final, pid_to_idx)
            excluded_assignments.append(excl)
            candidates.append(CandidateResult(
                rank=rank,
                groups=groups_final,
                satisfactions=satisfactions,
                status="FEASIBLE",
                stage_status={"min": s1, "sum": s2, "variance": "SKIPPED"},
                solve_results=[
                    SolveResult(s1, m_star, elapsed1),
                    SolveResult(s2, None, elapsed2),
                    SolveResult("SKIPPED", None, 0.0),
                ],
                scale_L=scale_L,
            ))
            continue

        T_star = int(round(solver2.objective_value))
        groups2 = _extract_groups(solver2, x, pid_list, n, k)

        # 段階2 OPTIMAL 時: T* を再計算値と照合する
        if s2 == "OPTIMAL":
            sat2 = compute_all_satisfactions(groups2, score_map)
            T_recalc = int(scale_L * sum(sat2.values()))
            _verify_objective(2, T_star, T_recalc)

        # ============================
        # 段階3: 分散最小化（Q = Σ U_i^2 の最小化）
        # ============================
        _add_hint_from_groups(model, x, groups2, pid_to_idx, n, k)

        model.add(T_var == T_star)

        # Q = Σ U_i^2
        sq_vars = []
        for i in range(n):
            sq = model.new_int_var(0, (2 * scale_L) ** 2, f"sq_{i}")
            model.add_multiplication_equality(sq, [U[i], U[i]])
            sq_vars.append(sq)
        Q_var = model.new_int_var(0, (2 * scale_L) ** 2 * n, "Q")
        model.add(Q_var == sum(sq_vars))
        model.minimize(Q_var)

        if progress_callback:
            progress_callback(rank, 3, "実行中")

        t0 = time.time()
        solver3, s3_int = _run_solver(model, time_limit, workers, seed)
        elapsed3 = time.time() - t0
        s3 = _status_str(s3_int)

        if progress_callback:
            progress_callback(rank, 3, s3)

        if s3_int not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # 段階3で解なし → 段階2の解を候補として採用
            groups_final = groups2
        else:
            groups_final = _extract_groups(solver3, x, pid_list, n, k)

        # 段階3 OPTIMAL 時: Q を再計算値と照合する
        if s3 == "OPTIMAL":
            sat3 = compute_all_satisfactions(groups_final, score_map)
            Q_recalc = sum(int(scale_L * u_i) ** 2 for u_i in sat3.values())
            Q_solver = int(round(solver3.objective_value))
            _verify_objective(3, Q_solver, Q_recalc)

        s3_final = s3 if s3_int in (cp_model.OPTIMAL, cp_model.FEASIBLE) else "FEASIBLE"

        all_optimal = (s1 == "OPTIMAL" and s2 == "OPTIMAL" and s3_final == "OPTIMAL")

        satisfactions = compute_all_satisfactions(groups_final, score_map)
        excl = _groups_to_exclusion(groups_final, pid_to_idx)
        excluded_assignments.append(excl)

        candidates.append(CandidateResult(
            rank=rank,
            groups=groups_final,
            satisfactions=satisfactions,
            status="OPTIMAL" if all_optimal else "FEASIBLE",
            stage_status={"min": s1, "sum": s2, "variance": s3_final},
            solve_results=[
                SolveResult(s1, m_star, elapsed1),
                SolveResult(s2, T_star, elapsed2),
                SolveResult(s3_final, int(round(solver3.objective_value)) if s3_int in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None, elapsed3),
            ],
            scale_L=scale_L,
        ))

    if len(candidates) >= top_n:
        end_reason = "top_n_reached"

    return candidates, end_reason
