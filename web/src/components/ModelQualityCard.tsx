"use client";

import { useEffect, useState } from "react";
import { ModelQuality } from "../types";

export default function ModelQualityCard() {
  const [mq, setMq] = useState<ModelQuality | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${basePath}/model_quality.json`)
      .then((res) => (res.ok ? res.json() : null))
      .then((json: ModelQuality | null) => setMq(json))
      .catch(() => setMq(null));
  }, []);

  // Hidden until a Phase 1 model is active and quality data is available.
  if (!mq || !mq.available || !mq.summary) return null;

  const s = mq.summary;
  const fmt = (v: number | null | undefined, digits = 3) =>
    v == null ? "---" : v.toFixed(digits);

  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <div className="flex items-center justify-between mb-1 gap-2">
        <h3 className="text-lg font-bold text-white">モデル品質（較正・予測力）</h3>
        {s.drift_warning && (
          <span className="px-2 py-1 rounded-full text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">
            ⚠ ドリフト警告
          </span>
        )}
      </div>
      <p className="text-xs text-slate-400 mb-4">
        保存済みモデル{" "}
        <span className="font-mono text-slate-300">{mq.active_model_version}</span>
        （{mq.horizon_days ?? "?"}営業日先予測）の検証指標です。Brierは低いほど、ICは高いほど良好です。
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">中央Brier</div>
          <div className="text-2xl font-bold text-emerald-300">{fmt(s.median_brier)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">中央IC</div>
          <div className="text-2xl font-bold text-blue-300">{fmt(s.median_ic)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">対象銘柄</div>
          <div className="text-2xl font-bold text-slate-200">{s.tickers}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">ドリフト</div>
          <div
            className={`text-2xl font-bold ${
              s.drift_warning ? "text-amber-300" : "text-emerald-300"
            }`}
          >
            {s.drift_warning ? "警告" : "正常"}
          </div>
        </div>
      </div>
    </section>
  );
}
