# Phase 2: クロスセクション + ポートフォリオ提案 Implementation Plan

作成日: 2026-06-09 JST

**Goal:** Phase 1 の保存済みモデル・マクロ特徴量・実現結果台帳を土台に、銘柄別の独立判断から、30〜50銘柄を相対評価するクロスセクションモデルと、リスク制約付きロングオンリー・ポートフォリオ提案へ移行する。日次パイプラインは止めず、Phase 2 model / universe / DB のいずれかが不十分な場合は Phase 1 per-ticker inference へフォールバックする。

**設計の正典:** `specification_document/improvement_roadmap.md` の Phase 2、§6.2、§7。Phase 1 実装計画は `specification_document/plans/2026-06-09-phase1-signal-quality-foundation.md`。

---

## 0. Phase 2 着手前提

### 確認済みの土台

- Phase 0/1 の主要機能は main に入っている。
  - `predictions.cs_rank` と `signals.target_weight` は Phase 2 用の列として既に存在する。
  - `model_registry` は `kind` で `per_ticker_horizon_v1` と `cross_sectional_*` を共存できる。
  - `src/model_store.py` は active pointer と version metadata を持つため、cross-sectional artifact へ拡張できる。ただし既存 `data/models/active_model.json` は Phase 1 fallback 用に維持し、Phase 2 は `data/models/active_cs_model.json` を追加する。
  - `src/macro.py` / `src/phase1.py` に macro feature join、feature reference、PSI がある。
  - `scripts/drift_check.py` は portfolio/global scope を追加しやすい形になっている。

### 未実装の土台

- `portfolio_snapshots`、`backtest_runs`、`backtest_equity` はまだ migration に存在しない。
- enabled universe は現状10銘柄規模で、Phase 2 の30〜50銘柄下限に満たない。
- `tickers.yml` の編集は deterministic script が担う。agent/LLM が直接 `tickers.yml` を編集してはいけない。
- DB あり統合スモークは `DATABASE_URL` が必要なため、ローカルでは DB 無効 no-op と純粋ロジックを中心に検証する。

---

## 1. Phase 2 の方針

### 実装範囲

Phase 2 は「収益の本丸」として次を実装する。

1. universe を 30〜50 銘柄へ拡張する deterministic pipeline。
2. 全銘柄×全日付の panel を使う cross-sectional model。
3. cross-sectional score から `expected_ret`、`prob_up`、`cs_rank` を日次保存する inference。
4. スコア上位からロングオンリー target portfolio を作る `src/portfolio.py`。
5. portfolio-level walk-forward backtest / KPI gate。
6. `portfolio_snapshots` と `docs/portfolio_latest.json` への「今日の建玉」出力。

### 非スコープ

- 自動発注、証券口座連携、約定管理。
- Phase 3 の本格UX。Phase 2では最小の portfolio card / JSON export まで。
- LLM による `tickers.yml` 直接編集。 universe の更新は `scripts/universe_select.py` または既存 `curation_merge.py` の deterministic guardrail に限定する。

### ロールアウト方針

Phase 2 は2段階で出す。

1. **shadow mode**: cross-sectional prediction と portfolio JSON/DB snapshot を生成するが、既存シグナル/通知の売買判断は Phase 1 を維持する。
2. **active mode**: portfolio KPI gate が通った場合のみ `signals.target_weight` と dashboard の「今日の建玉」を正式表示する。個別LINE通知は当面 Phase 1 のまま、portfolio digest は Phase 3 へ送る。

### 互換性ルール

- `TRADER_PORTFOLIO_ENABLED=false` なら Phase 2 は完全に無効。
- `TRADER_PORTFOLIO_MODE=shadow` が初期値。DB/active model/universe が不十分でも daily は成功する。
- enabled universe が `TRADER_CS_MIN_UNIVERSE` 未満なら cross-sectional path は skip し、Phase 1 inference を使う。
- active cross-sectional artifact が欠損または壊れている場合は Phase 1 per-ticker active artifact、さらに無ければ legacy fallback を使う。
- `model_registry.active` は kind 別 active として扱う。CS model を active にしても Phase 1 per-ticker active を非active化しない。
- `docs/state.json` / `docs/dashboard_index.json` 既存契約は壊さない。portfolio data は追加 JSON と optional UI にする。

---

