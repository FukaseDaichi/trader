# Phase 1: シグナル品質の足回り Implementation Plan

作成日: 2026-06-09 JST

**Goal:** Phase 0 の実現結果台帳を検証基盤として使いながら、銘柄別モデルのまま、ターゲット、特徴量、較正、モデル運用を改善する。日次パイプラインは止めず、保存済みモデルがない場合は現行の日次学習にフォールバックする。

**設計の正典:** `specification_document/improvement_roadmap.md` の Phase 1 と §6。Phase 0 実装計画は `specification_document/plans/2026-06-08-phase0-measurement-foundation.md`。

---

## 0. Phase 0 実装確認サマリ

2026-06-09 JST 時点のローカル確認結果。

### 確認できたこと

- Phase 0 の主要ファイルは実装済み。
  - `src/db_records.py`: signal/prediction 行マッピング、`compute_outcome`、`summarize_performance`
  - `src/db.py`: psycopg I/O、write-through、outbox、settlement 用 read/write
  - `migrations/0001_phase0_schema.sql`: Phase 0 最小テーブル
  - `scripts/db_migrate.py`: migration runner、tickers/legacy model seed
  - `scripts/settle_outcomes.py`: parquet から 1/5/10 営業日 outcome を確定
  - `main.py`: `db.record_run()` を例外握りつぶしで呼び出し
  - `src/dashboard.py`: `docs/performance_summary.json` を best-effort export
  - `.github/workflows/daily-preopen-core.yml`: DB env と settle step
  - `web/src/components/PerformanceCard.tsx`: 実績タイル

- DB なしで検証できるテストと構文確認は通過。
  - `uv run python tests/test_db_records.py` -> 14/14 passed
  - `uv run python tests/test_curation_merge.py` -> 8/8 passed
  - Python AST parse -> OK
  - workflow YAML parse -> OK
  - `npm run lint --prefix web` -> OK
  - `npm run build:prod --prefix web` -> OK
  - `TRADER_DB_ENABLED=false uv run python scripts/db_migrate.py --dry-run` -> DB disabled no-op
  - `TRADER_DB_ENABLED=false uv run python scripts/settle_outcomes.py --as-of 2026-06-09` -> DB disabled no-op
  - `psycopg` import -> OK

### 未確認または Phase 1 着手前に確認すること

- 実 DB への migration、write-through、outbox replay はローカルでは未確認。`DATABASE_URL` を持つ環境で統合スモークを必ず通す。
- `docs/state.json` の直近履歴を DB に seed するバックフィルは、Phase 0 実装計画のタスクには含まれていない。Phase 1 の A/B 検証を早めるため、Task 0 で小さく追加する。
- `benchmark_ret` / `excess_ret` は Phase 0 では NULL 固定。Phase 1 で TOPIX 系列を取得できるようになった後に埋める。
- `signals.prediction_id` は Phase 0 では NULL。Phase 1 では daily prediction insert 時に signal と紐づける。

---

## 1. Phase 1 の方針

### 実装範囲

Phase 1 は「銘柄別のまま品質を上げる」段階に限定する。クロスセクションモデル、ポートフォリオ最適化、今日の建玉は Phase 2 へ送る。

### 互換性ルール

- `TRADER_MODEL_MODE=auto` を標準にする。
  - active な Phase 1 model artifact があれば保存済みモデルで推論。
  - artifact が無い、壊れている、DB が不通の場合は現行 `train_and_predict()` にフォールバック。
- `docs/state.json` / `docs/dashboard_index.json` の既存契約は維持。
- `src/db.py` の例外非伝播方針は維持。DB 失敗で日次通知と dashboard export を止めない。

### 主要な成果物

- 5営業日 horizon のラベル生成と horizon-aware backtest
- マクロ/レジーム特徴量の parquet/DB snapshot 化
- OOS 較正、Brier/reliability 指標、`model_registry.calibration`
- 週次モデル永続化、`model_registry` への版登録、日次推論のみ運用
- Drift report と warning

---

## 2. 追加/変更ファイル

