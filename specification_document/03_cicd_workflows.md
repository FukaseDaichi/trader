# GitHub Actions仕様

更新日: 2026-06-16 JST

## ワークフロー一覧

| Workflow | ファイル | JST | 主処理 | commit対象 |
|---|---|---:|---|---|
| Daily Ticker Curation | `daily-ticker-curation.yml` | 平日 04:30 | 候補warmup → テクニカルscreen → Claude技術分析 → 決定論merge | `tickers.yml`, `data/`, `docs/curation/` |
| Daily Preopen Core | `daily-preopen-core.yml` | 平日 06:00 | マクロ更新 → `main.py` → 実現結果決済 → ドリフトチェック | `data/`, `docs/` |
| Daily Preopen Retry | `daily-preopen-retry.yml` | 平日 06:20/06:40 | 当日未更新なら core と同処理 | `data/`, `docs/` |
| Daily Publish Dashboard | `daily-publish-dashboard.yml` | core/retry成功後 | Next.js 静的ビルドを `docs/` へ同期 | `docs/` |
| Daily Watchdog | `daily-watchdog.yml` | 平日 12:30 | 成果物の鮮度・完全性 + ドリフト検証。失敗/ドリフト時に GitHub Issue 起票 | なし |
| Weekly Fundamental & Report | `weekly-fundamental-report.yml` | 土曜 07:00 | テクニカル更新 → マクロagent → ファンダagent → **[隔週]プールagent＋決定論merge** → 週次レポートagent → レポートURL通知 → **週間実績サマリ通知** | `reports/`, `docs/curation/`, `curation_pool.yml` |
| Weekly Model Retrain | `weekly-model-retrain.yml` | 土曜 08:00 | マクロ更新 → universe選定(report) → **Phase 1 実学習・版登録** → **Phase 2 CS再学習** → **shadow検証レポート** | `data/models/`, `docs/weekly_retrain_report.json`, `docs/cs_model_quality.json`, `docs/portfolio_backtest.json`, `docs/portfolio_shadow_report.json` |
| Weekly Universe Refresh | `weekly-universe-refresh.yml` | 日曜 07:00 | ユニバーススナップショット | `docs/universe_refresh_report.json` |
| Monthly Calendar Sync | `monthly-calendar-sync.yml` | 毎月1日 09:15 | JPX休日キャッシュ更新 | `data/jpx_holidays.json` |
| Monthly Full Audit | `monthly-full-audit.yml` | 第1日曜 09:00 | KPI月次監査 | `docs/monthly_audit.json` |
| Nightly Rotating Refresh | `nightly-rotating-refresh.yml` | 平日 19:30 | 銘柄をバケット分割して更新 | `data/`, `docs/rotating_refresh_report.json` |
| Quarterly Stress Test | `quarterly-stress-test.yml` | 四半期初日 10:00 | 高コスト前提 KPI 評価 | `docs/stress_test_report.json` |
| Manual DB Migrate | `manual-db-migrate.yml` | 手動（`workflow_dispatch`） | `migrations/*.sql` を冪等適用（`dry_run` でプレビュー） | なし（DB のみ） |

## 日次処理

`daily-ticker-curation` / `daily-preopen-core` / `daily-preopen-retry` / `daily-watchdog` は `scripts/jpx_calendar.py is-open` で JPX 営業日を確認し、休場日はスキップします。

`daily-preopen-core` のステップ順（Phase 0〜3 を含む現行）:

1. `scripts/update_macro_snapshots.py --as-of <today>`: マクロ系列取得 → `data/macro/macro_panel.parquet` 更新 + `macro_snapshots` upsert（失敗しても続行）
2. `main.py`: 日次シグナル + Phase 2 snapshot + 通知 + DB write-through + ダッシュボード出力。`DATABASE_URL` / LINE secrets / `TRADER_PORTFOLIO_*` などの env はこのステップに集約
3. `scripts/settle_outcomes.py --as-of <today> --refill-benchmark`: 1/5/10日実現結果 + TOPIX ベンチマーク決済 + settle 当日分の実績 JSON 再エクスポート（失敗は warning で続行）。`--refill-benchmark` は冪等で、`benchmark_ret` が NULL の既決済行を TOPIX プロキシデータがある限り自動補填する
4. `scripts/drift_check.py --as-of <today> || true`: `docs/drift_report.json` 出力
5. `.github/scripts/commit-and-push.sh` で commit/push

