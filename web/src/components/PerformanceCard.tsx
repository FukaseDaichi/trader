"use client";

import { useEffect, useState } from "react";
import { PerformanceSummary } from "../types";

export default function PerformanceCard() {
  const [perf, setPerf] = useState<PerformanceSummary | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${basePath}/performance_summary.json`)
      .then((res) => (res.ok ? res.json() : null))
      .then((json: PerformanceSummary | null) => setPerf(json))
      .catch(() => setPerf(null));
  }, []);

  // Render nothing until the file is available with data (Phase 0 is best-effort).
  if (!perf || !perf.available || !perf.horizons) return null;

  const h5 = perf.horizons["5"];
  const curve = perf.equity_curve || [];
  const cumReturn = curve.length ? curve[curve.length - 1].equity - 1 : null;

  const pct = (v: number | null | undefined) =>
    v == null ? "---" : `${(v * 100).toFixed(1)}%`;

  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <h3 className="text-lg font-bold text-white mb-1">実績トラックレコード（計測中）</h3>
      <p className="text-xs text-slate-400 mb-4">
        実際に出した買い系シグナル（BUY / やや買い）の実現結果です。サンプルが貯まるほど信頼度が上がります。
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">的中率(5日)</div>
          <div className="text-2xl font-bold text-emerald-300">{pct(h5?.hit_rate)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">平均リターン(5日)</div>
          <div className="text-2xl font-bold text-blue-300">{pct(h5?.avg_return)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">累積(1日複利)</div>
          <div className="text-2xl font-bold text-white">{pct(cumReturn)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">サンプル数</div>
          <div className="text-2xl font-bold text-slate-200">{perf.n_long_signals ?? 0}</div>
        </div>
      </div>
    </section>
  );
}