| ファイル | 区分 | 責務 |
|---|---|---|
| `.env.example` | 変更 | Phase 1 env を追記 |
| `migrations/0002_phase1_quality_schema.sql` | 新規 | `macro_snapshots`、model quality/drift 補助テーブル |
| `src/labels.py` | 新規 | 5d forward return、vol-normalized return、triple barrier label |
| `src/calibration.py` | 新規 | 依存追加なしの isotonic/PAVA 較正、Brier/reliability |
| `src/macro.py` | 新規 | 市場系列取得、macro snapshot、macro feature join |
| `src/model_store.py` | 新規 | LightGBM Booster artifact の保存/読込、metadata、active pointer |
| `src/model.py` | 変更 | Phase 1 training/prediction API を追加し legacy wrapper を維持 |
| `src/backtest.py` | 変更 | horizon-aware OOS、較正評価、5d KPI gate |
| `src/db_records.py` | 変更 | Phase 1 prediction fields を signal から反映 |
| `src/db.py` | 変更 | active model registry、prediction_id 紐づけ、macro/model quality upsert |
| `src/dashboard.py` | 変更 | `docs/model_quality.json` / drift warning export |
| `scripts/backfill_state_signals.py` | 新規 | `docs/state.json` 直近履歴を Phase 0 DB へ seed |
| `scripts/update_macro_snapshots.py` | 新規 | macro parquet と DB `macro_snapshots` を更新 |
| `scripts/weekly_model_retrain.py` | 変更 | 実学習、artifact 保存、model_registry 登録 |
| `scripts/drift_check.py` | 新規 | IC/AUC/Brier/PSI の監視、warning JSON、必要時 Issue |
| `.github/workflows/daily-preopen-core.yml` | 変更 | macro update と model-mode env |
| `.github/workflows/weekly-model-retrain.yml` | 変更 | DB env、artifact commit、active model 登録 |
| `.github/workflows/daily-watchdog.yml` | 変更 | drift check 呼び出し |
| `web/src/types/index.ts` | 変更 | model quality/drift 型 |
| `web/src/components/ModelQualityCard.tsx` | 新規 | Brier/reliability/drift の最小表示 |
| `web/src/app/page.tsx` | 変更 | `ModelQualityCard` を差し込み |
| `tests/test_labels.py` | 新規 | label 生成の standalone test |
| `tests/test_calibration.py` | 新規 | PAVA 較正と Brier/reliability test |
| `tests/test_macro_features.py` | 新規 | macro feature join の純粋ロジック test |
| `tests/test_model_store.py` | 新規 | metadata/active pointer の file I/O test |

---

## 3. 環境変数

`.env.example` に追加する。

```bash
# --- Phase 1: シグナル品質 ---
TRADER_MODEL_MODE=auto
TRADER_TARGET_HORIZON_DAYS=5
TRADER_LABEL_MODE=triple_barrier
TRADER_TB_TP_ATR=1.5
TRADER_TB_SL_ATR=1.0
TRADER_TB_MAX_DAYS=5
TRADER_CALIBRATION_MODE=isotonic
TRADER_MACRO_FEATURES_ENABLED=true
TRADER_MODEL_DIR=data/models
TRADER_MODEL_ACTIVE_FILE=data/models/active_model.json
TRADER_MIN_CALIBRATION_ROWS=60
TRADER_DRIFT_MIN_OUTCOMES=30
TRADER_DRIFT_MIN_IC=-0.02
TRADER_DRIFT_MAX_BRIER=0.30
TRADER_DRIFT_MAX_PSI=0.25
```

---

## 4. データ契約

### `model_registry`

Phase 1 では 1 回の週次学習につき 1 行を登録する。

- `version`: `per-ticker-v1-YYYYMMDD`
- `kind`: `per_ticker_horizon_v1`
- `universe`: enabled ticker list
- `feature_set`: technical + macro feature names
- `params`: LightGBM params、label config、horizon config
- `cv_metrics`: ticker 別と全体の IC/AUC/Brier/reliability
- `calibration`: ticker 別 isotonic knots
- `artifact_uri`: `data/models/per-ticker-v1-YYYYMMDD/metadata.json`
- `active`: active version のみ true

### `predictions`

Phase 1 日次予測では以下を埋める。

- `model_version`: active Phase 1 version
- `horizon_days`: 5
- `raw_score`: 未較正 probability または regression score
- `prob_up`: 較正後 probability
- `expected_ret`: 5d expected return estimate
- `features_hash`: 推論時 feature vector の hash

### `docs/model_quality.json`

```json
{
  "available": true,
  "generated_at": "2026-06-09 06:10:00",
  "active_model_version": "per-ticker-v1-20260613",
  "horizon_days": 5,
  "summary": {
    "tickers": 10,
    "median_brier": 0.24,
    "median_ic": 0.03,
    "drift_warning": false
  },
  "by_ticker": {
    "7011.JP": {
      "brier": 0.23,
      "ic": 0.05,
      "calibration_rows": 180,
      "psi_max": 0.12,
      "warning": false
    }
  }
}
```

