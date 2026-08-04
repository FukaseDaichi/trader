export interface TickerData {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  ma_5?: number | null;
  ma_20?: number | null;
  ma_60?: number | null;
  rsi?: number | null;
}

export type SignalAction = "BUY" | "MILD_BUY" | "HOLD" | "MILD_SELL" | "SELL";

export interface SignalThresholds {
  buy: number;
  mild_buy: number;
  mild_sell: number;
  sell: number;
  volatility_limit: number;
}

export interface ExitPlan {
  take_profit_price: number;
  stop_price: number;
  take_profit_pct: number;
  stop_pct: number;
  time_exit_days: number;
  atr: number;
  tp_atr_mult: number;
  sl_atr_mult: number;
}

export interface Signal {
  ticker: string;
  name: string;
  date: string;
  close: number | null;
  prob_up: number | null;
  action: SignalAction;
  reason: string;
  limit_price?: number | null;
  stop_loss?: number | null;
  take_profit_price?: number | null;
  stop_price?: number | null;
  take_profit_pct?: number | null;
  stop_pct?: number | null;
  time_exit_days?: number | null;
  exit_plan?: ExitPlan | null;
  raw_action?: SignalAction;
  gate_passed?: boolean;
  confidence_label?: string;
  confidence_reason?: string;
  thresholds?: SignalThresholds | null;
  threshold_optimization?: Record<string, unknown> | null;
  status?: "ok" | "failed";
  error?: string | null;
}

export interface TickerSignalHistoryEntry {
  date: string;
  signal: Signal;
}

export interface TickerSummary {
  ticker: string;
  name: string;
  latest_data: TickerData | null;
  avg_volume_20: number | null;
  prev_close?: number | null;
  change_pct?: number | null;
  latest_signal: Signal | null;
  data_file: string;
  rows: number;
}

export interface DashboardIndexData {
  last_update: string;
  tickers: Record<string, TickerSummary>;
}

export interface TickerDetailData {
  last_update: string;
  ticker: string;
  name: string;
  latest_signal: Signal | null;
  signals: TickerSignalHistoryEntry[];
  data: TickerData[];
}

export interface PerformanceHorizon {
  count: number;
  hit_rate: number | null;
  avg_return: number | null;
}

export interface ExecutionContractMetadata {
  contract_version: string;
  market_as_of_basis?: string;
  decision_timing?: string;
  entry_price_basis: string;
  exit_price_basis: string;
  benchmark_basis?: string;
  return_basis?: string;
  cost_treatment?: string;
  cost_bps_per_side?: number;
  slippage_bps_per_side?: number;
}

export interface AccountingMethodMetadata {
  name: string;
  selection: string;
  horizon_days?: number;
  fallback_reason?: string | null;
  eligible_cohorts?: number;
  selected_cohorts?: number;
  overlapping_horizon_returns_compounded: boolean;
  capital_per_cohort?: number;
  return_basis?: string;
  gross_return_source?: string;
  cost_model?: string;
  benchmark_return_basis?: string;
  benchmark_cost_model?: string;
  cost_bps_per_side?: number;
  slippage_bps_per_side?: number;
  round_trip_cost_rate?: number;
}

export interface ContractCoverage {
  required_contract_version: string;
  source_counts: Record<string, number>;
  included_rows: number;
  excluded_rows: number;
  fallback_assumption: string | null;
}

export interface BenchmarkCoverage {
  basis: string;
  selected_cohorts: number;
  available_cohorts: number;
  coverage_ratio: number | null;
  reason: string | null;
}

export interface PerformanceSummary {
  available: boolean;
  reason?: string;
  generated_at: string;
  as_of?: string;
  execution_contract?: ExecutionContractMetadata;
  accounting_method?: AccountingMethodMetadata;
  n_long_signals?: number;
  horizons?: Record<string, PerformanceHorizon>;
  equity_curve?: {
    date: string;
    equity: number;
    gross_daily_return?: number;
    cost_return?: number;
    net_daily_return?: number;
    daily_return: number;
    n: number;
  }[];
  db_size_mb?: number;
  storage_warning?: boolean;
}

export interface ModelQualityTicker {
  brier: number | null;
  brier_raw?: number | null;
  ic: number | null;
  auc?: number | null;
  calibration_rows?: number | null;
  psi_max?: number | null;
  warning?: boolean;
}

