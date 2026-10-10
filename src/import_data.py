"""
import_data.py
CSV/JSON の読み込みと incompatible_pairs の生成を担うモジュール。
- 参加者IDは必ず文字列として保持する。
- incompatible_pairs は master_ratings.csv の incompatible 列から生成する。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import NamedTuple


class Participant(NamedTuple):
    """参加者を表す値オブジェクト"""
    id: str   # 3桁ゼロ埋め文字列
    name: str


class Rating(NamedTuple):
    """評価データ1行を表す値オブジェクト"""
    rater_id: str
    target_id: str
    score: int
    incompatible: bool


class InputData(NamedTuple):
    """全入力データをまとめたコンテナ"""
    participants: list[Participant]
    ratings: list[Rating]
    group_sizes: list[int]
    incompatible_pairs: list[tuple[str, str]]  # 順序なしペア（小さいID, 大きいID）


def load_participants(path: Path) -> list[Participant]:
    """
    participants.csv を読み込む。
    ID は文字列のまま保持する。
    """
    participants: list[Participant] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            participants.append(Participant(
                id=row["id"],
                name=row["name"],
            ))
    return participants


def load_ratings(path: Path) -> list[Rating]:
    """
    master_ratings.csv を読み込む。
    必須列: rater_id, target_id, score, incompatible
    incompatible は大文字小文字を区別せず受け付け、小文字に統一する。
    score は文字列として検証し、int に変換する。
    """
    ratings: list[Rating] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            incompatible_str = row["incompatible"].lower()
            ratings.append(Rating(
                rater_id=row["rater_id"],
                target_id=row["target_id"],
                score=int(row["score"]),
                incompatible=(incompatible_str == "true"),
            ))
    return ratings


def load_constraints(path: Path) -> dict:
    """
    constraints.json を読み込み、辞書として返す。
    """
    with path.open(encoding="utf-8-sig") as f:
        return json.load(f)


def _load_constraints_inner(path: Path) -> dict:
    """constraints.json の読み込み（UTF-8 BOM 対応）"""
    with path.open(encoding="utf-8-sig") as f:
        return json.load(f)


def build_incompatible_pairs(ratings: list[Rating]) -> list[tuple[str, str]]:
    """
    master_ratings.csv の incompatible=true の行から、
    順序のない2人の組を生成する（重複は1組にまとめる）。
    戻り値: [(小さいID, 大きいID), ...] のリスト（ソート済み）
    """
    seen: set[tuple[str, str]] = set()
    for r in ratings:
        if r.incompatible:
            # 文字列として比較（桁固定なので数値順と一致）
            pair = (min(r.rater_id, r.target_id), max(r.rater_id, r.target_id))
            seen.add(pair)
    return sorted(seen)


def load_all(
    data_dir: Path,
) -> tuple[list[Participant] | None,
           list[Rating] | None,
           dict | None,
           list[str]]:
    """
    3つの入力ファイルをすべて読み込む。
    読み込み失敗時は None を返し、エラーメッセージのリストも返す。
    戻り値: (participants, ratings, constraints_dict, read_errors)
    read_errors は F-01 に相当するエラーのリスト。
    """
    participants_path = data_dir / "participants.csv"
    ratings_path = data_dir / "master_ratings.csv"
    constraints_path = data_dir / "constraints.json"

    read_errors: list[str] = []

    # participants.csv
    participants: list[Participant] | None = None
    try:
        participants = load_participants(participants_path)
    except FileNotFoundError:
        read_errors.append(
            f"[F-01] participants.csv: ファイルが存在しないか読み込めません: {participants_path}"
        )
    except Exception as e:
        read_errors.append(
            f"[F-01] participants.csv: 読み込みエラー: {e}"
        )

    # master_ratings.csv
    ratings: list[Rating] | None = None
    try:
        ratings = load_ratings(ratings_path)
    except FileNotFoundError:
        read_errors.append(
            f"[F-01] master_ratings.csv: ファイルが存在しないか読み込めません: {ratings_path}"
        )
    except Exception as e:
        read_errors.append(
            f"[F-01] master_ratings.csv: 読み込みエラー: {e}"
        )

    # constraints.json
    constraints: dict | None = None
    try:
        constraints = _load_constraints_inner(constraints_path)
    except FileNotFoundError:
        read_errors.append(
            f"[F-01] constraints.json: ファイルが存在しないか読み込めません: {constraints_path}"
        )
    except Exception as e:
        read_errors.append(
            f"[F-01] constraints.json: 読み込みエラー: {e}"
        )

    return participants, ratings, constraints, read_errors