DB 不通時は `{"available": false, "reason": "...", "generated_at": "..."}`。

---

## 5. 実装タスク

### Task 0: Phase 0 gate と短期バックフィル

**目的:** Phase 1 の A/B 検証土台が動いていることを確認する。

- [ ] `DATABASE_URL` がある環境で migration を適用する。
  ```bash
  uv run python scripts/db_migrate.py
  ```
- [ ] DB 有効状態で日次パイプラインを 1 回走らせ、`predictions` / `signals` に入ることを確認する。
  ```bash
  RUN_DATE_JST=2026-06-09 uv run python main.py
  ```
- [ ] `settle_outcomes.py` を実行し、前向きデータがある古い signal で outcome が埋まることを確認する。
  ```bash
  uv run python scripts/settle_outcomes.py --as-of 2026-06-09
  ```
- [ ] `scripts/backfill_state_signals.py` を追加し、`docs/state.json` の直近30日を `signals` / `predictions` へ seed する。outcome は既存 `settle_outcomes.py` で埋める。
- [ ] `docs/performance_summary.json` が `available: true` になることを確認する。

**Acceptance:**
- real DB で Phase 0 統合スモークが通る。
- `run_date` と `as_of_date` が DB 上で区別される。
- backfill は idempotent で、再実行しても重複しない。

### Task 1: Phase 1 config と schema

- [ ] `.env.example` に Phase 1 env を追加する。
- [ ] `migrations/0002_phase1_quality_schema.sql` を追加する。
  - `macro_snapshots`
  - `model_quality_snapshots`
  - `drift_reports`
  - 既存 `model_registry` はそのまま利用する。
- [ ] `scripts/db_migrate.py` が 0002 を idempotent に適用できることを確認する。

**Acceptance:**
- DB 無効時は従来通り no-op。
- DB 有効時に 0001 済み DB へ 0002 を追加適用できる。

### Task 2: label 生成を分離する

- [ ] `src/labels.py` を追加する。
  - `add_forward_return_labels(df, horizon_days=5)`
  - `add_vol_normalized_labels(df, horizon_days=5, vol_col="volatility")`
  - `add_triple_barrier_labels(df, horizon_days=5, tp_atr=1.5, sl_atr=1.0)`
  - `build_labelled_frame(df, config)`
- [ ] `tests/test_labels.py` を追加する。
- [ ] `src/model.py` / `src/backtest.py` の target 作成を `src.labels` 経由にする。
- [ ] legacy path は `TRADER_LABEL_MODE=binary_1d` で再現できるようにする。

**Acceptance:**
- `TRADER_TARGET_HORIZON_DAYS=5` で last 5 rows は target unknown として落ちる。
- triple barrier は先に触れた TP/SL を優先し、未到達なら time exit。
- 既存テストが通る。

### Task 3: horizon-aware backtest

- [ ] `src/backtest.py` に horizon-aware simulation を追加する。
  - signal は H 営業日有効。
  - 同一 ticker 内で重なる signal は、active stances の平均 exposure として日次 return に落とす。
  - turnover cost は exposure 変化に対して課す。
- [ ] `_collect_oos_predictions()` は 5d label と 5d realized return を返す。
- [ ] KPI metrics は現行の CAGR / MaxDD / Sharpe / Expectancy / Turnover を維持する。
- [ ] threshold optimization は較正後 probability を使う準備だけ入れ、Task 5 までは raw probability で動かす。

**Acceptance:**
- 現行の 1d mode と 5d mode の両方で `evaluate_kpi_gate()` が動く。
- `docs/backtest_report.json` に horizon と label_mode が入る。

### Task 4: macro snapshot と macro features

- [ ] `src/macro.py` を追加する。
  - 市場系列は configurable にする。初期値は TOPIX、日経平均、USDJPY、日経VI、JGB10y を想定するが、取得できない系列は欠損で継続。
  - yfinance/Stooq の symbol 差は設定で吸収し、コードに固定しすぎない。
  - `docs/curation/macro_latest.json` の `market_bias` / `regime` は定性的補助特徴として encode する。
