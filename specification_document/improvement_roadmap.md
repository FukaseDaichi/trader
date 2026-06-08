# 改修ロードマップ — 「計測駆動のクロスセクション・シグナル＋ポートフォリオ提案」への進化

作成日: 2026-06-08 JST
ステータス: 改修提案（将来案）。本書は as-built 仕様（`00_overview.md`〜`06_priority_matrix.md`、`ai_ticker_curation/`）ではなく、**これからどう変えるか**を定義します。

## 0. この文書の前提と意思決定サマリ

ブレインストーミングで以下を確定しました。

| 論点 | 決定 | 含意 |
|---|---|---|
| シグナルの使い方 | **手動トレードの参考** | 実績トラッキングと「今日の建玉」提案が直接効く。自動執行は当面スコープ外 |
| 改修の最優先 | **稼げる（シグナル品質・収益性）** | ターゲット設計・モデル・ポートフォリオが主役。可視化は従だが含める |
| インフラ許容 | **Neon Free の Postgres を第一候補** | GitHub Actions からの断続的な書き込み、SQL 集計、将来の移行容易性を重視。Supabase は Auth/Storage/API 直読みが必要になった場合の代替 |
| ドキュメント粒度 | **包括ロードマップ** | フェーズ分け・優先度・中長期ビジョンまで |
| 全体戦略 | **C案：計測ファースト段階移行** | Phase0 計測 → Phase1 品質 → Phase2 本丸 → Phase3 UX。各フェーズ出荷可能 |
| ポートフォリオ方向 | **ロングオンリー** | 信用取引不要。SELL は「持たない/手仕舞い」の意味で運用 |
| ユニバース | **30〜50 銘柄へ拡大** | 既存 `curation_pool.yml` から流動性上位を自動選定 |

### 設計原則（全フェーズ共通）

1. **計測なき改善はしない。** 出したシグナルの実現結果を貯め、すべての変更を A/B 検証する。
2. **日次シグナルを止めない。** DB など新規依存が落ちても、既存 parquet/JSON へフォールバックして縮退運転する。
3. **不可逆処理は決定論コードに限定。** LLM/モデルは提案のみ。発注・ユニバース変更・DB 確定書き込みはスクリプトが担う（現行の `curation_merge.py` 思想を踏襲）。
4. **後方互換を保ちながら移行。** 各フェーズで現行ダッシュボード契約（`docs/dashboard_index.json` 等）を壊さない。

---

## 1. エグゼクティブサマリ

現行システムは「データ取得 → 特徴量 → KPI ゲート付き予測 → シグナル → LINE/ダッシュボード」を GitHub Actions 上で無人運転する、完成度の高い基盤です。とくに **walk-forward OOS ＋コスト込み KPI ゲート ＋銘柄別閾値自動最適化**（`src/backtest.py`）は、過学習に対する規律として優れています。

一方で、**「稼ぐ」観点では構造的な天井**があります。要点は次の 5 つ。

1. **出したシグナルの実現結果を一切記録していない。** バックテストはあるが、ライブで出した予測がその後どうなったかの台帳がない。改善の良し悪しを検証できない。
2. **予測ターゲットが「翌日終値が上がるか」の二値**（`src/model.py`）。日次方向はほぼノイズで、信号対雑音比が低い。
3. **銘柄ごとに独立学習し、毎日ゼロから 4 モデルを再学習**（`train_and_predict`）。モデル永続化も版管理もなく、6,500 行/銘柄では過学習しやすく、再現性も乏しい。
4. **マクロを週次で調べているのにモデルへ入れていない**（`docs/curation/macro_latest.json` はファンダ/レポートのみ消費）。レジーム情報が予測に未活用。
5. **ポートフォリオ／リスク層がない。** 銘柄独立・固定サイズ（`BUY=1.0, MILD_BUY=0.5`）で、確信度や相関・セクター集中を加味した資金配分がない。「実際に稼ぐ」中核が欠けている。

本ロードマップは、これらを **計測（Phase0）→ 品質の足回り（Phase1）→ クロスセクション＋ポートフォリオ（Phase2）→ UX（Phase3）** の順で解消し、最終的に **拡張ユニバースを 1 つのモデルで相対評価し、リスク管理されたロングオンリー・ポートフォリオを毎朝提案する**システムへ進化させます。

---

## 2. 現状アーキテクチャの評価

### 2.1 強み（活かす資産）

- **KPI ゲートの規律**（`src/backtest.py`）: walk-forward、tuning/holdout 分割、コスト/スリッページ込み、閾値グリッド探索。ポートフォリオ単位へ自然に拡張できる土台。
- **データ取得の堅牢性**（`src/data_loader.py`）: Stooq ＋ yfinance フォールバック、鮮度判定、OHLCV 検証。
- **銘柄単位の障害分離**（`main.py`）: 1 銘柄の失敗が全体を止めない。
- **キュレーション基盤**（`scripts/curation_*`, `curation_pool.yml`）: ユニバース拡張に再利用可能な候補プールとガードレール。
- **無人運転 CI/CD**（`.github/workflows/*`）: JPX 営業日ガード、冪等ガード、watchdog。

### 2.2 構造的弱点（本ロードマップで解消）

