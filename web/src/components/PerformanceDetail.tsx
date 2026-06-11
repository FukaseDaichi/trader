"use client";

import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
} from "recharts";
import {
  PerformanceDetail as PerformanceDetailType,
  SignalOutcomesRecent,
} from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";
import Term from "./Term";
import { actionLabel } from "../lib/signal";

// ---- helpers ----------------------------------------------------------------

function pct(v: number | null | undefined, signed = false): string {
  if (v == null) return "—";
  const val = (v * 100).toFixed(2);
  return signed ? `${v >= 0 ? "+" : ""}${val}%` : `${val}%`;
}

// ---- sub-sections -----------------------------------------------------------

function NoData() {
  return <p className="text-slate-400 text-sm">データ蓄積中…</p>;
}

function RollingStats({ rolling }: { rolling: PerformanceDetailType["rolling"] }) {
  if (!rolling) return null;
  const stats = [
    { label: "的中率(直近20日)", value: pct(rolling.hit_rate_20d) },
    { label: "平均リターン(直近20日)", value: pct(rolling.avg_return_20d) },
    { label: "市場平均との差(直近20日)", value: pct(rolling.excess_return_20d) },
    { label: "安定度(60日)", value: rolling.sharpe_60d == null ? "—" : rolling.sharpe_60d.toFixed(2) },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      {stats.map(({ label, value }) => (
        <div key={label} className="bg-slate-800/60 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">{label}</div>
          <div className="text-lg font-bold text-slate-100">{value}</div>
        </div>
      ))}
    </div>
  );
}

function EquityCurveSection({ detail }: { detail: PerformanceDetailType | null }) {
  const curve = detail?.equity_curve ?? [];
  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <h3 className="mb-4 text-lg font-bold text-white">
        <Term k="equity_curve">資産の伸び</Term>
        <span className="ml-2 text-sm font-normal text-slate-400">
          vs <Term k="topix">市場平均</Term>
        </span>
      </h3>
      <RollingStats rolling={detail?.rolling} />
      {curve.length === 0 ? (
        <NoData />
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={curve}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickFormatter={(v) => `${((v - 1) * 100).toFixed(1)}%`} />
            <Tooltip
              contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", color: "#e2e8f0" }}
              formatter={(v: number | undefined) => [v == null ? "—" : `${((v - 1) * 100).toFixed(2)}%`]}
            />
            <Legend />
            <Line dataKey="strategy" stroke="#f87171" dot={false} name="AI" strokeWidth={2} />
            <Line dataKey="benchmark" stroke="#94a3b8" dot={false} name="市場平均" strokeWidth={1.5} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </section>
  );
}

function DrawdownSection({ detail }: { detail: PerformanceDetailType | null }) {
  const curve = detail?.drawdown_curve ?? [];
  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <h3 className="mb-4 text-lg font-bold text-white">
        <Term k="drawdown">一時的な落ち込み</Term>
      </h3>
      {curve.length === 0 ? (
        <NoData />
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={curve}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickFormatter={(v) => `${(v * 100).toFixed(1)}%`} />
            <Tooltip
              contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", color: "#e2e8f0" }}
              formatter={(v: number | undefined) => [v == null ? "—" : `${(v * 100).toFixed(2)}%`]}
            />
            <Area dataKey="drawdown" stroke="#60a5fa" fill="#60a5fa" fillOpacity={0.3} name="ドローダウン" />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </section>
  );
}

function ReliabilitySection({ detail }: { detail: PerformanceDetailType | null }) {
  const rel = detail?.reliability;
  const bins = (rel?.bins ?? []).filter((b) => b.count > 0);

  // Build bar chart data: each bin shows frac_up and mean_prob side by side
  const chartData = bins.map((b) => ({
    label: `${(b.bin_low * 100).toFixed(0)}–${(b.bin_high * 100).toFixed(0)}%`,
    frac_up: b.frac_up,
    mean_prob: b.mean_prob,
    count: b.count,
  }));

  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <h3 className="text-lg font-bold text-white">
          <Term k="calibration">確率の正直さ</Term>
        </h3>
        <span className="text-sm text-slate-400">
          <Term k="brier">予測のズレ点数</Term>: {rel?.brier == null ? "—" : rel.brier.toFixed(4)}
        </span>
      </div>
      {bins.length === 0 ? (
        <NoData />
      ) : (
        <>
          <p className="mb-3 text-xs text-slate-400">
            「70%」と言ったとき本当に70%上がっているか。赤(実際)とグレー(予測)が近いほど正直な予測です。
          </p>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 11 }} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} domain={[0, 1]} />
              <Tooltip
                contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", color: "#e2e8f0" }}
                formatter={(v: number | undefined) => [v == null ? "—" : `${(v * 100).toFixed(1)}%`]}
              />
              <Legend />
              <ReferenceLine y={0} stroke="#475569" />
              <Bar dataKey="frac_up" name="実際の上昇率" fill="#f87171" />
              <Bar dataKey="mean_prob" name="予測確率" fill="#94a3b8" />
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </section>
  );
}

