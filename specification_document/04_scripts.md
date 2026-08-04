# 補助スクリプト仕様

更新日: 2026-07-24 JST

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

未決済シグナルの 1/5/10 営業日実現結果（realized_ret / hit / MAE / MFE / exit_reason）を銘柄 parquet から計算し `signal_outcomes` へ upsert。約定契約 `next_session_open_to_close_v2` は、判断に使った `market_as_of_date` の次の市場行の寄付きで入り、H営業日目の終値で決済する。休日はカレンダー日加算せず、実在する次のOHLCV行で解決する。

TOPIXパネルは終値のみで同じ翌日寄付き基準を作れないため、v2の `benchmark_ret` / `excess_ret` は NULL とし比較不能理由を保存する。`--refill-benchmark` は旧 `close_to_close_v1` 行だけが対象。migration 0004適用後の既存結果は `--restate-execution-contract` でv2へ再計算・置換でき、通常実行もlegacy行をv2未決済として段階的に置換する。決済完了後に成績JSONを再エクスポートし、settle当日の実績を同日commitへ反映する。

### `scripts/backfill_state_signals.py`

`docs/state.json`（直近約30日）から `signals` / `predictions` を seed する初期バックフィル。冪等。

### `scripts/update_macro_snapshots.py`

マクロ系列（USD/JPY、TOPIX、日経、日経VI、JGB10y）を取得し、`docs/curation/macro_latest.json` の定性バイアスと合成して `data/macro/macro_panel.parquet` を更新、DB 有効時は `macro_snapshots` へ upsert。系列単位の取得失敗は欠損のまま継続。

### `scripts/weekly_model_retrain.py`

Phase 1の**実学習とatomic配備**: データ更新 → 特徴量 → v2ラベル → 銘柄別exact final LightGBM + tuning-only isotonic較正／閾値選択 + holdout-only KPIゲート → 一意versionの`.staging/`へschema v3 artifactを保存 → 全対象coverage・gate evidence・manifest/checksum検証 → 合格時だけimmutableな`data/models/<version>/`へpromoteし`active_model.json`をatomic更新 → `model_registry`登録 → `docs/weekly_retrain_report.json`と`docs/model_quality.json`を更新。銘柄単位の失敗はレポートへ残すが候補全体をrejectし、前activeを維持する。DB登録失敗はfile pointer provenance付きの安定event IDでoutboxへ待避し、再送時に現在pointerと一致しない古いactivationは適用しない。`state.json`や日次シグナルは更新せず、LINE通知もしない。

### `scripts/drift_check.py`

日次推論と同じruntime artifact/gate契約、manifest/checksum、候補合格を先に検証し、互換なactiveモデルだけrolling IC / Brier / hit-rate（signal-linked Phase 1 predictions × v2 signal_outcomes）と特徴量PSIを計算して`docs/drift_report.json`へ出力する。不一致時は旧artifactを正常扱いせずunavailableへfail-closeする。閾値（`TRADER_DRIFT_*`）割れはexit codeでworkflowへ伝搬し、watchdogがIssueを起票。

## Phase 2（クロスセクション・ポートフォリオ）

### `scripts/universe_select.py`

決定論のユニバース選定（流動性上位 + セクターキャップ + churn ガード）。既定は report のみで、`--apply` 指定時のみ `tickers.yml` を更新。weekly-model-retrain では report モードで実行。

### `scripts/weekly_cross_section_retrain.py`

クロスセクション LightGBM（ランカ）の週次学習: パネル構築 → walk-forward OOS 較正 → `data/models/cs-v1-*/` 保存 → `active_cs_model.json` 更新 → ポートフォリオ walk-forward バックテスト + `evaluate_portfolio_kpi_gate()` → `docs/cs_model_quality.json` / `docs/portfolio_backtest.json` 出力。

### `scripts/portfolio_shadow_report.py`

shadow期間のPhase 1 vs Phase 2比較（daily IC、的中率等）を`src/portfolio_shadow.py`の純粋ロジックで集計し、`docs/portfolio_shadow_report.json`を出力する。Phase 1は`signals.prediction_id`に直接紐付く予測、Phase 2は各日の`portfolio_snapshots.model_version`と完全一致するCS予測、結果は現行v2契約だけを使う。同じ`(date, ticker)`に決済済み結果・両予測・保存weightが揃うpaired日／銘柄だけを比較し、`n_dates`、`n_paired_dates`、`n_paired_records`、`date_coverage`、prediction/outcome/snapshot provenance、除外理由を監査できる。`active_readiness`はpairedな`shadow_days >= 10`、ポートフォリオゲート通過、CS daily IC ≥ Phase 1比−0.005を要求する。**activeへの切替自体は人間がenvを変更する**が、TOPIX同一basis coverage不足中はゲートがfail-closeする。