| # | 弱点 | 該当箇所 | 影響 | 解消フェーズ |
|---|---|---|---|---|
| W1 | ライブ実績の台帳が無い（30日で履歴消失） | `src/dashboard.py`（`MAX_HISTORY_DAYS=30`） | 改善の検証不能・トラックレコード喪失 | Phase0 |
| W2 | ターゲットが翌日二値 | `src/model.py`（`target = close.shift(-1) > close`） | 低 S/N・上限が低い | Phase1 |
| W3 | 毎日ゼロから再学習・モデル非永続・版管理なし | `train_and_predict` | 過学習・再現性欠如・計算浪費 | Phase1 |
| W4 | マクロ/レジームがモデル未投入 | `macro_latest.json` 消費経路 | レジーム転換に弱い | Phase1 |
| W5 | 銘柄独立・少データ | 銘柄別 parquet/モデル | クロスセクションαを取れない | Phase2 |
| W6 | ポートフォリオ/リスク層なし・固定サイズ | `src/predictor.py`, `src/backtest.py` のポジション写像 | 資金効率・集中リスク制御不能 | Phase2 |
| W7 | 確率が未較正 | `prob_up` をそのまま閾値判定 | 「上昇確率72%」が額面通りでない | Phase1 |
| W8 | git をDB代わりにし書き込み競合 | `.github/scripts/commit-and-push.sh`（rebase＋retry） | 並行 workflow の競合・リポジトリ肥大 | Phase0 |
| W9 | LINE 通知リトライ無し・個別のみ | `src/notifier.py` | 通知欠落・全体像が掴めない | Phase3 |

---

## 3. 目標アーキテクチャ（最高形）

```text
                     ┌──────────────────────────────────────────────┐
                     │   Neon Postgres (decision log SoR / Free)     │
                     │  prices? predictions signals signal_outcomes  │
                     │  portfolio_snapshots model_registry           │
                     │  backtest_runs/equity macro_snapshots         │
                     └───────▲───────────────────────────▲──────────┘
                             │ write-through / fallback    │ read
   毎営業日 04:30  ┌─────────┴──────────┐      毎営業日 06:00  ┌────┴───────────────┐
   ユニバース管理 │ curation (既存+拡張) │      日次パイプライン│ ingest → features  │
   （30〜50銘柄） └────────────────────┘                      │ → pooled CS model  │
                                                              │ → calibrated score │
   毎週末          ┌────────────────────┐                     │ → portfolio build  │
   学習/マクロ     │ weekly retrain      │                     │ → settle outcomes  │
   （永続モデル）  │ macro snapshot      │                     │ → notify (digest)  │
                  └────────────────────┘                     └────────┬───────────┘
                                                                       │
                  ┌────────────────────────────────────────────────────┴──┐
                  │  Dashboard (GitHub Pages, DB由来エクスポート)            │
                  │  資産曲線 vs TOPIX / 較正 / 個別結果 / 今日の建玉 / レジーム │
                  └────────────────────────────────────────────────────────┘
```

### 中核となる出力（手動トレーダー向け）

毎朝、次の 3 点が LINE とダッシュボードに届く状態を目標にします。

1. **今日の建玉（target book）**: 保有すべき銘柄・目標ウェイト・指値/損切り目安。
2. **昨日比の差分**: 新規・買い増し・減らし・手仕舞い。
3. **根拠と健全性**: レジーム、ポートフォリオ期待ボラ、直近の実現的中率。

---

## 4. データ基盤 / DB 設計

### 4.1 役割分担（推奨構成）

| データ種別 | 置き場所 | 理由 |
|---|---|---|
| 価格 OHLCV（時系列・追記主体） | **当面 parquet 継続**（`data/*.parquet`）＋ DuckDB で分析クエリ | 既存資産・git 版管理・無料・読み出し高速。Phase2 以降で必要なら `prices` テーブルへ移行 |
| 予測・シグナル・**実現結果**・ポートフォリオ・モデル版・マクロ・バックテスト | **Neon Postgres Free** | 関係クエリ・長期履歴・集計・版管理。git-as-DB の競合（W8）を解消。DB容量監視とアーカイブ方針を必須化 |

> **なぜ全部 Postgres にしないか**: 価格は単純追記の時系列でリレーションが薄く、parquet＋DuckDB が最も安く速い。無料 Postgres のストレージ枠（Neon/Supabase ともに Free は実質 0.5GB 級）も節約できる。逆に「予測と実現結果の突合・長期集計」は RDB が圧倒的に有利。**価格は parquet、意思決定ログは DB** が費用対効果の最適点。

### 4.2 DB サービス比較と採用判断（2026-06-08 JST 調査）

このシステムの DB 要件は、一般的な Web アプリよりかなり絞られています。

- 書き込み主体は GitHub Actions からの **日次バッチ**。常時接続・低レイテンシ API は不要。
- ダッシュボードは GitHub Pages の静的 JSON を読むため、DB を直接公開しない。
- 必要なのは、`signals` と `signal_outcomes` の突合、日付/銘柄/モデル版での集計、migration、トランザクション。
- 価格 OHLCV は DB に入れず、容量を意思決定ログに限定する。

