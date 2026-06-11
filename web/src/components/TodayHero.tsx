"use client";

import Link from "next/link";
import { Coffee, TrendingDown, TrendingUp } from "lucide-react";
import { format, parseISO } from "date-fns";
import { ja } from "date-fns/locale";
import { DashboardIndexData, Signal } from "../types";
import {
  actionLabel,
  formatProbability,
  isActionable,
  isBuySide,
  isSellSide,
} from "../lib/signal";
import Term from "./Term";

interface HeroItem {
  code: string;
  signal: Signal;
}

function formatDateJa(iso: string): string {
  if (!iso) return "";
  try {
    return format(parseISO(iso), "M/d(E)", { locale: ja });
  } catch {
    return iso;
  }
}

function HeroChip({ code, signal, side }: { code: string; signal: Signal; side: "buy" | "sell" }) {
  const probClass = side === "buy" ? "text-red-300" : "text-blue-300";
  const mild = signal.action === "MILD_BUY" || signal.action === "MILD_SELL";
  return (
    <Link
      href={`/stocks/${code}`}
      className="flex items-center justify-between gap-3 rounded-lg bg-slate-950/60 px-3 py-2.5 transition-colors hover:bg-slate-900"
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-slate-100">
          {signal.name || code}
          {mild && <span className="ml-2 text-xs font-normal text-slate-400">({actionLabel(signal.action)})</span>}
        </span>
        <span className="font-mono text-xs text-slate-500">{code}</span>
      </span>
      <span className={`shrink-0 text-sm font-bold ${probClass}`}>
        上がる確率 {formatProbability(signal.prob_up)}
      </span>
    </Link>
  );
}

export default function TodayHero({ data }: { data: DashboardIndexData }) {
  const all: HeroItem[] = Object.entries(data.tickers)
    .map(([code, t]) => ({ code, signal: t.latest_signal }))
    .filter((x): x is HeroItem => x.signal != null);

  const buys = all
    .filter((x) => isActionable(x.signal) && isBuySide(x.signal.action))
    .sort((a, b) => (b.signal.prob_up ?? 0) - (a.signal.prob_up ?? 0));
  const sells = all
    .filter((x) => isActionable(x.signal) && isSellSide(x.signal.action))
    .sort((a, b) => (a.signal.prob_up ?? 1) - (b.signal.prob_up ?? 1));
  const watchCount = all.length - buys.length - sells.length;

  const latestDate = all.reduce<string>(
    (acc, x) => (x.signal.date > acc ? x.signal.date : acc),
    "",
  );

  return (
    <section className="mb-10">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <h2 className="text-xl font-bold text-white">今日のAI判断</h2>
        {latestDate && <span className="text-sm text-slate-400">{formatDateJa(latestDate)}時点</span>}
      </div>

      {buys.length === 0 && sells.length === 0 ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-8 text-center">
          <Coffee size={28} className="mx-auto mb-3 text-slate-500" />
          <p className="text-lg font-bold text-slate-200">今日は「様子見」の日</p>
          <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-400">
            全{all.length}銘柄、<Term k="gate">成績テスト</Term>
            を合格した売買サインはありませんでした。無理に動かないのも大事な判断です。
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {buys.length > 0 && (
            <div
              className={`rounded-xl border border-red-500/40 bg-red-500/10 p-4 ${
                sells.length === 0 ? "md:col-span-2" : ""
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm font-bold text-red-300">
                  <TrendingUp size={16} /> 買い候補
                </span>
                <span className="text-xs font-semibold text-red-300">{buys.length}社</span>
              </div>
              <div className="space-y-2">
                {buys.map((x) => (
                  <HeroChip key={x.code} code={x.code} signal={x.signal} side="buy" />
                ))}
              </div>
            </div>
          )}
          {sells.length > 0 && (
            <div
              className={`rounded-xl border border-blue-500/40 bg-blue-500/10 p-4 ${
                buys.length === 0 ? "md:col-span-2" : ""
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm font-bold text-blue-300">
                  <TrendingDown size={16} /> 売り候補
                </span>
                <span className="text-xs font-semibold text-blue-300">{sells.length}社</span>
              </div>
              <div className="space-y-2">
                {sells.map((x) => (
                  <HeroChip key={x.code} code={x.code} signal={x.signal} side="sell" />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {(buys.length > 0 || sells.length > 0) && (
        <p className="mt-3 text-xs leading-relaxed text-slate-500">
          ほか{watchCount}銘柄は「様子見」。ここに出るのは
          <Term k="gate">成績テスト</Term>に合格した売買サインだけです。
        </p>
      )}
    </section>
  );
}
