-- Phase 1 schema. Adds macro snapshots plus model-quality / drift bookkeeping.
-- Forward-compatible additions only; Phase 0 tables are untouched.
-- model_registry (already created in 0001) is reused as-is for Phase 1 versions.

-- Macro / regime snapshot (roadmap §4.3). `raw` holds the full computed
-- feature payload so we are not constrained by the typed columns.
CREATE TABLE IF NOT EXISTS macro_snapshots (
  date        DATE PRIMARY KEY,
  usdjpy      DOUBLE PRECISION,
  topix       DOUBLE PRECISION,
  nikkei      DOUBLE PRECISION,
  nikkei_vi   DOUBLE PRECISION,
  jgb10y      DOUBLE PRECISION,
  market_bias TEXT,                      -- qualitative bias from macro_latest.json
  regime      TEXT,                      -- risk_on | risk_off | neutral
  raw         JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-ticker model quality snapshot, written by the weekly retrain and/or
-- drift check. One row per (run_date, model_version, ticker, horizon_days).
CREATE TABLE IF NOT EXISTS model_quality_snapshots (
  run_date         DATE NOT NULL,
  model_version    TEXT NOT NULL,
  ticker           TEXT NOT NULL,
  horizon_days     INT  NOT NULL,
  brier            DOUBLE PRECISION,     -- calibrated Brier
  brier_raw        DOUBLE PRECISION,     -- uncalibrated Brier (for A/B)
  ic               DOUBLE PRECISION,     -- corr(score, forward return)
  auc              DOUBLE PRECISION,
  hit_rate         DOUBLE PRECISION,
  calibration_rows INT,
  psi_max          DOUBLE PRECISION,
  warning          BOOLEAN NOT NULL DEFAULT FALSE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_date, model_version, ticker, horizon_days)
);

-- Drift reports. One row per drift evaluation that breaches thresholds (or a
-- summary row per run); `metrics` carries the full computed payload.
CREATE TABLE IF NOT EXISTS drift_reports (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,
  model_version TEXT,
  scope         TEXT NOT NULL,           -- ticker code or 'portfolio' / 'global'
  status        TEXT NOT NULL,           -- ok | warning | insufficient_sample
  breached      BOOLEAN NOT NULL DEFAULT FALSE,
  metrics       JSONB NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_macro_snapshots_date ON macro_snapshots (date);
CREATE INDEX IF NOT EXISTS idx_model_quality_run ON model_quality_snapshots (run_date);
CREATE INDEX IF NOT EXISTS idx_drift_reports_run ON drift_reports (run_date);