| 候補 | Free 枠の要点（確認時点） | 適合度 | 判断 |
|---|---|---|---|
| **Neon Postgres** | 0.5GB storage/project、100 CU-hours/month/project、idle 時 scale-to-zero、5GB egress/month。Free 超過時は compute 停止 | **高**。Postgres そのもの、CI バッチと相性が良い。Auth/Storage など不要な機能が少ない | **採用第一候補** |
| **Supabase Postgres** | 500MB database size、5GB egress、1GB file storage、Free は 2 project、1週間 inactive で pause。500MB 超過で read-only | 中〜高。Postgres + 管理画面は便利。ただし本件では Auth/Realtime/Storage を使わず、Free pause/read-only が余計な運用要素 | Neon 不調時、または将来 DB 直読み API/RLS/Auth が必要になった場合の代替 |
| **Firebase / Firestore** | Spark は支払い方法不要。Firestore は 1GiB stored、50k reads/day、20k writes/day、10GiB egress/month | 低〜中。無料枠は十分だが NoSQL/document 課金。`signal_outcomes` の join/集計/再現性管理が Postgres より遠い | 不採用。モバイル/リアルタイム/ユーザー単位同期が主役なら再検討 |
| **Cloudflare D1** | SQLite ベース。5GB storage、5M rows read/day、100k rows written/day | 中。無料枠は大きいが Postgres ではなく、row-scan 課金と Workers 寄りの運用になる | 不採用。Cloudflare Workers 上で直接配信する構成へ変えるなら候補 |
| **Turso/libSQL** | SQLite/libSQL。5GB storage、500M rows read/month、10M rows written/month | 中。無料枠は強いが Postgres 互換ではない。将来の分析 SQL/拡張/移行で制約 | 不採用。Postgres 無料枠が不足した場合の低コスト SQLite 系代替 |

**結論**: Phase0 は **Neon Free の Postgres** で開始する。理由は、現在の構成が「GitHub Actions から短時間だけ接続して書く」形であり、Neon の serverless/scale-to-zero と自然に合うため。Supabase は良いサービスだが、このプロジェクトでは Auth、Storage、Realtime、Edge Functions の価値をまだ使わない。Firebase は無料枠こそ広いが、今回の本質である「予測、シグナル、実現結果、モデル版の関係クエリ」には RDB のほうが素直。

**容量見積もり**: 50銘柄、年間252営業日、`predictions`/`signals`/`signal_outcomes(1d,5d,10d)` を全保存しても、年間の主テーブル行数は概算 63,000 行。価格 OHLCV と大型モデル artifact を DB 外に置く限り、数年は 0.5GB 内に収まる見込み。ただし JSONB の肥大と index を考慮し、**400MB 到達で警告、450MB 到達で古い backtest equity/detail の parquet アーカイブまたは有料化判断**を入れる。

**調査ソース**:
- Neon Pricing / Plans: https://neon.com/pricing, https://neon.com/docs/introduction/plans
- Supabase Billing / Database Size: https://supabase.com/docs/guides/platform/billing-on-supabase, https://supabase.com/docs/guides/platform/database-size
- Firebase Firestore Pricing: https://firebase.google.com/docs/firestore/pricing
- Cloudflare D1 Pricing: https://developers.cloudflare.com/d1/platform/pricing/
- Turso Pricing: https://turso.tech/pricing

### 4.3 スキーマ（DDL ドラフト）