`daily-preopen-retry` は `scripts/run_guard.py needs-core-run`（`docs/state.json` の当日エントリ確認）で冪等化。`daily-ticker-curation` は `scripts/curation_guard.py needs-run` で冪等化。

`daily-watchdog` は鮮度・完全性チェック（`scripts/workflow_watchdog.py`）に加えてドリフトチェックを実行し、それぞれ失敗時に GitHub Issue を起票します。

## 週次処理

- **weekly-model-retrain（土曜 08:00）**: `weekly_model_retrain.py` が銘柄別モデルを実学習して `data/models/<version>/` + `active_model.json` + `model_registry` に登録。続いて `weekly_cross_section_retrain.py` が CS モデルを学習し、ポートフォリオ walk-forward バックテスト + `evaluate_portfolio_kpi_gate()` を実行して `docs/cs_model_quality.json` / `docs/portfolio_backtest.json` を出力。最後に `portfolio_shadow_report.py` が Phase 1 vs Phase 2 の比較と `active_readiness` を `docs/portfolio_shadow_report.json` へ出力
- **weekly-fundamental-report（土曜 07:00）**: テクニカル更新 → Claude のマクロ/ファンダ agent → **隔週（14日）の pool refresh**（cadence ガード → `/jp-stock-pool-screen` → 決定論 `curation_pool_merge.py` が候補母集団 `curation_pool.yml` を更新。いずれも `continue-on-error: true` で週次本体を巻き込まない）→ レポートライター agent → commit（`reports` / `docs/curation` / `curation_pool.yml`）→ `curation_notify.py` でレポート URL を LINE 通知 → 隔週 `curation_pool_notify.py` でプール変更を通知 → `weekly_performance_notify.py` で週間実績サマリを LINE 通知（DB 不通・実績ゼロは no-op）。詳細は `ai_ticker_curation/07_pool_refresh.md`

## publishの同期仕様

publish workflow は `docs/dashboard_index.json` と `docs/tickers/` を `web/public/` へコピー → `npm ci` → `npm run build:prod` → `web/out/` を `docs/` へ `rsync --delete` します。

rsync の `--exclude` リストには、パイプラインが `docs/` 直下に書く**すべての**データ JSON（`state.json`、`backtest_report.json`、`performance_summary.json`、`performance_detail.json`、`signal_outcomes_recent.json`、`model_quality.json`、`drift_report.json`、`portfolio_latest.json`、`portfolio_backtest.json`、`portfolio_shadow_report.json`、`cs_model_quality.json`、監査系レポート、`curation/`、`CNAME`、`.nojekyll` 等）が登録されています。

**重要**: `docs/` 直下に新しいデータファイルを追加する場合は、必ずこの exclude リストにも追加すること。漏れると次回 publish で削除されます。`tests/test_publish_workflow.py` がコード側の出力と exclude リストの整合を再発防止ガードとして検査します。

## 権限と排他

書き込み系 workflow は `contents: write`、watchdog は `contents: read` + `issues: write`、`manual-db-migrate` は `contents: read` のみ（本番 DB へ適用するだけで commit/push しない）。日次 core/retry は concurrency group `daily-core-main` で直列化、publish は `daily-publish-main`、キュレーション系は `daily-curation-main` / `weekly-fundamental-main`。

commit/push は `.github/scripts/commit-and-push.sh` に集約（`git add -A` → commit → `git pull --rebase --autostash` → push 最大 3 回リトライ、差分なしは正常終了）。

## 現行制約

- Claude agent の細粒度ツール制限は workflow の `claude_args` と `.claude/settings.local.json` による補助で、最終的な不可逆変更は決定論スクリプトと commit helper が担保
