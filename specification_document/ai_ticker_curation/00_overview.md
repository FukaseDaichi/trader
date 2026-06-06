# AI銘柄キュレーション 設計方針 — 概要

作成日: 2026-06-06 JST ／ 改訂: 2026-06-06 (rev.2)
ステータス: 設計方針（To-Be）。実装前のレビュー用ドラフト。

> rev.2 の変更点: ファンダメンタル分析を**週次**に変更。テクニカルは**日次**のまま。週次でファンダ＋テクニカルを総合した**解説レポート(Markdown)**を生成し、その**GitHub URL を LINE 通知**する（女の子ナビが「〜だね！」と話すカジュアル文体）。

> このサブディレクトリ配下の文書は「これから作る機能」の設計方針です。`specification_document/` 直下の `00`〜`06` は現行コード(as-built)の仕様であり、本設計とは分離しています。

## 1. 目的

日本株のトレンドを継続的に分析し、「上がりそうな銘柄」で `tickers.yml` の有効ユニバースを安全に少数入替する。さらに週に一度、人が読んで楽しい**総合解説レポート**を自動生成して LINE に届ける。GitHub Actions 上で `claude-code-action@v1` + `CLAUDE_CODE_OAUTH_TOKEN`（サブスク枠）により無人運用する。

設計の核は「**LLMの判断（分析・採点・文章生成）**」と「**リポジトリの不可逆変更（`tickers.yml`編集・push）**」の分離。後者はテスト可能でガード付きの決定論コードが担い、無人自動pushの安全性を担保する。

## 2. 確定した運用方針

| 論点 | 決定 | 根拠 |
|---|---|---|
| 実行環境 | **GitHub Actions（クラウド）** | 端末常時起動が不要。既存日次ワークフロー群と統一。OAuthトークンはサブスク(`claude setup-token`)で発行。 |
| 反映フロー | **`main` へ自動 commit & push（ガード付き）** | 検証・churn上限・JPX営業日・スキーマ検証を通過時のみ。 |
| ユニバース回転 | **基本維持＋少数入替** | 既存の学習履歴(`data/*.parquet`)を壊さない。1日上限（既定2銘柄）。新規候補はwarmup後に昇格。 |
| **ファンダ頻度** | **週次（土曜）** | 業績・開示は日次で変わらない。週1更新で十分かつクォータ節約。スコアは翌週の日次マージでキャッシュ利用。 |
| **テクニカル頻度** | **日次（平日）** | 価格/出来高トレンドは日々変化。日次の銘柄入替を駆動。 |
| **週次レポート** | **ファンダ＋テクニカル総合のMarkdown** | `reports/` に出力→push→**GitHub URL を LINE 通知**。女の子ナビのカジュアル解説。 |

## 3. スコープ

### やること
- **日次（平日04:30 JST）**: テクニカル・エージェント(Sonnet)が候補を採点 → 決定論マージが「テクニカル(当日) + ファンダ(直近週のキャッシュ)」で合成 → `tickers.yml` を少数入替 → push。
- **週次（土曜07:00 JST）**: ファンダ・エージェント(Opus/Web)がスコア更新 → レポートライター・エージェントが総合解説Markdownを生成 → `reports/weekly_YYYY-MM-DD.md` を push → LINEでGitHub URL通知。
- 各エージェントは**構造化JSON / Markdown を所定パスに書くだけ**で `tickers.yml` は触らない。
- ガード（JPX営業日・冪等・churn上限・セクター上限・スキーマ検証・rebase-safe push）。

### やらないこと
- 売買執行・発注（既存方針どおりシグナル生成まで）。
- 既存予測パイプライン(`main.py`)・モデルの作り替え（入力 `tickers.yml` を更新するだけ）。
- フロントエンド(`web/`)改修（任意拡張として `06` に記載）。

## 4. 全体アーキテクチャ

