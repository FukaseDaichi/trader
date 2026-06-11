"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { DashboardIndexData, Signal, SignalAction, TickerSummary } from "../types";
import {
  ACTION_STYLE,
  actionBadgeClass,
  actionLabel,
  changeTextClass,
  formatChangePct,
  formatPrice,
  formatProbability,
  isActionable,
  probTextClass,
} from "../lib/signal";
import { matchesTicker } from "../lib/search";
import { heatLabel } from "../lib/indicators";
import Term from "./Term";

type FilterKey = "all" | "buy" | "sell" | "hold";
type SortKey = "recommended" | "prob_desc" | "prob_asc" | "change_desc" | "code";

interface Row {
  code: string;
  t: TickerSummary;
}

function inFilter(filter: FilterKey, action: SignalAction | undefined): boolean {
  if (filter === "all") return true;
  if (!action) return filter === "hold";
  if (filter === "buy") return action === "BUY" || action === "MILD_BUY";
  if (filter === "sell") return action === "SELL" || action === "MILD_SELL";
  return action === "HOLD";
}

/** おすすめ順: 成績テスト合格の売買候補が先頭、続いて確率の極端さ順 */
function recommendedScore(t: TickerSummary): number {
  const s = t.latest_signal;
  if (!s || s.prob_up == null) return -1;
  return (isActionable(s) ? 1 : 0) + Math.abs(s.prob_up - 0.5);
}

function ProbBar({ signal }: { signal: Signal | null }) {
  const p = signal?.prob_up;
  if (p == null || !signal) return <span className="text-xs text-slate-500">---</span>;
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
        <span
          className="block h-full rounded-full"
          style={{
            width: `${Math.round(p * 100)}%`,
            backgroundColor: ACTION_STYLE[signal.action].barColor,
          }}
        />
      </span>
      <span className={`w-14 shrink-0 text-right text-xs font-semibold ${probTextClass(p)}`}>
        {formatProbability(p)}
      </span>
    </span>
  );
}

