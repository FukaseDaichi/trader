-- Model-agnostic realized-return ledger for evaluation only.
--
-- signal_outcomes settles actionable signals only, so HOLD names have no
-- realized return and the Phase 1 vs Phase 2 shadow comparison / drift metrics
-- see only a few names per day.  This table records the same
-- next_session_open_to_close_v2 return for every (ticker, market date) that
-- has a prediction, independent of the action taken.
--
-- Evaluation only: the KPI gate, performance summaries and notifications never
-- read this table, and signal_outcomes is left untouched.

CREATE TABLE IF NOT EXISTS market_outcomes (
  ticker             TEXT NOT NULL,
  market_as_of_date  DATE NOT NULL,
  horizon_days       INT  NOT NULL,
  contract_version   TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_market_outcomes_asof
  ON market_outcomes (market_as_of_date);