```
■ 毎営業日 04:30 JST ── .github/workflows/daily-ticker-curation.yml
  checkout → uv sync → JPX営業日判定 → 冪等ガード → 候補データ warmup(data/watchlist/)
    │
    ▼ ② Technical Agent  (claude-code-action@v1 / Sonnet 4.6 / ローカル指標・Web禁止)
    │     → docs/curation/technical_latest.json
    ▼ ③ scripts/curation_merge.py（決定論・ガード）
    │     combined = w_t·technical(当日) + w_f·fundamental(直近週のキャッシュ)
    │     churn上限/セクター上限/warmup充足/昇降格 → tickers.yml 少数入替
    │     → docs/curation/decision_YYYY-MM-DD.json（監査）+ data/watchlist→data/ 移管
    ▼ ④ tickers.yml スキーマ検証 → ⑤ commit → git pull --rebase → push
    │
    ▼（最新 tickers.yml が main 反映済み）
  ■ 06:00 JST ── 既存 daily-preopen-core.yml → 最新ユニバースで main.py 実行

■ 毎週 土曜 07:00 JST ── .github/workflows/weekly-fundamental-report.yml
  checkout → uv sync
    │
    ▼ ① Fundamental Agent (claude-code-action@v1 / Opus 4.8 / Web調査)
    │     → docs/curation/fundamental_latest.json（週次更新＝翌週の日次マージで使用）
    ▼ ⑥ Report Writer Agent (claude-code-action@v1 / Sonnet 4.6 / 女の子ペルソナ)
    │     reads fundamental_latest + technical_latest + 直近 decision_*
    │     → reports/weekly_YYYY-MM-DD.md（「〜だね！」のカジュアル総合解説）
    ▼ ⑦ commit → push
    ▼ ⑧ scripts/curation_notify.py → LINE に GitHub URL 通知（カジュアル文体）
        例: https://github.com/<owner>/<repo>/blob/main/reports/weekly_2026-06-06.md
```

## 5. なぜこの構成か（設計判断）

- **頻度の分離（テク日次 / ファンダ週次）**: テクニカルは日々変化するので日次入替を駆動。ファンダは週次更新で十分（業績/開示は日次で動かない）うえ、Opusの消費を週1に抑えられる。日次マージは直近週のファンダscoreをキャッシュ利用するので、毎日の判断も“業績の裏付け付き”を維持できる。
- **LLM分析と不可逆変更の分離**: `tickers.yml` 編集はテスト可能な決定論コードに限定。エージェントはJSON/Markdownを書くだけ。無人自動pushの安全性を構造で担保。
- **基本維持＋少数入替（warmup付き）**: 既存はKPIゲート(walk-forward)に履歴が必要。新規候補は `data/watchlist/` で先にwarmupし、十分な履歴かつ上位スコアの時だけ昇格。
- **レポートは別エージェント**: 構造化分析と“読み物”は性質が違うため、レポート生成を専用エージェント(ペルソナ)に分離。週1なのでクォータ影響も小さい。
- **既存資産の再利用**: `selection-framework` / `add_features` / `notifier`(LINE) / `jpx_calendar` / `run_guard` / 日次commit作法をそのまま活用。

## 6. 文書一覧

| ファイル | 内容 |
|---|---|
| `00_overview.md` | 本書。目的・確定方針・全体像・設計判断。 |
| `01_agent_design.md` | 3エージェント（テクニカル/ファンダ/レポートライター）の役割・モデル・ツール・入出力・頻度。 |
| `02_merge_guardrails.md` | 決定論マージの合成（テク日次＋ファンダ週次キャッシュ）・ガード・churn・warmup・昇降格。 |
| `03_workflows_cicd.md` | 2ワークフロー（日次キュレーション / 週次ファンダ＋レポート）・OAuth・スケジュール・rebase・LINE。 |
| `04_data_contracts.md` | 全ファイルスキーマ（候補JSON・監査・週次レポートmd・`tickers.yml`拡張）と統合制約。 |
| `05_weekly_report.md` | 週次レポートの構成・**女の子ナビのペルソナ/文体規約**・GitHub URL・LINE通知文面。 |
| `06_rollout_risks.md` | 段階導入・クォータ予算・障害/ロールバック・観測性・実装チェックリスト・残課題。 |

## 7. 検証済みの技術前提（2026-06）

- アクション: `anthropics/claude-code-action@v1`（GA）。
- サブスク認証: `with: claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}`（`claude setup-token`で発行）。
- `prompt:` は平文または `/skill-name`（skillは `.claude/skills/` 配下、checkout済み前提）。
- `claude_args:` は **CLIフラグ文字列**。例 `--model claude-opus-4-8 --max-turns 30 --allowedTools "..."`。
- モデルID: Opus = `claude-opus-4-8` / Sonnet = `claude-sonnet-4-6`。
- 既存コードとの統合制約（本リポジトリで確認）:
  - `sync_data_files()` は**トップレベル `data/*.parquet` のみ**走査しenabled外を `data/archive/` へ退避。→ warmupは `data/watchlist/` に置く。
  - `load_tickers()` は `tickers`/`settings.max_tickers` のみ参照。→ `watchlist:`/`settings.curation:` 追加は後方互換。
  - 既存 `daily-publish-dashboard.yml` は `rsync --delete web/out/ docs/` を行う。→ **週次レポートは `docs/` 外の `reports/` に出力**して衝突回避。`docs/curation/` を使う場合は rsync 除外に `curation` を追加。
