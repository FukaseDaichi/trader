# 全銘柄の実現リターン台帳（`market_outcomes`）— 評価専用

作成日: 2026-10-25 JST
状態: Step 1〜3 実装済み（1本の PR にまとめた）。migration 0005 の適用と Step 4 の判断待ち

## 目的

HOLD を含む有効50銘柄すべてについて、予測後の実現リターン（`next_session_open_to_close_v2`）を
**評価専用**に記録する。Phase 1 vs Phase 2 の shadow 比較と drift 監視を、actionable
シグナルの有無に左右されない標本で測れるようにする。

売買判断・KPI ゲート・成績 KPI（`performance_*.json`）・通知には一切使わない。

## 背景（2026-10-24 週の観測）

- `scripts/settle_outcomes.py` は actionable（BUY / MILD_BUY / SELL / MILD_SELL）なシグナルしか
  決済しない（`db.fetch_unsettled()` の `s.action IN (...)`）。HOLD の (date, ticker) には
  `realized_ret` が存在しない。
- `scripts/portfolio_shadow_report.py` は `signal_outcomes` を `signals` に LEFT JOIN して
  `realized_ret` を得ている。このため paired 比較は「Phase 1 が actionable を出した銘柄」だけで
  計算される。
  - 2026-10-24 時点: `n_paired_dates=30`、`n_paired_records=99`（1日あたり約3.3銘柄）。
  - `top_n=8` が1日の paired 銘柄数を上回るため、Phase 1/2 の top-N が同じ集合になる。
    `topn_realized_return` / `hit_rate` / `max_drawdown` の delta は構造的に 0.0。
  - `cs_ic_vs_phase1`（`active_readiness` の入力、`< -0.005` で not ready）は約3銘柄の
    断面 IC の日次平均で、ノイズが大きい。さらに標本は「Phase 1 の確信度が極端な銘柄」に偏る。
  - 2026-10-19〜23 は gate 通過 1/50・全 HOLD だったため、paired 観測が1件も増えなかった。
- `src/portfolio_shadow.py` の docstring は「`realized_ret` は (date, ticker, horizon) の性質で、
  どのモデルの予測にも共通」と定義しているが、データ源は actionable に限られており、
  この定義と実装がずれている。
- drift（`docs/drift_report.json`）も同じ台帳を使うため、2026-10-23 時点で 50/50 銘柄が
  `insufficient_sample`（合計190件、最大29件）。

## 方針

### 採用: 新テーブル `market_outcomes`（モデル非依存）

`signal_outcomes` には手を入れず、(ticker, market_as_of_date, horizon, contract) をキーとする
別テーブルを作る。

- 既存の読み手（`performance.py`、`dashboard.export_*`、`db.fetch_outcome_rows` ほか）は
  変更不要で、出力はバイト単位で不変。
- 実現リターンは action に依存しない市場の事実なので、signal 単位ではなく市場日単位で持つ方が
  docstring の定義と一致する。

### 不採用: HOLD も `signal_outcomes` に決済する

行数は自然に増えるが、`signal_outcomes` の全読み手（実現的中率、平均リターン、equity curve、
`signal_outcomes_recent.json`、drift）に HOLD が混入する。全読み手へ actionable フィルタを
足す必要があり、1か所でも漏れるとユーザー向けの成績 KPI が静かに変わる。リスクに対して
得るものがない。

## スキーマ（migration `0005_market_outcomes.sql`）

```sql
CREATE TABLE IF NOT EXISTS market_outcomes (
  ticker             TEXT NOT NULL,
  market_as_of_date  DATE NOT NULL,   -- 予測に使った最後の市場日（signals/predictions.as_of_date）
  horizon_days       INT  NOT NULL,   -- OUTCOME_HORIZONS (1, 5, 10)
  contract_version   TEXT NOT NULL,   -- next_session_open_to_close_v2
  entry_date         DATE NOT NULL,
  eval_date          DATE NOT NULL,
  entry_price        DOUBLE PRECISION NOT NULL,
  exit_price         DOUBLE PRECISION NOT NULL,
  entry_price_basis  TEXT NOT NULL,
  exit_price_basis   TEXT NOT NULL,
  realized_ret       DOUBLE PRECISION NOT NULL,
  benchmark_ret      DOUBLE PRECISION,
  excess_ret         DOUBLE PRECISION,
  benchmark_basis    TEXT NOT NULL,
  settled_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, market_as_of_date, horizon_days, contract_version)
);
CREATE INDEX IF NOT EXISTS idx_market_outcomes_asof ON market_outcomes (market_as_of_date);
```

