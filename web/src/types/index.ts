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

export interface Signal {
  ticker: string;
  name: string;
  date: string;
  close: number;
  prob_up: number;
  action: SignalAction;
  reason: string;
  limit_price?: number | null;
  stop_loss?: number | null;
  raw_action?: SignalAction;
  gate_passed?: boolean;
  confidence_label?: string;
  confidence_reason?: string;
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
