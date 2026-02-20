"use client";

import { Signal } from "../types";
import {
  ArrowUpCircle,
  ArrowDownCircle,
  ChevronUpCircle,
  ChevronDownCircle,
  MinusCircle,
  AlertCircle,
} from "lucide-react";
import clsx from "clsx";
import { actionLabel, actionBadgeClass, actionCardClass } from "../lib/signal";

interface SignalCardProps {
  signal: Signal;
}

function ActionIcon({ action }: { action: Signal["action"] }) {
  const size = 16;
  switch (action) {
    case "BUY":       return <ArrowUpCircle size={size} />;
    case "MILD_BUY":  return <ChevronUpCircle size={size} />;
    case "MILD_SELL": return <ChevronDownCircle size={size} />;
    case "SELL":      return <ArrowDownCircle size={size} />;
    default:          return <MinusCircle size={size} />;
  }
}

export default function SignalCard({ signal }: SignalCardProps) {
  return (
    <div className={clsx("p-6 rounded-xl border shadow-lg transition-all", actionCardClass(signal.action))}>
      <div className="flex items-start justify-between">
        <div>
            <h3 className="text-lg font-bold text-slate-100">{signal.name}</h3>
            <p className="text-sm text-slate-400">{signal.ticker}</p>
        </div>
        <div className="text-right">
             <div className={clsx(
                 "inline-flex items-center gap-2 px-3 py-1 rounded-full text-sm font-bold",
                 actionBadgeClass(signal.action)
             )}>
                <ActionIcon action={signal.action} />
                {actionLabel(signal.action)}
             </div>
             <div className="mt-1 text-xs text-slate-500">{signal.date}</div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <div>
            <p className="text-xs text-slate-400 uppercase">上昇確率</p>
            <p className="text-2xl font-bold text-slate-100">{(signal.prob_up * 100).toFixed(1)}%</p>
        </div>
        <div>
            <p className="text-xs text-slate-400 uppercase">終値</p>
            <p className="text-2xl font-bold text-slate-100">¥{signal.close.toLocaleString()}</p>
        </div>
      </div>

      {(signal.limit_price || signal.stop_loss) && (
          <div className="mt-4 pt-4 border-t border-slate-700/50 grid grid-cols-2 gap-4 text-sm">
            {signal.limit_price && (
                <div>
                   <span className="text-slate-400">指値目安: </span>
                   <span className="text-slate-200 font-mono">¥{signal.limit_price}</span>
                </div>
            )}
            {signal.stop_loss && (
                <div>
                   <span className="text-slate-400">損切目安: </span>
                   <span className="text-slate-200 font-mono">¥{signal.stop_loss}</span>
                </div>
            )}
          </div>
      )}

      {signal.reason && (
        <div className="mt-4 flex items-start gap-2 bg-slate-900/50 p-3 rounded text-sm text-slate-300">
            <AlertCircle size={16} className="mt-0.5 shrink-0 text-slate-500" />
            <p>{signal.reason}</p>
        </div>
      )}
    </div>
  );
}