function OutcomesTable({ outcomes }: { outcomes: SignalOutcomesRecent | null }) {
  const rows = outcomes?.rows ?? [];
  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <h3 className="mb-4 text-lg font-bold text-white">最近のサインの結果</h3>
      {rows.length === 0 ? (
        <NoData />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300 min-w-[700px]">
            <thead>
              <tr className="border-b border-slate-800 text-xs text-slate-500">
                <th className="py-2 pr-3 text-left">いつ</th>
                <th className="py-2 pr-3 text-left">銘柄</th>
                <th className="py-2 pr-3 text-left">判断</th>
                <th className="py-2 pr-3 text-right"><Term k="prob_up">上がる確率</Term></th>
                <th className="py-2 pr-3 text-right">結果</th>
                <th className="py-2 pr-3 text-right"><Term k="excess_return">市場平均との差</Term></th>
                <th className="py-2 pr-3 text-center">当たった?</th>
                <th className="py-2 pr-3 text-right"><Term k="mae_mfe">最大逆行</Term></th>
                <th className="py-2 text-right"><Term k="mae_mfe">最大順行</Term></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={`${row.entry_date}_${row.ticker}_${i}`} className="border-b border-slate-800/60 hover:bg-slate-800/40 transition-colors">
                  <td className="py-2 pr-3 text-slate-400 font-mono text-xs">{row.entry_date}</td>
                  <td className="py-2 pr-3">
                    <div className="font-medium text-slate-200">{row.name ?? row.ticker}</div>
                    {row.name && <div className="text-xs text-slate-500 font-mono">{row.ticker}</div>}
                  </td>
                  <td className="py-2 pr-3">
                    <span className="text-xs font-semibold">{actionLabel(row.action)}</span>
                  </td>
                  <td className="py-2 pr-3 text-right">{pct(row.conviction)}</td>
                  <td className="py-2 pr-3 text-right font-mono">
                    <span className={row.realized_ret == null ? "text-slate-400" : row.realized_ret >= 0 ? "text-red-300" : "text-blue-300"}>
                      {pct(row.realized_ret, true)}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">
                    <span className={row.excess_ret == null ? "text-slate-400" : row.excess_ret >= 0 ? "text-red-300" : "text-blue-300"}>
                      {pct(row.excess_ret, true)}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-center">
                    {row.hit === true ? (
                      <span className="rounded-full bg-red-500/20 px-2 py-0.5 text-xs text-red-300">○ 当たり</span>
                    ) : row.hit === false ? (
                      <span className="rounded-full bg-blue-500/20 px-2 py-0.5 text-xs text-blue-300">× はずれ</span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-blue-300">{pct(row.mae, true)}</td>
                  <td className="py-2 text-right font-mono text-red-300">{pct(row.mfe, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---- main component ---------------------------------------------------------

export default function PerformanceDetail() {
  const [detail, setDetail] = useState<PerformanceDetailType | null>(null);
  const [outcomes, setOutcomes] = useState<SignalOutcomesRecent | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<PerformanceDetailType>(
      `${basePath}/performance_detail.json`,
      (v): v is PerformanceDetailType => isAvailablePayload(v),
    ).then(setDetail);
    fetchJson<SignalOutcomesRecent>(
      `${basePath}/signal_outcomes_recent.json`,
      (v): v is SignalOutcomesRecent => isAvailablePayload(v),
    ).then(setOutcomes);
  }, []);

  // If available:false, fall back to showing placeholders in each section
  const effectiveDetail = detail?.available === false ? null : detail;
  const effectiveOutcomes = outcomes?.available === false ? null : outcomes;

  return (
    <div>
      <EquityCurveSection detail={effectiveDetail} />
      <DrawdownSection detail={effectiveDetail} />
      <ReliabilitySection detail={effectiveDetail} />
      <OutcomesTable outcomes={effectiveOutcomes} />
    </div>
  );
}
