"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { PerformanceSummary } from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";
import Term from "./Term";

export default function PerformanceCard() {
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
    <section className="mb-8 rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <div className="mb-1 flex items-center justify-between">
        <h2 className="text-lg font-bold text-white">AIの成績</h2>
        <Link href="/performance" className="text-sm text-blue-400 hover:underline">
          くわしく見る →
        </Link>
      </div>
      <p className="mb-4 text-xs text-slate-400">
        実際に出した買いサインのあとに株価がどうなったかの記録です。サンプルが貯まるほど信頼度が上がります。
      </p>
      <div className="grid grid-cols-3 gap-4">
        <div>
          <div className="mb-1 text-xs text-slate-500">
            <Term k="hit_rate">的中率</Term>
            <span className="ml-1">(5日後)</span>
          </div>
          <div className="text-2xl font-bold text-slate-100">{pct(h5?.hit_rate)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">
            <Term k="equity_curve">通算リターン</Term>
          </div>
          <div className="text-2xl font-bold text-slate-100">{pct(cumReturn)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">サイン回数</div>
          <div className="text-2xl font-bold text-slate-200">{perf.n_long_signals ?? 0}</div>
        </div>
      </div>
    </section>
  );
}
