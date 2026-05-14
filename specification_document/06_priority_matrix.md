# 重要修正項目

更新日: 2026-05-14 JST

この一覧は、ソースコードを正として確認した残課題のうち、運用停止、誤運用、データ損失、シグナル品質低下、ユーザーへの誤表示につながるものだけに絞っています。単なるリファクタ、型ヒント追加、ログ整備、未使用依存、`.gitignore`整理などは、この優先表からは外します。

## P0: 最優先で直す

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| 銘柄単位の例外分離がない | `main.py` | 1銘柄の`update_data()`、`add_features()`、`evaluate_kpi_gate()`、`train_and_predict()`などで例外が出ると、以降の銘柄処理、KPIレポート出力、ダッシュボード更新まで止まる | ticker loopを銘柄単位の`try/except`で囲み、失敗銘柄は`backtest_report.json`と`state.json`へ失敗状態として残す |
| GitHub Actionsのpush競合 | `.github/workflows/*.yml` | 書き込み系workflowはそれぞれcommit/pushするが、横断的な排他やpush直前の`git pull --rebase`がない | push処理を共通化し、rebase/retryを入れる。必要なら書き込み系workflowを同一concurrency groupで直列化する |
| 週次再学習が日次処理をそのまま実行している | `weekly-model-retrain.yml`, `main.py`, `src/dashboard.py` | 土曜の`weekly-model-retrain`が通知だけ無効にして`main.py`を実行するため、週末日付の`state.json`や`dashboard_index.json`を生成し得る | 再学習専用スクリプトを分け、週次実行では`state.json`とダッシュボードJSONを更新しない。日次処理を使う場合はrun modeを追加する |
| 非有効銘柄のparquetを日次開始時に即削除する | `src/data_loader.py`, `main.py` | `sync_data_files(active_codes)`が`tickers.yml`から外れた`data/*.parquet`を即削除する。設定ミスや一時的な無効化で履歴データを失う | 日次処理では削除せず、`data/archive/`へ移動するか、明示的なcleanupコマンドだけで削除する |

## P1: シグナル品質と表示信頼性を守る

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| OHLCV整合性検証が弱い | `src/data_loader.py` | `_normalize_ohlcv()`は必須列、日付、数値変換までは見るが、正値、`low <= open/close <= high`、異常な日次変化は検証しない | `_validate_ohlcv()`を追加し、異常行の除外または警告を`backtest_report.json`や監査レポートに残す |
| `tickers.yml`の構造検証が不足 | `src/config.py` | `code`/`name`欠落、型違い、重複tickerを明示検証しないため、後段で`KeyError`や重複出力が起き得る | `load_tickers()`で必須キー、型、重複、`settings.max_tickers`をまとめて検証し、起動時に分かるエラーにする |
| 自動最適化後の閾値がUIに出ない | `src/backtest.py`, `src/dashboard.py`, `web/src/app/page.tsx` | バックエンドは銘柄ごとに閾値を最適化するが、シグナルJSONには使用閾値がなく、一覧画面は既定閾値を「現行ロジック」として説明している | `signal`またはticker JSONに実際の`thresholds`を出力し、UI説明を銘柄別の値または「既定値」に合わせる |
| watchdog失敗が外部通知されない | `scripts/workflow_watchdog.py`, `daily-watchdog.yml` | 日次成果物チェックはexit code 1で失敗するだけで、LINE、Issue、メールなどの通知がない | watchdog失敗時に通知する。最低限GitHub Issue作成またはLINE通知を追加し、日次停止を見落とさないようにする |

## P2: 次に見る運用品質

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| チャート空データ時のガード不足 | `web/src/components/StockChart.tsx` | 空配列または価格欠損だけの配列で`Math.min(...[])`/`Math.max(...[])`が不正なdomainを作る | 空データ時はチャートを描画せず、銘柄名と「価格データなし」を表示する |
| LINE通知のリトライがない | `src/notifier.py` | LINE Push APIの一時失敗は`print()`して終了し、シグナル通知が失われる | 429/5xx/timeoutに限定して短いbackoff retryを入れ、最終失敗をレポートへ残す |
