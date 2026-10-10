"""
main.py
エントリーポイント。
CLI 引数を解析し、検証・最適化・出力の一連の処理を実行する。

終了コード:
  0: 正常終了（1件以上の候補を出力）
  1: コマンドライン引数の誤り
  2: 入力検証エラー
  3: 解が得られなかった
  4: 内部エラー
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.import_data import build_incompatible_pairs, load_all
from src.metrics import compute_scale_L
from src.optimize import optimize
from src.output import write_outputs
from src.validate import print_messages, validate_all


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 引数を解析する"""
    parser = argparse.ArgumentParser(
        description="参加者の相互評価をもとに公平なグループ分けを行うシステム",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="入力データディレクトリ（participants.csv, master_ratings.csv, constraints.json を含む）",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="出力先ディレクトリ（日時のサブディレクトリが作られる）",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="出力する候補の最大件数",
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=60.0,
        help="ソルバー1回あたりの制限時間（秒）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="ソルバーのスレッド数",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="ソルバーの乱数シード",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="再現性優先モード（workers を強制的に 1 にする）",
    )
    return parser.parse_args(argv)


def progress_callback(rank: int, stage: int, status: str) -> None:
    """最適化の進捗を画面に表示する（評価点・同席不可は出力しない）"""
    stage_name = {1: "最低満足度最大化", 2: "合計満足度最大化", 3: "分散最小化"}.get(stage, f"段階{stage}")
    print(f"  候補{rank} - {stage_name}: {status}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """メイン処理。終了コードを返す。"""
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)

    # --- 入力読み込み ---
    participants, ratings, constraints, read_errors = load_all(data_dir)

    # --- 入力検証 ---
    errors, warnings = validate_all(
        participants=participants,
        ratings=ratings,
        constraints=constraints,
        read_errors=read_errors,
        data_dir=data_dir,
    )
    print_messages(errors, warnings)

    if errors:
        print(f"入力検証エラーが {len(errors)} 件あります。処理を中断します。", file=sys.stderr)
        return 2

    # ここまで来たら participants, ratings, constraints はすべて有効
    assert participants is not None
    assert ratings is not None
    assert constraints is not None

    # incompatible_pairs の生成
    incompatible_pairs = build_incompatible_pairs(ratings)
    group_sizes: list[int] = constraints["group_sizes"]
    scale_L = compute_scale_L(sorted(group_sizes))

    print(f"参加者数: {len(participants)}", file=sys.stderr)
    print(f"人数構成: {sorted(group_sizes)}", file=sys.stderr)
    print(f"同席不可ペア数: {len(incompatible_pairs)}", file=sys.stderr)
    print(f"スケール係数 L: {scale_L}", file=sys.stderr)
    print(f"最大候補数: {args.top_n}", file=sys.stderr)
    print("最適化を開始します...", file=sys.stderr)

    # --- 最適化 ---
    run_params = {
        "top_n": args.top_n,
        "time_limit": args.time_limit,
        "workers": args.workers,
        "seed": args.seed,
        "deterministic": args.deterministic,
    }

    try:
        candidates, end_reason = optimize(
            participants=participants,
            ratings=ratings,
            group_sizes=group_sizes,
            incompatible_pairs=incompatible_pairs,
            top_n=args.top_n,
            time_limit=args.time_limit,
            workers=1 if args.deterministic else args.workers,
            seed=args.seed,
            deterministic=args.deterministic,
            progress_callback=progress_callback,
        )
    except ValueError as e:
        # _verify_objective による目的値不一致など
        print(f"内部エラー（目的値不一致）: {e}", file=sys.stderr)
        return 4
    except Exception as e:
        print(f"内部エラー（最適化）: {e}", file=sys.stderr)
        return 4

    if not candidates:
        # end_reason は status_str になっている（候補1で解なし時）
        if end_reason == "INFEASIBLE":
            print(
                "同席不可の制約などにより、解が存在しません。終了コード 3 で終了します。",
                file=sys.stderr,
            )
        elif end_reason == "UNKNOWN":
            print(
                "制限時間内に解が見つかりませんでした。終了コード 3 で終了します。",
                file=sys.stderr,
            )
        elif end_reason == "MODEL_INVALID":
            print("内部エラー（モデルが不正）。終了コード 4 で終了します。", file=sys.stderr)
            return 4
        else:
            print(f"解が得られませんでした（{end_reason}）。終了コード 3 で終了します。", file=sys.stderr)
        return 3

    print(f"候補 {len(candidates)} 件が得られました（終了理由: {end_reason}）", file=sys.stderr)

    # --- 結果の再計算・照合 ---
    # metrics.py で再計算（optimize.py とは独立）
    # 照合は optimize.py 内で行われるため、ここでは出力に進む

    # --- 出力 ---
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        sub_dir = write_outputs(
            output_dir=output_dir,
            candidates=candidates,
            participants=participants,
            group_sizes=group_sizes,
            scale_L=scale_L,
            run_params=run_params,
            end_reason=end_reason,
            data_dir=data_dir,
        )
    except Exception as e:
        print(f"内部エラー（出力）: {e}", file=sys.stderr)
        return 4

    print(f"出力先: {sub_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
