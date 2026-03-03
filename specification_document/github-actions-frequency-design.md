# GitHub Actions 頻度設計（実装レビュー反映版）

更新日: 2026-03-03 (JST)
対象コード: `.github/workflows/*.yml`, `scripts/jpx_calendar.py`, `scripts/run_guard.py`, `scripts/workflow_watchdog.py`

## 1. 目的

- 日次の意思決定に必要な処理を朝に集約しつつ、重い処理は夜間・週次・月次へ分離する。
- 失敗時に全停止しないよう、`core/retry/publish/watchdog` を分割して運用安定性を上げる。

## 2. 実装済みワークフロー一覧（現状）

| Workflow | 用途 | Trigger (UTC cron) | JST時刻 | timeout | concurrency |
|---|---|---|---|---:|---|
| `daily-preopen-core.yml` | 日次本処理（`main.py`） | `0 21 * * 0-4` | 平日 06:00 | 30m | `daily-core-main` |
| `daily-preopen-retry.yml` | 日次再実行 | `20 21 * * 0-4`, `40 21 * * 0-4` | 平日 06:20/06:40 | 30m | `daily-core-main` |
| `daily-publish-dashboard.yml` | `web/out` を `docs/` へ同期 | `workflow_run` + 手動 | core/retry 後 | 20m | `daily-publish-main` |
| `daily-watchdog.yml` | 成果物健全性チェック | `30 3 * * 1-5` | 平日 12:30 | 20m | なし |
| `weekly-model-retrain.yml` | 週次再評価（通知無効） | `0 23 * * 5` | 土曜 08:00 | 120m | `weekly-retrain-main` |
| `weekly-universe-refresh.yml` | 週次ユニバースレポート | `0 22 * * 6` | 日曜 07:00 | 120m | `weekly-universe-main` |
| `monthly-calendar-sync.yml` | JPX休日キャッシュ更新 | `15 0 1 * *` | 毎月1日 09:15 | 20m | なし |
| `monthly-full-audit.yml` | 月次KPI監査 | `0 0 1-7 * 0` | 第1日曜 09:00 | 240m | `monthly-audit-main` |
| `nightly-rotating-refresh.yml` | 夜間ローテ更新 | `30 10 * * 1-5` | 平日 19:30 | 45m | なし |
| `nightly-feature-precompute.yml` | 特徴量事前計算 | `0 11 * * 1-5` | 平日 20:00 | 60m | なし |
| `quarterly-stress-test.yml` | 四半期ストレステスト | `0 1 1 1,4,7,10 *` | 四半期初日 10:00 | 120m | なし |

## 3. 日次チェーンの実際の制御

1. 営業日判定
- `daily-preopen-core` / `retry` / `watchdog` は先頭で `jpx_calendar.py is-open` を実行。
- 休場日は重い処理をスキップ。

2. 再実行ガード
- `daily-preopen-retry` は `run_guard.py needs-core-run` を使い、当日更新済みなら即終了。

3. publishガード
- `daily-publish-dashboard` は `run_guard.py has-today-update` を使い、当日更新がなければビルドをスキップ。

4. 監視
- `daily-watchdog` は以下を検証。
  - `docs/state.json` の当日更新
  - `docs/history_data.json` の整合
  - `docs/backtest_report.json` の件数妥当性

## 4. 頻度設計の妥当性（現状評価）

1. 妥当な点
- 朝の意思決定系と夜間/週次メンテが分離されている。
- core/retry が同一 concurrency group で競合しない。
- publish は `workflow_run` 起点で、処理成功時だけ実施される。

2. 残課題
- `weekly-model-retrain` は現状 `main.py` 実行であり、専用学習ジョブには未分離。
- `weekly-universe-refresh` は snapshot レポート中心で、銘柄入替ロジックは未実装。
- リソース使用量の自動可視化（minutes実績）は未導入。

## 5. 次の改善方針

1. retrain専用スクリプト化
- `main.py` 一括実行ではなく、学習更新専用パスへ分離。

2. universe refresh 実装化
- 現在の placeholder を、候補抽出/除外理由付きの実運用ロジックへ拡張。

3. 監視強化
- watchdog 結果を `docs/` に保存し、推移を可視化。
- 失敗時の通知チャネル（LINE または Issue 自動起票）を追加。

## 6. 運用ポリシー（現時点）

1. 必須稼働
- `daily-preopen-core`
- `daily-preopen-retry`
- `daily-publish-dashboard`
- `daily-watchdog`

2. 推奨稼働
- `weekly-model-retrain`
- `weekly-universe-refresh`
- `monthly-calendar-sync`
- `monthly-full-audit`

3. 拡張運用
- `nightly-rotating-refresh`
- `nightly-feature-precompute`
- `quarterly-stress-test`
