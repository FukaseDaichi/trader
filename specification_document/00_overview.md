# 全体調査レポート — 概要

## 調査日

2026-03-04

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
| HIGH | 11 |
| MEDIUM | 21 |
| LOW | 18 |
| **合計** | **54** |

※ 合計54件は、領域別ドキュメント間の再掲を含む集計値。

## 最も影響が大きい問題 (TOP 3)

1. **git push 競合** — 10のワークフローが `git pull --rebase` なしで `main` にプッシュ。同時実行で non-fast-forward エラーが発生する
2. **HTTP リクエストにタイムアウト未設定** — `data_loader.py` の Stooq API 呼び出しが無制限にハングする可能性
3. **volatility NaN 時の表示崩れ** — `predictor.py` で通知文言が `nan%` になる可能性
