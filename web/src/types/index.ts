export interface TickerData {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  return_1d?: number;
  ma_5?: number;
  ma_20?: number;
  ma_60?: number;
  rsi?: number;
  volatility?: number;
}

export interface TickerInfo {
  name: string;
  data: TickerData[];
}

export interface Signal {
  ticker: string;
  name: string;
  date: string;
  close: number;
  prob_up: number;
  action: "BUY" | "MILD_BUY" | "HOLD" | "MILD_SELL" | "SELL";
  reason: string;
  limit_price?: number;
  stop_loss?: number;
}

export interface HistoryEntry {
  date: string;
  signals: Signal[];
}

export interface HistoryData {
  last_update: string;
  tickers: Record<string, TickerInfo>;
  signals_history: HistoryEntry[];
}