## 通知

### `scripts/weekly_performance_notify.py`

DB から直近 7 日分の outcome を取得し、`digest.build_weekly_summary()` の文面を `notifier.send_line_text()` で送信。DB 不通・実績ゼロは exit 0 の no-op。

### `scripts/curation_notify.py`

週次レポートの GitHub blob URL を LINE 通知（カジュアルなナビ文体、レポート先頭の `###` 見出しを注目銘柄として取り込み）。送信は `notifier.send_line_text()` 経由（リトライ付き）。LINE 未設定時は本文を標準出力へ。

### `scripts/curation_pool_notify.py`

隔週プールリフレッシュの結果（`docs/curation/pool_decision_latest.json` の追加/除外）を `notifier.send_line_text()` で LINE 通知。母集団に変化がない・LINE 未設定・送信失敗は no-op（best-effort）。

## 運用・監査

### `scripts/universe_refresh.py`

現在の有効銘柄のスナップショット（データ有無・行数・最新日）を `docs/universe_refresh_report.json` へ出力。`tickers.yml` は変更しない。

### `scripts/rotating_refresh.py`

有効銘柄を `--buckets`（既定5）で分割し、JST 曜日に対応するバケットだけ `update_data()`。失敗銘柄があれば exit 1。

### `scripts/monthly_audit.py`

全有効銘柄の`evaluate_kpi_gate()`を独立実行し、集計（passed/failed件数、平均CAGR/MaxDD/Sharpe/期待値/turnover）を`docs/monthly_audit.json`へ出力する。これは月次の再シミュレーション診断であり、配備Phase 1 artifactのexact-candidate証跡や日次actionのゲートではない。

### `scripts/stress_test.py`

KPI 設定の `--cost-bps`（既定20）と `--slippage-bps`（既定10）だけを高コスト前提に変えて再評価し、`docs/stress_test_report.json` を出力。

## AI銘柄キュレーション

詳細設計は `ai_ticker_curation/` を正とします。

- `scripts/curation_common.py`: 共通ヘルパ（パス、`tickers.yml` 読み書き、`settings.curation` 既定値、JST 時刻）
- `scripts/curation_warmup.py`: 候補プールの未enabled銘柄を `data/watchlist/` へ取得
- `scripts/technical_screen.py`: 決定論テクニカルスコア（0-100）を `docs/curation/technical_*.json` へ出力。agent 失敗時の安全網
- `scripts/curation_merge.py`: 安全クリティカルな決定論 merge。tech/fund スコア合成、warmup・cooldown・churn/セクターキャップ・conservative mode のガードレール下で `--apply` 時のみ `tickers.yml` を更新
- `scripts/curation_pool_merge.py`: 候補母集団 `curation_pool.yml` の唯一の書き手（隔週・決定論・LLM 非使用）。ローカル parquet 流動性フロア／`min_fund_score_to_add`／churn・セクター上限／cooldown／add-only・replace 自動切替のガード下で `--apply` 時のみプールを更新し、`docs/curation/pool_decision_*.json` 監査と `data/watchlist/` の stale 掃除を行う。`--check-due` で隔週 cadence ガード。詳細は `ai_ticker_curation/07_pool_refresh.md`

## `.claude/skills/*`（CI から起動される agent skill）

- `jp-stock-technical-screen`: `technical_screen.py` の結果を精査して `technical_latest.json` を更新（`tickers.yml` 非編集）
- `global-macro-screen`: 金利・為替など一次情報から `docs/curation/macro_latest.json` を出力（週次）
- `jp-stock-fundamental-screen`: 一次情報から `fundamental_latest.json` を出力（週次、`tickers.yml` 非編集）
- `jp-stock-pool-screen`: ファンダ＋流動性で母集団候補 `docs/curation/pool_candidates_latest.json` を提案（隔週、`curation_pool.yml`/`tickers.yml` 非編集）
- `weekly-stock-report`: ファンダ・テクニカル・決定ログから `reports/weekly_*.md` を生成

## 実装上の共通点

- 多くのスクリプトは `ROOT_DIR` を `sys.path` へ追加し、リポジトリ外からも `src.*` を import 可能
- DB参照や毎回再生成できるsnapshot書き込みは、`TRADER_DB_ENABLED=false`または`DATABASE_URL`未設定でno-op。週次Phase 1のmodel registry登録など再送が必要なイベントは`data/outbox/`へ保存
- 監査・再学習・バックテスト系レポートの `generated_at` は、`src.timeutil.now_jst_iso()` による timezone-aware な JST ISO 8601 形式
