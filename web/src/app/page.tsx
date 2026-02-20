"use client";

import { useEffect, useState } from "react";
import { HistoryData } from "../types";
import { RefreshCw, TrendingUp, TrendingDown, Minus, ChevronUp, ChevronDown } from "lucide-react";
import Link from "next/link";
import { actionLabel, actionBadgeClass, probTextClass } from "../lib/signal";

function ActionIcon({ action }: { action: string }) {
  switch (action) {
    case "BUY":       return <TrendingUp size={14} />;
    case "MILD_BUY":  return <ChevronUp size={14} />;
    case "MILD_SELL": return <ChevronDown size={14} />;
    case "SELL":      return <TrendingDown size={14} />;
    default:          return <Minus size={14} />;
  }
}

export default function Home() {
  const [data, setData] = useState<HistoryData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const basePath = process.env.NODE_ENV === "development" ? "" : "/trader";
    const dataUrl = `${basePath}/history_data.json`;

    fetch(dataUrl)
      .then((res) => res.json())
      .then((json: HistoryData) => {
        setData(json);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load history data", err);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div className="min-h-screen bg-slate-950 flex items-center justify-center text-slate-400">読み込み中...</div>;
  }

  if (!data) {
    return <div className="min-h-screen bg-slate-950 flex items-center justify-center text-red-400">データの読み込みに失敗しました。</div>;
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-200 p-4 md:p-8">
      <header className="max-w-7xl mx-auto mb-12 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
            <h1 className="text-3xl font-bold text-white tracking-tight">AI株式トレーダー</h1>
            <p className="text-slate-400">最終更新: {data.last_update}</p>
        </div>
      </header>

      <div className="max-w-7xl mx-auto">
        <h2 className="text-xl font-bold text-slate-100 mb-6 flex items-center gap-2">
            <RefreshCw size={20} className="text-blue-400" />
            監視銘柄一覧
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {Object.keys(data.tickers).map(code => {
                const ticker = data.tickers[code];
                const latestData = ticker.data.length > 0 ? ticker.data[ticker.data.length - 1] : null;
                const latestSignal = data.signals_history[0]?.signals.find(s => s.ticker === code);

                return (
                    <Link
                        key={code}
                        href={`/stocks/${code}`}
                        className="block bg-slate-900 rounded-xl border border-slate-800 p-6 hover:border-blue-500/50 hover:bg-slate-800/80 transition-all group"
                    >
                        <div className="flex justify-between items-start mb-4">
                            <div>
                                <h3 className="text-lg font-bold text-slate-100 group-hover:text-blue-400 transition-colors">{ticker.name}</h3>
                                <div className="text-sm text-slate-500 font-mono">{code}</div>
                            </div>
                            {latestSignal && (
                                <div className={`px-3 py-1 rounded-full text-xs font-bold flex items-center gap-1 ${actionBadgeClass(latestSignal.action)}`}>
                                    <ActionIcon action={latestSignal.action} />
                                    {actionLabel(latestSignal.action)}
                                </div>
                            )}
                        </div>

                        <div className="flex justify-between items-end">
                            <div>
                                <div className="text-xs text-slate-500 uppercase mb-1">現在値</div>
                                <div className="text-2xl font-bold text-white">
                                    {latestData ? `¥${latestData.close?.toLocaleString()}` : "---"}
                                </div>
                            </div>
                            {latestSignal && (
                                <div className="text-right">
                                    <div className="text-xs text-slate-500 uppercase mb-1">上昇確率</div>
                                    <div className={`text-lg font-bold ${probTextClass(latestSignal.prob_up)}`}>
                                        {(latestSignal.prob_up * 100).toFixed(1)}%
                                    </div>
                                </div>
                            )}
                        </div>
                    </Link>
                );
            })}
        </div>
      </div>
    </main>
  );
}