## 2. 追加/変更ファイル

| ファイル | 区分 | 責務 |
|---|---|---|
| `.env.example` | 変更 | Phase 2 env を追記 |
| `migrations/0003_phase2_portfolio_schema.sql` | 新規 | `portfolio_snapshots`、`backtest_runs`、`backtest_equity`、`universe_snapshots` |
| `src/universe.py` | 新規 | pool/watchlist/enabled の deterministic universe selection、流動性計算、sector cap |
| `scripts/universe_select.py` | 新規 | 30〜50銘柄候補を選定し、report出力。`--apply` 時のみ `tickers.yml` 更新 |
| `src/cross_section.py` | 新規 | panel build、日付内zscore/rank、sector/liquidity features、CS label |
| `src/cs_model.py` | 新規 | LightGBM ranker/regressor training、OOS IC/precision@N、daily inference |
| `src/model_store.py` | 変更 | cross-sectional single-model bundle layout の保存/読込 |
| `scripts/weekly_cross_section_retrain.py` | 新規 | CS model artifact 作成、model_registry 登録、quality report |
| `src/portfolio.py` | 新規 | target weight、制約、ボラターゲット、ヒステリシス、diff生成 |
| `src/portfolio_backtest.py` | 新規 | walk-forward portfolio backtest、TOPIX比較、KPI算出 |
| `src/backtest.py` | 変更 | portfolio KPI gate の薄い wrapper / report統合 |
| `src/db_records.py` | 変更 | portfolio snapshot/backtest row mapping の純粋ロジック |
| `src/db.py` | 変更 | portfolio/backtest upsert、latest snapshot read |
| `main.py` | 変更 | Phase 2 inference + portfolio build を orchestration |
| `src/dashboard.py` | 変更 | `docs/portfolio_latest.json` / `docs/portfolio_backtest.json` export |
| `scripts/drift_check.py` | 変更 | portfolio-level IC/IR/turnover/drift summary |
| `.github/workflows/weekly-model-retrain.yml` | 変更 | universe select + CS retrain + artifact commit |
| `.github/workflows/daily-preopen-core.yml` | 変更 | portfolio mode env と portfolio snapshot export |
| `web/src/types/index.ts` | 変更 | portfolio JSON 型 |
| `web/src/components/PortfolioCard.tsx` | 新規 | 最小の今日の建玉カード |
| `web/src/app/page.tsx` | 変更 | `PortfolioCard` を品質/実績カードの近くへ差し込み |
| `tests/test_universe.py` | 新規 | universe selection guardrail |
| `tests/test_cross_section.py` | 新規 | panel normalization / no future leakage |
| `tests/test_portfolio.py` | 新規 | constraints / hysteresis / diff |
| `tests/test_portfolio_backtest.py` | 新規 | portfolio metrics / TOPIX comparison |

---

## 3. 環境変数

`.env.example` に追加する。

```bash
# --- Phase 2: クロスセクション + ポートフォリオ ---
TRADER_PORTFOLIO_ENABLED=false
TRADER_PORTFOLIO_MODE=shadow        # shadow|active
TRADER_UNIVERSE_TARGET_SIZE=40
TRADER_CS_MODEL_ACTIVE_FILE=data/models/active_cs_model.json
TRADER_CS_MIN_UNIVERSE=30
TRADER_CS_OBJECTIVE=ranker          # ranker|regression
TRADER_CS_TOP_N=8
TRADER_CS_LABEL_HORIZON_DAYS=5
TRADER_CS_MIN_DAILY_NAMES=20
TRADER_CS_PANEL_LOOKBACK_YEARS=5
TRADER_PORTFOLIO_TARGET_VOL=0.12    # annualized
TRADER_PORTFOLIO_MAX_NAME_WEIGHT=0.20
TRADER_PORTFOLIO_SECTOR_CAP=0.40
TRADER_PORTFOLIO_MAX_GROSS=1.00
TRADER_PORTFOLIO_MIN_WEIGHT=0.03
TRADER_PORTFOLIO_NOTRADE_BAND=0.02
TRADER_PORTFOLIO_MIN_EXPECTED_RET=0.0
TRADER_PORTFOLIO_RISK_OFF_GROSS_MULT=0.50
TRADER_PORTFOLIO_COV_LOOKBACK_DAYS=60
TRADER_PORTFOLIO_BACKTEST_MIN_SHARPE=0.30
TRADER_PORTFOLIO_BACKTEST_MAX_DD=0.25
TRADER_PORTFOLIO_BACKTEST_MIN_IR=0.00
TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER=0.40
```

