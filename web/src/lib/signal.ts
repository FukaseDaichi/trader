import type { Signal } from "../types";

/** 5-level action → Japanese label */
export function actionLabel(action: Signal["action"]): string {
  switch (action) {
    case "BUY":       return "買い";
    case "MILD_BUY":  return "やや買い";
    case "HOLD":      return "HOLD";
    case "MILD_SELL": return "やや売り";
    case "SELL":      return "売り";
  }
}

/** Badge background + text colour classes */
export function actionBadgeClass(action: Signal["action"]): string {
  switch (action) {
    case "BUY":       return "bg-red-500/20 text-red-400";
    case "MILD_BUY":  return "bg-orange-500/20 text-orange-400";
    case "HOLD":      return "bg-slate-700 text-slate-300";
    case "MILD_SELL": return "bg-cyan-500/20 text-cyan-400";
    case "SELL":      return "bg-green-500/20 text-green-400";
  }
}

/** Text colour class for inline signal text */
export function actionTextClass(action: Signal["action"]): string {
  switch (action) {
    case "BUY":       return "text-red-400";
    case "MILD_BUY":  return "text-orange-400";
    case "HOLD":      return "text-slate-400";
    case "MILD_SELL": return "text-cyan-400";
    case "SELL":      return "text-green-400";
  }
}

/** Card border / background classes for SignalCard */
export function actionCardClass(action: Signal["action"]): string {
  switch (action) {
    case "BUY":       return "bg-red-900/20 border-red-500/50";
    case "MILD_BUY":  return "bg-orange-900/20 border-orange-500/50";
    case "HOLD":      return "bg-slate-800 border-slate-700";
    case "MILD_SELL": return "bg-cyan-900/20 border-cyan-500/50";
    case "SELL":      return "bg-green-900/20 border-green-500/50";
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
