"""
conftest.py
テスト共通のフィクスチャ定義。
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
from fractions import Fraction
from pathlib import Path

import pytest

from src.import_data import Participant, Rating


# --- §12.1 の基準となる小さな例 ---

PARTICIPANTS_4 = [
    Participant("001", "Alice"),
    Participant("002", "Bob"),
    Participant("003", "Carol"),
    Participant("004", "Dave"),
]

# §12.1 の master_ratings.csv
RATINGS_4 = [
    Rating("001", "002", 2, False),
    Rating("001", "003", 0, False),
    Rating("001", "004", -1, False),
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

GROUP_SIZES_2x2 = [2, 2]


def make_data_dir(
    participants: list[Participant],
    ratings: list[Rating],
    group_sizes: list[int],
    extra_constraints: dict | None = None,
) -> Path:
    """
    一時ディレクトリに入力ファイルを作成し、そのパスを返す。
    テスト終了後に tmpdir は自動削除されない（tmp_path フィクスチャを使う場合は tmp_path を渡す）。
    """
    tmpdir = Path(tempfile.mkdtemp())

    # participants.csv
    with (tmpdir / "participants.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name"])
        for p in participants:
            writer.writerow([p.id, p.name])

    # master_ratings.csv
    with (tmpdir / "master_ratings.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rater_id", "target_id", "score", "incompatible", "response_id", "submitted_at"])
        for r in ratings:
            writer.writerow([
                r.rater_id, r.target_id, r.score,
                "true" if r.incompatible else "false",
                "test", "2026-10-08T12:00:00+09:00",
            ])

    # constraints.json
    constraints = {"group_sizes": group_sizes}
    if extra_constraints:
        constraints.update(extra_constraints)
    with (tmpdir / "constraints.json").open("w", encoding="utf-8") as f:
        json.dump(constraints, f)

    return tmpdir


@pytest.fixture
def data_dir_4(tmp_path):
    """§12.1 の N=4 標準入力データ"""
    return make_data_dir(PARTICIPANTS_4, RATINGS_4, GROUP_SIZES_2x2)