- `hit` / `mae` / `mfe` / `exit_reason` は持たない（action に依存する、または評価に不要）。
- 日付・価格・benchmark の意味は `signal_outcomes` の v2 契約（`05_cross_cutting.md`）と同一。
  benchmark は同一 basis・日付完全一致のみで、欠損は NULL のまま（0 や 1 倍で補完しない）。
- 行数の目安: 50銘柄 × 3 horizon × 約245営業日 ≈ 3.7万行/年。DB 容量警告（400MB）への影響は軽微。
- 適用は人間が `manual-db-migrate.yml` を実行する。未適用の間、書き手と読み手は
  「テーブルなし」を検知して縮退する（後述）。

## 実装ステップ

Step 1〜3 は1本の PR にまとめる（2026-10-25 にユーザーが決定）。Step 1〜3 は既存の判定
（`active_readiness`、drift breach）を一切変えない。判定の入力を切り替えるのは Step 4 で、人間の判断を挟む。

### Step 1: 台帳の書き手

- `scripts/settle_market_outcomes.py` を新設する。
  - 対象: `predictions` に存在する (ticker, as_of_date) のうち、`market_outcomes` に未決済の
    (horizon, contract) があるもの。Phase 1 / Phase 2 どちらの予測でも、同じ市場日は1行に集約する。
  - 計算: `settle_outcomes.py` と同じ `resolve_execution_window()`・`compute_benchmark_ret()`・
    `_load_topix_by_date()` を import して再利用する。`realized_ret` は
    `exit_price / entry_price - 1`。
  - 冪等: PK で `ON CONFLICT DO NOTHING`。前方データ不足の行は次回以降に決済する。
  - `--since YYYY-MM-DD`: 対象の `as_of_date` を絞る。既定は全期間なので、初回実行が過去分の
    backfill を兼ねる（parquet の価格履歴から再計算できるため、過去60日の shadow window を即座に埋められる）。
  - 縮退: DB 無効、接続不可、テーブル未作成（`UndefinedTable`）、その他の例外は全て
    ログを出して exit 0 で終える。
- `daily-preopen-core.yml` と `daily-preopen-retry.yml` の「Settle realized outcomes」の直後に、
  `continue-on-error: true`・`timeout-minutes: 5` のステップとして追加する。既存の決済ステップとは別プロセスにし、
  新スクリプトの失敗が既存決済へ波及しないようにする。
- `settle_outcomes.py` は変更しない。
- テスト: `tests/test_settle_market_outcomes.py`（DB 不要。fake conn で冪等性、HOLD を含むこと、
  前方データ不足、benchmark 欠損時の NULL、テーブル未作成時の exit 0 を確認）。

### Step 2: shadow report に全銘柄比較を併記

- `scripts/portfolio_shadow_report.py` に、`market_outcomes` から `realized_ret` を取る2本目の
  クエリを追加する。join は `predictions.as_of_date = market_outcomes.market_as_of_date`
  （Phase 1 / Phase 2 とも同じ市場日の同じ実現リターンを共有する）。
- 出力 JSON の末尾に `full_universe` ブロックを**追加**する（`build_shadow_report()` と同じ
  `n_paired_*` / `date_coverage` / `comparison` 構造に `outcome_source` / `informational_only` を付けたもの）。
  - 既存の `comparison`・`n_paired_*`・`date_coverage`・`active_readiness` は従来の
    actionable-only 計算のまま変えない。
  - テーブル未作成・取得失敗時は `full_universe: {"available": false, "reason": ...}`
    とし、既存キーは影響を受けない。
- `src/portfolio_shadow.py` の計算関数（`compare_phase1_phase2` ほか）は入力レコードを変えるだけで
  再利用する。関数のシグネチャと既存出力は変えない。
