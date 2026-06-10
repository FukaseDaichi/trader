# AI銘柄キュレーション 現行仕様 — 概要

更新日: 2026-06-06 JST
ステータス: 実装済み機能の As-built 仕様。

このサブディレクトリは、AI 銘柄キュレーションの現行仕様です。実装は `.github/workflows/daily-ticker-curation.yml`、`.github/workflows/weekly-fundamental-report.yml`、`scripts/curation_*`、`scripts/technical_screen.py`、`.claude/skills/*`、`curation_pool.yml`、`tickers.yml settings.curation` にあります。

## 1. 目的

日本株の候補群を継続的に分析し、「上がりそうな銘柄」で `tickers.yml` の有効ユニバースを安全に少数入替します。さらに週に一度、ファンダメンタルとテクニカルを総合した Markdown レポートを生成し、GitHub URL を LINE に通知します。

設計の核は、LLM の判断と不可逆変更の分離です。Claude agent は構造化 JSON または Markdown を所定パスに書くだけで、`tickers.yml` の編集と push は決定論スクリプトと workflow が担います。

## 2. 運用方針

| 論点 | 現行 |
|---|---|
| 実行環境 | GitHub Actions |
| Agent 実行 | `anthropics/claude-code-action@v1` + `CLAUDE_CODE_OAUTH_TOKEN` |
| ユニバース反映 | `curation_merge.py` が guardrail 通過時のみ `tickers.yml` を更新 |
| テクニカル頻度 | 平日 04:30 JST の日次 |
| マクロ頻度 | 土曜 07:00 JST の週次、ファンダ前。`macro_latest.json` をファンダ/レポートが消費（merge は不参照） |
| ファンダ頻度 | 土曜 07:00 JST の週次。日次 merge は直近 `fundamental_latest.json` をキャッシュ利用 |
| 週次レポート | `reports/weekly_YYYY-MM-DD.md` と `reports/weekly_latest.md` |
| 通知 | `curation_notify.py` が週次レポートの GitHub blob URL を LINE 通知 |

## 3. スコープ

### やること

- 日次: 候補データ warmup、決定論テクニカル baseline、Claude technical agent、決定論 merge、`tickers.yml` 更新
- 週次: Claude macro agent（金利・為替）、Claude fundamental agent、Claude report writer、レポート URL の LINE 通知
- 決定ログを `docs/curation/decision_*.json` に残す
- 新規候補は `data/watchlist/` で warmup し、十分な履歴がある場合だけ昇格させる

### やらないこと

- 売買執行・発注
- 既存予測パイプラインや LightGBM モデルの置換
- フロントエンドでのキュレーション履歴の一覧表示（Phase 3 の `RegimeBanner` が `macro_latest.json` の表示と決定ログ・週次レポートへのリンクを持つのみ。`../02_frontend_web.md` 参照）

## 4. 全体アーキテクチャ

```text
毎営業日 04:30 JST: daily-ticker-curation.yml
  checkout → uv sync → JPX営業日判定 → curation_guard
    → curation_warmup.py
    → technical_screen.py（決定論baseline）
    → /jp-stock-technical-screen（任意精査、continue-on-error）
    → curation_merge.py（週次fundamental cache + 当日technical）
    → load_tickers()検証
    → commit-and-push.sh

毎営業日 06:00 JST: daily-preopen-core.yml
  最新 tickers.yml で main.py 実行

毎週 土曜 07:00 JST: weekly-fundamental-report.yml
  checkout → uv sync → technical_screen.py
    → /global-macro-screen（金利・為替・世界情勢のWeb調査、continue-on-error）
    → /jp-stock-fundamental-screen（Web調査、continue-on-error）
    → /weekly-stock-report（Markdown生成、continue-on-error）
    → commit-and-push.sh
    → curation_notify.py（LINE、失敗は非致命）
```

## 5. 主な設計判断

- テクニカルは日々変化するため日次で評価します。
- ファンダメンタルは週次更新とし、平日はキャッシュを利用します。
- 新規昇格は tech/fund の両軸、warmup、churn、セクター、cooldown、ファンダ鮮度で guard します。
- `data/watchlist/` は gitignore され、publish や日次本体の `sync_data_files()` に巻き込まれません。
- `reports/` は `docs/` 外なので GitHub Pages publish の `rsync --delete` で削除されません。
- `docs/curation/` は publish workflow の rsync 除外に追加済みです。

## 6. 文書一覧

| ファイル | 内容 |
|---|---|
| `00_overview.md` | 本書。目的・全体像・設計判断 |
| `01_agent_design.md` | 4 agent の役割、skill、入出力 |
| `02_merge_guardrails.md` | `curation_merge.py` の合成ロジックと guardrail |
| `03_workflows_cicd.md` | 2 workflow の実行順、認証、障害時挙動 |
| `04_data_contracts.md` | ファイルスキーマと統合制約 |
| `05_weekly_report.md` | 週次レポートの構成、文体、LINE通知 |
| `06_rollout_risks.md` | 段階導入、残課題、ロールバック |

## 7. 技術前提

- Claude skills は `.claude/skills/{jp-stock-technical-screen,global-macro-screen,jp-stock-fundamental-screen,weekly-stock-report}/` に配置済み
- `curation_pool.yml` は流動性のある日本株候補を保持
- `tickers.yml settings.curation` が運用パラメータを保持
- `data/watchlist/` は `.gitignore` 対象
- `scripts/curation_merge.py` の純粋関数 `compute_decision()` は `tests/test_curation_merge.py` で単体テスト可能
