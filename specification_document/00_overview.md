# 現行仕様概要

更新日: 2026-06-06 JST

このディレクトリの仕様は、ソースコードを正として整理した現行仕様です。将来案ではなく、2026-06-06 時点でリポジトリに存在する `src/`、`scripts/`、`web/`、`.github/workflows/`、`.claude/skills/`、設定ファイルの実装に合わせています。

## 対象

| 領域 | 対象 | 詳細 |
|---|---|---|
| バックエンド | `main.py`, `src/*.py` | `01_backend_python.md` |
| フロントエンド | `web/` | `02_frontend_web.md` |
| GitHub Actions | `.github/workflows/*.yml`, `.github/scripts/*.sh` | `03_cicd_workflows.md` |
| 補助スクリプト | `scripts/*.py`, `.claude/skills/` | `04_scripts.md` |
| データ契約・横断仕様 | `tickers.yml`, `curation_pool.yml`, `data/`, `docs/`, `reports/`, env | `05_cross_cutting.md` |
| 問題点・改善点 | 実装レビュー結果 | `06_priority_matrix.md` |
| AI銘柄キュレーション | 日次/週次キュレーション実装 | `ai_ticker_curation/` |

## システムの現在地

このプロジェクトは、日本株の監視銘柄に対して以下を自動実行します。

1. 平日 04:30 JST に AI 銘柄キュレーションが候補データを warmup し、テクニカル評価と週次ファンダメンタルキャッシュを統合して、ガード付きで `tickers.yml` の有効ユニバースを少数入替する
2. 平日 06:00 JST に `tickers.yml` の有効銘柄を読み込む
3. Stooq または yfinance から日足 OHLCV を取得する
4. 無効銘柄のトップレベル `data/*.parquet` を `data/archive/` へ退避する
5. 価格・出来高・テクニカル特徴量を生成する
6. LightGBM で翌日の上昇確率を推定する
7. OOS バックテストとコスト/スリッページ込みの KPI ゲートで売買可能性を制御する
8. ゲートを通過した非 `HOLD` シグナルだけ LINE 通知する
9. `docs/` 配下にダッシュボード用 JSON と監査レポートを出力する
10. Next.js の静的エクスポートを GitHub Pages 用に `docs/` へ同期する
11. 土曜 07:00 JST にファンダメンタル評価と週次 Markdown レポートを生成し、レポートの GitHub URL を LINE 通知する

## 正とするデータ契約

現行フロントエンドは `docs/dashboard_index.json` と `docs/tickers/*.json` を読む構成です。旧来の `docs/history_data.json` は主要契約ではなく、存在する場合はダッシュボード出力や publish workflow で削除されます。

AI 銘柄キュレーションの作業物は `docs/curation/*.json`、週次レポートは `reports/weekly_*.md` です。`reports/` は GitHub Pages の publish 同期対象外で、LINE 通知では GitHub blob URL として案内します。
