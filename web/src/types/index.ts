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
