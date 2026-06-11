import type { Signal, SignalAction } from "../types";

export interface ActionStyle {
  label: string;
  badgeClass: string;
  textClass: string;
  cardClass: string;
  rowTintClass: string;
  barColor: string;
}

/**
 * 表示トークンの単一ソース。
 * 配色ルール: 買い=赤系 / やや買い=橙 / 様子見=グレー / やや売り=シアン / 売り=青系。
 * 緑(emerald)は「成績テスト合格」など健全性の意味専用(売りには使わない)。
 */
export const ACTION_STYLE: Record<SignalAction, ActionStyle> = {
  BUY: {
    label: "買い",
    badgeClass: "bg-red-500/20 text-red-300",
    textClass: "text-red-300",
    cardClass: "bg-red-900/20 border-red-500/50",
    rowTintClass: "bg-red-500/5",
    barColor: "#ef4444",
  },
  MILD_BUY: {
    label: "やや買い",
    badgeClass: "bg-orange-500/20 text-orange-300",
    textClass: "text-orange-300",
    cardClass: "bg-orange-900/20 border-orange-500/50",
    rowTintClass: "bg-orange-500/5",
    barColor: "#f97316",
  },
  HOLD: {
    label: "様子見",
    badgeClass: "bg-slate-700/80 text-slate-300",
    textClass: "text-slate-400",
    cardClass: "bg-slate-800 border-slate-700",
    rowTintClass: "",
    barColor: "#475569",
  },
  MILD_SELL: {
    label: "やや売り",
    badgeClass: "bg-cyan-500/20 text-cyan-300",
    textClass: "text-cyan-300",
    cardClass: "bg-cyan-900/20 border-cyan-500/50",
    rowTintClass: "bg-cyan-500/5",
    barColor: "#06b6d4",
  },
  SELL: {
    label: "売り",
    badgeClass: "bg-blue-500/20 text-blue-300",
    textClass: "text-blue-300",
    cardClass: "bg-blue-900/20 border-blue-500/50",
    rowTintClass: "bg-blue-500/5",
    barColor: "#3b82f6",
  },
};

export function actionLabel(action: SignalAction): string {
  return ACTION_STYLE[action].label;
}

export function actionBadgeClass(action: SignalAction): string {
  return ACTION_STYLE[action].badgeClass;
}

export function actionTextClass(action: SignalAction): string {
  return ACTION_STYLE[action].textClass;
}

export function actionCardClass(action: SignalAction): string {
  return ACTION_STYLE[action].cardClass;
}

export function isBuySide(action: SignalAction): boolean {
  return action === "BUY" || action === "MILD_BUY";
}

export function isSellSide(action: SignalAction): boolean {
  return action === "SELL" || action === "MILD_SELL";
}

/** 「今日のAI判断」ヒーロー掲載条件(LINE digest と同じ): 成績テスト合格 かつ 様子見以外 */
export function isActionable(signal: Signal | null | undefined): boolean {
  return !!signal && signal.gate_passed === true && signal.action !== "HOLD";
}

/** 確率の文字色。買い寄り=赤系 / 売り寄り=青系。 */
export function probTextClass(prob: number | null | undefined): string {
  if (prob == null) return "text-slate-400";
  if (prob >= 0.8) return "text-red-300";
  if (prob >= 0.65) return "text-orange-300";
  if (prob <= 0.1) return "text-blue-300";
  if (prob <= 0.25) return "text-cyan-300";
  return "text-slate-300";
}

export function confidenceLabel(signal: Signal): string {
  if (signal.confidence_label) return signal.confidence_label;
  if (signal.gate_passed === true) return "自信あり";
  if (signal.gate_passed === false) return "自信なし";
  if ((signal.reason || "").includes("KPI")) return "自信なし";
  return "判定なし";
}

/** 成績テスト(KPIゲート)の結果をやさしい言葉のピル用トークンで返す */
export function gateLabel(signal: Signal): { text: string; className: string } {
  const label = confidenceLabel(signal);
  if (label === "自信あり") {
    return {
      text: "成績テスト合格",
      className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    };
  }
  if (label === "自信なし") {
    return {
      text: "成績テスト不合格",
      className: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    };
  }
  return {
    text: "成績テスト対象外",
    className: "bg-slate-700 text-slate-300 border-slate-600",
  };
}

export function formatProbability(prob: number | null | undefined): string {
  return prob == null ? "---" : `${(prob * 100).toFixed(1)}%`;
}

export function formatPrice(v: number | null | undefined): string {
  return v == null ? "---" : `¥${v.toLocaleString()}`;
}

export function formatChangePct(v: number | null | undefined): string {
  if (v == null) return "---";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

/** 前日比の文字色。日本式: 上昇=赤 / 下落=青。 */
export function changeTextClass(v: number | null | undefined): string {
  if (v == null) return "text-slate-400";
  if (v > 0) return "text-red-300";
  if (v < 0) return "text-blue-300";
  return "text-slate-300";
}
