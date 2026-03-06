import type { Signal, SignalAction } from "../types";

/** 5-level action → Japanese label */
export function actionLabel(action: SignalAction): string {
  switch (action) {
    case "BUY":
      return "買い";
    case "MILD_BUY":
      return "やや買い";
    case "HOLD":
      return "HOLD";
    case "MILD_SELL":
      return "やや売り";
    case "SELL":
      return "売り";
  }
}

/** Badge background + text colour classes */
export function actionBadgeClass(action: SignalAction): string {
  switch (action) {
    case "BUY":
      return "bg-red-500/20 text-red-400";
    case "MILD_BUY":
      return "bg-orange-500/20 text-orange-400";
    case "HOLD":
      return "bg-slate-700 text-slate-300";
    case "MILD_SELL":
      return "bg-cyan-500/20 text-cyan-400";
    case "SELL":
      return "bg-green-500/20 text-green-400";
  }
}

/** Text colour class for inline signal text */
export function actionTextClass(action: SignalAction): string {
  switch (action) {
    case "BUY":
      return "text-red-400";
    case "MILD_BUY":
      return "text-orange-400";
    case "HOLD":
      return "text-slate-400";
    case "MILD_SELL":
      return "text-cyan-400";
    case "SELL":
      return "text-green-400";
  }
}

/** Card border / background classes for SignalCard */
export function actionCardClass(action: SignalAction): string {
  switch (action) {
    case "BUY":
      return "bg-red-900/20 border-red-500/50";
    case "MILD_BUY":
      return "bg-orange-900/20 border-orange-500/50";
    case "HOLD":
      return "bg-slate-800 border-slate-700";
    case "MILD_SELL":
      return "bg-cyan-900/20 border-cyan-500/50";
    case "SELL":
      return "bg-green-900/20 border-green-500/50";
  }
}

/** Probability colour for the overview card */
export function probTextClass(prob: number): string {
  if (prob >= 0.80) return "text-red-400";
  if (prob >= 0.65) return "text-orange-400";
  if (prob <= 0.10) return "text-green-400";
  if (prob <= 0.25) return "text-cyan-400";
  return "text-slate-300";
}

export function confidenceLabel(signal: Signal): string {
  if (signal.confidence_label) return signal.confidence_label;
  if (signal.gate_passed === true) return "自信あり";
  if (signal.gate_passed === false) return "自信なし";
  if ((signal.reason || "").includes("KPI")) return "自信なし";
  return "判定なし";
}

export function confidenceBadgeClass(signal: Signal): string {
  const label = confidenceLabel(signal);
  if (label === "自信あり") return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
  if (label === "自信なし") return "bg-amber-500/20 text-amber-300 border-amber-500/40";
  return "bg-slate-700 text-slate-300 border-slate-600";
}
