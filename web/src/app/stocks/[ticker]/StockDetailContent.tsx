"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import {
  SignalOutcomeRow,
  SignalOutcomesRecent,
  TickerDetailData,
} from "../../../types";
import StockChart from "../../../components/StockChart";
import SignalNarrative from "../../../components/SignalNarrative";
import ThresholdGauge from "../../../components/ThresholdGauge";
import SiteHeader from "../../../components/SiteHeader";
import SiteFooter from "../../../components/SiteFooter";
import Term from "../../../components/Term";
import {
  actionBadgeClass,
  actionLabel,
  actionTextClass,
  changeTextClass,
  formatChangePct,
  formatPrice,
  formatProbability,
  gateLabel,
} from "../../../lib/signal";
import { fetchJson, isAvailablePayload } from "../../../lib/fetchJson";

interface Props {
  ticker: string;
}

function pctSigned(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

export default function StockDetailContent({ ticker }: Props) {
  const tickerCode = decodeURIComponent(ticker);

  const [data, setData] = useState<TickerDetailData | null>(null);
  const [outcomes, setOutcomes] = useState<SignalOutcomesRecent | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${basePath}/tickers/${encodeURIComponent(tickerCode)}.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json: TickerDetailData) => {
        setData(json);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load ticker detail", err);
        setLoading(false);
      });
    fetchJson<SignalOutcomesRecent>(
      `${basePath}/signal_outcomes_recent.json`,
      (v): v is SignalOutcomesRecent => isAvailablePayload(v),
    ).then(setOutcomes);
  }, [tickerCode]);

  const outcomeByDate = useMemo(() => {
    const m = new Map<string, SignalOutcomeRow>();
    for (const row of outcomes?.rows ?? []) {
      if (row.ticker === tickerCode) m.set(row.entry_date, row);
    }
    return m;
  }, [outcomes, tickerCode]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        読み込み中...
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-950 text-red-400">
        <p>データの読み込みに失敗しました ({tickerCode})</p>
        <Link href="/" className="text-blue-400 hover:underline">
          ダッシュボードに戻る
        </Link>
      </div>
    );
  }

  const latestSignal = data.latest_signal;
  const last = data.data[data.data.length - 1];
  const prev = data.data[data.data.length - 2];
  const change =
    last?.close != null && prev?.close != null && prev.close !== 0
      ? (last.close - prev.close) / prev.close
      : null;
  const gate = latestSignal ? gateLabel(latestSignal) : null;

  return (
    <main className="min-h-screen bg-slate-950 p-4 text-slate-200 md:p-8">
      <SiteHeader updated={data.last_update} />

      <div className="mx-auto max-w-7xl">
        <Link
          href="/"
          className="mb-4 inline-flex items-center gap-1 text-sm text-slate-400 hover:text-white"
        >
          <ArrowLeft size={16} /> 銘柄一覧へ
        </Link>

        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-2xl font-bold tracking-tight text-white">{data.name}</h1>
            <span className="font-mono text-sm text-slate-500">{tickerCode}</span>
            <span className="text-xl font-bold text-white">{formatPrice(last?.close)}</span>
            {change != null && (
              <span className={`text-sm font-semibold ${changeTextClass(change)}`}>
                {formatChangePct(change)}
              </span>
            )}
          </div>
          {latestSignal && gate && (
            <div className="flex items-center gap-2">
              <span
                className={`rounded-full px-3 py-1 text-sm font-bold ${actionBadgeClass(latestSignal.action)}`}
              >
                {actionLabel(latestSignal.action)}
              </span>
              <span
                className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${gate.className}`}
              >
                <Term k="gate">{gate.text}</Term>
              </span>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
          <div className="space-y-8 lg:col-span-2">
            <StockChart data={data.data} tickerName={data.name} />
          </div>

          <div className="space-y-6">
            {latestSignal ? (
              <SignalNarrative signal={latestSignal} />
            ) : (
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-slate-500">
                本日のシグナルはありません。
              </div>
            )}

            {latestSignal?.prob_up != null && latestSignal.thresholds && (
              <ThresholdGauge
                probUp={latestSignal.prob_up}
                thresholds={latestSignal.thresholds}
              />
            )}

            <div>
              <h2 className="mb-3 text-lg font-bold text-slate-100">これまでのサインと結果</h2>
              <div className="max-h-[480px] space-y-2 overflow-y-auto pr-2">
                {data.signals.map((entry) => {
                  const sig = entry.signal;
                  const outcome = outcomeByDate.get(entry.date);
                  return (
                    <div
                      key={`${entry.date}-${sig.action}`}
                      className="flex items-center justify-between gap-2 rounded border border-slate-800/50 bg-slate-900/50 p-3 text-sm"
                    >
                      <span className="font-mono text-xs text-slate-400">{entry.date}</span>
                      <span className={`font-bold ${actionTextClass(sig.action)}`}>
                        {actionLabel(sig.action)}
                      </span>
                      <span className="text-xs text-slate-500">
                        {formatProbability(sig.prob_up)}
                      </span>
                      <span className="w-24 text-right text-xs">
                        {outcome?.realized_ret != null ? (
                          <span
                            className={
                              outcome.realized_ret >= 0 ? "text-red-300" : "text-blue-300"
                            }
                          >
                            {pctSigned(outcome.realized_ret)}
                            {outcome.hit === true && " ○"}
                            {outcome.hit === false && " ×"}
                          </span>
                        ) : (
                          <span className="text-slate-600">—</span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
      <SiteFooter />
    </main>
  );
}
