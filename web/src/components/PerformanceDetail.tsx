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
  SignalOutcomeRow,
} from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";

// ---- helpers ----------------------------------------------------------------

function pct(v: number | null | undefined, signed = false): string {
  if (v == null) return "—";
  const val = (v * 100).toFixed(2);
  return signed ? `${v >= 0 ? "+" : ""}${val}%` : `${val}%`;
}

function actionLabel(action: SignalOutcomeRow["action"]): string {
  switch (action) {
    case "BUY":       return "BUY";
    case "MILD_BUY":  return "やや買い";
    case "HOLD":      return "HOLD";
    case "MILD_SELL": return "やや売り";
    case "SELL":      return "SELL";
    default:          return action;
  }
}

// ---- sub-sections -----------------------------------------------------------

function NoData() {
  return <p className="text-slate-400 text-sm">データ蓄積中…</p>;
}

function RollingStats({ rolling }: { rolling: PerformanceDetailType["rolling"] }) {
  if (!rolling) return null;
  const stats = [
    { label: "的中率(20d)", value: pct(rolling.hit_rate_20d) },
    { label: "平均リターン(20d)", value: pct(rolling.avg_return_20d) },
    { label: "超過リターン(20d)", value: pct(rolling.excess_return_20d) },
    { label: "シャープ(60d)", value: rolling.sharpe_60d == null ? "—" : rolling.sharpe_60d.toFixed(2) },
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
      <h3 className="text-lg font-bold text-white mb-4">資産曲線 vs TOPIX</h3>
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
            <Line dataKey="strategy" stroke="#f87171" dot={false} name="戦略" strokeWidth={2} />
            <Line dataKey="benchmark" stroke="#94a3b8" dot={false} name="TOPIX" strokeWidth={1.5} />
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
      <h3 className="text-lg font-bold text-white mb-4">ドローダウン</h3>
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
      <div className="flex items-center gap-4 mb-4">
        <h3 className="text-lg font-bold text-white">信頼性（較正）</h3>
        <span className="text-sm text-slate-400">
          Brier: {rel?.brier == null ? "—" : rel.brier.toFixed(4)}
        </span>
      </div>
      {bins.length === 0 ? (
        <NoData />
      ) : (
        <>
          <p className="text-xs text-slate-400 mb-3">
            赤: 実際の上昇率（frac_up）　グレー: 予測確率（mean_prob）　完全較正なら一致
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
      <h3 className="text-lg font-bold text-white mb-4">個別シグナル結果（直近）</h3>
      {rows.length === 0 ? (
        <NoData />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300 min-w-[700px]">
            <thead>
              <tr className="text-xs text-slate-500 uppercase border-b border-slate-800">
                <th className="text-left py-2 pr-3">日付</th>
                <th className="text-left py-2 pr-3">銘柄</th>
                <th className="text-left py-2 pr-3">アクション</th>
                <th className="text-right py-2 pr-3">確信度</th>
                <th className="text-right py-2 pr-3">実現</th>
                <th className="text-right py-2 pr-3">超過</th>
                <th className="text-center py-2 pr-3">的中</th>
                <th className="text-right py-2 pr-3">MAE</th>
                <th className="text-right py-2">MFE</th>
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
                      <span className="px-2 py-0.5 rounded-full text-xs bg-red-500/20 text-red-300">的中</span>
                    ) : row.hit === false ? (
                      <span className="px-2 py-0.5 rounded-full text-xs bg-blue-500/20 text-blue-300">外れ</span>
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
