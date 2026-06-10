"use client";

import { useEffect, useState } from "react";
import { MacroLatest } from "../types";
import { fetchJson } from "../lib/fetchJson";

function biasLabel(bias: string): { text: string; className: string } {
  switch (bias) {
    case "risk_on":
      return { text: "リスクオン", className: "text-red-300 font-bold" };
    case "risk_off":
      return { text: "リスクオフ", className: "text-blue-300 font-bold" };
    default:
      return { text: "中立", className: "text-slate-300 font-bold" };
  }
}

export default function RegimeBanner() {
  const [macro, setMacro] = useState<MacroLatest | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<MacroLatest>(
      `${basePath}/curation/macro_latest.json`,
      (v): v is MacroLatest => typeof v === "object" && v !== null,
    ).then(setMacro);
  }, []);

  if (!macro || !macro.market_bias) return null;

  const { text, className } = biasLabel(macro.market_bias);
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

  return (
    <div className="max-w-7xl mx-auto mb-6">
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl px-5 py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500 uppercase">マクロレジーム</span>
          <span className={className}>{text}</span>
          {macro.as_of && (
            <span className="text-xs text-slate-500">({macro.as_of})</span>
          )}
        </div>
        <div className="flex items-center gap-4 text-sm">
          <a
            href="https://github.com/FukaseDaichi/trader/tree/main/reports"
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-400 hover:underline"
          >
            週次レポート
          </a>
          <a
            href={`${basePath}/curation/decision_latest.json`}
            className="text-blue-400 hover:underline"
          >
            キュレーション決定ログ
          </a>
        </div>
      </div>
    </div>
  );
}