- [ ] `scripts/update_macro_snapshots.py` を追加する。
  - `data/macro/*.parquet` を更新。
  - DB 有効時は `macro_snapshots` に当日 snapshot を upsert。
  - DB 無効時も parquet は更新できる。
- [ ] `add_macro_features(stock_df, macro_panel, ticker_info)` を実装する。
  - 水準、20/60日 return、20日 volatility、200日線 regime、USDJPY trend など。
  - stock date に前方のみで join し、未来情報を入れない。
- [ ] `tests/test_macro_features.py` を追加する。

**Acceptance:**
- macro 取得が一部失敗しても日次モデルは止まらない。
- feature join が未来参照しないことをテストで確認する。

### Task 5: 較正ロジック

- [ ] `src/calibration.py` を追加する。
  - `fit_isotonic_pava(scores, labels)`
  - `apply_isotonic(calibrator, scores)`
  - `brier_score(prob, labels)`
  - `reliability_bins(prob, labels, n_bins=10)`
- [ ] `tests/test_calibration.py` を追加する。
- [ ] OOS tuning fold で calibrator を fit し、holdout fold に apply する。
- [ ] `model_registry.calibration` に ticker 別 calibrator を保存できる JSON 形にする。

**Acceptance:**
- 較正前後の Brier を report に出す。
- 較正後 Brier が悪化する ticker は `calibration_mode=none` へ fallback できる。

### Task 6: model artifact と active model

- [ ] `src/model_store.py` を追加する。
  - `save_model_bundle(version, ticker, boosters, metadata)`
  - `load_model_bundle(version, ticker)`
  - `write_active_model(version, metadata)`
  - `read_active_model()`
- [ ] artifact layout:
  ```text
  data/models/
    active_model.json
    per-ticker-v1-YYYYMMDD/
      metadata.json
      7011.JP/
        fold_0.txt
        fold_1.txt
        fold_2.txt
        final.txt
        calibration.json
        feature_reference.json
  ```
- [ ] `tests/test_model_store.py` を追加する。
- [ ] `.gitignore` は data models を必要に応じて調整する。artifact を commit する運用なら ignore しない。

**Acceptance:**
- 保存した LightGBM Booster を読み戻して同じ feature vector に同じ prediction を返す。
- `active_model.json` が壊れている場合は legacy daily training に fallback する。

### Task 7: weekly retrain を実学習に格上げ

- [ ] `scripts/weekly_model_retrain.py` を改修する。
  - enabled ticker ごとに Phase 1 labelled frame を作る。
  - technical + macro features で fold models と final model を学習する。
  - OOS metrics、calibration、feature reference を保存する。
  - `model_registry` に version を登録し、新 version を active にする。
  - 従来の `docs/weekly_retrain_report.json` も維持する。
- [ ] DB 不通時は artifact と report は作成し、registry 登録だけ skip する。
- [ ] 失敗 ticker は report に残し、他 ticker の学習は続ける。

**Acceptance:**
- `data/models/per-ticker-v1-YYYYMMDD/` が生成される。
- DB 有効時に `model_registry.active=true` が新 version へ切り替わる。
- 週次 workflow が artifact と report を commit する。

### Task 8: daily inference を保存済みモデルへ切替

- [ ] `main.py` の per-ticker 処理を `TRADER_MODEL_MODE` 対応にする。
  - `legacy`: 現行どおり毎日学習。
  - `phase1`: active model 必須。なければ failed HOLD。
  - `auto`: active model があれば推論、なければ legacy fallback。
- [ ] Phase 1 prediction result を signal に付与する。
  - `model_version`
  - `horizon_days`
  - `raw_score`
  - `prob_up`
  - `expected_ret`
  - `features_hash`
- [ ] `src/db_records.signal_to_prediction_row()` が上記 fields を DB に保存するようにする。
- [ ] `signals.prediction_id` を可能なら upsert 時に紐づける。

**Acceptance:**
- 日次実行で毎日 LightGBM を再学習しない path が動く。
- active model が無くても `auto` では従来パイプラインが完走する。
- `predictions.model_version` が `legacy-daily-v0` ではなく Phase 1 version になる。

### Task 9: model quality export と dashboard

- [ ] `src/dashboard.py` に `export_model_quality()` を追加する。
- [ ] `docs/model_quality.json` を出力する。
- [ ] `web/src/components/ModelQualityCard.tsx` を追加する。
- [ ] `web/src/app/page.tsx` に `PerformanceCard` の近くへ差し込む。
- [ ] `web/src/types/index.ts` に型を追加する。

