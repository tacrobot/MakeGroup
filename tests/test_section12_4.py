"""
test_section12_4.py
§12.4 出力とプライバシーのテスト。

- candidates.md / candidates.json / run_info.json に評価点・同席不可の指定が含まれないこと。
- same_group の要素が id と name だけであること。
- 出力されたグループ構成と順位が入力データおよび最適化結果と一致すること。
"""

from __future__ import annotations

import json
import re
import tempfile
from fractions import Fraction
from pathlib import Path

import pytest

from src.import_data import build_incompatible_pairs
from src.metrics import compute_all_satisfactions, compute_candidate_metrics
from src.optimize import optimize
from src.output import (
    generate_candidates_json,
    generate_candidates_md,
    generate_run_info_json,
    write_outputs,
)
from tests.conftest import PARTICIPANTS_4, RATINGS_4, GROUP_SIZES_2x2, make_data_dir


def _run_and_write(tmp_path: Path) -> tuple[list, dict, dict, str]:
    """最適化を実行して出力ファイルを生成し、内容を返す"""
    from datetime import datetime, timezone, timedelta
    JST = timezone(timedelta(hours=9))

    candidates, end_reason = optimize(
        participants=PARTICIPANTS_4,
        ratings=RATINGS_4,
        group_sizes=GROUP_SIZES_2x2,
        incompatible_pairs=[],
        top_n=3,
        time_limit=30,
        workers=1,
        seed=0,
    )

    data_dir = make_data_dir(PARTICIPANTS_4, RATINGS_4, GROUP_SIZES_2x2)
    sub_dir = write_outputs(
        output_dir=tmp_path / "output",
        candidates=candidates,
        participants=PARTICIPANTS_4,
        group_sizes=GROUP_SIZES_2x2,
        scale_L=1,
        run_params={"top_n": 3, "time_limit": 30, "workers": 1, "seed": 0, "deterministic": False},
        end_reason=end_reason,
        data_dir=Path(data_dir),
    )

    md_content = (sub_dir / "candidates.md").read_text(encoding="utf-8")
    json_data = json.loads((sub_dir / "candidates.json").read_text(encoding="utf-8"))
    run_info = json.loads((sub_dir / "run_info.json").read_text(encoding="utf-8"))

    return candidates, json_data, run_info, md_content


# 評価点の生データが含まれてはいけないパターン
# 満足度の分数値（-2, -1 など）は出力に含まれてよい。
# 禁止するのは: +2 などの表記、score フィールド名、incompatible フィールド
_SCORE_PATTERNS = [
    r"\+2", r"\+1",          # フォームの評価点表記（+記号付き）
    r'"score"\s*:',          # score フィールド
    r'"incompatible"\s*:',   # incompatible フィールド
]


def test_no_score_in_md(tmp_path):
    """candidates.md に評価点・同席不可の指定が含まれない"""
    _, _, _, md = _run_and_write(tmp_path)
    for pat in _SCORE_PATTERNS:
        matches = re.findall(pat, md, re.IGNORECASE)
        assert not matches, f"candidates.md にパターン '{pat}' が含まれています: {matches}"


def test_no_score_in_candidates_json(tmp_path):
    """candidates.json に評価点・同席不可の指定が含まれない"""
    _, json_data, _, _ = _run_and_write(tmp_path)
    json_str = json.dumps(json_data)
    for pat in _SCORE_PATTERNS:
        matches = re.findall(pat, json_str, re.IGNORECASE)
        assert not matches, f"candidates.json にパターン '{pat}' が含まれています: {matches}"


def test_no_score_in_run_info(tmp_path):
    """run_info.json に評価点・同席不可の指定が含まれない"""
    _, _, run_info, _ = _run_and_write(tmp_path)
    run_info_str = json.dumps(run_info)
    for pat in _SCORE_PATTERNS:
        matches = re.findall(pat, run_info_str, re.IGNORECASE)
        assert not matches, f"run_info.json にパターン '{pat}' が含まれています: {matches}"


def test_same_group_only_id_and_name(tmp_path):
    """candidates.json の same_group は id と name だけを持つ"""
    _, json_data, _, _ = _run_and_write(tmp_path)
    for cand in json_data["candidates"]:
        for p in cand["participants"]:
            for sg in p["same_group"]:
                allowed_keys = {"id", "name"}
                extra = set(sg.keys()) - allowed_keys
                assert not extra, f"same_group に余分なキー {extra} があります: {sg}"


def test_group_structure_matches_optimizer(tmp_path):
    """candidates.json のグループ構成が最適化結果と一致する"""
    candidates, json_data, _, _ = _run_and_write(tmp_path)
    for cand, json_cand in zip(candidates, json_data["candidates"]):
        # グループメンバーの集合が一致するか
        opt_group_sets = {frozenset(g) for g in cand.groups}
        json_group_sets = {frozenset(g["members"]) for g in json_cand["groups"]}
        assert opt_group_sets == json_group_sets, \
            f"候補{cand.rank}: グループ構成が不一致\n最適化: {opt_group_sets}\nJSON: {json_group_sets}"


def test_metrics_match_optimizer(tmp_path):
    """candidates.json の指標が最適化結果（metrics.py による再計算）と一致する"""
    candidates, json_data, _, _ = _run_and_write(tmp_path)
    for cand, json_cand in zip(candidates, json_data["candidates"]):
        metrics = compute_candidate_metrics(cand.satisfactions)

        json_min = json_cand["metrics"]["min"]["exact"]
        json_sum = json_cand["metrics"]["sum"]["exact"]

        def parse_exact(s: str) -> Fraction:
            if "/" in s:
                num, den = s.split("/")
                return Fraction(int(num), int(den))
            return Fraction(int(s))

        assert parse_exact(json_min) == metrics.min_satisfaction
        assert parse_exact(json_sum) == metrics.sum_satisfaction


def test_rank_order_in_json(tmp_path):
    """candidates.json の rank は 1 から順番になっている"""
    _, json_data, _, _ = _run_and_write(tmp_path)
    for i, cand in enumerate(json_data["candidates"], start=1):
        assert cand["rank"] == i, f"rank が {i} でなく {cand['rank']} になっています"


def test_output_dir_created(tmp_path):
    """output ディレクトリと日時サブディレクトリが作られる"""
    from main import main
    data_dir = make_data_dir(PARTICIPANTS_4, RATINGS_4, GROUP_SIZES_2x2)
    output_dir = tmp_path / "output"
    code = main([
        "--data-dir", str(data_dir),
        "--output-dir", str(output_dir),
        "--top-n", "1",
        "--time-limit", "30",
        "--workers", "1",
    ])
    assert code == 0
    sub_dirs = list(output_dir.iterdir())
    assert len(sub_dirs) == 1
    sub_dir = sub_dirs[0]
    assert (sub_dir / "candidates.md").exists()
    assert (sub_dir / "candidates.json").exists()
    assert (sub_dir / "run_info.json").exists()
