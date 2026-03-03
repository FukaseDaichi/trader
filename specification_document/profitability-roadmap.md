# 株式収益システム 高収益化ロードマップ（現行実装レビュー反映版）

更新日: 2026-03-03 (JST)
対象コード: `main.py`, `src/*.py`, `scripts/*.py`, `.github/workflows/*.yml`

## 1. 到達目標（再定義）

- 最終目標は、`予測精度` ではなく `コスト込みの資産曲線` を改善すること。
- 評価軸は次で固定する。
  - CAGR
  - Max Drawdown
  - Sharpe
  - Expectancy
  - Turnover

## 2. 現行実装で既にできていること（コード確認済み）

1. 日次パイプライン
- `main.py` で、銘柄ごとに `update_data -> add_features -> KPIゲート -> 学習予測 -> シグナル生成 -> 通知` を実行。
- `tickers.yml` の有効銘柄と `data/*.parquet` を `sync_data_files` で同期し、不要データを自動削除。

2. 特徴量とモデル
- `src/model.py` で 35 特徴量（リターン、MA乖離、RSI、MACD、ATR、出来高、ローソク足、カレンダー等）を生成。
- LightGBM を walk-forward（3 folds, purge gapあり）で学習し、最終確率をアンサンブル平均で算出。

3. KPIゲート（OOS + コスト/スリッページ込み）
- `src/backtest.py` で OOS 予測を収集し、売買ルールをシミュレーション。
- `CAGR / MaxDD / Sharpe / Expectancy / Trades / Turnover` を算出。
- 閾値（BUY/MILD_BUY/MILD_SELL/SELL）は候補グリッドから日次最適化可能。
- ゲート未達時は `HOLD` に強制し、`docs/backtest_report.json` に理由を出力。

4. 運用基盤（GitHub Actions）
- 朝の core/retry、publish、watchdog、週次再学習、週次ユニバース更新、月次監査、四半期ストレステストまで実装済み。
- JPX 営業日判定 (`scripts/jpx_calendar.py`) と当日更新ガード (`scripts/run_guard.py`) を導入済み。

5. 可視化と成果物
- `src/dashboard.py` が `docs/state.json`, `docs/dashboard_index.json`, `docs/tickers/*.json` を更新。
- フロントは `web/` の静的ビルドを `docs/` に同期して公開。

## 3. 収益面で残っているギャップ（60点要因）

1. ポートフォリオ最適化が未実装
- 現在は銘柄ごとの独立判定。銘柄横断の配分最適化ロジックは未導入（`src/portfolio.py` なし）。

2. 目標変数が方向（二値）中心
- 現行は「翌日上昇/下落」を予測。利幅・下方リスクを直接最適化するターゲットは未導入。

3. 特徴量ソースが価格系列中心
- ファンダメンタル、イベント、地合い要因は未統合。

4. 実験管理の標準化が不足
- 月次/四半期レポートはあるが、実験ID単位での比較管理（再現性ログ）は未整備。

## 4. 高収益化ロードマップ（現実装に接続する順序）

## P0（最優先: 2〜4週間）

1. 銘柄横断の配分レイヤーを追加
- `src/portfolio.py` を新設し、`main.py` の全シグナルを入力に最終配分を決定。
- 初期ロジックは `edge = prob_up - mild_buy_threshold` とボラ調整で ranking。
- 上限（1銘柄上限、現金比率）を必須化。

2. KPIゲートと配分評価の接続
- 単銘柄の gate 成績だけでなく、日次の採用銘柄集合ベースで portfolio KPI を出力。
- `docs/portfolio_report.json` を追加し、月次監査に統合。

3. 売買回転率の抑制
- 配分変化が小さい場合はリバランスを抑制（差分閾値）。

## P1（次段階: 1〜2か月）

4. 予測ターゲットを拡張
- 二値分類に加えて期待リターン推定を追加（回帰 or マルチタスク）。

5. 特徴量拡張
- `market regime`（指数トレンド/ボラ）を追加。
- 企業イベント系データの導入余地を設計（データ品質ガードとセット）。

6. 候補母集団の拡張
- `scripts/universe_refresh.py` を placeholder から実運用ロジックへ拡張し、Layer化（Universe/Candidate/Core）を実装。

## P2（運用高度化: 2か月〜）

7. 実験管理
- パラメータ・期間・KPIを実験IDで保存し、採用判定を自動化。

8. ドリフト監視と自動再学習
- 特徴量分布・成績悪化を検知したときの臨時再学習トリガーを追加。

## 5. 次の1週間で着手する具体タスク

1. `src/portfolio.py` の最小版を追加（ranking + weight cap + cash）。
2. `main.py` で銘柄シグナル後に `optimize_portfolio` を呼ぶ。
3. `docs/portfolio_report.json` の出力を追加。
4. `scripts/monthly_audit.py` へ portfolio 集計項目を追加。
5. `web/` に配分サマリ（採用銘柄、ウェイト、現金比率）を表示。