**Acceptance:**
- DB/quality data が無い場合はカード非表示。
- Brier、IC、active model version、drift warning が見える。
- `npm run lint --prefix web` と `npm run build:prod --prefix web` が通る。

### Task 10: drift check

- [ ] `scripts/drift_check.py` を追加する。
  - DB の `predictions` と `signal_outcomes` を join し、active model の rolling IC/Brier/hit rate を計算。
  - feature reference と当日 feature の PSI を計算。
  - `docs/drift_report.json` を出力。
  - 閾値割れなら `drift_reports` に保存し、workflow から Issue 作成できる exit/status を返す。
- [ ] `daily-watchdog.yml` に drift check を追加する。
- [ ] Issue 作成は `GH_TOKEN` がある GitHub Actions 上だけ有効にし、ローカルでは warning JSON のみ。

**Acceptance:**
- outcome sample が `TRADER_DRIFT_MIN_OUTCOMES` 未満なら `insufficient_sample` として警告のみ。
- 閾値割れ時に dashboard warning が出る。

### Task 11: workflows と rollout

- [ ] `daily-preopen-core.yml`
  - `scripts/update_macro_snapshots.py` を `main.py` 前に追加。
  - Phase 1 env を追加。
  - `TRADER_MODEL_MODE=auto` で開始する。
- [ ] `weekly-model-retrain.yml`
  - `DATABASE_URL` / Phase 1 env を渡す。
  - `scripts/update_macro_snapshots.py` の後に `scripts/weekly_model_retrain.py`。
  - `data/models/`, `docs/weekly_retrain_report.json`, `docs/model_quality.json` を commit 対象にする。
- [ ] `daily-watchdog.yml`
  - `scripts/drift_check.py` を追加。

**Acceptance:**
- model artifact が無い初回でも daily は legacy fallback で成功する。
- 週次 retrain 後の翌営業日から Phase 1 inference が使われる。

---

## 6. Verification

DB なしローカルで通すもの:

```bash
uv run python tests/test_labels.py
uv run python tests/test_calibration.py
uv run python tests/test_macro_features.py
uv run python tests/test_model_store.py
uv run python tests/test_db_records.py
uv run python tests/test_curation_merge.py
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-preopen-core.yml')); yaml.safe_load(open('.github/workflows/weekly-model-retrain.yml')); print('workflow YAML OK')"
npm run lint --prefix web
npm run build:prod --prefix web
```

DB あり staging/production 相当で通すもの:

```bash
uv run python scripts/db_migrate.py
uv run python scripts/update_macro_snapshots.py --as-of 2026-06-09
uv run python scripts/weekly_model_retrain.py --output docs/weekly_retrain_report.json
RUN_DATE_JST=2026-06-09 TRADER_MODEL_MODE=auto uv run python main.py
uv run python scripts/settle_outcomes.py --as-of 2026-06-09
uv run python scripts/drift_check.py --as-of 2026-06-09
```

---

## 7. Acceptance Criteria

- `model_registry` に週次で `per-ticker-v1-YYYYMMDD` が積まれ、active version が 1 つだけになる。
- 日次パイプラインは active artifact がある場合に推論のみで動き、legacy daily training より実行時間が短くなる。
- `predictions` に `horizon_days=5`、`expected_ret`、Phase 1 `model_version` が入る。
- 較正後 `prob_up` の Brier が未較正比で同等以上。悪化 ticker は uncalibrated fallback になる。
- macro feature 取得失敗時も日次シグナル、LINE、dashboard は止まらない。
- drift 閾値割れ時に `docs/drift_report.json` と dashboard warning が出る。
- `docs/state.json` / `docs/dashboard_index.json` の既存契約が壊れない。

---

## 8. Rollback

- 即時 rollback は `TRADER_MODEL_MODE=legacy`。
- active artifact だけ無効化する場合は `data/models/active_model.json` を退避し、weekly retrain を再実行する。
- DB schema は前方互換の追加のみ。既存 Phase 0 tables は削除しない。
- Phase 1 dashboard cards は data unavailable なら非表示にするため、JSON 欠損で UI は壊れない。

---

## 9. Phase 2 へ送るもの

- クロスセクション panel model
- ユニバース 30-50 銘柄への拡大
- ポートフォリオ target weight
- セクター制約、逆ボラ sizing、ボラターゲット、ヒステリシス
- `portfolio_snapshots` と「今日の建玉」UI
