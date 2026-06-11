/** RSI を「過熱感」のやさしいラベルへ */
export function heatLabel(rsi: number | null | undefined): {
  text: string;
  className: string;
} {
  if (rsi == null) return { text: "---", className: "text-slate-500" };
  if (rsi >= 70) return { text: "過熱気味", className: "text-red-300" };
  if (rsi <= 30) return { text: "売られすぎ", className: "text-cyan-300" };
  if (rsi >= 55) return { text: "やや強い", className: "text-slate-300" };
  if (rsi <= 45) return { text: "やや弱い", className: "text-slate-300" };
  return { text: "ふつう", className: "text-slate-400" };
}

/** 出来高/20日平均 を「商いの活発さ」ラベルへ */
export function activityLabel(
  volume: number | null | undefined,
  avg20: number | null | undefined,
): { text: string; ratioText: string; className: string } {
  if (volume == null || avg20 == null || avg20 <= 0) {
    return { text: "---", ratioText: "---", className: "text-slate-500" };
  }
  const ratio = volume / avg20;
  const ratioText = `${ratio.toFixed(1)}x`;
  if (ratio >= 1.5) return { text: "かなり活発", ratioText, className: "text-amber-300" };
  if (ratio >= 1.1) return { text: "やや活発", ratioText, className: "text-slate-300" };
  if (ratio >= 0.8) return { text: "ふつう", ratioText, className: "text-slate-400" };
  return { text: "閑散", ratioText, className: "text-slate-500" };
}
