# 現行仕様概要

更新日: 2026-05-03 JST

このディレクトリの仕様は、ソースコードを正として再整理したものです。過去の問題一覧や将来設計メモは、現行仕様と混ざらないように整理し、残課題は`06_priority_matrix.md`へ集約しました。

## 対象

| 領域 | 対象 | 詳細 |
|---|---|---|
| バックエンド | `main.py`, `src/*.py` | `01_backend_python.md` |
| フロントエンド | `web/` | `02_frontend_web.md` |
| GitHub Actions | `.github/workflows/*.yml` | `03_cicd_workflows.md` |
| 補助スクリプト | `scripts/*.py` | `04_scripts.md` |
| データ契約・横断仕様 | `tickers.yml`, `data/`, `docs/`, env | `05_cross_cutting.md` |
| 問題点・改善点 | 実装レビュー結果 | `06_priority_matrix.md` |

## システムの現在地

このプロジェクトは、日本株の監視銘柄に対して日次で以下を実行します。

1. `tickers.yml`から有効銘柄を読み込む
2. Stooqまたはyfinanceから日足OHLCVを取得する
3. 価格・出来高・テクニカル特徴量を生成する
4. LightGBMで翌日の上昇確率を推定する
5. OOSバックテストとコスト/スリッページ込みのKPIゲートで売買可能性を制御する
6. ゲートを通過した非`HOLD`シグナルだけLINE通知する
7. `docs/`配下にダッシュボード用JSONと監査レポートを出力する
8. Next.jsの静的エクスポートをGitHub Pages用に`docs/`へ同期する

## 正とするデータ契約

現行フロントエンドは`docs/dashboard_index.json`と`docs/tickers/*.json`を読む構成です。旧来の`docs/history_data.json`は主要契約ではなく、存在する場合はダッシュボード出力やpublish workflowで削除されます。

## 今回整理した古い文書

以下の旧メモは、内容を現行仕様または課題一覧に統合したため削除対象です。

- `github-actions-frequency-design.md`
- `portfolio-optimization-strategy.md`
- `profitability-roadmap.md`
