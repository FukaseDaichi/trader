# 段階導入・リスク・残課題

更新日: 2026-06-06 JST

AI 銘柄キュレーションの主要部品は実装済みです。この文書は、現行運用のリスク、ロールバック、残課題を整理します。

## 1. 実装状況

| 項目 | 状態 |
|---|---|
| `.claude/skills/jp-stock-technical-screen/` | 実装済み |
| `.claude/skills/jp-stock-fundamental-screen/` | 実装済み |
| `.claude/skills/weekly-stock-report/` | 実装済み |
| `scripts/technical_screen.py` | 実装済み |
| `scripts/curation_warmup.py` | 実装済み |
| `scripts/curation_merge.py` | 実装済み |
| `tests/test_curation_merge.py` | 実装済み |
| `scripts/curation_guard.py` | 実装済み |
| `scripts/curation_notify.py` | 実装済み |
| `curation_pool.yml` | 実装済み |
| `daily-ticker-curation.yml` | 実装済み |
| `weekly-fundamental-report.yml` | 実装済み |
| `tickers.yml settings.curation` | 実装済み |
| `daily-publish-dashboard.yml` の `curation` exclude | 実装済み |
| `.gitignore` の `data/watchlist/` | 実装済み |

## 2. 段階的運用

現行 workflow は `workflow_dispatch.inputs.apply` で dry-run 運用できます。

| フェーズ | 推奨設定 | 目的 |
|---|---|---|
| 観測 | `daily-ticker-curation` を `apply=false` | `decision_*.json` の妥当性確認 |
| レポート先行 | `weekly-fundamental-report` を運用 | ファンダ score、文体、LINE URL 通知を確認 |
| add のみ | `max_daily_swaps: 0`, `max_daily_adds: 1` | 降格なしで新規昇格を観測 |
| 少数入替 | `max_daily_swaps: 2` | 本運用 |

## 3. リスクと緩和

| リスク | 影響 | 緩和策 |
|---|---|---|
| LLM の誤採点 | 不適切な候補が上位化 | 最終反映は `curation_merge.py` の guardrail、監査ログ、churn 上限 |
| ファンダキャッシュ陳腐化 | 古い業績根拠で昇格 | `max_fundamental_age_days` 超過で conservative mode |
| コールドスタート | 履歴不足で KPI gate 不通過 | `data/watchlist/` warmup と `min_warmup_rows` |
| whipsaw | 日次往復入替 | `cooldown_days`, `min_gap`, `max_daily_swaps` |
| レポートの事実誤り | 誤情報通知 | skill で入力 JSON 由来に限定。validator 追加が残課題 |
| push 競合 | workflow 失敗 | commit helper の rebase + retry |
| publish の削除 | `docs/curation/` 消失 | `rsync --exclude 'curation'` 済み |
| LINE 通知失敗 | レポート未通知 | 失敗は非致命。リトライ追加が残課題 |

## 4. 観測性

- 日次判断: `docs/curation/decision_YYYY-MM-DD.json`
- 最新判断: `docs/curation/decision_latest.json`
- warmup 結果: `docs/curation/warmup_report.json`
- 週次レポート: `reports/weekly_YYYY-MM-DD.md`
- 既存日次成果物: `docs/state.json`, `docs/backtest_report.json`, `docs/dashboard_index.json`
- 健全性: `daily-watchdog.yml`, `monthly_audit.py`

## 5. ロールバック手順

1. 問題コミットを特定する。代表的な commit message は `AI ticker curation (YYYY-MM-DD)` または `Weekly fundamental & report (YYYY-MM-DD)`
2. `git revert <sha>` で戻す
3. 即時停止する場合は `settings.curation.enabled: false` にする
4. 原因は `decision_*.json`、`technical_latest.json`、`fundamental_latest.json`、週次レポートから追跡する

## 6. 残課題

| 課題 | 現状 | 推奨 |
|---|---|---|
| レポート validator | 未実装 | front matter、免責、銘柄コード存在、空ファイルを通知前に検証 |
| LINE retry | 未実装 | 429/5xx/timeout に限定して backoff retry |
| archive からの自動復元 | 未実装 | 再昇格時に `data/archive/{code}.parquet` を検出して復元 |
| TOPIX 相対力 | 未実装 | 指数データ取得を入れるか、現行の個別モメンタムで継続 |
| フロント表示 | 未実装 | `decision_latest.json` と `reports/weekly_latest.md` へのリンクを dashboard に追加 |
| `.claude/settings.local.json` | ローカル設定として存在 | CI 側の tool 制限は workflow の `claude_args` が主。必要なら repo-wide settings を整理 |

## 7. 要点

- LLM は分析・採点・文章生成だけを担う
- `tickers.yml` の不可逆変更は決定論 merge が担う
- 日次はテクニカル、週次はファンダとレポート
- warmup、churn、cooldown、セクター、ファンダ鮮度で安全側に倒す
- 監査ログと `git revert` で可観測・可逆にする
