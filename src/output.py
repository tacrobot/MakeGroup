"""
output.py
出力ファイル生成モジュール。
仕様書 §9 に定義された candidates.md / candidates.json / run_info.json を生成する。

プライバシー規則 (§11):
- 評価点・同席不可の指定は、すべての出力に含めてはならない。
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from src.import_data import Participant
from src.metrics import (
    CandidateMetrics,
    GroupMetrics,
    compute_candidate_metrics,
    compute_group_metrics,
    fraction_to_display,
    fraction_to_exact_str,
    fraction_to_float6,
)
from src.optimize import CandidateResult

# 日本標準時
JST = timezone(timedelta(hours=9))


def _now_jst() -> datetime:
    return datetime.now(tz=JST)


def _sha256_file(path: Path) -> str:
    """ファイルの SHA-256 を計算する"""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return "unknown"


def _format_fraction_pair(f: Fraction, decimal_places: int = 2) -> dict:
    """value（小数）と exact（分数文字列）のペアを返す"""
    return {
        "value": fraction_to_float6(f),
        "exact": fraction_to_exact_str(f),
    }


def _is_ordering_guaranteed(candidates: list[CandidateResult]) -> bool:
    """すべての候補が OPTIMAL のとき、順位は保証される"""
    return all(c.status == "OPTIMAL" for c in candidates)


def generate_candidates_md(
    candidates: list[CandidateResult],
    participants: list[Participant],
    group_sizes: list[int],
    run_time: datetime,
) -> str:
    """candidates.md の文字列を生成する"""
    lines: list[str] = []
    n = len(participants)
    pid_name = {p.id: p.name for p in participants}

    lines.append("# グループ分け候補\n")
    lines.append(f"実行日時: {run_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"参加者数: {n}")
    sizes_str = ", ".join(str(s) for s in sorted(group_sizes))
    lines.append(f"人数構成: {sizes_str}")

    ordering_ok = _is_ordering_guaranteed(candidates)
    if not ordering_ok:
        lines.append("")
        lines.append(
            "⚠️ 注意: 一部の候補が FEASIBLE のため、候補の順位は最適性が保証されません。"
        )

    for cand in candidates:
        sats = cand.satisfactions
        metrics = compute_candidate_metrics(sats)
        group_metrics = compute_group_metrics(
            cand.groups, group_sizes, sats
        )

        lines.append("")
        lines.append(f"## 候補{cand.rank}（状態: {cand.status}）")
        lines.append("")
        lines.append(f"最低満足度: {fraction_to_display(metrics.min_satisfaction)}")
        lines.append(f"合計満足度: {fraction_to_display(metrics.sum_satisfaction)}")
        lines.append(f"平均満足度: {fraction_to_display(metrics.mean_satisfaction)}")
        lines.append(f"分散: {fraction_to_display(metrics.variance)}")
        lines.append("")

        # グループ
        lines.append("### グループ")
        lines.append("")
        for gm in group_metrics:
            lines.append(
                f"グループ{gm.group_no}（{gm.size}人、平均満足度 "
                f"{fraction_to_display(gm.mean_satisfaction)}）"
            )
            for pid in gm.members:
                lines.append(f"{pid} {pid_name.get(pid, '')}")
            lines.append("")

        # 個人ごとの詳細
        lines.append("### 個人ごとの詳細")
        lines.append("")

        # グループメンバーの逆引き
        pid_to_group: dict[str, int] = {}
        pid_to_members: dict[str, list[str]] = {}
        for gm in group_metrics:
            for pid in gm.members:
                pid_to_group[pid] = gm.group_no
                pid_to_members[pid] = [m for m in gm.members if m != pid]

        for p in participants:
            pid = p.id
            group_no = pid_to_group.get(pid, 0)
            sat = sats.get(pid, Fraction(0))
            same_group = sorted(pid_to_members.get(pid, []))

            lines.append(f"{pid} {p.name}")
            lines.append(f"所属: グループ{group_no}")
            lines.append(f"満足度: {fraction_to_display(sat)}")
            lines.append("同じグループ:")
            for mpid in same_group:
                lines.append(f"{mpid} {pid_name.get(mpid, '')}")
            lines.append("")

    return "\n".join(lines) + "\n"


def generate_candidates_json(
    candidates: list[CandidateResult],
    participants: list[Participant],
    group_sizes: list[int],
    scale_L: int,
) -> dict:
    """candidates.json の辞書を生成する"""
    pid_name = {p.id: p.name for p in participants}

    candidates_list: list[dict] = []
    for cand in candidates:
        sats = cand.satisfactions
        metrics = compute_candidate_metrics(sats)
        group_metrics = compute_group_metrics(cand.groups, group_sizes, sats)

        # グループリスト
        groups_json: list[dict] = []
        for gm in group_metrics:
            groups_json.append({
                "group_no": gm.group_no,
                "size": gm.size,
                "mean_satisfaction": _format_fraction_pair(gm.mean_satisfaction),
                "members": gm.members,
            })

        # 参加者ごとの詳細
        pid_to_group: dict[str, int] = {}
        pid_to_members: dict[str, list[str]] = {}
        for gm in group_metrics:
            for pid in gm.members:
                pid_to_group[pid] = gm.group_no
                pid_to_members[pid] = [m for m in gm.members if m != pid]

        participants_json: list[dict] = []
        for p in participants:
            pid = p.id
            same_group_ids = sorted(pid_to_members.get(pid, []))
            # same_group には id と name だけを含む（§9.2）
            same_group_list = [
                {"id": m, "name": pid_name.get(m, "")} for m in same_group_ids
            ]
            participants_json.append({
                "id": pid,
                "name": p.name,
                "group_no": pid_to_group.get(pid, 0),
                "satisfaction": _format_fraction_pair(sats.get(pid, Fraction(0))),
                "same_group": same_group_list,
            })

        candidates_list.append({
            "rank": cand.rank,
            "status": cand.status,
            "stage_status": cand.stage_status,
            "metrics": {
                "min": _format_fraction_pair(metrics.min_satisfaction),
                "sum": _format_fraction_pair(metrics.sum_satisfaction),
                "mean": _format_fraction_pair(metrics.mean_satisfaction),
                "variance": _format_fraction_pair(metrics.variance),
            },
            "groups": groups_json,
            "participants": participants_json,
        })

    return {
        "scale_L": scale_L,
        "candidates": candidates_list,
    }


def generate_run_info_json(
    participants: list[Participant],
    group_sizes: list[int],
    scale_L: int,
    run_params: dict,
    candidates: list[CandidateResult],
    end_reason: str,
    data_dir: Path,
    run_time: datetime,
) -> dict:
    """run_info.json の辞書を生成する"""
    import ortools

    # 入力ファイルの SHA-256
    file_hashes = {}
    for fname in ["participants.csv", "master_ratings.csv", "constraints.json"]:
        fpath = data_dir / fname
        file_hashes[fname] = _sha256_file(fpath)

    # ソルバー実行ログ
    solver_runs: list[dict] = []
    stage_names = ["min", "sum", "variance"]
    for cand in candidates:
        for stage_idx, sr in enumerate(cand.solve_results):
            solver_runs.append({
                "rank": cand.rank,
                "stage": stage_names[stage_idx] if stage_idx < len(stage_names) else f"stage{stage_idx+1}",
                "status": sr.status,
                "elapsed_sec": round(sr.elapsed, 3),
                "objective": sr.objective,
            })

    ordering_guaranteed = _is_ordering_guaranteed(candidates)

    return {
        "run_time": run_time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "participants_count": len(participants),
        "group_sizes": sorted(group_sizes),
        "scale_L": scale_L,
        "parameters": {
            "top_n": run_params.get("top_n"),
            "time_limit": run_params.get("time_limit"),
            "workers": run_params.get("workers"),
            "seed": run_params.get("seed"),
            "deterministic": run_params.get("deterministic", False),
        },
        "versions": {
            "python": sys.version,
            "ortools": ortools.__version__,
        },
        "input_file_sha256": file_hashes,
        "solver_runs": solver_runs,
        "end_reason": end_reason,
        "ordering_guaranteed": ordering_guaranteed,
    }


def write_outputs(
    output_dir: Path,
    candidates: list[CandidateResult],
    participants: list[Participant],
    group_sizes: list[int],
    scale_L: int,
    run_params: dict,
    end_reason: str,
    data_dir: Path,
) -> Path:
    """
    出力ファイルをサブディレクトリに書き込む。
    戻り値: 作成したサブディレクトリのパス
    """
    run_time = _now_jst()
    sub_dir_name = run_time.strftime("%Y%m%d_%H%M%S")
    sub_dir = output_dir / sub_dir_name
    sub_dir.mkdir(parents=True, exist_ok=True)

    # candidates.md
    md_content = generate_candidates_md(candidates, participants, group_sizes, run_time)
    (sub_dir / "candidates.md").write_text(md_content, encoding="utf-8")

    # candidates.json
    json_data = generate_candidates_json(candidates, participants, group_sizes, scale_L)
    (sub_dir / "candidates.json").write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # run_info.json
    run_info = generate_run_info_json(
        participants, group_sizes, scale_L, run_params, candidates, end_reason, data_dir, run_time
    )
    (sub_dir / "run_info.json").write_text(
        json.dumps(run_info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return sub_dir
