# 補助スクリプト仕様

更新日: 2026-06-11 JST

## ガード・カレンダー

### `scripts/jpx_calendar.py`

JPX 営業日判定と休日キャッシュ同期。`is-open`（指定日または今日 JST が営業日か）と `sync`（`data/jpx_holidays.json` 更新）。休日ソースは `https://holidays-jp.github.io/api/v1/date.json` + 年末年始補完。リモート失敗時はローカルキャッシュで判定継続。`--github-output` で `GITHUB_OUTPUT` へ書き出し。

### `scripts/run_guard.py`

`docs/state.json` の当日 JST エントリ有無で日次 core の冪等を判定（`needs-core-run` / `has-today-update`）。

### `scripts/curation_guard.py`

日次キュレーションの冪等ガード（`needs-run`: 当日の `docs/curation/decision_*.json` 有無）。

### `scripts/workflow_watchdog.py`

日次成果物の健全性検証: `state.json` 当日エントリ、`dashboard_index.json` の鮮度と enabled 銘柄、`tickers/{code}.json` の存在とサイズ上限、`backtest_report.json` のエントリ数。失敗時 exit 1（workflow 側で Issue 起票）。

## 計測・品質（Phase 0/1）

### `scripts/db_migrate.py`

`migrations/*.sql` を `schema_migrations` 管理で冪等適用。`DATABASE_URL` 必須。enabled 銘柄と legacy モデル版の seed も行う。

### `scripts/settle_outcomes.py`

未決済シグナルの 1/5/10 営業日実現結果（realized_ret / hit / MAE / MFE / exit_reason）を銘柄 parquet から計算し `signal_outcomes` へ upsert。`data/macro/macro_panel.parquet` の TOPIX 列から `benchmark_ret` / `excess_ret` を同時に埋める（系列欠損時は NULL で継続）。`--refill-benchmark` で既存 NULL 行の埋め直し（冪等）。決済完了後に `performance_summary.json` / `performance_detail.json` / `signal_outcomes_recent.json` を再エクスポートし、settle 当日の実績を同日 commit に反映する。

### `scripts/backfill_state_signals.py`

`docs/state.json`（直近約30日）から `signals` / `predictions` を seed する初期バックフィル。冪等。

### `scripts/update_macro_snapshots.py`

マクロ系列（USD/JPY、TOPIX、日経、日経VI、JGB10y）を取得し、`docs/curation/macro_latest.json` の定性バイアスと合成して `data/macro/macro_panel.parquet` を更新、DB 有効時は `macro_snapshots` へ upsert。系列単位の取得失敗は欠損のまま継続。

### `scripts/weekly_model_retrain.py`

Phase 1 の**実学習**: データ更新 → 特徴量 → ラベル → 銘柄別 LightGBM + isotonic 較正の学習 → `data/models/<version>/` へ保存 → `model_registry` 登録 → `active_model.json` 更新 → `docs/weekly_retrain_report.json` 出力。`state.json` やダッシュボード JSON は更新せず、LINE 通知もしない。銘柄単位の失敗はレポートに記録して継続。

### `scripts/drift_check.py`

active モデルの rolling IC / Brier / hit-rate（DB の predictions × signal_outcomes）と特徴量 PSI を計算し `docs/drift_report.json` を出力。閾値（`TRADER_DRIFT_*`）割れは exit code で workflow に伝搬し、watchdog が Issue を起票。

## Phase 2（クロスセクション・ポートフォリオ）

### `scripts/universe_select.py`

決定論のユニバース選定（流動性上位 + セクターキャップ + churn ガード）。既定は report のみで、`--apply` 指定時のみ `tickers.yml` を更新。weekly-model-retrain では report モードで実行。

### `scripts/weekly_cross_section_retrain.py`

クロスセクション LightGBM（ランカ）の週次学習: パネル構築 → walk-forward OOS 較正 → `data/models/cs-v1-*/` 保存 → `active_cs_model.json` 更新 → ポートフォリオ walk-forward バックテスト + `evaluate_portfolio_kpi_gate()` → `docs/cs_model_quality.json` / `docs/portfolio_backtest.json` 出力。

