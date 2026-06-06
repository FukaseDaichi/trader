# 重要修正項目

更新日: 2026-06-06 JST

この一覧は、ソースコードを正として確認した残課題のうち、運用停止、誤運用、データ損失、シグナル品質低下、ユーザーへの誤表示につながるものに絞っています。単なるリファクタ、型ヒント追加、ログ整備、未使用依存などは、この優先表からは外します。

## 対応済み

P0/P1 の重要修正は実装済みです。

| 優先度 | 対応内容 | 主な変更先 |
|---|---|---|
| P0 | 銘柄単位の例外分離と失敗状態のレポート/ダッシュボード記録 | `main.py` |
| P0 | 書き込み系 workflow の commit/push 共通化、rebase、push retry | `.github/scripts/commit-and-push.sh`, `.github/workflows/*.yml` |
| P0 | 週次再学習を日次処理から分離し、`state.json` とダッシュボード JSON を更新しない専用処理へ変更 | `scripts/weekly_model_retrain.py`, `weekly-model-retrain.yml` |
| P0 | 無効銘柄 parquet の即削除をやめ、`data/archive/` へ退避 | `src/data_loader.py` |
| P1 | OHLCV の正値、OHLC 関係、異常終値変化を検証し、警告をレポートへ記録 | `src/data_loader.py`, `main.py`, `scripts/monthly_audit.py`, `scripts/stress_test.py` |
| P1 | `tickers.yml` の必須キー、型、重複 ticker、`settings` 構造を起動時に検証 | `src/config.py` |
| P1 | 実際に使ったシグナル閾値を JSON へ出力し、UI に表示 | `src/predictor.py`, `main.py`, `web/src/app/page.tsx`, `web/src/components/SignalCard.tsx` |
| P1 | watchdog 失敗時に GitHub Issue を作成 | `.github/workflows/daily-watchdog.yml` |
| P1 | AI 銘柄キュレーションを agent 出力と決定論 merge に分離し、`tickers.yml` の不可逆変更をスクリプト側へ限定 | `.claude/skills/*`, `scripts/curation_merge.py`, `.github/workflows/daily-ticker-curation.yml` |
| P1 | publish の `rsync --delete` で `docs/curation/` が消えないよう除外 | `.github/workflows/daily-publish-dashboard.yml` |
| P1 | warmup 候補データを `data/watchlist/` に分離し gitignore | `src/data_loader.py`, `scripts/curation_warmup.py`, `.gitignore` |

## P2: 次に見る運用品質

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| チャート空データ時のガード不足 | `web/src/components/StockChart.tsx` | 空配列または価格欠損だけの配列で `Math.min(...[])` / `Math.max(...[])` が不正な domain を作る | 空データ時はチャートを描画せず、銘柄名と「価格データなし」を表示する |
| LINE 通知のリトライがない | `src/notifier.py`, `scripts/curation_notify.py` | LINE Push API の一時失敗は `print()` して終了し、通知が失われる | 429/5xx/timeout に限定して短い backoff retry を入れ、最終失敗をレポートへ残す |
| 週次レポート品質検証が未実装 | `.claude/skills/weekly-stock-report`, `scripts/curation_notify.py` | Markdown の免責・front matter・銘柄コード存在チェックは agent 手順頼み | 通知前に軽量 validator を追加し、不合格時は通知をスキップする |
| `data/features/*.parquet` が運用効果を持たない | `scripts/feature_precompute.py`, `nightly-feature-precompute.yml` | 生成されるが commit されず、日次処理も読まない | 不要なら workflow 停止、使うなら日次 pipeline の入力契約へ組み込む |
