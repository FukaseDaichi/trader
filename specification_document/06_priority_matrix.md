# 問題点・改善点・今後の修正箇所

更新日: 2026-05-03 JST

ソースコードを正として確認した、現時点の残課題一覧です。過去文書にあった「HTTPタイムアウト未設定」など、すでに実装済みの内容は除外しています。

## P0: 運用停止・誤運用につながる課題

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| 銘柄単位の例外ハンドリング不足 | `main.py` | 1銘柄の例外で日次処理全体が止まり、ダッシュボード更新まで到達しない可能性 | ticker loopを`try/except`で囲み、失敗銘柄をレポートへ記録 |
| GitHub Actionsのpush競合 | `.github/workflows/*.yml` | 複数workflowがpull/rebaseなしでpushする | push前に`git pull --rebase`、またはpush専用concurrencyを追加 |
| 週次再学習が専用処理ではない | `weekly-model-retrain.yml`, `main.py` | 土曜に`main.py`をそのまま実行し、`state.json`やdocsを週末日付で更新し得る | retrain専用スクリプト化、または`RUN_DATE_JST`/dashboard更新の扱いを分離 |
| feature precomputeが未活用 | `feature_precompute.py`, workflow | `data/features/*.parquet`を生成するがcommitも参照もされない | 活用するなら日次処理を読み取り対応、不要ならworkflow削除 |

## P1: データ品質・シグナル品質

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| OHLCV整合性検証が弱い | `src/data_loader.py` | 必須列と数値変換は見るが、価格の正値・OHLC関係・異常値は見ない | `_validate_ohlcv()`を追加し、異常データは警告/除外 |
| parquet同期が削除固定 | `sync_data_files()` | `tickers.yml`から外れた銘柄のparquetを即削除 | バックアップ移動またはdry-run/confirmオプションを追加 |
| Stooq/yfinance差分の履歴上書き | `update_data()` | 同一日付は新データ後勝ち | 旧値との差分が大きい場合に警告し、監査レポートに残す |
| KPIゲート失敗理由の粒度 | `backtest.py`, `main.py` | 失敗理由は文字列リスト中心 | UI表示用に構造化した失敗コード/説明へ分離 |
| `tickers.yml`の構造検証不足 | `src/config.py` | `code`/`name`欠落時に後段で壊れる | `load_tickers()`で必須キー、型、重複tickerを検証 |

## P2: フロントエンド品質

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| チャート空データ時のガード不足 | `StockChart.tsx` | `Math.min(...[])`や`Math.max(...[])`で不正domainになり得る | 空データ時のプレースホルダー表示を追加 |
| JSONのランタイム検証なし | `page.tsx`, `StockDetailContent.tsx` | TypeScript型のみ | 軽量validatorまたはzod導入 |
| 自動閾値と説明文がズレる | `page.tsx` | 固定閾値80/65/25/10/4を説明 | 実際の`thresholds`をJSONへ出して表示、または説明を「既定値」に変更 |
| エラー/404/loading境界が少ない | `web/src/app/` | クライアント内の簡易loading/errorのみ | `error.tsx`, `not-found.tsx`, `loading.tsx`を追加 |
| アクセシビリティ不足 | `StockChart.tsx`, `StockDetailContent.tsx` | chartの代替情報、アイコンリンクのariaが限定的 | `aria-label`, `aria-pressed`, 代替サマリを追加 |

## P3: 監視・保守性

| 課題 | 対象 | 現状 | 推奨対応 |
|---|---|---|---|
| watchdogが通知しない | `workflow_watchdog.py`, workflow | exit code 1のみ | LINE/Issue/メール通知を追加 |
| LINE通知のリトライなし | `src/notifier.py` | 一時失敗で通知が失われる | exponential backoffで数回リトライ |
| timezone naiveなレポート時刻 | `src/backtest.py`, `scripts/*.py` | `datetime.now()`が混在 | `ZoneInfo("Asia/Tokyo")`へ統一 |
| `print()`中心のログ | 全Python | ログレベル・構造化なし | `logging`を導入 |
| 型ヒント不足 | `src/model.py`, `src/data_loader.py`, `src/backtest.py` | 一部のみ型あり | 変更頻度の高い関数から型付け |
| テスト基盤なし | リポジトリ全体 | `tests/`なし、pytest/vitestなし | predictor/config/backtest/data_loaderからユニットテスト追加 |
| `numpy`直接依存未宣言 | `pyproject.toml` | 複数ファイルでimportするが依存にない | `numpy`を直接dependencyへ追加 |
| `.gitignore`不足 | `.gitignore` | `data/features/`, `web/.next/`などのトップレベル除外が弱い | 生成物除外を追加 |

## 中期改善

1. 銘柄横断ポートフォリオ層を追加する
- 現状は銘柄ごとの独立判定です。
- `prob_up`, `gate_passed`, `volatility`, `thresholds`を使って採用順位とウェイトを決める`src/portfolio.py`を追加すると、売買回転率や集中リスクを制御しやすくなります。

2. 予測ターゲットを拡張する
- 現状は翌日上昇/下落の二値分類です。
- 期待リターン、下方リスク、値幅を直接見るモデルを追加すると、上昇確率だけでは拾えない期待値改善が可能です。

3. ユニバース更新を実装化する
- `scripts/universe_refresh.py`は現状スナップショットです。
- 候補抽出、除外理由、流動性フィルタ、ファンダメンタル根拠を持つ実運用ロジックに拡張できます。

4. ダッシュボードにバックテスト/閾値情報を表示する
- 現在UIに表示されるのは主に最新シグナルと価格指標です。
- 銘柄別のゲート失敗理由、最適化閾値、holdout KPIを表示すると、なぜ見送りになったかを理解しやすくなります。
