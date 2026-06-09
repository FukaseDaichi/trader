-- Phase 2 schema. Cross-sectional portfolio bookkeeping.
-- Forward-compatible additions only; Phase 0/1 tables are untouched.

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
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

CREATE TABLE IF NOT EXISTS backtest_runs (
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

CREATE TABLE IF NOT EXISTS backtest_equity (
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

CREATE TABLE IF NOT EXISTS universe_snapshots (
  run_date        DATE PRIMARY KEY,
  target_size     INT NOT NULL,
  selected_size   INT NOT NULL,
  status          TEXT NOT NULL,          -- ok | insufficient_universe
  members         JSONB NOT NULL,         -- [{code, name, sector, liquidity, combined, rows}]
  sector_exposure JSONB,
  diff_from_prev  JSONB,
  warnings        JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_as_of ON portfolio_snapshots (as_of_date);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_run ON backtest_runs (run_date);
CREATE INDEX IF NOT EXISTS idx_universe_snapshots_run ON universe_snapshots (run_date);
