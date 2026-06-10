# 現行仕様概要

更新日: 2026-06-11 JST

このディレクトリの仕様は、ソースコードを正として整理した現行仕様です。2026-06-11 時点でリポジトリに存在する `main.py`、`src/`、`scripts/`、`web/`、`.github/workflows/`、`.claude/skills/`、`migrations/`、設定ファイルの実装に合わせています（Phase 0〜3 実装済み）。

## 対象

| 領域 | 対象 | 詳細 |
|---|---|---|
| バックエンド | `main.py`, `src/*.py` | `01_backend_python.md` |
| フロントエンド | `web/` | `02_frontend_web.md` |
| GitHub Actions | `.github/workflows/*.yml`, `.github/scripts/*.sh` | `03_cicd_workflows.md` |
| 補助スクリプト | `scripts/*.py`, `.claude/skills/` | `04_scripts.md` |
| データ契約・横断仕様 | `tickers.yml`, `curation_pool.yml`, `data/`, `docs/`, `migrations/`, env | `05_cross_cutting.md` |
| 課題・バックログ | 実装レビュー結果と運用チェック | `06_issues_and_backlog.md` |
| AI銘柄キュレーション | 日次/週次キュレーション実装 | `ai_ticker_curation/` |

## レイヤ構成

日本株の監視ユニバース（約50銘柄）に対する自動予測・売買シグナルシステムで、4 つのレイヤが重なっています。

| レイヤ | 役割 | 主な実装 |
|---|---|---|
| 日次シグナル | OHLCV取得 → 特徴量 → KPIゲート → 予測 → 5段階シグナル → LINE通知 | `main.py`, `src/data_loader.py`, `src/model.py`, `src/backtest.py`, `src/predictor.py`, `src/notifier.py` |
| Phase 0 計測 | 予測・シグナルを Neon Postgres へ write-through し、1/5/10営業日後の実現結果を決済 | `src/db.py`, `src/db_records.py`, `scripts/settle_outcomes.py`, `migrations/` |
| Phase 1 品質 | 5日トリプルバリアラベル・isotonic較正・マクロ特徴量・週次学習の永続モデル・ドリフト監視 | `src/labels.py`, `src/calibration.py`, `src/macro.py`, `src/model_store.py`, `src/phase1.py`, `scripts/weekly_model_retrain.py`, `scripts/drift_check.py` |
| Phase 2 ポートフォリオ（shadow） | 全銘柄横断のクロスセクション・ランカと、リスク制約付きロングオンリー目標建玉の毎朝提案 | `src/universe.py`, `src/cross_section.py`, `src/cs_model.py`, `src/portfolio.py`, `src/portfolio_backtest.py`, `scripts/weekly_cross_section_retrain.py` |
| Phase 3 UX・堅牢化 | TOPIXベンチマーク決済、実績ダッシュボード（`/performance`）、日次ダイジェスト・週次サマリ通知（リトライ付き）、active配線 | `src/digest.py`, `src/performance.py`, `src/notifier.py`, `scripts/weekly_performance_notify.py`, `web/src/app/performance/` |

Phase 2 は **shadow モード**で本番稼働中です。shadow では Phase 1 のシグナル・通知を一切変更しません。`TRADER_PORTFOLIO_MODE=active` への切替は環境変数 1 行で完結する配線（`portfolio.merge_target_weights`）が実装済みですが、切替自体は `docs/portfolio_shadow_report.json` の `active_readiness` を確認したうえでの**人間の判断**です（`06_issues_and_backlog.md` の運用チェックリスト参照）。

## 日次処理の現在地

平日の JPX 営業日に GitHub Actions が以下を自動実行します（時刻は JST）。

1. 04:30 — AI 銘柄キュレーション: 候補 warmup → テクニカル screen → Claude 精査 → 決定論 merge で `tickers.yml` を少数入替
2. 06:00 — preopen core:
   1. マクロスナップショット更新（`scripts/update_macro_snapshots.py`）
   2. `main.py`: 銘柄ごとにデータ更新 → 特徴量（34テクニカル+11マクロ）→ KPI ゲート → 保存済みモデル推論（無ければ legacy 学習）→ 5段階シグナル
   3. ループ後: Phase 2 クロスセクション推論 + ポートフォリオ snapshot → active 時のみ `target_weight` をシグナルへマージ → LINE 通知（日次ダイジェスト 1 通に買い/売り銘柄名を集約。個別通知は既定無効）→ DB write-through → `docs/` エクスポート
   4. 実現結果の決済（`scripts/settle_outcomes.py`、TOPIX ベンチマーク付き）→ settle 当日分の実績 JSON 再エクスポート
   5. ドリフトチェック（`scripts/drift_check.py`）
3. 06:20 / 06:40 — 当日未更新時のみのリトライ
4. core/retry 成功後 — Next.js 静的ビルドを `docs/` へ publish
5. 12:30 — watchdog: 成果物の鮮度・完全性とドリフトを検証し、異常時は GitHub Issue を起票

週次は土曜にモデル再学習（Phase 1 銘柄別 + Phase 2 クロスセクション + shadow 検証レポート）とファンダメンタル評価・週次レポート・週間実績サマリ通知、日曜にユニバース refresh が走ります。

## 正とするデータ契約

フロントエンドの必須契約は `docs/dashboard_index.json` と `docs/tickers/*.json` です。`performance_summary.json` / `performance_detail.json` / `signal_outcomes_recent.json` / `model_quality.json` / `portfolio_latest.json` / `curation/macro_latest.json` は任意契約で、欠損または `available: false` のときフロントは該当カード/セクションを非表示にします。旧 `docs/history_data.json` は廃止済みの契約で、存在すれば削除されます。

計測の SoR（System of Record）は Neon Postgres（`DATABASE_URL`、スキーマは `migrations/`）です。DB 不通時は `data/outbox/` の JSONL へ退避し、次回実行時にリプレイします。**DB・マクロ・保存モデル・Phase 2 のどれが落ちても日次シグナル生成は止まらない**（フォールバックまたはスキップ + ログ）ことがシステム全体の不変条件です。

AI 銘柄キュレーションの作業物は `docs/curation/*.json`、週次レポートは `reports/weekly_*.md`（GitHub Pages 同期対象外、LINE には GitHub blob URL を通知）です。
