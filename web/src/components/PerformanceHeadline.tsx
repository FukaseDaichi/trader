"use client";

import { useEffect, useState } from "react";
import { PerformanceSummary } from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";
import Term from "./Term";

export default function PerformanceHeadline() {
  const [perf, setPerf] = useState<PerformanceSummary | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<PerformanceSummary>(
      `${basePath}/performance_summary.json`,
      (v): v is PerformanceSummary => isAvailablePayload(v),
    ).then(setPerf);
  }, []);

  if (!perf || !perf.available || !perf.horizons) return null;

  const h5 = perf.horizons["5"];
  const curve = perf.equity_curve || [];
  const cumReturn = curve.length ? curve[curve.length - 1].equity - 1 : null;
  const pct = (v: number | null | undefined) =>
    v == null ? "---" : `${(v * 100).toFixed(1)}%`;

  return (
    <section className="mb-8">
      <p className="mb-3 text-sm leading-relaxed text-slate-400">
        買いサインのあと実際どうだったか、の通算成績です。
        <Term k="topix">市場平均(TOPIX)</Term>と比べて意味があったかを見ます。
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
          <div className="mb-1 text-xs text-slate-500">
            <Term k="hit_rate">的中率</Term>
            <span className="ml-1">(5日後)</span>
          </div>
          <div className="text-3xl font-bold text-slate-100">{pct(h5?.hit_rate)}</div>
          <div className="mt-1 text-xs text-slate-500">サイン {h5?.count ?? 0} 回</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
          <div className="mb-1 text-xs text-slate-500">平均リターン(5日後)</div>
          <div className="text-3xl font-bold text-slate-100">{pct(h5?.avg_return)}</div>
          <div className="mt-1 text-xs text-slate-500">1回のサインあたり</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
          <div className="mb-1 text-xs text-slate-500">
            <Term k="equity_curve">通算リターン</Term>
          </div>
          <div className="text-3xl font-bold text-slate-100">{pct(cumReturn)}</div>
          <div className="mt-1 text-xs text-slate-500">シグナル通りに売買した場合</div>
        </div>
      </div>
    </section>
  );
}
