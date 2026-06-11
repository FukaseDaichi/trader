"use client";

import { SignalThresholds } from "../types";
import Term from "./Term";

interface Props {
  probUp: number;
  thresholds: SignalThresholds;
}

/** 今日の上がる確率が、銘柄ごとの判断ラインのどこにあるかを示す横ゲージ */
export default function ThresholdGauge({ probUp, thresholds }: Props) {
  const pos = (v: number) => `${Math.min(100, Math.max(0, v * 100))}%`;
  // ラベルの重なりを避けるため2段に振り分ける(row: 0=上段, 1=下段)
  const markers = [
    { key: "sell", value: thresholds.sell, label: `売り ${(thresholds.sell * 100).toFixed(0)}%`, row: 0 },
    { key: "mild_buy", value: thresholds.mild_buy, label: `やや買い ${(thresholds.mild_buy * 100).toFixed(0)}%`, row: 1 },
    { key: "buy", value: thresholds.buy, label: `買い ${(thresholds.buy * 100).toFixed(0)}%`, row: 0 },
  ];
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <div className="mb-4 text-xs text-slate-400">
        今日の<Term k="prob_up">上がる確率</Term>は<Term k="threshold">判断ライン</Term>のどこ?
      </div>
      <div className="relative mx-1 h-2 rounded-full bg-slate-800">
        <span
          className="absolute left-0 top-0 block h-2 rounded-full bg-slate-600"
          style={{ width: pos(probUp) }}
        />
        {markers.map((m) => (
          <span
            key={m.key}
            className="absolute -top-1 block h-4 w-0.5 bg-slate-500"
            style={{ left: pos(m.value) }}
          />
        ))}
        <span
          className="absolute -top-1.5 block h-5 w-2 -translate-x-1/2 rounded-sm bg-white"
          style={{ left: pos(probUp) }}
        />
      </div>
      <div className="relative mt-2 h-9 text-xs text-slate-500">
        {markers.map((m) => (
          <span
            key={m.key}
            className="absolute -translate-x-1/2 whitespace-nowrap"
            style={{ left: pos(m.value), top: m.row === 0 ? 0 : "1.1rem" }}
          >
            {m.label}
          </span>
        ))}
      </div>
      <p className="text-xs font-semibold text-slate-200">今日: {(probUp * 100).toFixed(1)}%</p>
    </div>
  );
}
