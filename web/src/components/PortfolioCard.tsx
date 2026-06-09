"use client";

import { useEffect, useState } from "react";
import { PortfolioLatest, PortfolioDiffType } from "../types";

const DIFF_LABEL: Record<PortfolioDiffType, string> = {
  new: "新規",
  increase: "増",
  decrease: "減",
  exit: "退出",
  hold: "据置",
};

const DIFF_CLASS: Record<PortfolioDiffType, string> = {
  new: "text-red-300",
  increase: "text-red-300",
  decrease: "text-blue-300",
  exit: "text-blue-300",
  hold: "text-slate-400",
};

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "---";
  return (v * 100).toFixed(1) + "%";
}

export default function PortfolioCard() {
  const [pf, setPf] = useState<PortfolioLatest | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${basePath}/portfolio_latest.json`)
      .then((res) => (res.ok ? res.json() : null))
      .then((json: PortfolioLatest | null) => setPf(json))
      .catch(() => setPf(null));
  }, []);

  if (!pf || !pf.available || !pf.positions || pf.positions.length === 0) return null;

  const ds = pf.diff_summary;

  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <div className="flex items-center justify-between mb-1 gap-2 flex-wrap">
        <h3 className="text-lg font-bold text-white">今日の建玉（ポートフォリオ提案）</h3>
        <div className="flex items-center gap-2">
          {pf.mode === "active" ? (
            <span className="px-2 py-1 rounded-full text-xs font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
              本番反映
            </span>
          ) : (
            <span className="px-2 py-1 rounded-full text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">
              シャドー検証
            </span>
          )}
        </div>
      </div>
      {pf.model_version && (
        <p className="text-xs text-slate-400 mb-4">
          モデル{" "}
          <span className="font-mono text-slate-300">{pf.model_version}</span>
          {pf.as_of_date && `（基準日: ${pf.as_of_date}）`}
        </p>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">グロス</div>
          <div className="text-2xl font-bold text-emerald-300">{fmtPct(pf.gross_exposure)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">想定ボラ</div>
          <div className="text-2xl font-bold text-blue-300">{fmtPct(pf.expected_vol)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">想定リターン</div>
          <div className="text-2xl font-bold text-slate-200">{fmtPct(pf.expected_ret)}</div>
        </div>
        {ds && (
          <div>
            <div className="text-xs text-slate-500 uppercase mb-1">変動内訳</div>
            <div className="text-sm font-semibold text-slate-300 leading-snug mt-1">
              <span className="text-red-300">新規 {ds.add}</span>
              {" / "}
              <span className="text-blue-300">減 {ds.trim}</span>
              {" / "}
              <span className="text-blue-300">退出 {ds.exit}</span>
              {" / "}
              <span className="text-slate-400">据置 {ds.hold}</span>
            </div>
          </div>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-slate-500 uppercase border-b border-slate-800">
              <th className="text-left pb-2 pr-4">銘柄</th>
              <th className="text-right pb-2 pr-4">比率</th>
              <th className="text-center pb-2 pr-4">増減</th>
              <th className="text-right pb-2 pr-4">ランク</th>
              <th className="text-right pb-2 pr-4">期待リターン</th>
              <th className="text-right pb-2">上昇確率</th>
            </tr>
          </thead>
          <tbody>
            {pf.positions.map((pos) => (
              <tr key={pos.ticker} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-2 pr-4">
                  <div className="font-semibold text-slate-100">{pos.name ?? pos.ticker}</div>
                  <div className="text-xs text-slate-500 font-mono">{pos.ticker}</div>
                </td>
                <td className="py-2 pr-4 text-right font-mono text-slate-200">
                  {fmtPct(pos.target_weight)}
                </td>
                <td className="py-2 pr-4 text-center">
                  <span className={`font-semibold ${DIFF_CLASS[pos.diff_type]}`}>
                    {DIFF_LABEL[pos.diff_type]}
                  </span>
                </td>
                <td className="py-2 pr-4 text-right text-slate-300">
                  {pos.cs_rank ?? "---"}
                </td>
                <td className="py-2 pr-4 text-right text-slate-300">
                  {fmtPct(pos.expected_ret)}
                </td>
                <td className="py-2 text-right text-slate-300">
                  {fmtPct(pos.prob_up)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
