"use client";

import { Sparkles } from "lucide-react";
import clsx from "clsx";
import { Signal } from "../types";
import {
  actionCardClass,
  actionLabel,
  actionTextClass,
  formatPrice,
  formatProbability,
  isBuySide,
  isSellSide,
} from "../lib/signal";
import Term from "./Term";

function pctText(v: number): string {
  return `${(v * 100).toFixed(0)}%`;
}

/** シグナルのフィールドから「AIのひとこと」を日本語文として自動合成する */
export default function SignalNarrative({ signal }: { signal: Signal }) {
  const p = signal.prob_up;
  const th = signal.thresholds;
  const action = signal.action;
  const raw = signal.raw_action;

  const actionSpan = (
    <span className={clsx("font-bold", actionTextClass(action))}>{actionLabel(action)}</span>
  );

  let body: React.ReactNode;

  if (signal.gate_passed === false && raw && raw !== "HOLD" && action === "HOLD") {
    // 素の予測は売買寄りだが成績テスト不合格で様子見に落とされたケース
    body = (
      <>
        AIの素の予測は「{actionLabel(raw)}」寄り(<Term k="prob_up">上がる確率</Term>{" "}
        {formatProbability(p)})ですが、この銘柄は<Term k="gate">成績テスト</Term>
        に不合格のため「様子見」にしています。
        {signal.confidence_reason && (
          <span className="text-slate-400">({signal.confidence_reason})</span>
        )}
      </>
    );
  } else if (p != null && th && isBuySide(action)) {
    const line = action === "BUY" ? th.buy : th.mild_buy;
    body = (
      <>
        判断は{actionSpan}。<Term k="prob_up">上がる確率</Term>は{formatProbability(p)}で、
        この銘柄の<Term k="threshold">{action === "BUY" ? "買いライン" : "やや買いライン"}</Term>(
        {pctText(line)})を超えています。
        {signal.gate_passed === true && (
          <>
            過去データでの<Term k="gate">成績テスト</Term>にも合格している、本命のシグナルです。
          </>
        )}
      </>
    );
  } else if (p != null && th && isSellSide(action)) {
    const line = action === "SELL" ? th.sell : th.mild_sell;
    body = (
      <>
        判断は{actionSpan}。<Term k="prob_up">上がる確率</Term>は{formatProbability(p)}と低く、
        <Term k="threshold">{action === "SELL" ? "売りライン" : "やや売りライン"}</Term>(
        {pctText(line)})を下回っています。
        {signal.gate_passed === true && <>持っている場合は手放すことを検討するサインです。</>}
      </>
    );
  } else if (p != null) {
    body = (
      <>
        判断は{actionSpan}。<Term k="prob_up">上がる確率</Term>は{formatProbability(p)}で、
        買いとも売りとも言い切れない<Term k="threshold">判断ライン</Term>の間にあります。
        今日は急いで動く必要はありません。
      </>
    );
  } else {
    body = <>{signal.reason || "本日の予測データがありません。"}</>;
  }

  const takeProfit = signal.take_profit_price;
  const stopPrice = signal.stop_price ?? signal.stop_loss;
  const showPlan =
    isBuySide(action) &&
    (signal.limit_price != null || takeProfit != null || stopPrice != null);

  return (
    <div className={clsx("rounded-xl border p-5", actionCardClass(action))}>
      <div className="mb-2 flex items-center gap-2 text-xs text-slate-400">
        <Sparkles size={14} />
        AIのひとこと
        <span className="text-slate-500">({signal.date})</span>
      </div>
      <p className="text-sm leading-relaxed text-slate-200">{body}</p>
      {showPlan && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3">
            {takeProfit != null && (
              // 日本語UI: 赤=上昇。利確は上のライン。
              <div className="rounded-lg bg-slate-900/60 px-3 py-2.5">
                <Term k="take_profit" className="text-xs text-slate-400">
                  利確ライン
                </Term>
                <div className="mt-1 text-base font-bold text-red-300">
                  {formatPrice(takeProfit)}
                  {signal.take_profit_pct != null && (
                    <span className="ml-1 text-xs font-normal text-red-300/70">
                      ({signedPct(signal.take_profit_pct)})
                    </span>
                  )}
                </div>
              </div>
            )}
            {stopPrice != null && (
              // 日本語UI: 青=下落。損切りは下のライン。
              <div className="rounded-lg bg-slate-900/60 px-3 py-2.5">
                <Term k="stop_loss" className="text-xs text-slate-400">
                  撤退ライン
                </Term>
                <div className="mt-1 text-base font-bold text-blue-300">
                  {formatPrice(stopPrice)}
                  {signal.stop_pct != null && (
                    <span className="ml-1 text-xs font-normal text-blue-300/70">
                      ({signedPct(signal.stop_pct)})
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
            {signal.limit_price != null && (
              <span>
                <Term k="limit_price">買ってよい上限</Term>{" "}
                <span className="font-semibold text-slate-200">
                  {formatPrice(signal.limit_price)}
                </span>
              </span>
            )}
            {signal.time_exit_days != null && (
              <span>
                <Term k="time_exit">手仕舞い期限</Term>{" "}
                <span className="font-semibold text-slate-200">
                  {signal.time_exit_days}営業日
                </span>
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function signedPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}