---

## 4. データ契約

### `model_registry`

Phase 2 では cross-sectional model を 1 回の週次学習につき 1 行登録する。

- `version`: `cs-v1-YYYYMMDD`
- `kind`: `cross_sectional_ranker_v1` または `cross_sectional_regression_v1`
- `universe`: 学習時の enabled ticker list
- `feature_set`: raw technical + macro + cross-sectional normalized + static features
- `params`: LightGBM params、objective、label config、portfolio config hash
- `cv_metrics`: overall / by_period / precision@N / daily IC / top-bottom spread / portfolio backtest summary
- `calibration`: score bucket -> up probability / expected return map
- `artifact_uri`: `data/models/cs-v1-YYYYMMDD/metadata.json`
- `active`: 同一 `kind` 内の active version のみ true

### Artifact layout

```text
data/models/
  active_model.json
  active_cs_model.json
  cs-v1-YYYYMMDD/
    metadata.json
    model.txt
    calibration.json
    feature_reference.json
    feature_schema.json
    sector_encoder.json
    universe.json
    oos_predictions.parquet
```

`active_cs_model.json` は Phase 2 専用 pointer として以下を持つ。既存 `active_model.json` は Phase 1 rollback 用に維持する。

```json
{
  "version": "cs-v1-20260613",
  "kind": "cross_sectional_ranker_v1",
  "horizon_days": 5,
  "portfolio_enabled": true,
  "universe_size": 40,
  "macro_features_enabled": true
}
```

### `predictions`

Phase 2 daily inference では全 enabled ticker に以下を保存する。

- `model_version`: active `cs-v1-*`
- `horizon_days`: 5
- `raw_score`: cross-sectional model score
- `prob_up`: score bucket / calibrator 由来の上昇確率
- `expected_ret`: score bucket / regression 由来の期待5日リターン
- `cs_rank`: 当日 universe 内順位。1 が最上位
- `features_hash`: 当日 feature vector hash

### `signals`

Phase 2 active mode では、portfolio output に基づき以下を反映する。

- `action`: target_weight > 0 なら `BUY` / `MILD_BUY`、ゼロなら `HOLD`
- `raw_action`: score-only action
- `conviction`: score percentile or calibrated probability
- `target_weight`: portfolio target weight
- `gate_passed`: portfolio KPI gate と per-ticker sanity gate の両方が通った場合 true
- `reason`: rank、expected_ret、vol、sector cap、risk regime を短く含める

### `portfolio_snapshots`

`migrations/0003_phase2_portfolio_schema.sql` で追加する。

