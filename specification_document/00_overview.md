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

## Issue #3 の修正方針（`history_data.json` 肥大化）

### 目的

- ダッシュボード初回表示の読み込み負荷を大幅に削減する
- 配信データを「UIで実際に使う項目」に限定し、不要な特徴量を配信しない
- 再発防止のため、成果物サイズに上限ルールを設ける

### 実装方針

1. **出力カラム最小化（最優先）**
   - `dashboard.py` の JSON 出力を以下に限定:
     - `date`, `open`, `high`, `low`, `close`, `volume`, `ma_5`, `ma_20`, `ma_60`, `rsi`
   - 学習用途の特徴量 (`return_*`, `macd_*`, `atr_*`, `streak`, `gap` など) は JSON から除外

2. **履歴期間を制限**
   - 各銘柄の出力を直近約2年（目安: 500営業日）に制限
   - 古い履歴は学習用 parquet に保持し、フロント配信対象からは外す

3. **データ分割配信（必要なら第2段階）**
   - `history_data.json` を「一覧表示に必要な最小情報」に寄せる
   - 銘柄詳細チャートは `docs/tickers/{code}.json` のような銘柄別ファイルを遅延読み込み

4. **サイズ監視の自動化**
   - CI/watchdog に「`docs/history_data.json` が閾値超過なら警告/失敗」を追加
   - 初期目標: `history_data.json` を **5MB未満**（段階目標: 10MB未満 → 5MB未満）

### 完了条件（DoD）

- `docs/history_data.json` のサイズが目標値以下
- ダッシュボードの初回読込時間が現状比で明確に改善
- 既存UI（一覧・詳細チャート・シグナル履歴）が欠損なく表示される