export default function StockExplorer({ data }: { data: DashboardIndexData }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [sort, setSort] = useState<SortKey>("recommended");

  const rows = useMemo<Row[]>(
    () => Object.entries(data.tickers).map(([code, t]) => ({ code, t })),
    [data],
  );

  const counts = useMemo(() => {
    const c = { all: rows.length, buy: 0, sell: 0, hold: 0 };
    for (const { t } of rows) {
      const a = t.latest_signal?.action;
      if (a === "BUY" || a === "MILD_BUY") c.buy += 1;
      else if (a === "SELL" || a === "MILD_SELL") c.sell += 1;
      else c.hold += 1;
    }
    return c;
  }, [rows]);

  const visible = useMemo(() => {
    const filtered = rows.filter(
      ({ code, t }) =>
        matchesTicker(query, code, t.name) && inFilter(filter, t.latest_signal?.action),
    );
    const arr = [...filtered];
    switch (sort) {
      case "prob_desc":
        arr.sort((a, b) => (b.t.latest_signal?.prob_up ?? -1) - (a.t.latest_signal?.prob_up ?? -1));
        break;
      case "prob_asc":
        arr.sort((a, b) => (a.t.latest_signal?.prob_up ?? 2) - (b.t.latest_signal?.prob_up ?? 2));
        break;
      case "change_desc":
        arr.sort((a, b) => (b.t.change_pct ?? -Infinity) - (a.t.change_pct ?? -Infinity));
        break;
      case "code":
        arr.sort((a, b) => a.code.localeCompare(b.code));
        break;
      default:
        arr.sort((a, b) => recommendedScore(b.t) - recommendedScore(a.t));
    }
    return arr;
  }, [rows, query, filter, sort]);

  const hasChange = rows.some(({ t }) => t.change_pct != null);

  const chips: { key: FilterKey; label: string; activeClass: string }[] = [
    { key: "all", label: `すべて ${counts.all}`, activeClass: "border-slate-500 bg-slate-800 text-white" },
    { key: "buy", label: `買い ${counts.buy}`, activeClass: "border-red-500/60 bg-red-500/15 text-red-300" },
    { key: "sell", label: `売り ${counts.sell}`, activeClass: "border-blue-500/60 bg-blue-500/15 text-blue-300" },
    { key: "hold", label: `様子見 ${counts.hold}`, activeClass: "border-slate-500 bg-slate-800 text-slate-300" },
  ];

  return (
    <section className="mb-10">
      <div className="mb-3 flex flex-col gap-2 sm:flex-row">
        <label className="flex flex-1 items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 focus-within:border-slate-500">
          <Search size={16} className="shrink-0 text-slate-500" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="銘柄名・コードで検索(例: トヨタ / 7203)"
            className="w-full bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none"
          />
        </label>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300 focus:border-slate-500 focus:outline-none"
          aria-label="並び替え"
        >
          <option value="recommended">おすすめ順</option>
          <option value="prob_desc">上がる確率が高い順</option>
          <option value="prob_asc">上がる確率が低い順</option>
          {hasChange && <option value="change_desc">前日比が大きい順</option>}
          <option value="code">コード順</option>
        </select>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {chips.map((chip) => (
          <button
            key={chip.key}
            type="button"
            onClick={() => setFilter(chip.key)}
            className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
              filter === chip.key
                ? chip.activeClass
                : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
            }`}
          >
            {chip.label}
          </button>
        ))}
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60">
        <div className="hidden grid-cols-[1.6fr_1fr_0.8fr_1.2fr_0.8fr] gap-3 px-3 py-2 text-xs text-slate-500 md:grid">
          <span>銘柄</span>
          <span>株価{hasChange && " / 前日比"}</span>
          <span>判断</span>
          <span>
            <Term k="prob_up">上がる確率</Term>
          </span>
          <span>
            <Term k="rsi">過熱感</Term>
          </span>
        </div>

        {visible.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-slate-500">
            「{query}」に当てはまる銘柄がありません
          </p>
        )}

        {visible.map(({ code, t }) => {
          const s = t.latest_signal;
          const tint = s ? ACTION_STYLE[s.action].rowTintClass : "";
          const heat = heatLabel(t.latest_data?.rsi);
          return (
            <div key={code} className={tint}>
              <Link
                href={`/stocks/${code}`}
                className="hidden grid-cols-[1.6fr_1fr_0.8fr_1.2fr_0.8fr] items-center gap-3 border-t border-slate-800/70 px-3 py-2.5 transition-colors hover:bg-slate-800/40 md:grid"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-slate-100">{t.name}</span>
                  <span className="font-mono text-xs text-slate-500">{code}</span>
                </span>
                <span className="text-sm text-slate-200">
                  {formatPrice(t.latest_data?.close)}
                  {t.change_pct != null && (
                    <span className={`ml-1.5 text-xs ${changeTextClass(t.change_pct)}`}>
                      {formatChangePct(t.change_pct)}
                    </span>
                  )}
                </span>
                <span>
                  {s ? (
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${actionBadgeClass(s.action)}`}>
                      {actionLabel(s.action)}
                    </span>
                  ) : (
                    <span className="text-xs text-slate-500">---</span>
                  )}
                </span>
                <ProbBar signal={s} />
                <span className={`text-xs ${heat.className}`}>{heat.text}</span>
              </Link>

              <Link
                href={`/stocks/${code}`}
                className="flex items-center justify-between gap-3 border-t border-slate-800/70 px-3 py-3 transition-colors hover:bg-slate-800/40 md:hidden"
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-slate-100">{t.name}</span>
                    {s && (
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-bold ${actionBadgeClass(s.action)}`}>
                        {actionLabel(s.action)}
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500">
                    <span className="font-mono">{code}</span>
                    <span className="ml-2 text-slate-400">{formatPrice(t.latest_data?.close)}</span>
                    {t.change_pct != null && (
                      <span className={`ml-1.5 ${changeTextClass(t.change_pct)}`}>
                        {formatChangePct(t.change_pct)}
                      </span>
                    )}
                  </span>
                </span>
                <span className={`shrink-0 text-sm font-bold ${probTextClass(s?.prob_up)}`}>
                  {formatProbability(s?.prob_up)}
                </span>
              </Link>
            </div>
          );
        })}
      </div>
    </section>
  );
}