export interface ModelQuality {
  available: boolean;
  reason?: string;
  generated_at: string;
  active_model_version?: string;
  horizon_days?: number | null;
  summary?: {
    tickers: number;
    median_brier: number | null;
    median_ic: number | null;
    drift_warning: boolean;
  };
  by_ticker?: Record<string, ModelQualityTicker>;
}

export interface EquityPoint {
  date: string;
  entry_date?: string;
  strategy: number;
  benchmark: number | null;
  gross_period_return?: number;
  cost_return?: number;
  period_return?: number;
  gross_benchmark_return?: number | null;
  benchmark_return?: number | null;
  n: number;
}
export interface DrawdownPoint { date: string; drawdown: number; }
export interface ReliabilityBin { bin_low: number; bin_high: number; mean_prob: number | null; frac_up: number | null; count: number; }
export interface PerformanceDetail {
  available: boolean; reason?: string; generated_at: string; as_of?: string;
  horizon_days?: number; history_days?: number;
  execution_contract?: ExecutionContractMetadata;
  contract_coverage?: ContractCoverage;
  accounting_method?: AccountingMethodMetadata;
  benchmark_coverage?: BenchmarkCoverage;
  benchmark_unavailable_reason?: string | null;
  signal_quality?: {
    overlapping_samples_allowed: boolean;
    compounded_into_equity: boolean;
    metrics: string[];
  };
  equity_curve?: EquityPoint[]; drawdown_curve?: DrawdownPoint[];
  rolling?: {
    hit_rate_20d: number | null;
    avg_return_20d: number | null;
    excess_return_20d: number | null;
    sharpe_60d: number | null;
    sharpe_observations?: number;
    sharpe_annualization_periods?: number;
    sharpe_selection?: string;
    sharpe_fallback_reason?: string | null;
    sharpe_return_basis?: string;
    sharpe_round_trip_cost_rate?: number;
  };
  reliability?: {
    brier: number | null;
    bins: ReliabilityBin[];
    provenance?: {
      phase: "phase1";
      source: "signals.prediction_id";
      candidate_signal_count: number;
      observation_count: number;
      linked_prediction_count: number;
      conviction_fallback_count: number;
      excluded_count: number;
      exclusions: Record<string, number>;
      model_versions: { model_version: string; count: number }[];
      first_entry_date: string | null;
      last_entry_date: string | null;
      outcome_contract_versions: string[];
      compatibility_contract: Record<string, unknown> | null;
      fallback_contract_assumption: string | null;
    };
  };
}
export interface SignalOutcomeRow {
  market_as_of_date: string | null; entry_date: string; eval_date: string | null;
  ticker: string; name: string | null; action: SignalAction;
  conviction: number | null; horizon_days: number; realized_ret: number | null;
  benchmark_ret: number | null; excess_ret: number | null; hit: boolean | null;
  mae: number | null; mfe: number | null; exit_reason: string | null;
  entry_price: number | null; exit_price: number | null;
  entry_price_basis: string | null; exit_price_basis: string | null;
  contract_version: string | null; benchmark_basis: string | null;
}
export interface SignalOutcomesRecent {
  available: boolean;
  reason?: string;
  generated_at: string;
  execution_contract?: ExecutionContractMetadata;
  contract_coverage?: ContractCoverage;
  rows?: SignalOutcomeRow[];
}
export interface MacroLatest { market_bias?: string; as_of?: string; summary?: string; }

export type PortfolioDiffType = "new" | "increase" | "decrease" | "exit" | "hold";

export interface PortfolioPosition {
  ticker: string;
  name: string | null;
  sector: string | null;
  target_weight: number;
  prev_weight: number;
  diff_type: PortfolioDiffType;
  cs_rank: number | null;
  expected_ret: number | null;
  prob_up: number | null;
  volatility: number | null;
  limit_price: number | null;
  stop_loss: number | null;
}

export interface PortfolioLatest {
  available: boolean;
  reason?: string;
  generated_at?: string;
  run_date?: string;
  as_of_date?: string;
  mode?: "shadow" | "active";
  status?: "ok" | "failed";
  model_version?: string;
  gross_exposure?: number;
  net_exposure?: number;
  expected_vol?: number;
  expected_ret?: number;
  sector_exposure?: Record<string, number>;
  diff_summary?: { add: number; trim: number; exit: number; hold: number };
  positions?: PortfolioPosition[];
  warnings?: string[];
  constraints?: Record<string, unknown>;
}
