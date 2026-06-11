"use client";

import { useEffect, useState } from "react";
import { PortfolioLatest, PortfolioDiffType } from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";
import Term from "./Term";

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
    fetchJson<PortfolioLatest>(
      `${basePath}/portfolio_latest.json`,
      (v): v is PortfolioLatest => isAvailablePayload(v),
    ).then(setPf);
  }, []);

  if (!pf || !pf.available || !pf.positions || pf.positions.length === 0) return null;

  const ds = pf.diff_summary;

  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <div className="flex items-center justify-between mb-1 gap-2 flex-wrap">
        <h3 className="text-lg font-bold text-white">AIのおすすめ配分</h3>
        <div className="flex items-center gap-2">
          {pf.mode === "active" ? (
            <span className="rounded-full border border-emerald-500/40 bg-emerald-500/20 px-2 py-1 text-xs font-bold text-emerald-300">
              自動反映中
            </span>
          ) : (
            <span className="rounded-full border border-amber-500/40 bg-amber-500/20 px-2 py-1 text-xs font-bold text-amber-300">
              <Term k="shadow_mode">提案モード</Term>
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
          <div className="mb-1 text-xs text-slate-500">投資にあてる割合</div>
          <div className="text-2xl font-bold text-slate-100">{fmtPct(pf.gross_exposure)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">想定の値動き幅</div>
          <div className="text-2xl font-bold text-slate-100">{fmtPct(pf.expected_vol)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">期待リターン</div>
          <div className="text-2xl font-bold text-slate-100">{fmtPct(pf.expected_ret)}</div>
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
            <tr className="border-b border-slate-800 text-xs text-slate-500">
              <th className="pb-2 pr-4 text-left">銘柄</th>
              <th className="pb-2 pr-4 text-right">配分</th>
              <th className="pb-2 pr-4 text-center">前回比</th>
              <th className="pb-2 pr-4 text-right"><Term k="cs_rank">AI順位</Term></th>
              <th className="pb-2 pr-4 text-right">期待リターン</th>
              <th className="pb-2 text-right"><Term k="prob_up">上がる確率</Term></th>
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
