# 公平なグループ分けシステム

参加者同士の相互評価をもとに、OR-Tools CP-SAT で最適なグループ分けを計算するシステム。

## 全体の流れ

```
Google フォーム（評価収集）
  └─ Apps Script（回答集計 → master_ratings）
       └─ スプレッドシート（マスターデータ）
            └─ CSV 書き出し → data/
                 └─ Python（最適化）→ output/
```

作業は次の4段階。

1. フォームで評価を集める
2. 入力ファイルを `data/` に準備する
3. 最適化を実行する
4. 候補を確認し、最終グループを共有する

## 必要環境

- Python 3.11 以降
- [uv](https://github.com/astral-sh/uv) パッケージマネージャ
- Google アカウント（フォームとスプレッドシート用）

## セットアップ

### Python の依存関係

```powershell
uv sync
```

### Google Apps Script

1. Google スプレッドシートを作成し、`participants` シートを追加する
2. シートの「拡張機能」>「Apps Script」でスクリプトエディタを開く
3. `apps-script/main.gs` の内容を貼り付けて保存する
4. スプレッドシートを再読み込みする。「グループ分け」メニューが表示される

## 使い方

### 1. フォームで評価を集める

1. `participants` シートに参加者の ID（`001` 形式）と名前を入力する
2. 「グループ分け」>「フォームを作成」を実行する
   - 参加者数が多い場合は処理が分割され、自動的に完了する
   - 完了は画面の表示か、「フォーム作成の状態を確認」で確認できる
3. 表示された URL を参加者に共有する
4. 全員が回答したら、「回答受付を締め切る」を実行する
5. 「評価データを再集計」を実行し、`master_ratings` が更新されたことを確認する

### 2. 入力ファイルを準備する

`data/` ディレクトリに次の3ファイルを配置します。

| ファイル | 作り方 |
| --- | --- |
| `participants.csv` | スプレッドシートの `participants` を UTF-8 CSV として書き出す |
| `master_ratings.csv` | スプレッドシートの `master_ratings` を UTF-8 CSV として書き出す |
| `constraints.json` | グループ人数を設定する |

注意: CSV を Excel で開かないでください。ID のゼロ埋めが消えます。

必要に応じて、`master_ratings.csv` の `incompatible` 列だけをテキストエディタで編集します。

各ファイルの形式は次のとおりです。

**participants.csv**

```csv
id,name
001,田中
002,佐藤
003,鈴木
004,高橋
```

**master_ratings.csv**

```csv
rater_id,target_id,score,incompatible,response_id,submitted_at
001,002,2,false,abc123,2026-10-08T12:34:56+09:00
...
```

**constraints.json**

```json
{
  "group_sizes": [4, 5, 5, 5]
}
```

### 3. 最適化を実行する

```powershell
# 標準実行
uv run python main.py

# 全オプション指定
uv run python main.py --data-dir data --output-dir output --top-n 10 --time-limit 60 --workers 8 --seed 0

# 再現性優先モード（workers が強制的に 1 になる）
uv run python main.py --deterministic
```

実行すると、`output/YYYYMMDD_HHMMSS/` に次のファイルが生成されます。

| ファイル | 内容 |
| --- | --- |
| `candidates.md` | 人が読む候補リスト |
| `candidates.json` | 機械処理用の候補データ |
| `run_info.json` | 実行条件とソルバー結果の記録 |

### 4. 候補を確認し、最終グループを共有する

1. `candidates.md` の候補を確認する
2. 管理者が最終グループを決定する
3. 決定後の所属だけを参加者に共有する

### 終了コード

| コード | 意味 |
| ------ | ---- |
| 0 | 正常終了（1件以上の候補を出力） |
| 1 | コマンドライン引数の誤り |
| 2 | 入力検証エラー |
| 3 | 解が得られなかった |
| 4 | 内部エラー |

### テストの実行

```powershell
uv run pytest tests/
```

### ディレクトリ構造

```
MakeGroup/
├─ data/                    # 入力データ（Git 管理外）
│  ├─ participants.csv
│  ├─ master_ratings.csv
│  └─ constraints.json
├─ output/                  # 最適化結果（Git 管理外）
│  └─ YYYYMMDD_HHMMSS/
│     ├─ candidates.md
│     ├─ candidates.json
│     └─ run_info.json
├─ src/
│  ├─ import_data.py        # CSV/JSON 読み込み・incompatible_pairs 生成
│  ├─ validate.py           # 入力検証（エラーコード F-01〜C-04、警告 W-01〜W-02）
│  ├─ metrics.py            # 指標計算（fractions.Fraction 使用）
│  ├─ optimize.py           # CP-SAT による最適化（3段階辞書式）
│  └─ output.py             # 出力ファイル生成
├─ tests/                   # pytest テスト
├─ apps-script/
│  └─ main.gs               # Google Apps Script
├─ main.py                  # エントリーポイント
├─ pyproject.toml           # パッケージ設定
├─ requirements.txt         # ライブラリバージョン固定
├─ .gitignore
└─ README.md
```