```sql
-- 銘柄マスタ（tickers.yml / curation を反映）
CREATE TABLE tickers (
  code        TEXT PRIMARY KEY,         -- "7011.JP"
  name        TEXT NOT NULL,
  sector      TEXT,
  enabled     BOOLEAN NOT NULL DEFAULT TRUE,
  source      TEXT,                     -- manual | curation
  added_on    DATE,
  disabled_on DATE
);

-- モデル版管理（W3 解消）
CREATE TABLE model_registry (
  version      TEXT PRIMARY KEY,        -- "cs-2026-06-08" 等
  trained_at   TIMESTAMPTZ NOT NULL,
  kind         TEXT NOT NULL,           -- per_ticker | cross_sectional
  universe     JSONB NOT NULL,          -- 学習時の銘柄集合
  feature_set  JSONB NOT NULL,          -- 使用特徴量
  params       JSONB NOT NULL,
  cv_metrics   JSONB NOT NULL,          -- IC, AUC, Brier 等
  calibration  JSONB,                   -- 較正マップ（Phase1）
  artifact_uri TEXT,                    -- モデル本体の保管先（後述）
  active       BOOLEAN NOT NULL DEFAULT FALSE
);

-- Phase0 migration 時に "legacy-daily-v0" を seed する。
-- 現行の日次再学習モデルは artifact が無いため、model_registry には
-- kind='per_ticker_legacy_daily' / active=true / artifact_uri=NULL として記録する。

-- 予測（モデル生出力）
CREATE TABLE predictions (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,          -- 予測実行日(JST)
  as_of_date    DATE NOT NULL,          -- 予測に使った最新価格日
  ticker        TEXT NOT NULL REFERENCES tickers(code),
  model_version TEXT NOT NULL REFERENCES model_registry(version),
  horizon_days  INT  NOT NULL,          -- 主軸 5
  raw_score     DOUBLE PRECISION,       -- 回帰/ランカ生スコア
  prob_up       DOUBLE PRECISION,       -- 較正後の上昇確率
  expected_ret  DOUBLE PRECISION,       -- 期待先行リターン
  cs_rank       INT,                    -- ユニバース内ランク
  features_hash TEXT,                   -- 再現性
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_date, ticker, model_version, horizon_days)
);

-- シグナル（人間/ポートフォリオ向け判断）
CREATE TABLE signals (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,
  as_of_date    DATE NOT NULL,          -- 予測に使った最新価格日
  ticker        TEXT NOT NULL REFERENCES tickers(code),
  prediction_id BIGINT REFERENCES predictions(id),
  action        TEXT NOT NULL,          -- BUY/MILD_BUY/HOLD/MILD_SELL/SELL
  raw_action    TEXT,                   -- ゲート未達でも残す元判断
  conviction    DOUBLE PRECISION,       -- 0..1
  target_weight DOUBLE PRECISION,       -- ポートフォリオ目標比率(Phase2)
  thresholds    JSONB,
  gate_passed   BOOLEAN NOT NULL,
  limit_price   DOUBLE PRECISION,
  stop_loss     DOUBLE PRECISION,
  reason        TEXT,
  status        TEXT NOT NULL,          -- ok | failed
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_date, ticker)
);

-- 実現結果台帳（★最重要・W1 解消）
CREATE TABLE signal_outcomes (
  signal_id      BIGINT NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  horizon_days   INT NOT NULL,          -- 1 / 5 / 10 を別行で
  entry_date     DATE NOT NULL,         -- 通常は signals.as_of_date
  eval_date      DATE NOT NULL,         -- 評価確定日
  entry_close    DOUBLE PRECISION,
  exit_close     DOUBLE PRECISION,
  realized_ret   DOUBLE PRECISION,      -- ネット（コスト控除可）
  benchmark_ret  DOUBLE PRECISION,      -- 同期間 TOPIX
  excess_ret     DOUBLE PRECISION,
  hit            BOOLEAN,               -- 方向一致
  mae            DOUBLE PRECISION,      -- 最大逆行
  mfe            DOUBLE PRECISION,      -- 最大順行
  exit_reason    TEXT,                  -- tp | sl | time
  PRIMARY KEY (signal_id, horizon_days)
);

-- ポートフォリオ日次スナップショット(Phase2)
CREATE TABLE portfolio_snapshots (
  run_date        DATE PRIMARY KEY,
  positions       JSONB NOT NULL,       -- [{ticker, weight, limit, stop}]
  diff_from_prev  JSONB,                -- add/trim/exit
  gross_exposure  DOUBLE PRECISION,
  sector_exposure JSONB,
  expected_vol    DOUBLE PRECISION,
  regime          TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- マクロ/レジーム(Phase1 で特徴量化)
CREATE TABLE macro_snapshots (
  date     DATE PRIMARY KEY,
  usdjpy   DOUBLE PRECISION,
  topix    DOUBLE PRECISION,
  nikkei   DOUBLE PRECISION,
  nikkei_vi DOUBLE PRECISION,
  jgb10y   DOUBLE PRECISION,
  regime   TEXT,                        -- risk_on | risk_off | neutral
  raw      JSONB
);

-- バックテスト（履歴シードと継続評価）
CREATE TABLE backtest_runs (
  id         BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  scope      TEXT NOT NULL,             -- per_ticker | portfolio
  config     JSONB NOT NULL,
  period     JSONB NOT NULL,
  metrics    JSONB NOT NULL
);
CREATE TABLE backtest_equity (
  run_id BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
  date   DATE NOT NULL,
  equity DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (run_id, date)
);

-- migration 適用履歴（Alembic を使わない場合の最小管理）
CREATE TABLE schema_migrations (
  version    TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.4 可用性・フォールバック（原則2の具体化）

- 新規 `src/db.py`（薄いデータアクセス層）を導入。書き込みは **write-through**。初期は ORM を使わず `psycopg` + SQL で十分。
- DB 接続不可時は **ローカル JSONL（`data/outbox/*.jsonl`）へキュー**し、次回実行でリプレイ。日次シグナル生成は DB 状態に依存させない。
- 接続情報は GitHub Actions Secret（`DATABASE_URL`）。ローカルは `.env`。`.env.example` に追記。
- `DATABASE_URL` が未設定、`TRADER_DB_ENABLED=false`、または migration 未適用の場合は、DB 書き込みだけを skip して従来処理を継続する。
- outbox は `event_id`（`run_date:ticker:event_type`）で冪等化し、DB 復旧後も重複 upsert にならないようにする。

### 4.5 移行・バックフィル戦略

1. **価格履歴を保有しているため、過去の実現リターンは正確に再計算可能**。Phase0 で `state.json`（直近30日）の既存シグナルを `signals` に取り込み、parquet から `signal_outcomes` を埋める。
2. さらに **現行モデルを過去に遡ってバックテストし `backtest_runs` にシード**することで、ライブ台帳が貯まる前から長期の擬似トラックレコードを提示できる。
3. ダッシュボードは段階的に DB 由来データへ切替。移行中は現行 JSON 契約を維持。

---

## 5. フェーズ別ロードマップ

各フェーズは独立して出荷可能（原則）。受け入れ基準（Acceptance）を満たしたら次へ。

### Phase 0 — 計測基盤（DB ＋ 実現結果台帳）

**目的**: モデルを変えずに「出したシグナルが実際どうだったか」を貯め始める。以降の全改善の検証土台。

**施策**
- **Neon Free** で Postgres project を作成し、`DATABASE_URL` を GitHub Actions Secret / local `.env` に設定する。
- `src/db.py`（接続・upsert・フォールバックキュー）と `4.3` のスキーマ migration（`scripts/db_migrate.py`）を追加する。初期は `psycopg` のみでよく、Alembic は Phase1 以降で migration が増えたら導入する。
- `main.py` の日次出力を `predictions`/`signals` に write-through する。既存 `docs/state.json` / `docs/dashboard_index.json` は維持する。
- **日付契約を明確化**: `run_date` は workflow 実行日、`as_of_date` は予測に使った最新価格日（現行 signal の `date`）とする。outcome は `as_of_date` 起点で 1d/5d/10d を評価する。
- **`scripts/settle_outcomes.py`**: 価格更新後、未確定シグナルの 1d/5d/10d 実現リターン・TOPIX 超過・hit・MAE/MFE を計算し `signal_outcomes` を埋める。TOPIX データが未取得の場合は `benchmark_ret`/`excess_ret` を NULL にして処理を継続する。
- バックフィル（`4.5`）。最初は `docs/state.json` の直近30日だけを seed し、擬似長期 backtest の DB seed は Phase0 後半に分ける。
- ダッシュボードに最小タイル: 直近の実現的中率、平均実現リターン、BUY/MILD_BUY を額面通り従った場合の簡易資産曲線。
- DB 容量監視: migration または dashboard export 時に `pg_database_size` を取得し、400MB 以上で警告 JSON を出す。

**Phase 0 実装計画（推奨順）**

1. **0A: provider/bootstrap**
   - Neon project 作成、接続先 region は GitHub Actions から近い US または Asia を選択（レイテンシは重要でない）。
   - GitHub Secrets: `DATABASE_URL`。`.env.example`: `TRADER_DB_ENABLED`, `TRADER_DB_FALLBACK_DIR`, `TRADER_DB_WRITE_TIMEOUT_SEC`, `TRADER_DB_STORAGE_WARN_MB` を追加。
   - `pyproject.toml` に `psycopg[binary]` を追加。
2. **0B: migration**
   - `scripts/db_migrate.py` を idempotent に実装。`schema_migrations` を使い、`tickers.yml` の enabled 銘柄と `legacy-daily-v0` model_registry を seed。
   - Phase0 で日次運用に使う最小テーブルは `tickers`, `model_registry`, `predictions`, `signals`, `signal_outcomes`, `schema_migrations`。他テーブルは作成だけして空でよい。
3. **0C: write-through + outbox**
   - `src/db.py` に `upsert_prediction_signal_batch(signals, run_date)` と `flush_outbox()` を実装。
   - 失敗時は `data/outbox/YYYY-MM-DD.jsonl` に `event_id` 付きで保存。DB 復旧時は upsert で重複吸収。
   - `main.py` は DB 例外を握りつぶしてログだけ出し、通知と dashboard export を止めない。
4. **0D: outcome settlement**
   - `scripts/settle_outcomes.py --as-of YYYY-MM-DD` を追加。
   - 未確定 horizon の signal を DB から取得し、各 ticker parquet から `entry_date` と `eval_date` の close/high/low を読む。
   - `BUY/MILD_BUY` は long、`SELL/MILD_SELL` は「持たない/手仕舞い」評価として方向 hit と avoided-loss 指標を分ける。Phase0 の簡易資産曲線は long 系だけで開始する。
5. **0E: dashboard export**
   - `src/dashboard.py` に DB 由来の `docs/performance_summary.json` を追加出力。DB 不通なら既存 JSON のみ出力。
   - Web はまず小さな実績タイルだけ追加し、詳細画面の大改修は Phase3 へ送る。
6. **0F: workflow**
   - `daily-preopen-core.yml` の `Run prediction script` に DB env を渡す。
   - `main.py` 実行後に `uv run python scripts/settle_outcomes.py --as-of "$TODAY_JST"` を実行。settle 失敗は workflow failure にせず、警告ファイルを `docs/` に出して commit 対象にする。

**主な変更/新規**: `src/db.py`(新), `scripts/db_migrate.py`(新), `scripts/settle_outcomes.py`(新), `main.py`, `.github/workflows/daily-preopen-core.yml`, `.env.example`, ダッシュボード出力。

**受け入れ基準**
- 日次実行後、`signals` と `predictions` に当日分が入り、`run_date` と `as_of_date` が区別されている。
- 翌営業日以降、`signal_outcomes` に 1d 実現結果が確定する。
- DB を停止しても日次シグナル・LINE 通知は従来どおり動く（フォールバック検証）。
- outbox に溜まったイベントを DB 復旧後にリプレイでき、重複行が発生しない。
- ダッシュボードに「実現的中率」と「平均実現リターン」が表示される。
- `docs/state.json` / `docs/dashboard_index.json` の既存契約が壊れない。
- DB サイズが 400MB 以上になった場合に警告が出る。

**リスク/緩和**: Neon Free の compute/storage 枠超過 → フォールバックキュー、容量監視、DB へ保存する対象を意思決定ログに限定。秘密情報 → Secret 管理・接続文字列をログに出さない。日付ズレ → `run_date`/`as_of_date` を明示して settlement の起点を固定。

---

### Phase 1 — シグナル品質の足回り（銘柄別のまま底上げ）

**目的**: ターゲット・特徴量・較正・モデル運用を是正し、銘柄別予測の質と再現性を上げる。Phase0 の台帳で各変更を A/B 検証。

**施策**
1. **ターゲット再設計（W2）**: 翌日二値を廃し、主軸 **5 営業日先のリターン**。ラベルは次のいずれかを選択可能に。
   - **トリプルバリア**: 利確 `+k·ATR`、損切り `−m·ATR`、時間 `H 日`。最初に触れたバリアでラベル化（手動トレードの利確/損切りと整合）。
   - **ボラ正規化先行リターン**: `r_{t→t+H} / σ_t` を回帰。
   - 後方互換のため二値ヘッドも残し、`predictions.expected_ret` と `prob_up` を併存。
2. **マクロ/レジーム特徴量（W4）**: `macro_snapshots` を日次更新し、USD/JPY 水準・20/60日トレンド・ボラ、TOPIX/日経の 200 日線レジーム・20日リターン・騰落幅、日経VI、JGB10y、セクター相対モメンタムをモデル特徴量に追加。`risk_on/off` レジームラベルを付与。
   - 市場/為替の系列取得を `src/data_loader.py` に追加（Stooq の指数/FX シンボル）。
3. **確率較正（W7）**: OOS 予測に isotonic/Platt を適用し、`prob_up` を実測度数に整合。`model_registry.calibration` に保存、ダッシュボードに信頼度（reliability/Brier）を表示。
4. **モデル永続化＋週次学習＋版管理＋ドリフト検知（W3）**:
   - 毎日ゼロ学習をやめ、**週次で学習・シリアライズ**（artifact は `data/models/` parquet/booster ＋ メタを `model_registry`）。日次は保存済みモデルで推論。
   - **ドリフト検知**: 直近 OOS の IC（`corr(score, 先行リターン)`）/AUC の移動平均、特徴量 PSI を監視。閾値割れで `daily-watchdog` から Issue ＋ ダッシュボード警告 ＋ 再学習トリガ。
   - `scripts/weekly_model_retrain.py` を「学習可否レポート」から **実学習・登録** へ格上げ。

**主な変更/新規**: `src/model.py`, `src/backtest.py`, `src/data_loader.py`, `scripts/weekly_model_retrain.py`, `scripts/drift_check.py`(新), `src/config.py`(env 追加), 各 workflow。

**受け入れ基準**
- `model_registry` に週次で版が積まれ、日次は推論のみ（実行時間短縮を計測）。
- 較正後 `prob_up` の Brier スコアが未較正比で改善。
- マクロ特徴量投入で holdout の IC が有意に改善（台帳で確認）。
- ドリフト閾値割れ時に Issue が立つ。

**リスク/緩和**: 特徴量増による過学習 → walk-forward 厳守・特徴量重要度監視・台帳で実地検証。市場データ取得の不安定 → 取得失敗時は当該特徴量を欠損扱いにしモデルを止めない。

---

### Phase 2 — クロスセクション ＋ ポートフォリオ（収益の本丸）

**目的**: 銘柄独立をやめ、拡張ユニバースを 1 つのモデルで相対評価し、リスク管理されたロングオンリー・ポートフォリオを毎朝提案する。

**施策**
1. **ユニバース拡張（30〜50銘柄）**: `curation_pool.yml` から流動性（20日平均売買代金）上位を自動選定。`curation_merge.py` のガードレール（churn/セクター上限/warmup）を流用。
2. **プールしたクロスセクションモデル（W5）**: 全銘柄×全日付のパネルを 1 モデルで学習。
   - **日付内クロスセクション正規化**: 各特徴量を日次で z-score/ランク化。「相対的強さ」を学習。
   - 静的特徴: セクター、流動性。LightGBM **ランカ（lambdarank, 日付=group）** か、先行リターン回帰＋日次正規化。
   - 出力をランク化し `predictions.cs_rank` / `expected_ret` に格納。
3. **ポートフォリオ構築（W6）**: `src/portfolio.py`(新) で日次の目標建玉を生成。
   - **選定**: スコア上位 N（品質バーは Phase1 ゲートの後継）。
   - **サイジング**: `raw_w_i ∝ conviction_i / σ_i`（逆ボラ）。`σ_i` は 20日実現ボラ or ATR%。
   - **制約**: 1 銘柄上限（例 20–25%）、**セクター上限**（`settings.curation.sector_cap_pct=40` を流用）、グロス上限。
   - **ボラターゲット**: ポートフォリオ期待ボラ ≈ 目標（例 年率 10–15%）になるようグロスをスケール。`risk_off` レジームで縮小。
   - **回転率制御（ヒステリシス）**: 目標ウェイト変化が無トレード幅未満なら据え置き。コスト（現行 `cost_bps+slippage_bps=15bps`）を抑制。
4. **ポートフォリオ単位 KPI ゲート/バックテスト**: `src/backtest.py` を銘柄単位からポートフォリオ単位へ拡張。walk-forward でポートフォリオを再構築し、Sharpe/Sortino/MaxDD/Calmar/回転率/容量、**対 TOPIX のα・β・情報比・トラッキングエラー**を評価。`backtest_runs(scope=portfolio)` ＋ `backtest_equity` に保存。
5. **日次「今日の建玉」提案**: `portfolio_snapshots` に保存し、保有・目標ウェイト・指値/損切り・昨日比差分（新規/買い増し/減らし/手仕舞い）を出力。

**主な変更/新規**: `src/portfolio.py`(新), `src/model.py`(パネル学習), `src/backtest.py`(ポートフォリオ化), `scripts/universe_select.py`(新 or curation 拡張), `main.py`(オーケストレーション), `tickers.yml`/`curation_pool.yml`。

**受け入れ基準**
- 30〜50 銘柄で日次ポートフォリオが生成され、セクター/銘柄/グロス制約を満たす。
- ポートフォリオ walk-forward が対 TOPIX で正のα・許容 MaxDD・現実的回転率を示す（`backtest_runs`）。
- 「今日の建玉」＋差分が DB とダッシュボードに出る。
- ライブ `signal_outcomes` 集計が、クロスセクション移行後に銘柄別比で改善（IC/情報比）。

**リスク/緩和**: 小ユニバースでのクロスセクション弱さ → 30銘柄を下限とし、N が小さい日は per-ticker モデルへフォールバック。過度な集中/回転 → 制約とヒステリシスを必須化。レジーム誤判定 → ボラターゲットで段階的に露出調整（オン/オフの二値断にしない）。

---

### Phase 3 — 使いやすさ（手動トレードの意思決定 UX）

**目的**: 稼ぐための情報を、手動トレーダーが朝に 1 分で判断できる形にする。

**施策**
1. **実績ダッシュボード**: DB 由来で、
   - **資産曲線（戦略 vs TOPIX）**、ドローダウン、ローリング Sharpe。
   - **実現的中率・較正図**（信頼性曲線）。
   - **個別シグナルの結果履歴**（hit/MAE/MFE/exit理由）。
   - **今日の建玉＋昨日比差分**、セクター露出、期待ボラ。
   - **レジーム・バナー**、週次レポート/キュレーション決定ログへのリンク（現行未実装の解消）。
2. **通知強化（W9）**:
   - **日次ポートフォリオ・ダイジェスト**（1 通で今日の建玉＋差分＋レジーム＋直近実績）。個別シグナル通知と併用。
   - **LINE リトライ**（429/5xx/timeout に限定した backoff、最終失敗をレポート）— `06_priority_matrix.md` の P2 を回収。
   - **週次パフォーマンス・サマリ**（実現実績の要約）。
3. **チャート堅牢化**: 空データ時の描画ガード（P2）、JSON ランタイムバリデーション。

**主な変更/新規**: `web/src/*`（実績ビュー・建玉ビュー）, `src/notifier.py`, `scripts/curation_notify.py`, `src/dashboard.py`（DB 由来エクスポート）。

**受け入れ基準**
- ダッシュボードで戦略の資産曲線と実現的中率が確認できる。
- 毎朝 1 通のダイジェストが届き、リトライで欠落しない。
- 空データ銘柄でチャートが壊れない。

---

## 6. シグナル品質の技術詳細（Phase1 補足）

### 6.1 ターゲット候補の比較

| 方式 | 長所 | 短所 | 採否 |
|---|---|---|---|
| 翌日二値（現行） | 単純 | S/N 最低・上限低 | 廃止（互換で残置） |
| k 日先二値/回帰 | S/N 改善・horizon 調整可 | k 選定が必要 | **主軸（5日）** |
| トリプルバリア | 利確/損切りと整合・経路依存を捕捉 | パラメータ（k,m,H）増 | **推奨ラベル** |
| ボラ正規化リターン | リスク調整・銘柄横断比較に有利 | 解釈やや難 | クロスセクションで採用 |

### 6.2 特徴量の拡張

- **既存 34 特徴量を維持**（`FEATURE_COLS`）。
- **マクロ/レジーム**: USD/JPY（水準・トレンド・ボラ）、TOPIX/日経（200日線レジーム・20日リターン・breadth）、日経VI、JGB10y、セクター相対モメンタム、`risk_on/off`。
- **クロスセクション（Phase2）**: 日付内 z-score/ランク、ユニバース中央値に対する相対強さ、セクター中立化リターン。
- **静的**: セクター、流動性。

### 6.3 較正とドリフト

- **較正**: OOS で isotonic 回帰、`model_registry.calibration` に保存。指標は Brier/reliability。
- **ドリフト**: 移動 IC・AUC、特徴量 PSI。閾値割れで再学習トリガ＋Issue＋ダッシュボード警告。IC は `corr(score_t, 実現先行リターン_t)` の移動平均で監視。

---

## 7. ポートフォリオ ＆ リスク設計（Phase2 補足）

ロングオンリー前提。資金額は仮定せず、すべて比率で設計（後で円換算）。

```text
1) 選定:    score 上位 N（品質バー通過のみ）
2) 逆ボラ:  raw_w_i = conviction_i / σ_i
3) 正規化:  w_i = raw_w_i / Σ raw_w
4) 制約:    per-name ≤ w_max(例0.25), per-sector ≤ 0.40, gross ≤ 1.0
5) ボラ目標: gross *= clip(target_vol / portfolio_vol, vmin, vmax)
            （portfolio_vol は共分散から推定、risk_off で縮小）
6) ヒステリシス: |w_i,new − w_i,prev| < band → 据え置き（回転率・コスト抑制）
7) 出力:    今日の建玉＋指値/損切り＋昨日比差分
```

**ポートフォリオ KPI（対 TOPIX）**: CAGR / Sharpe / Sortino / MaxDD / Calmar / 回転率 / 的中率 / 平均勝敗 / α・β・情報比・トラッキングエラー / 容量。現行 `src/backtest.py` のコスト/スリッページ・walk-forward 思想をそのまま踏襲。

---

## 8. 横断仕様（設定・運用・CI/CD・セキュリティ）

- **設定**: 現行の env 駆動（`src/config.py`）を踏襲し追加。
  - `DATABASE_URL`(Secret), `TRADER_DB_ENABLED`, `TRADER_DB_FALLBACK_DIR`, `TRADER_DB_WRITE_TIMEOUT_SEC`, `TRADER_DB_STORAGE_WARN_MB`
  - `TRADER_TARGET_HORIZON_DAYS=5`, `TRADER_LABEL_MODE=triple_barrier|vol_norm|binary`
  - `TRADER_TB_TP_ATR`, `TRADER_TB_SL_ATR`, `TRADER_TB_MAX_DAYS`
  - `TRADER_PORTFOLIO_TARGET_VOL`, `TRADER_PORTFOLIO_MAX_NAME_W`, `TRADER_PORTFOLIO_SECTOR_CAP`, `TRADER_PORTFOLIO_NOTRADE_BAND`, `TRADER_PORTFOLIO_TOP_N`
- **CI/CD**:
  - `daily-preopen-core.yml` に Neon DB write-through と settle を追加。DB 不通時は workflow を失敗させず outbox/警告 JSON に退避。
  - `weekly-model-retrain.yml` を実学習・版登録へ格上げ。
  - 新規 `daily-settle`（または core に統合）と `drift_check`。
  - git-as-DB の競合（W8）は意思決定ログを DB へ移すことで緩和。価格 parquet の commit は継続。
- **セキュリティ**: 接続文字列は Secret のみ。ログ/レポートへ秘匿情報を出さない。最小権限の DB ロール（アプリ用は DDL 不可）。

---

## 9. リスクと緩和（総括）

| リスク | 影響 | 緩和 |
|---|---|---|
| Neon Free の compute/storage 枠超過 | 書き込み欠落 | フォールバックキュー、バッチ書き込み、データ量を意思決定ログに限定、400MB 警告 |
| 過学習（特徴量/ターゲット増） | ライブ劣化 | walk-forward 厳守、較正、**実現結果台帳での A/B 検証**、ドリフト検知 |
| 小ユニバースのクロスセクション弱さ | αが出ない | 30銘柄下限、N 不足日は per-ticker へフォールバック |
| レジーム誤判定 | 大きな逆風 | ボラターゲットで段階調整、断定的オン/オフを避ける |
| 回転率増→コスト負け | 実質リターン低下 | ヒステリシス必須、コスト込みバックテストで検証 |
| 新規依存（DB/秘密）増 | 運用複雑化 | 縮退運転を原則化、`src/db.py` に隔離、最小権限 |
| 手動執行とのズレ | 提案と実約定の乖離 | 指値/損切りを明示、差分（add/trim/exit）を提示、約定記録は将来 DB 化 |

---

## 10. 優先度・実装順序・工数感

| フェーズ | 価値 | リスク | 依存 | 目安 |
|---|---|---|---|---|
| **Phase 0 計測基盤** | 高（検証土台） | 低 | なし | 小〜中 |
| **Phase 1 品質足回り** | 高 | 中 | Phase0（検証に必須） | 中 |
| **Phase 2 本丸** | 最高（収益） | 中〜高 | Phase1（特徴量/較正/版管理） | 大 |
| **Phase 3 UX** | 中（手動トレード直結） | 低 | Phase0/2（DB データ） | 中 |

**推奨着手順**: Phase0 → Phase1 → Phase2 → Phase3（C案）。Phase0 と Phase1 の一部（マクロ取得・較正）は並行可能。各フェーズは受け入れ基準を満たしてから次へ。

---

## 11. 付録

### 11.1 将来の自動執行（現スコープ外）

手動トレードが前提のため未着手だが、設計上は接続点を用意する。`portfolio_snapshots.diff_from_prev` を発注指示（CSV/ブローカー API）へ写像する `src/execution.py` を将来追加。約定を `fills` テーブルに記録し、提案 vs 実約定の乖離を計測。**発注の不可逆処理は決定論コードに限定**（原則3）。

### 11.2 用語

- **IC（情報係数）**: スコアと実現先行リターンの相関。クロスセクション予測力の指標。
- **トリプルバリア**: 利確・損切り・時間の 3 つの境界で取引結果をラベル化する手法。
- **ヒステリシス（無トレード幅）**: 小さなウェイト変化では再調整しないことで回転率・コストを抑える仕組み。
- **較正**: 予測確率を実測度数に一致させる後処理（isotonic/Platt）。

### 11.3 関連文書

- as-built 仕様: `00_overview.md`〜`06_priority_matrix.md`
- AI 銘柄キュレーション: `ai_ticker_curation/`
- 本書が回収する既知課題: `06_priority_matrix.md` の P2（LINE リトライ・チャート空データ・週次品質検証・feature precompute の扱い）