```sql
CREATE TABLE portfolio_snapshots (
  run_date        DATE PRIMARY KEY,
  as_of_date      DATE NOT NULL,
  model_version   TEXT REFERENCES model_registry(version),
  mode            TEXT NOT NULL,         -- shadow | active
  status          TEXT NOT NULL,         -- ok | fallback | failed
  positions       JSONB NOT NULL,        -- [{ticker, weight, prev_weight, diff_type, ...}]
  diff_from_prev  JSONB,
  gross_exposure  DOUBLE PRECISION,
  net_exposure    DOUBLE PRECISION,
  sector_exposure JSONB,
  expected_ret    DOUBLE PRECISION,
  expected_vol    DOUBLE PRECISION,
  constraints     JSONB,
  warnings        JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `backtest_runs` / `backtest_equity`

```sql
CREATE TABLE backtest_runs (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,
  model_version TEXT,
  scope         TEXT NOT NULL,           -- portfolio
  start_date    DATE NOT NULL,
  end_date      DATE NOT NULL,
  params        JSONB NOT NULL,
  metrics       JSONB NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE backtest_equity (
  run_id           BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
  date             DATE NOT NULL,
  equity           DOUBLE PRECISION NOT NULL,
  benchmark_equity DOUBLE PRECISION,
  daily_return     DOUBLE PRECISION,
  benchmark_return DOUBLE PRECISION,
  drawdown         DOUBLE PRECISION,
  gross_exposure   DOUBLE PRECISION,
  turnover         DOUBLE PRECISION,
  PRIMARY KEY (run_id, date)
);
```

### `docs/portfolio_latest.json`

DB 不通時も artifact + local JSON で best-effort export する。

```json
{
  "available": true,
  "generated_at": "2026-06-09 06:15:00",
  "run_date": "2026-06-09",
  "as_of_date": "2026-06-08",
  "mode": "shadow",
  "status": "ok",
  "model_version": "cs-v1-20260613",
  "gross_exposure": 0.82,
  "expected_vol": 0.12,
  "expected_ret": 0.018,
  "sector_exposure": {"電機": 0.24, "銀行": 0.16},
  "diff_summary": {"add": 2, "trim": 1, "exit": 1, "hold": 4},
  "positions": [
    {
      "ticker": "7011.JP",
      "name": "三菱重工業",
      "sector": "機械・重工",
      "target_weight": 0.18,
      "prev_weight": 0.12,
      "diff_type": "increase",
      "cs_rank": 1,
      "expected_ret": 0.024,
      "prob_up": 0.63,
      "volatility": 0.021,
      "limit_price": 4500,
      "stop_loss": 4350
    }
  ],
  "warnings": []
}
```

Unavailable:

```json
{"available": false, "reason": "insufficient_universe", "generated_at": "..."}
```

---

## 5. 実装タスク

### Task 0: Phase 1 gate と実運用前チェック

**目的:** Phase 2 の前提である保存済みモデル、DB、実績台帳が壊れていないことを確認する。

- [ ] `main` 上で Phase 1 の DB 無し検証を再実行する。
  ```bash
  uv run python tests/test_labels.py
  uv run python tests/test_calibration.py
  uv run python tests/test_macro_features.py
  uv run python tests/test_model_store.py
  uv run python tests/test_db_records.py
  uv run python tests/test_drift_check.py
  npm run lint --prefix web
  npm run build:prod --prefix web
  ```
- [ ] DB あり環境で `scripts/db_migrate.py` と `main.py` の smoke を通す。
- [ ] Phase 1 active model が存在しない場合でも `TRADER_MODEL_MODE=auto` で daily が完走することを確認する。

**Acceptance:**
- Phase 1 daily が壊れていない。
- Phase 2 を無効にした状態で挙動が main 現状と一致する。

### Task 1: Phase 2 config と schema

- [ ] `.env.example` に Phase 2 env を追記する。
- [ ] `src/config.py` に `get_portfolio_config()` / `get_cross_section_config()` を追加する。
- [ ] `src/db.py` の `register_model_version()` を kind 別 active に変更する。
  - `make_active=True` の場合は同じ `kind` の model だけを active/inactive 更新する。
  - `per_ticker_horizon_v1` と `cross_sectional_*` が同時に1つずつ active になれることをテストする。
- [ ] `migrations/0003_phase2_portfolio_schema.sql` を追加する。
  - `portfolio_snapshots`
  - `backtest_runs`
  - `backtest_equity`
  - `universe_snapshots`
- [ ] `scripts/db_migrate.py` が 0003 を idempotent に適用できることを確認する。

**Acceptance:**
- DB 無効時は従来通り no-op。
- DB 有効時に 0001/0002 済み DB へ 0003 が追加適用できる。
- Phase 2 env 未設定時は disabled/shadow の安全側で動く。

### Task 2: universe selection と 30〜50銘柄への拡張準備

**目的:** クロスセクションが成立する銘柄数を deterministic に確保する。

- [ ] `src/universe.py` を追加する。
  - `load_universe_candidates(tickers_yml, curation_pool_yml)`
  - `compute_liquidity(df)` = 20日平均売買代金
  - `rank_candidates(candidates, liquidity, combined_score, warmup_rows)`
  - `apply_sector_cap(candidates, sector_cap_pct)`
  - `select_target_universe(target_size, min_warmup_rows)`
- [ ] `scripts/universe_select.py` を追加する。
  - デフォルトは report only。
  - `--apply` を付けた場合だけ `tickers.yml` を deterministic に更新する。
  - existing enabled / watchlist / pool を入力にし、`data/*.parquet` の rows と liquidity を使う。
  - `settings.curation.max_universe` は Phase 2 rollout 時に script が変更する。
- [ ] `docs/curation/universe_selection_latest.json` を出力する。
- [ ] `tests/test_universe.py` を追加する。

**Acceptance:**
- 30銘柄以上の候補がある場合、sector cap を守った target universe が生成される。
- 候補不足なら `insufficient_universe` を返し、`tickers.yml` は変更しない。
- `--apply` の再実行は idempotent。
- agent/LLM が `tickers.yml` を手で編集しない。

### Task 3: cross-sectional panel builder

- [ ] `src/cross_section.py` を追加する。
  - `build_ticker_feature_frame(ticker, macro_panel, ticker_info)`
  - `build_panel(tickers, macro_panel, label_config)`
  - `add_cross_sectional_features(panel)`
  - `add_sector_features(panel)`
  - `add_liquidity_features(panel)`
  - `build_cs_labels(panel)`
- [ ] 日付内特徴量を追加する。
  - z-score: return/RSI/MACD/ATR/volume/liquidity 系
  - percentile rank: score が外れ値に強いよう併用
  - sector-relative rank: 同一sector内での相対強さ
  - raw macro features: 全銘柄共通なので日付内正規化しない
- [ ] label は次を持つ。
  - `fwd_return`
  - `target_vol_norm = fwd_return / volatility`
  - `target_rank_bucket` = 日付内 forward return の 0..4 bucket
  - `target_up`
- [ ] `tests/test_cross_section.py` を追加する。

**Acceptance:**
- 同一日内で future data を使わずに z-score/rank が作られる。
- 日付ごとの group size が `TRADER_CS_MIN_DAILY_NAMES` 未満なら training から落ちる。
- missing macro / sector / liquidity でも panel build は止まらない。

### Task 4: cross-sectional model training

- [ ] `src/cs_model.py` を追加する。
  - `train_cs_model(panel, config)`
  - `predict_cs_model(bundle, latest_panel)`
  - `fit_score_calibration(oos_predictions)`
  - `cs_metrics(oos_predictions, top_n)`
- [ ] objective は env で選択する。
  - `ranker`: LightGBM `lambdarank`、group = date、label = `target_rank_bucket`
  - `regression`: LightGBM regression、target = `target_vol_norm`
  - group size 不足時は regression fallback を許可する。
- [ ] OOS metrics を計算する。
  - daily IC / rank IC
  - precision@N
  - top-bottom spread
  - hit_rate@N
  - Brier / score bucket up probability
  - turnover estimate
- [ ] `src/model_store.py` に CS bundle 保存/読込と `active_cs_model.json` 読み書きを追加する。
- [ ] `scripts/weekly_cross_section_retrain.py` を追加する。
  - macro update 済み panel を構築
  - artifact 保存
  - `model_registry` 登録
  - `docs/cs_model_quality.json` 出力

**Acceptance:**
- `data/models/cs-v1-YYYYMMDD/` が生成される。
- `model_registry.kind` が `cross_sectional_*` の active version になる。
- OOS daily IC / precision@N / top-bottom spread が report に出る。
- 学習失敗時も既存 Phase 1 artifact は壊さない。

### Task 5: daily cross-sectional inference

- [ ] `main.py` に Phase 2 inference context を追加する。
  - `TRADER_PORTFOLIO_ENABLED=false`: skip
  - `shadow`: predictions / portfolio JSON は生成、既存通知は維持
  - `active`: portfolio KPI gate 通過時に `signals.target_weight` を反映
- [ ] active CS bundle がある場合、当日 enabled universe の latest panel を作る。
- [ ] 全銘柄の score を rank 化し、`predictions` に `cs_rank` / `expected_ret` / `prob_up` を保存する。
- [ ] DB 不通時は `docs/portfolio_latest.json` だけ best-effort に出す。

**Acceptance:**
- active CS model が無い場合、daily は Phase 1 path で成功する。
- active CS model がある場合、毎日 LightGBM を再学習せず inference のみで動く。
- `predictions.model_version` が `cs-v1-*`、`cs_rank` が 1..N で保存される。

### Task 6: portfolio construction

- [ ] `src/portfolio.py` を追加する。
  - `select_candidates(predictions, top_n, min_expected_ret)`
  - `estimate_covariance(price_frames, lookback_days)`
  - `initial_inverse_vol_weights(candidates)`
  - `apply_name_cap(weights, max_name_weight)`
  - `apply_sector_cap(weights, sectors, sector_cap)`
  - `scale_to_target_vol(weights, covariance, target_vol, max_gross, regime_multiplier)`
  - `apply_hysteresis(new_weights, prev_weights, notrade_band, min_weight)`
  - `build_portfolio_snapshot(...)`
  - `diff_positions(prev, current)`
- [ ] limit/stop は現行 `predictor.generate_signal()` の考え方を踏襲しつつ、target_weight付き position に出す。
- [ ] risk_off regime では gross を `TRADER_PORTFOLIO_RISK_OFF_GROSS_MULT` で段階縮小する。
- [ ] `tests/test_portfolio.py` を追加する。

**Acceptance:**
- per-name / sector / gross / min weight / no-trade band を満たす。
- covariance が足りない場合は diagonal volatility fallback。
- 全候補が不適格でも cash portfolio として成功する。

### Task 7: portfolio-level backtest / KPI gate

- [ ] `src/portfolio_backtest.py` を追加する。
  - walk-forward で OOS prediction -> portfolio build -> daily PnL を計算
  - turnover cost = exposure change × `(cost_bps + slippage_bps)`
  - TOPIX benchmark return を macro panel から取得
  - equity / benchmark equity / drawdown を出す
- [ ] metrics を実装する。
  - CAGR / Sharpe / Sortino / MaxDD / Calmar
  - turnover / average gross / capacity proxy
  - alpha / beta / information ratio / tracking error
  - hit_rate@portfolio / topN realized return
- [ ] `src/backtest.py` から portfolio KPI gate wrapper を呼べるようにする。
- [ ] `backtest_runs` / `backtest_equity` へ保存する DB I/O を追加する。
- [ ] `docs/portfolio_backtest.json` を出力する。
- [ ] `tests/test_portfolio_backtest.py` を追加する。

**Acceptance:**
- 30銘柄以上の historical panel で backtest が完走する。
- KPI gate は MaxDD / Sharpe / IR / turnover を判定する。
- DB 不通でも JSON report は出る。

### Task 8: portfolio DB write-through と dashboard export

- [ ] `src/db_records.py` に portfolio snapshot row mapping を追加する。
- [ ] `src/db.py` に以下を追加する。
  - `upsert_portfolio_snapshot(conn, row)`
  - `fetch_latest_portfolio_snapshot(conn)`
  - `insert_backtest_run(conn, row, equity_rows)`
- [ ] `src/dashboard.py` に `export_portfolio_latest()` / `export_portfolio_backtest()` を追加する。
- [ ] `web/src/types/index.ts` に型追加。
- [ ] `web/src/components/PortfolioCard.tsx` を追加する。
- [ ] `web/src/app/page.tsx` へ差し込む。

**Acceptance:**
- `docs/portfolio_latest.json` が無い/available false の場合、UI は非表示。
- available true の場合、上位建玉、gross、expected vol、diff summary が見える。
- `npm run lint --prefix web` / `npm run build:prod --prefix web` が通る。

### Task 9: workflow rollout

- [ ] `.github/workflows/weekly-model-retrain.yml`
  - `scripts/update_macro_snapshots.py`
  - `scripts/universe_select.py` report
  - `scripts/weekly_cross_section_retrain.py`
  - `data/models/`, `docs/cs_model_quality.json`, `docs/portfolio_backtest.json` を commit 対象に追加
- [ ] `.github/workflows/daily-preopen-core.yml`
  - Phase 2 env を追加
  - `TRADER_PORTFOLIO_ENABLED=true`
  - 初期は `TRADER_PORTFOLIO_MODE=shadow`
- [ ] `.github/workflows/daily-watchdog.yml`
  - portfolio-level drift / KPI warning を読む

**Acceptance:**
- 初回 artifact なしでも daily は Phase 1 fallback で成功する。
- weekly retrain 後、shadow portfolio JSON が毎営業日出る。
- active rollout は `TRADER_PORTFOLIO_MODE=active` の env 変更だけで可能。

### Task 10: shadow validation と active化判定

- [ ] shadow mode を最低10営業日運用し、`portfolio_snapshots` と realized outcomes を蓄積する。
- [ ] Phase 1 per-ticker と Phase 2 CS/portfolio を比較する。
  - daily IC
  - topN realized return
  - turnover cost
  - drawdown
  - hit_rate / expected_ret calibration
- [ ] `docs/portfolio_shadow_report.json` を追加する。
- [ ] active化条件を満たしたら `.github/workflows/daily-preopen-core.yml` の env を `active` へ変更する。

**Acceptance:**
- shadow期間の realized topN / IR が Phase 1 比で同等以上。
- portfolio KPI gate が通る。
- active化しても DB/JSON/dashboard の既存契約が壊れない。

---

## 6. Verification

DB なしローカルで通すもの:

```bash
uv run python tests/test_universe.py
uv run python tests/test_cross_section.py
uv run python tests/test_portfolio.py
uv run python tests/test_portfolio_backtest.py
uv run python tests/test_labels.py
uv run python tests/test_calibration.py
uv run python tests/test_macro_features.py
uv run python tests/test_model_store.py
uv run python tests/test_db_records.py
uv run python tests/test_drift_check.py
uv run python tests/test_curation_merge.py
uv run python -c "import yaml; [yaml.safe_load(open(p)) for p in ['.github/workflows/daily-preopen-core.yml','.github/workflows/weekly-model-retrain.yml','.github/workflows/daily-watchdog.yml']]; print('workflow YAML OK')"
TRADER_DB_ENABLED=false uv run python scripts/db_migrate.py --dry-run
TRADER_DB_ENABLED=false uv run python scripts/universe_select.py --target-size 40
TRADER_DB_ENABLED=false uv run python scripts/weekly_cross_section_retrain.py --dry-run
npm run lint --prefix web
npm run build:prod --prefix web
```

DB あり staging/production 相当で通すもの:

```bash
uv run python scripts/db_migrate.py
uv run python scripts/update_macro_snapshots.py --as-of 2026-06-09
uv run python scripts/universe_select.py --target-size 40
uv run python scripts/weekly_cross_section_retrain.py --output docs/cs_model_quality.json
RUN_DATE_JST=2026-06-09 TRADER_PORTFOLIO_ENABLED=true TRADER_PORTFOLIO_MODE=shadow uv run python main.py
uv run python scripts/settle_outcomes.py --as-of 2026-06-09
uv run python scripts/drift_check.py --as-of 2026-06-09
```

---

## 7. Acceptance Criteria

- enabled universe が 30〜50 銘柄に拡張され、sector cap と warmup guardrail を満たす。
- `model_registry` に `cs-v1-YYYYMMDD` が週次で積まれ、cross-sectional kind 内の active version が1つだけになる。
- 日次パイプラインは active CS artifact がある場合に inference のみで動き、`predictions.cs_rank` と `expected_ret` が埋まる。
- portfolio snapshot が毎営業日生成され、per-name / sector / gross / volatility / hysteresis constraints を満たす。
- portfolio walk-forward が TOPIX 比で許容 MaxDD、正のIRまたは同等以上のrisk-adjusted return、現実的turnoverを示す。
- `docs/portfolio_latest.json` と最小 dashboard card に「今日の建玉」＋差分が出る。
- Phase 2 disabled/shadow/fallback 時に Phase 1 daily signal と dashboard 既存契約が壊れない。

---

## 8. Rollback

- 即時 rollback は `TRADER_PORTFOLIO_ENABLED=false`。
- shadow へ戻す場合は `TRADER_PORTFOLIO_MODE=shadow`。
- active CS model だけ無効化する場合は `data/models/active_cs_model.json` を退避する。Phase 1 の `data/models/active_model.json` はそのまま使う。
- universe を戻す場合は deterministic script の report を使い、`scripts/universe_select.py --target-size 10 --apply` または `curation_merge.py` の guardrail 経由で縮小する。
- DB schema は前方互換の追加のみ。`portfolio_snapshots` / `backtest_*` は削除しない。
- portfolio card は `available=false` で非表示にするため、JSON欠損でも UI は壊れない。

---

## 9. Phase 3 へ送るもの

- LINE の日次 portfolio digest。
- 約定/fills 管理と提案 vs 実約定の乖離計測。
- 詳細な portfolio dashboard（資産曲線、sector exposure、drawdown、注文差分）。
- 空データ/JSON schema validation の強化。
- broker CSV/API への発注指示出力。
