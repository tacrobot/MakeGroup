"""
validate.py
入力データの検証を担うモジュール。
仕様書 §7 に定義されたエラーコード F-01〜C-04 と警告 W-01〜W-02 を実装する。

- すべての違反を収集してから表示する（途中停止しない）。
- エラーがあれば終了コード 2 で終了する。
- 評価点・同席不可の値はエラーメッセージに含めない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from src.import_data import Participant, Rating


# 参加者ID のパターン（3桁ゼロ埋め文字列）
_ID_PATTERN = re.compile(r"^[0-9]{3}$")

# 有効な score の文字列表記
_VALID_SCORES = {"-2", "-1", "0", "1", "2"}

# 有効な incompatible の文字列（大文字小文字区別なし）
_VALID_INCOMPATIBLE = {"true", "false"}


def _err(code: str, msg: str) -> str:
    """エラーメッセージの書式統一"""
    return f"[{code}] {msg}"


def _warn(code: str, msg: str) -> str:
    """警告メッセージの書式統一"""
    return f"[{code}] {msg}"


def validate_participants(
    participants: list[Participant] | None,
    raw_rows: list[dict] | None = None,
) -> list[str]:
    """
    participants の検証。
    raw_rows は CSV の生の辞書リスト（ヘッダー検証用）。
    """
    errors: list[str] = []
    if participants is None:
        # F-01 は load_all で既にエラーになっているため、ここでは何もしない
        return errors

    # P-01: ヘッダー検証は raw_rows で行う（import_data 側で DictReader を使うため
    # ヘッダーが違う場合は KeyError になる。ここでは行データで代替検証）
    if raw_rows is not None:
        if raw_rows and set(raw_rows[0].keys()) - {"id", "name"}:
            pass  # 余分な列があっても問題なし
        # ヘッダーに 'id' または 'name' がない場合
        if raw_rows and ("id" not in raw_rows[0] or "name" not in raw_rows[0]):
            errors.append(_err("P-01", "participants.csv: ヘッダーが `id,name` ではありません"))
            return errors  # 以降の検証は意味がないので打ち切る

    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    prev_id: str | None = None

    for p in participants:
        # P-02: ID の形式
        if not _ID_PATTERN.match(p.id):
            errors.append(_err("P-02", f"participants.csv: ID `{p.id}` が `^[0-9]{{3}}$` に一致しません"))

        # P-03: ID の重複
        if p.id in seen_ids:
            errors.append(_err("P-03", f"participants.csv: ID `{p.id}` が重複しています"))
        else:
            seen_ids.add(p.id)

        # P-04: name の空・前後空白・改行
        if not p.name:
            errors.append(_err("P-04", f"participants.csv: ID `{p.id}` の名前が空です"))
        elif p.name != p.name.strip():
            errors.append(_err("P-04", f"participants.csv: ID `{p.id}` の名前に前後の空白があります"))
        elif "\n" in p.name or "\r" in p.name:
            errors.append(_err("P-04", f"participants.csv: ID `{p.id}` の名前に改行が含まれています"))

        # P-05: name の重複
        if p.name and p.name in seen_names:
            errors.append(_err("P-05", f"participants.csv: 名前 `{p.name}` が重複しています"))
        elif p.name:
            seen_names.add(p.name)

        # P-06: ID 昇順
        if prev_id is not None and p.id < prev_id:
            errors.append(_err("P-06", f"participants.csv: 行がID昇順になっていません（`{prev_id}` の後に `{p.id}`）"))
        prev_id = p.id

    # P-07: 参加者数 N >= 4
    n = len(participants)
    if n < 4:
        errors.append(_err("P-07", f"participants.csv: 参加者数 N={n} が4未満です"))

    return errors


def validate_participants_header(path: Path) -> list[str]:
    """
    participants.csv のヘッダーだけを検証する（P-01）。
    """
    errors: list[str] = []
    try:
        import csv as csv_mod
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv_mod.reader(f)
            header = next(reader, None)
        if header is None or header != ["id", "name"]:
            errors.append(_err("P-01", f"participants.csv: ヘッダーが `id,name` ではありません（実際: {header}）"))
    except Exception:
        pass  # F-01 で処理済み
    return errors


def validate_ratings_header(path: Path) -> list[str]:
    """
    master_ratings.csv の必須ヘッダー（R-01）を検証する。
    """
    errors: list[str] = []
    try:
        import csv as csv_mod
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv_mod.reader(f)
            header = next(reader, None)
        if header is None:
            errors.append(_err("R-01", "master_ratings.csv: ヘッダーがありません"))
            return errors
        required = {"rater_id", "target_id", "score", "incompatible"}
        missing = required - set(header)
        if missing:
            errors.append(_err("R-01", f"master_ratings.csv: 必須列 {sorted(missing)} がありません"))
    except Exception:
        pass  # F-01 で処理済み
    return errors


def _validate_ratings_raw(path: Path) -> tuple[list[str], list[dict]]:
    """
    master_ratings.csv を生の辞書リストとして読み込み、
    incompatible の文字列を検証する（R-07 は文字列レベルでのみ検出可能）。
    戻り値: (errors, raw_rows)
    """
    errors: list[str] = []
    raw_rows: list[dict] = []
    try:
        import csv as csv_mod
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv_mod.DictReader(f)
            for i, row in enumerate(reader, start=2):  # 2行目〜
                raw_rows.append(row)
                # R-04: score の文字列表記
                score_str = row.get("score", "")
                if score_str not in _VALID_SCORES:
                    errors.append(_err(
                        "R-04",
                        f"master_ratings.csv: {i}行目の `score` の表記 `{score_str}` は無効です"
                        "（-2, -1, 0, 1, 2 のいずれかでなければなりません）",
                    ))
                # R-07: incompatible の文字列
                incompatible_str = row.get("incompatible", "")
                if incompatible_str.lower() not in _VALID_INCOMPATIBLE:
                    errors.append(_err(
                        "R-07",
                        f"master_ratings.csv: {i}行目の `incompatible` の値 `{incompatible_str}` は無効です"
                        "（true または false でなければなりません）",
                    ))
    except Exception:
        pass
    return errors, raw_rows


def validate_ratings(
    ratings: list[Rating] | None,
    participants: list[Participant] | None,
    ratings_path: Path | None = None,
) -> list[str]:
    """
    master_ratings の検証（R-01〜R-07）。
    ratings_path があれば R-04, R-07 を文字列レベルで検証する。
    """
    errors: list[str] = []
    if ratings is None:
        return errors

    # R-04, R-07 は文字列レベルで検証
    if ratings_path is not None and ratings_path.exists():
        raw_errors, _ = _validate_ratings_raw(ratings_path)
        errors.extend(raw_errors)

    if participants is None:
        # R-02 の検証には participants が必要
        return errors

    valid_ids: set[str] = {p.id for p in participants}
    n = len(participants)

    # (rater_id, target_id) の重複検査用
    seen_pairs: dict[tuple[str, str], int] = {}

    for i, r in enumerate(ratings, start=2):
        pair = (r.rater_id, r.target_id)

        # R-02: rater_id, target_id が参加者に存在する
        if r.rater_id not in valid_ids:
            errors.append(_err("R-02", f"master_ratings.csv: {i}行目の `rater_id` `{r.rater_id}` は参加者に存在しません"))
        if r.target_id not in valid_ids:
            errors.append(_err("R-02", f"master_ratings.csv: {i}行目の `target_id` `{r.target_id}` は参加者に存在しません"))

        # R-03: rater_id == target_id の禁止
        if r.rater_id == r.target_id:
            errors.append(_err("R-03", f"master_ratings.csv: {i}行目の `rater_id` と `target_id` が同じです（{r.rater_id}）"))

        # R-05: 同じ (rater_id, target_id) の重複
        if pair in seen_pairs:
            errors.append(_err(
                "R-05",
                f"master_ratings.csv: ({r.rater_id}, {r.target_id}) の組が {i}行目と{seen_pairs[pair]}行目で重複しています",
            ))
        else:
            seen_pairs[pair] = i

    # R-06: 回答者ごとに自分以外の全員分の行があるか
    if not errors or all("[R-04]" in e or "[R-07]" in e for e in errors):
        # R-02, R-03, R-05 がなければ R-06 を検証
        pass
    # R-06 は R-02/R-03/R-05 に関わらず必ず検証する
    rater_targets: dict[str, set[str]] = {}
    for r in ratings:
        if r.rater_id not in rater_targets:
            rater_targets[r.rater_id] = set()
        rater_targets[r.rater_id].add(r.target_id)

    expected_targets = valid_ids
    for pid in valid_ids:
        actual = rater_targets.get(pid, set())
        expected = expected_targets - {pid}
        missing = sorted(expected - actual)
        if missing:
            errors.append(_err(
                "R-06",
                f"master_ratings.csv: 回答者 `{pid}` に対象者 {missing} の行がありません",
            ))
        extra = actual - expected
        # extra は R-02 か R-03 で既に検出済みなので重複しない

    return errors


def validate_constraints(
    constraints: dict | None,
    participants: list[Participant] | None,
) -> list[str]:
    """
    constraints.json の検証（C-01〜C-04）。
    """
    errors: list[str] = []
    if constraints is None:
        return errors

    # C-04: group_sizes 以外のキーがある
    extra_keys = set(constraints.keys()) - {"group_sizes"}
    if extra_keys:
        errors.append(_err("C-04", f"constraints.json: 不明なキー {sorted(extra_keys)} があります"))

    # C-01: group_sizes が存在する
    if "group_sizes" not in constraints:
        errors.append(_err("C-01", "constraints.json: `group_sizes` キーがありません"))
        return errors

    group_sizes = constraints["group_sizes"]

    # C-02: group_sizes が整数のリストで、各要素が 2 以上
    if not isinstance(group_sizes, list):
        errors.append(_err("C-02", "constraints.json: `group_sizes` はリストでなければなりません"))
        return errors

    for i, s in enumerate(group_sizes):
        if not isinstance(s, int) or isinstance(s, bool):
            errors.append(_err("C-02", f"constraints.json: `group_sizes[{i}]` は整数でなければなりません"))
        elif s < 2:
            errors.append(_err("C-02", f"constraints.json: `group_sizes[{i}]` = {s} は2以上でなければなりません"))

    # C-03: group_sizes の合計が N と一致する
    if participants is not None:
        n = len(participants)
        total = sum(s for s in group_sizes if isinstance(s, int) and not isinstance(s, bool))
        if total != n:
            errors.append(_err(
                "C-03",
                f"constraints.json: `group_sizes` の合計 {total} が参加者数 N={n} と一致しません",
            ))

    return errors


def validate_all(
    participants: list[Participant] | None,
    ratings: list[Rating] | None,
    constraints: dict | None,
    read_errors: list[str],
    data_dir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """
    全入力の検証を実行する。
    戻り値: (errors, warnings)
    errors が空のときのみ最適化に進んでよい。
    """
    errors: list[str] = list(read_errors)
    warnings: list[str] = []

    participants_path = (data_dir / "participants.csv") if data_dir else None
    ratings_path = (data_dir / "master_ratings.csv") if data_dir else None

    # P-01: ヘッダー検証
    if participants_path and participants_path.exists():
        errors.extend(validate_participants_header(participants_path))

    # R-01: 必須ヘッダー検証
    if ratings_path and ratings_path.exists():
        errors.extend(validate_ratings_header(ratings_path))

    # 各モジュールの検証
    errors.extend(validate_participants(participants))
    errors.extend(validate_ratings(participants=participants, ratings=ratings, ratings_path=ratings_path))
    errors.extend(validate_constraints(constraints=constraints, participants=participants))

    # W-01: ある回答者が全員に同じ評価点をつけている
    if ratings:
        rater_scores: dict[str, list[int]] = {}
        for r in ratings:
            rater_scores.setdefault(r.rater_id, []).append(r.score)
        for rater_id, scores in rater_scores.items():
            if len(scores) > 1 and len(set(scores)) == 1:
                warnings.append(_warn(
                    "W-01",
                    f"master_ratings.csv: 回答者 `{rater_id}` が全員に同じ評価をつけています",
                ))

    # W-02: N > 40
    if participants and len(participants) > 40:
        warnings.append(_warn("W-02", f"参加者数 N={len(participants)} が40を超えています（動作は保証されません）"))

    return errors, warnings


def print_messages(errors: list[str], warnings: list[str]) -> None:
    """エラーと警告を標準エラー出力に表示する"""
    for w in warnings:
        print(w, file=sys.stderr)
    for e in errors:
        print(e, file=sys.stderr)
