# GitHub Actions仕様

更新日: 2026-06-06 JST

## ワークフロー一覧

| Workflow | ファイル | JST | 主処理 | commit対象 |
|---|---|---:|---|---|
| Daily Ticker Curation | `daily-ticker-curation.yml` | 平日 04:30 | JPX営業日なら候補warmup、テクニカルscreen、Claude技術分析、決定論merge | `tickers.yml`, `data/`, `docs/curation/` |
| Daily Preopen Core | `daily-preopen-core.yml` | 平日 06:00 | JPX営業日なら `main.py` | `data/`, `docs/` |
| Daily Preopen Retry | `daily-preopen-retry.yml` | 平日 06:20/06:40 | 当日未更新なら `main.py` | `data/`, `docs/` |
| Daily Publish Dashboard | `daily-publish-dashboard.yml` | core/retry成功後 | Next.js 静的ビルドを `docs/` へ同期 | `docs/` |
| Daily Watchdog | `daily-watchdog.yml` | 平日 12:30 | 日次成果物チェック、失敗時 Issue 作成 | なし |
| Weekly Fundamental & Report | `weekly-fundamental-report.yml` | 土曜 07:00 | ファンダ評価、週次Markdownレポート、LINE通知 | `reports/`, `docs/curation/` |
| Weekly Model Retrain | `weekly-model-retrain.yml` | 土曜 08:00 | `weekly_model_retrain.py` でデータ更新と学習可否レポート | `data/`, `docs/weekly_retrain_report.json` |
| Weekly Universe Refresh | `weekly-universe-refresh.yml` | 日曜 07:00 | ユニバーススナップショット | `docs/universe_refresh_report.json` |
| Monthly Calendar Sync | `monthly-calendar-sync.yml` | 毎月1日 09:15 | JPX休日キャッシュ更新 | `data/jpx_holidays.json` |
| Monthly Full Audit | `monthly-full-audit.yml` | 第1日曜 09:00 | KPI月次監査 | `docs/monthly_audit.json` |
| Nightly Rotating Refresh | `nightly-rotating-refresh.yml` | 平日 19:30 | 銘柄をバケット分割して更新 | `data/`, `docs/rotating_refresh_report.json` |
| Nightly Feature Precompute | `nightly-feature-precompute.yml` | 平日 20:00 | 特徴量 parquet 生成、レポート | `docs/feature_precompute_report.json` |
| Quarterly Stress Test | `quarterly-stress-test.yml` | 四半期初日 10:00 | 高コスト前提 KPI 評価 | `docs/stress_test_report.json` |

## 日次処理

`daily-ticker-curation`、`daily-preopen-core`、`daily-preopen-retry`、`daily-watchdog` は `jpx_calendar.py is-open` で JPX 営業日を確認します。休場日は処理をスキップします。

`daily-ticker-curation` は `curation_guard.py needs-run` で当日の `docs/curation/decision_YYYY-MM-DD.json` または `decision_latest.json` を確認し、すでに実行済みならスキップします。

`daily-preopen-retry` は `run_guard.py needs-core-run` で `docs/state.json` の当日エントリを確認し、すでに更新済みなら実行しません。

`daily-publish-dashboard` は `workflow_run` で core/retry 成功後に起動します。手動実行時は `force_publish=true` で当日更新チェックを回避できます。

## AI銘柄キュレーション

日次キュレーションの流れ:

1. `curation_warmup.py --pool curation_pool.yml --out-dir data/watchlist` で未enabled候補の価格データを取得
2. `technical_screen.py --pool curation_pool.yml --date <JST>` で決定論ベースラインを出力
3. Claude Code Action の `/jp-stock-technical-screen` が `technical_latest.json` を必要に応じて精査
4. `curation_merge.py --technical docs/curation/technical_latest.json --date <JST> --apply|--dry-run` が、週次キャッシュ `fundamental_latest.json` と合成して `tickers.yml` を更新
5. `src.config.load_tickers()` で `tickers.yml` を検証
6. `.github/scripts/commit-and-push.sh` で commit/push

週次ファンダ・レポートの流れ:

1. `technical_screen.py` を実行して週次レポート用の最新テクニカルを用意
2. Claude Code Action の `/jp-stock-fundamental-screen` が `docs/curation/fundamental_latest.json` と日付版を出力
3. Claude Code Action の `/weekly-stock-report` が `reports/weekly_YYYY-MM-DD.md` と `reports/weekly_latest.md` を出力
4. commit/push 後、`curation_notify.py` がレポートの GitHub URL を LINE 通知

Claude agent ステップは `continue-on-error: true` です。エージェント失敗時でも、日次側は決定論ベースラインと merge の保守挙動で現状維持できます。

## publishの同期仕様

publish workflow は以下を行います。

1. `docs/dashboard_index.json` を `web/public/dashboard_index.json` へコピー
2. `docs/tickers/` を `web/public/tickers/` へコピー
3. `web/public/history_data.json` を削除
4. `npm ci --prefix web`
5. `npm run build:prod --prefix web`
6. `web/out/` を `docs/` へ `rsync --delete`

`rsync` では `state.json`、`backtest_report.json`、監査系 JSON、`CNAME`、`.nojekyll`、`docs/curation/` などを除外します。週次レポートは `reports/` に出力されるため publish の削除対象外です。

## 権限と排他

書き込み系 workflow は `contents: write` です。watchdog は `contents: read` と `issues: write` です。

日次 core/retry は同じ `daily-core-main` で直列化されます。publish は `daily-publish-main` で最新実行を優先します。キュレーション系は `daily-curation-main` と `weekly-fundamental-main` でそれぞれ直列化されます。

書き込み系 workflow の commit/push は `.github/scripts/commit-and-push.sh` へ集約しています。このスクリプトは `git add -A`、commit、`git pull --rebase --autostash`、push 最大 3 回リトライを行い、差分がなければ正常終了します。

## 現行制約

- `nightly-feature-precompute` が生成する `data/features/*.parquet` は commit 対象にも日次処理の入力にもなっていない
- Claude agent の細粒度ツール制限は workflow の `claude_args` と `.claude/settings.local.json` による補助で、最終的な不可逆変更は決定論スクリプトと commit helper で担保している
