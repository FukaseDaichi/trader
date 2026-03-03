# 全体調査レポート — 概要

## 調査日

2026-03-03

## 対象リポジトリ

`trader` — 日本株式自動予測・売買シグナルシステム

## 調査範囲

| 領域 | 対象ファイル数 | ドキュメント |
|------|-------------|-------------|
| Python バックエンド (`src/`, `main.py`) | 8 | `01_backend_python.md` |
| フロントエンド (`web/`) | 12 | `02_frontend_web.md` |
| CI/CD ワークフロー (`.github/workflows/`) | 11 | `03_cicd_workflows.md` |
| スクリプト (`scripts/`) | 7 | `04_scripts.md` |
| 横断的課題 | — | `05_cross_cutting.md` |
| 修正優先度マトリクス | — | `06_priority_matrix.md` |

## 検出された問題の概要

| 重大度 | 件数 |
|--------|------|
| CRITICAL | 4 |
| HIGH | 12 |
| MEDIUM | 22 |
| LOW | 18 |
| **合計** | **56** |

※ 合計56件は、領域別ドキュメント間の再掲を含む集計値。

## 最も影響が大きい問題 (TOP 3)

1. **git push 競合** — 10のワークフローが `git pull --rebase` なしで `main` にプッシュ。同時実行で non-fast-forward エラーが発生する
2. **HTTP リクエストにタイムアウト未設定** — `data_loader.py` の Stooq API 呼び出しが無制限にハングする可能性
3. **閾値最適化の過学習リスク** — `backtest.py` は閾値候補（最大1500、既定設定では1134）を OOS データで探索し、同じ OOS で最終 KPI 判定まで行っている

## TOP3-3 補足（現状確認と修正方針）

1. 実装現状（`src/backtest.py`）
- `_collect_oos_predictions()` で銘柄ごとの OOS 予測を作成
- `_optimize_thresholds()` が同じ OOS 上で最良閾値を選択
- `evaluate_kpi_gate()` が同じ OOS で再評価し、ゲート通過可否を決定

2. 過学習リスクの中身
- 閾値選択バイアス（winner's curse）により、実運用時の KPI を過大評価しやすい
- 候補数が多いほど、偶然当たった閾値を採用する確率が上がる

3. 銘柄ごとの学習期間の実態
- `main.py` は銘柄ごとに独立処理（銘柄別に `evaluate_kpi_gate()` と `train_and_predict()` を実行）
- KPIゲートは `TRADER_BT_VALIDATION_YEARS`（既定4年）の直近期間を使用
- 予測モデルも直近4年を使用（`src/model.py` 側は現状ハードコード）

4. 修正方針（概要）
- 閾値最適化用期間と KPI 最終判定期間を時系列で分離する（例: 先頭2foldで最適化、最後1foldで判定）
- `docs/backtest_report.json` に tuning/holdout を分けた指標を出力し、楽観バイアスを監視する
