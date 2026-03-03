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

## 最も影響が大きい問題 (TOP 5)

1. **git push 競合** — 10のワークフローが `git pull --rebase` なしで `main` にプッシュ。同時実行で non-fast-forward エラーが発生する
2. **HTTP リクエストにタイムアウト未設定** — `data_loader.py` の Stooq API 呼び出しが無制限にハングする可能性
3. **dashboard.py が全価格履歴＋全特徴量を JSON 出力** — `history_data.json` が実測約50MBに膨張し、ページ読込を阻害
4. **閾値最適化の過学習リスク** — `backtest.py` が 1500 候補をOOSデータで探索しつつ同データで評価
5. **フロントエンドの fetch に `res.ok` チェック未実装** — HTTP エラー時に JSON パースが失敗し白画面化
