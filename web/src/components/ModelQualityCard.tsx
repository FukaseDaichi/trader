"use client";

import { useEffect, useState } from "react";
import { ModelQuality } from "../types";
import { fetchJson, isAvailablePayload } from "../lib/fetchJson";
import Term from "./Term";

export default function ModelQualityCard() {
  const [mq, setMq] = useState<ModelQuality | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<ModelQuality>(
      `${basePath}/model_quality.json`,
      (v): v is ModelQuality => isAvailablePayload(v),
    ).then(setMq);
  }, []);

  if (!mq || !mq.available || !mq.summary) return null;

  const s = mq.summary;
  const fmt = (v: number | null | undefined, digits = 3) =>
    v == null ? "---" : v.toFixed(digits);
  const healthy = !s.drift_warning;

  return (
    <section className="mb-8 rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-lg font-bold text-white">モデルの健康診断</h3>
        <span
          className={`rounded-full border px-3 py-1 text-xs font-bold ${
            healthy
              ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
              : "border-amber-500/40 bg-amber-500/15 text-amber-300"
          }`}
        >
          {healthy ? "良好" : "注意"}
        </span>
      </div>
      <p className="mb-4 text-xs text-slate-400">
        いま使っている予測モデル(
        <span className="font-mono text-slate-300">{mq.active_model_version}</span>
        )が壊れていないかの週次チェックです。「注意」になると自動で警告が飛びます。
      </p>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <div className="mb-1 text-xs text-slate-500">
            <Term k="brier">予測のズレ点数</Term>
          </div>
          <div className="text-2xl font-bold text-slate-100">{fmt(s.median_brier)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">
            <Term k="ic">順位の当たり具合</Term>
          </div>
          <div className="text-2xl font-bold text-slate-100">{fmt(s.median_ic)}</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-500">チェック対象</div>
          <div className="text-2xl font-bold text-slate-200">{s.tickers}銘柄</div>
        </div>
      </div>
    </section>
  );
}
