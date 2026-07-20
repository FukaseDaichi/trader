-- Executable-price outcome contract (DR-002).
--
-- Existing rows were measured from the signal market date's close.  Preserve
-- that provenance explicitly before new settlements switch to the first
-- executable next-session open.  The legacy entry_close/exit_close columns
-- remain populated for backward compatibility while callers migrate to the
-- unambiguous entry_price/exit_price names.

ALTER TABLE signal_outcomes
  ADD COLUMN IF NOT EXISTS market_as_of_date DATE,
  ADD COLUMN IF NOT EXISTS entry_price DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS entry_price_basis TEXT,
  ADD COLUMN IF NOT EXISTS exit_price_basis TEXT,
  ADD COLUMN IF NOT EXISTS contract_version TEXT,
  ADD COLUMN IF NOT EXISTS benchmark_basis TEXT;

UPDATE signal_outcomes
SET market_as_of_date = COALESCE(market_as_of_date, entry_date),
    entry_price = COALESCE(entry_price, entry_close),
    exit_price = COALESCE(exit_price, exit_close),
    entry_price_basis = COALESCE(entry_price_basis, 'market_as_of_close'),
    exit_price_basis = COALESCE(exit_price_basis, 'horizon_session_close'),
    contract_version = COALESCE(contract_version, 'close_to_close_v1'),
    benchmark_basis = COALESCE(
      benchmark_basis,
      CASE
        WHEN benchmark_ret IS NULL THEN 'unavailable'
        ELSE 'market_as_of_close_to_horizon_close'
      END
    );

ALTER TABLE signal_outcomes
  ALTER COLUMN market_as_of_date SET NOT NULL,
  ALTER COLUMN entry_price_basis SET NOT NULL,
  ALTER COLUMN exit_price_basis SET NOT NULL,
  ALTER COLUMN contract_version SET NOT NULL,
  ALTER COLUMN benchmark_basis SET NOT NULL,
  ALTER COLUMN entry_price_basis SET DEFAULT 'market_as_of_close',
  ALTER COLUMN exit_price_basis SET DEFAULT 'horizon_session_close',
  ALTER COLUMN contract_version SET DEFAULT 'close_to_close_v1',
  ALTER COLUMN benchmark_basis SET DEFAULT 'unavailable';

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_contract
  ON signal_outcomes (contract_version, entry_date);
