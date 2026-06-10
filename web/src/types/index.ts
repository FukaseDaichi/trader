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

export interface PerformanceSummary {
  available: boolean;
  reason?: string;
  generated_at: string;
  as_of?: string;
  n_long_signals?: number;
  horizons?: Record<string, PerformanceHorizon>;
  equity_curve?: { date: string; equity: number; daily_return: number; n: number }[];
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

export interface EquityPoint { date: string; strategy: number; benchmark: number; n: number; }
export interface DrawdownPoint { date: string; drawdown: number; }
export interface ReliabilityBin { bin_low: number; bin_high: number; mean_prob: number | null; frac_up: number | null; count: number; }
export interface PerformanceDetail {
  available: boolean; reason?: string; generated_at: string; as_of?: string;
  horizon_days?: number; history_days?: number;
  equity_curve?: EquityPoint[]; drawdown_curve?: DrawdownPoint[];
  rolling?: { hit_rate_20d: number | null; avg_return_20d: number | null; excess_return_20d: number | null; sharpe_60d: number | null; };
  reliability?: { brier: number | null; bins: ReliabilityBin[]; };
}
export interface SignalOutcomeRow {
  entry_date: string; ticker: string; name: string | null; action: SignalAction;
  conviction: number | null; horizon_days: number; realized_ret: number | null;
  benchmark_ret: number | null; excess_ret: number | null; hit: boolean | null;
  mae: number | null; mfe: number | null; exit_reason: string | null;
}
export interface SignalOutcomesRecent { available: boolean; reason?: string; generated_at: string; rows?: SignalOutcomeRow[]; }
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
