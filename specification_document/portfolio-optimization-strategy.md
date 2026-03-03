# ポートフォリオ最適化設計（現行コード整合版）

更新日: 2026-03-03 (JST)
対象コード: `main.py`, `src/`, `scripts/universe_refresh.py`, `web/`

## 1. 要約

- 現状は「銘柄ごとにシグナル判定」までが実装済みで、銘柄横断の最終配分レイヤーは未実装。
- 本設計は、既存ロジックを壊さずに `単銘柄判定 -> 銘柄横断配分` を追加するための最短ルートを定義する。

## 2. 現行実装の確認結果

1. 実装済み
- `main.py` は銘柄単位処理で `signals: list[dict]` を作成。
- `src/backtest.py` は単銘柄 KPI gate を提供。
- `src/dashboard.py` / `web/` は銘柄ごとの表示に最適化。
- `scripts/universe_refresh.py` は現状 `tickers.yml` のスナップショット生成（Phase-1 placeholder）。

2. 未実装
- `src/portfolio.py`（配分最適化モジュール）なし。
- `main.py` で全銘柄を横断した採用順位付け・配分決定なし。
- `docs/portfolio_report.json` の生成なし。
- Web 側でポートフォリオ（銘柄ウェイト/キャッシュ比率）表示なし。

## 3. 設計原則

1. 既存の KPI gate を前段フィルタとして活用する。
2. 配分ロジックは v1 を単純化し、説明可能性を優先する。
3. 取引回転率を抑える（毎日の過剰入替を避ける）。
4. 将来の候補母集団拡張（Universe/Candidate/Core）に接続可能な入出力を定義する。

## 4. v1 配分ロジック（まず実装する範囲）

## 4.1 入力

- `signals`（`main.py` で生成済み）
  - `ticker`
  - `prob_up`
  - `action`
  - `close`
- `latest_features`（少なくとも `volatility`）
- `thresholds`（KPI gate の最適化結果）

## 4.2 採用条件

- `action in {BUY, MILD_BUY}`
- KPI gate `passed == true`
- 必須データ欠損なし

## 4.3 スコア

- `edge = prob_up - mild_buy_threshold`
- `risk_adj_score = max(edge, 0) / max(volatility, volatility_floor)`
- スコア降順で採用順位を決定

## 4.4 ウェイト計算

- `w_raw = max(risk_adj_score, 0)`
- 正規化して合計 1.0 にする
- 制約
  - 1銘柄上限（例: 25%）
  - 最小採用スコア未満は除外
- 余剰は `cash_weight`

## 4.5 回転率ガード

- 前回配分との差分が `rebalance_epsilon` 未満なら据え置き
- 最小売買単位未満は実質変更なし扱い

## 5. 追加するデータ契約

新規 `docs/portfolio_report.json`（日次更新）

```json
{
  "generated_at": "2026-03-03 06:05:00",
  "summary": {
    "selected_count": 5,
    "cash_weight": 0.30,
    "turnover": 0.18
  },
  "positions": [
    {
      "ticker": "8306.JP",
      "name": "三菱UFJフィナンシャル・グループ",
      "weight": 0.22,
      "score": 1.84,
      "prob_up": 0.74,
      "volatility": 0.021
    }
  ],
  "dropped": [
    {
      "ticker": "6857.JP",
      "reason": "kpi_gate_failed"
    }
  ]
}
```

## 6. 変更対象ファイル

1. 新規
- `src/portfolio.py`
  - `optimize_portfolio(signals, feature_snapshot, prev_portfolio, config)`

2. 変更
- `main.py`
  - 単銘柄シグナル生成後に配分最適化呼び出しを追加
- `src/dashboard.py`
  - `portfolio_report.json` の生成または同期
- `web/src/app/page.tsx`
  - ポートフォリオ概要カード表示（採用銘柄数、キャッシュ比率、上位ウェイト）
- `scripts/monthly_audit.py`
  - portfolio 集計（平均 cash_weight、平均 turnover 等）を追加

## 7. 段階導入

1. Phase 1（現行 `tickers.yml` のみ対象）
- 上記 v1 を実装
- 成果物: `docs/portfolio_report.json`

2. Phase 2（候補母集団拡張）
- `scripts/universe_refresh.py` を実装化し、`Candidate` 層を導入
- 日次詳細学習は上位候補のみ

3. Phase 3（本格制約）
- セクター制約・相関制約を追加
- `tickers.yml` にセクター等メタデータを拡張

## 8. 受け入れ基準（v1）

1. 日次で `portfolio_report.json` が生成される。
2. 1銘柄上限と cash_weight が必ず成立する。
3. KPI gate fail 銘柄は採用されない。
4. 過去30日で turnover の異常スパイクが抑制される。