### `scripts/portfolio_shadow_report.py`

shadow 期間の Phase 1 vs Phase 2 比較（daily IC、的中率等）を `src/portfolio_shadow.py` の純粋ロジックで集計し、`docs/portfolio_shadow_report.json` を出力。active 化判断のための `active_readiness`（`shadow_days >= 10` かつポートフォリオゲート通過かつ CS daily IC ≥ Phase 1 比 −0.005）を含む。**active への切替自体は人間が env を変更する**。

## 通知

### `scripts/weekly_performance_notify.py`

DB から直近 7 日分の outcome を取得し、`digest.build_weekly_summary()` の文面を `notifier.send_line_text()` で送信。DB 不通・実績ゼロは exit 0 の no-op。

### `scripts/curation_notify.py`

週次レポートの GitHub blob URL を LINE 通知（カジュアルなナビ文体、レポート先頭の `###` 見出しを注目銘柄として取り込み）。送信は `notifier.send_line_text()` 経由（リトライ付き）。LINE 未設定時は本文を標準出力へ。

## 運用・監査

### `scripts/universe_refresh.py`

現在の有効銘柄のスナップショット（データ有無・行数・最新日）を `docs/universe_refresh_report.json` へ出力。`tickers.yml` は変更しない。

### `scripts/rotating_refresh.py`

有効銘柄を `--buckets`（既定5）で分割し、JST 曜日に対応するバケットだけ `update_data()`。失敗銘柄があれば exit 1。

### `scripts/feature_precompute.py`

有効銘柄の特徴量を `data/features/{code}.parquet` へ保存し、レポートを出力。**現状この生成物はどの処理からも読まれず commit もされない**（`06_issues_and_backlog.md` 参照）。

### `scripts/monthly_audit.py`

全有効銘柄の `evaluate_kpi_gate()` を実行し、集計（passed/failed 件数、平均 CAGR/MaxDD/Sharpe/期待値/turnover）を `docs/monthly_audit.json` へ出力。

### `scripts/stress_test.py`

KPI 設定の `--cost-bps`（既定20）と `--slippage-bps`（既定10）だけを高コスト前提に変えて再評価し、`docs/stress_test_report.json` を出力。

## AI銘柄キュレーション

詳細設計は `ai_ticker_curation/` を正とします。

- `scripts/curation_common.py`: 共通ヘルパ（パス、`tickers.yml` 読み書き、`settings.curation` 既定値、JST 時刻）
- `scripts/curation_warmup.py`: 候補プールの未enabled銘柄を `data/watchlist/` へ取得
- `scripts/technical_screen.py`: 決定論テクニカルスコア（0-100）を `docs/curation/technical_*.json` へ出力。agent 失敗時の安全網
- `scripts/curation_merge.py`: 安全クリティカルな決定論 merge。tech/fund スコア合成、warmup・cooldown・churn/セクターキャップ・conservative mode のガードレール下で `--apply` 時のみ `tickers.yml` を更新

## `.claude/skills/*`（CI から起動される agent skill）

- `jp-stock-technical-screen`: `technical_screen.py` の結果を精査して `technical_latest.json` を更新（`tickers.yml` 非編集）
- `global-macro-screen`: 金利・為替など一次情報から `docs/curation/macro_latest.json` を出力（週次）
- `jp-stock-fundamental-screen`: 一次情報から `fundamental_latest.json` を出力（週次、`tickers.yml` 非編集）
- `weekly-stock-report`: ファンダ・テクニカル・決定ログから `reports/weekly_*.md` を生成

## 実装上の共通点

- 多くのスクリプトは `ROOT_DIR` を `sys.path` へ追加し、リポジトリ外からも `src.*` を import 可能
- DB 系スクリプトは `TRADER_DB_ENABLED=false` または `DATABASE_URL` 未設定で no-op
- 監査系レポートの `generated_at` は一部 timezone naive な `datetime.now()`、キュレーション系は `+09:00` 付き（`06_issues_and_backlog.md` の低優先課題）
