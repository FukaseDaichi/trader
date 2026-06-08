-- Phase 0 schema. Full roadmap §4.3 schema; Phase 0 only writes
-- tickers / model_registry / predictions / signals / signal_outcomes.

CREATE TABLE IF NOT EXISTS tickers (
  code        TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  sector      TEXT,
  enabled     BOOLEAN NOT NULL DEFAULT TRUE,
  source      TEXT,
  added_on    DATE,
  disabled_on DATE
);

CREATE TABLE IF NOT EXISTS model_registry (
  version      TEXT PRIMARY KEY,
  trained_at   TIMESTAMPTZ NOT NULL,
  kind         TEXT NOT NULL,
  universe     JSONB NOT NULL,
  feature_set  JSONB NOT NULL,
  params       JSONB NOT NULL,
  cv_metrics   JSONB NOT NULL,
  calibration  JSONB,
  artifact_uri TEXT,
  active       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS predictions (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,
  as_of_date    DATE NOT NULL,
  ticker        TEXT NOT NULL REFERENCES tickers(code),
  model_version TEXT NOT NULL REFERENCES model_registry(version),
  horizon_days  INT  NOT NULL,
  raw_score     DOUBLE PRECISION,
  prob_up       DOUBLE PRECISION,
  expected_ret  DOUBLE PRECISION,
  cs_rank       INT,
  features_hash TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_date, ticker, model_version, horizon_days)
);

CREATE TABLE IF NOT EXISTS signals (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,
  as_of_date    DATE NOT NULL,
  ticker        TEXT NOT NULL REFERENCES tickers(code),
  prediction_id BIGINT REFERENCES predictions(id),
  action        TEXT NOT NULL,
  raw_action    TEXT,
  conviction    DOUBLE PRECISION,
  target_weight DOUBLE PRECISION,
  thresholds    JSONB,
  gate_passed   BOOLEAN NOT NULL,
  limit_price   DOUBLE PRECISION,
  stop_loss     DOUBLE PRECISION,
  reason        TEXT,
  status        TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_date, ticker)
);

CREATE TABLE IF NOT EXISTS signal_outcomes (
  signal_id      BIGINT NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  horizon_days   INT NOT NULL,
  entry_date     DATE NOT NULL,
  eval_date      DATE NOT NULL,
  entry_close    DOUBLE PRECISION,
  exit_close     DOUBLE PRECISION,
  realized_ret   DOUBLE PRECISION,
  benchmark_ret  DOUBLE PRECISION,
  excess_ret     DOUBLE PRECISION,
  hit            BOOLEAN,
  mae            DOUBLE PRECISION,
  mfe            DOUBLE PRECISION,
  exit_reason    TEXT,
  PRIMARY KEY (signal_id, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_signals_as_of ON signals (as_of_date);
CREATE INDEX IF NOT EXISTS idx_predictions_run ON predictions (run_date);