- docstring の `realized_ret` 定義と実データ源のずれを解消する注記を入れる。
- テスト: `tests/test_portfolio_shadow.py` に、全銘柄レコードで top-N が Phase 1/2 で別集合になるケース、
  `market_outcomes` 不在時に既存キーがバイト一致するケースを追加する。

### Step 3: drift に全予測ベースの指標を参考表示

- `scripts/drift_check.py` の銘柄別出力に `all_predictions`（`ic` / `brier` / `n_outcomes` /
  `metric_status` / `thresholds_exceeded`）を、トップレベルに `all_predictions` 要約を**参考値として追加**する。
  DB に保存される `summary` は変えない。
- breach 判定（`breach_reasons`）・Issue 起票・watchdog の終了コードには使わない。
  過去分を backfill すると即座に `min_outcomes=30` を超えるため、判定に入れると初日に
  breach と Issue が大量に出るおそれがある。
- 重複排除は既存と同じ (ticker, entry_date) 単位（`market_outcomes` の PK で自然に1行）。

### Step 4: 判定入力の切替（人間の判断が必要）

Step 2 の `full_universe` を最低2回の週次レビュー（土曜の再学習後）で
既存 `comparison` と並べて記録してから、人間が次を判断する。

- `active_readiness.cs_ic_vs_phase1` の入力を全銘柄比較へ切り替えるか。
- `shadow_days` を全銘柄 paired dates で数えるか。
- drift breach の標本を全予測へ切り替えるか（閾値 `min_ic` / `max_brier` の再設定を含む）。

切替は判定基準の変更なので、`06_issues_and_backlog.md` に決定記録（判定者・根拠・旧値と新値）を
残す。active 化そのものは引き続き `TRADER_PORTFOLIO_MODE` の手動変更でのみ行う。

## 不変条件との照合

| 不変条件 | 本計画での扱い |
| --- | --- |
| 日次シグナル生成を止めない | 新スクリプトは別ステップ・`continue-on-error`・全例外で exit 0。`main.py` は変更しない |
| KPI ゲート | `market_outcomes` はゲート・`performance.py` から読まない |
| Phase 2 shadow のバイト不変 | portfolio snapshot・target weight・`portfolio_latest.json` は不変。shadow report は既存キー不変でキー追加のみ。`active_readiness` は Step 4 まで不変 |
| `docs/` の `--exclude` | 新規ファイルなし（既存 JSON へのキー追加のみ）。Step 3 で新ファイルを作る場合は exclude へ追加 |
| `tickers.yml` / `curation_pool.yml` | 触らない |
| parquet を削除しない | 読むだけ。無効化銘柄は既存と同じく `load_archived_data()` で読む |

各 PR は `pipeline-safety-reviewer` で審査してからマージする。

## 文書の更新

- `specification_document/05_cross_cutting.md`: DB テーブル一覧に `market_outcomes`、
  `portfolio_shadow_report.json` / `drift_report.json` の追加キー。
- `specification_document/04_scripts.md`: `settle_market_outcomes.py`。
- `specification_document/03_cicd_workflows.md`: core / retry の新ステップ。
- `specification_document/06_issues_and_backlog.md`: 「着手条件が既に揃っているもの」に本件を追加し、
  turnover 再校正より先に行う理由（再校正の根拠となる shadow 分布の標本が薄い）を記す。
  完了後は本計画を削除してバックログへ戻す。

## 確認事項（着手前）

- DB の `predictions` に、shadow window（60日）を backfill できる期間の Phase 1 / Phase 2 予測が
  残っているか。このセッションでは DB に接続できず未確認。
- 2026-10-19 の Core キャンセル日のように予測行が欠けた日は、決済対象がないだけで正しい。
  coverage の除外理由に出ることを Step 2 のテストで確認する。

## 完了の定義

- Step 1〜3 がマージされ、`portfolio_shadow_report.json` の `full_universe` で、
  paired 銘柄数が1日あたり概ね有効銘柄数に近づいている。
- 既存の `comparison` / `active_readiness` / `drift_report.json` の判定キーが、変更前とバイト一致している。
- Step 4 の判断材料（2週分の並列記録）が `06_issues_and_backlog.md` の週次記録にある。
