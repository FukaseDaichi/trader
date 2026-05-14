# 重要修正項目

更新日: 2026-05-14 JST

この一覧は、ソースコードを正として確認した残課題のうち、運用停止、誤運用、データ損失、シグナル品質低下、ユーザーへの誤表示につながるものだけに絞っています。単なるリファクタ、型ヒント追加、ログ整備、未使用依存、`.gitignore`整理などは、この優先表からは外します。

## 対応済み

2026-05-14にP0/P1の重要修正は実装済みです。

| 優先度 | 対応内容 | 主な変更先 |
|---|---|---|
| P0 | 銘柄単位の例外分離と失敗状態のレポート/ダッシュボード記録 | `main.py` |
| P0 | 書き込み系workflowのcommit/push共通化、rebase、push retry | `.github/scripts/commit-and-push.sh`, `.github/workflows/*.yml` |
| P0 | 週次再学習を日次処理から分離し、`state.json`とダッシュボードJSONを更新しない専用処理へ変更 | `scripts/weekly_model_retrain.py`, `weekly-model-retrain.yml` |
| P0 | 無効銘柄parquetの即削除をやめ、`data/archive/`へ退避 | `src/data_loader.py` |
| P1 | OHLCVの正値、OHLC関係、異常終値変化を検証し、警告をレポートへ記録 | `src/data_loader.py`, `main.py`, `scripts/monthly_audit.py`, `scripts/stress_test.py` |
| P1 | `tickers.yml`の必須キー、型、重複ticker、`settings`構造を起動時に検証 | `src/config.py` |
| P1 | 実際に使ったシグナル閾値をJSONへ出力し、UIに表示 | `src/predictor.py`, `main.py`, `web/src/app/page.tsx`, `web/src/components/SignalCard.tsx` |
| P1 | watchdog失敗時にGitHub Issueを作成 | `.github/workflows/daily-watchdog.yml` |

## P2: 次に見る運用品質

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| チャート空データ時のガード不足 | `web/src/components/StockChart.tsx` | 空配列または価格欠損だけの配列で`Math.min(...[])`/`Math.max(...[])`が不正なdomainを作る | 空データ時はチャートを描画せず、銘柄名と「価格データなし」を表示する |
| LINE通知のリトライがない | `src/notifier.py` | LINE Push APIの一時失敗は`print()`して終了し、シグナル通知が失われる | 429/5xx/timeoutに限定して短いbackoff retryを入れ、最終失敗をレポートへ残す |
