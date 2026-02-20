"use client";

import { useEffect, useState } from "react";
import { HistoryData } from "../../../types";
import StockChart from "../../../components/StockChart";
import SignalCard from "../../../components/SignalCard";
import { ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { actionLabel, actionTextClass } from "../../../lib/signal";

interface Props {
    ticker: string;
}

export default function StockDetailContent({ ticker }: Props) {
  const tickerCode = decodeURIComponent(ticker);
  
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

  if (!data || !data.tickers[tickerCode]) {
    return (
        <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center text-red-400 gap-4">
            <p>データの読み込みに失敗しました ({tickerCode})</p>
            <Link href="/" className="text-blue-400 hover:underline">ダッシュボードに戻る</Link>
        </div>
    );
  }

  const currentTicker = data.tickers[tickerCode];
  // Find latest signal for this ticker
  const latestSignal = data.signals_history[0]?.signals.find(s => s.ticker === tickerCode);

  return (
    <main className="min-h-screen bg-slate-950 text-slate-200 p-4 md:p-8">
      <header className="max-w-7xl mx-auto mb-8 flex items-center gap-4">
        <Link href="/" className="p-2 bg-slate-900 rounded-full hover:bg-slate-800 transition-colors">
            <ArrowLeft size={24} className="text-slate-400" />
        </Link>
        <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">{currentTicker.name} ({tickerCode})</h1>
            <p className="text-slate-400 text-sm">最終更新: {data.last_update}</p>
        </div>
      </header>

      <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Main Chart Area */}
        <div className="lg:col-span-2 space-y-8">
            <StockChart data={currentTicker.data} tickerName={currentTicker.name} />
        </div>

        {/* Sidebar Info */}
        <div className="space-y-6">
            <h2 className="text-xl font-bold text-slate-100 flex items-center gap-2">
                <RefreshCw size={20} className="text-blue-400" />
                最新の予測
            </h2>
            {latestSignal ? (
                <SignalCard signal={latestSignal} />
            ) : (
                <div className="p-4 bg-slate-900 rounded border border-slate-800 text-slate-500">
                    本日のシグナルはありません。
                </div>
            )}

            <div className="mt-8">
                <h3 className="text-lg font-bold text-slate-100 mb-4">シグナル履歴</h3>
                <div className="space-y-4 max-h-[500px] overflow-y-auto pr-2">
                    {data.signals_history.map((entry, idx) => {
                        const sig = entry.signals.find(s => s.ticker === tickerCode);
                        if (!sig) return null;
                        return (
                            <div key={idx} className="p-3 bg-slate-900/50 rounded border border-slate-800/50 flex justify-between items-center text-sm">
                                <span className="text-slate-400">{entry.date}</span>
                                <span className={`font-bold ${actionTextClass(sig.action)}`}>
                                    {actionLabel(sig.action)}
                                </span>
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
      </div>
    </main>
  );
}
