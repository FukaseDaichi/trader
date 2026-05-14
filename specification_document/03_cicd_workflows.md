# GitHub Actions仕様

更新日: 2026-05-14 JST

## ワークフロー一覧

| Workflow | ファイル | JST | 主処理 | commit対象 |
|---|---|---:|---|---|
| Daily Preopen Core | `daily-preopen-core.yml` | 平日 06:00 | JPX営業日なら`main.py` | `data/`, `docs/` |
| Daily Preopen Retry | `daily-preopen-retry.yml` | 平日 06:20/06:40 | 当日未更新なら`main.py` | `data/`, `docs/` |
| Daily Publish Dashboard | `daily-publish-dashboard.yml` | core/retry成功後 | Next.js静的ビルドを`docs/`へ同期 | `docs/` |
| Daily Watchdog | `daily-watchdog.yml` | 平日 12:30 | 日次成果物チェック、失敗時Issue作成 | なし |
| Weekly Model Retrain | `weekly-model-retrain.yml` | 土曜 08:00 | `weekly_model_retrain.py`でデータ更新と学習可否レポート | `data/`, `docs/weekly_retrain_report.json` |
| Weekly Universe Refresh | `weekly-universe-refresh.yml` | 日曜 07:00 | ユニバーススナップショット | `docs/universe_refresh_report.json` |
| Monthly Calendar Sync | `monthly-calendar-sync.yml` | 毎月1日 09:15 | JPX休日キャッシュ更新 | `data/jpx_holidays.json` |
| Monthly Full Audit | `monthly-full-audit.yml` | 第1日曜 09:00 | KPI月次監査 | `docs/monthly_audit.json` |
| Nightly Rotating Refresh | `nightly-rotating-refresh.yml` | 平日 19:30 | 銘柄をバケット分割して更新 | `data/`, `docs/rotating_refresh_report.json` |
| Nightly Feature Precompute | `nightly-feature-precompute.yml` | 平日 20:00 | 特徴量parquet生成、レポート | `docs/feature_precompute_report.json` |
| Quarterly Stress Test | `quarterly-stress-test.yml` | 四半期初日 10:00 | 高コスト前提KPI評価 | `docs/stress_test_report.json` |

## 日次処理

`daily-preopen-core`、`daily-preopen-retry`、`daily-watchdog`は`jpx_calendar.py is-open`でJPX営業日を確認します。休場日は処理をスキップします。

`daily-preopen-retry`は`run_guard.py needs-core-run`で`docs/state.json`の当日エントリを確認し、すでに更新済みなら実行しません。

`daily-publish-dashboard`は`workflow_run`でcore/retry成功後に起動します。手動実行時は`force_publish=true`で当日更新チェックを回避できます。

`weekly-model-retrain`は日次処理の`main.py`を呼ばず、`state.json`やダッシュボードJSONを更新しません。

## publishの同期仕様

publish workflowは以下を行います。

1. `docs/dashboard_index.json`を`web/public/dashboard_index.json`へコピー
2. `docs/tickers/`を`web/public/tickers/`へコピー
3. `web/public/history_data.json`を削除
4. `npm ci --prefix web`
5. `npm run build:prod --prefix web`
6. `web/out/`を`docs/`へ`rsync --delete`

`rsync`では`state.json`、`backtest_report.json`、監査系JSON、`CNAME`、`.nojekyll`などを除外します。

## 権限と排他

書き込み系workflowは`contents: write`です。日次core/retryは同じ`daily-core-main`で直列化されます。publishは`daily-publish-main`で最新実行を優先します。

書き込み系workflowのcommit/pushは`.github/scripts/commit-and-push.sh`へ集約しています。このスクリプトはcommit後に`git pull --rebase --autostash`を行い、pushを最大3回リトライします。

## 現行制約

- `nightly-feature-precompute`が生成する`data/features/*.parquet`はcommit対象にも日次処理の入力にもなっていない
