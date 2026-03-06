"use client";

import { useEffect, useState } from "react";
import { DashboardIndexData } from "../types";
import { RefreshCw, TrendingUp, TrendingDown, Minus, ChevronUp, ChevronDown } from "lucide-react";
import Link from "next/link";
import { actionLabel, actionBadgeClass, probTextClass, confidenceLabel, confidenceBadgeClass } from "../lib/signal";

function ActionIcon({ action }: { action: string }) {
  switch (action) {
    case "BUY":       return <TrendingUp size={14} />;
    case "MILD_BUY":  return <ChevronUp size={14} />;
    case "MILD_SELL": return <ChevronDown size={14} />;
    case "SELL":      return <TrendingDown size={14} />;
    default:          return <Minus size={14} />;
  }
}

function describeRsi(rsi?: number) {
  if (rsi == null) {
    return {
      label: "データ不足",
      className: "text-slate-400",
      note: "RSIが算出できていません",
    };
  }

  if (rsi >= 70) {
    return {
      label: "買われすぎ気味",
      className: "text-red-300",
      note: "短期的な過熱に注意",
    };
  }

  if (rsi <= 30) {
    return {
      label: "売られすぎ気味",
      className: "text-emerald-300",
      note: "反発余地を確認",
    };
  }

  if (rsi >= 55) {
    return {
      label: "やや強い",
      className: "text-blue-300",
      note: "買い優勢だが過熱ではない",
    };
  }

  if (rsi <= 45) {
    return {
      label: "やや弱い",
      className: "text-amber-300",
      note: "売り優勢だが急落水準ではない",
    };
  }

  return {
    label: "中立",
    className: "text-slate-300",
    note: "売買の力が拮抗",
  };
}

function describeVolume(volume?: number, avg20?: number) {
  if (volume == null || avg20 == null || avg20 <= 0) {
    return {
      ratioText: "---",
      label: "比較不可",
      className: "text-slate-400",
      note: "20日平均と比較できません",
    };
  }

  const ratio = volume / avg20;

  if (ratio >= 1.5) {
    return {
      ratioText: `${ratio.toFixed(2)}x`,
      label: "かなり活発",
      className: "text-emerald-300",
      note: "普段より注目されている状態",
    };
  }

  if (ratio >= 1.1) {
    return {
      ratioText: `${ratio.toFixed(2)}x`,
      label: "やや活発",
      className: "text-blue-300",
      note: "平均より取引が多い",
    };
  }

  if (ratio >= 0.8) {
    return {
      ratioText: `${ratio.toFixed(2)}x`,
      label: "平均並み",
      className: "text-slate-300",
      note: "通常の出来高レンジ",
    };
  }

  return {
    ratioText: `${ratio.toFixed(2)}x`,
    label: "低調",
    className: "text-amber-300",
    note: "参加者が少なめ",
  };
}

export default function Home() {
  const [data, setData] = useState<DashboardIndexData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    const dataUrl = `${basePath}/dashboard_index.json`;

    fetch(dataUrl)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json: DashboardIndexData) => {
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
        <p className="text-sm text-slate-400 mb-6">
          「自信あり」は過去検証をクリアした状態、「自信なし」は予測値は表示するが売買は見送りの状態です。
        </p>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-8">
          <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5">
            <h3 className="text-lg font-bold text-white mb-2">RSIって何？（初心者向け）</h3>
            <p className="text-sm text-slate-300 leading-relaxed">
              RSIは「最近の値上がりの強さ」と「値下がりの強さ」を0〜100で表す指標です。
              目安として70以上は買われすぎ、30以下は売られすぎと言われます。
            </p>
            <div className="mt-3 text-xs text-slate-400 space-y-1">
              <p>・70以上: 短期的に過熱しやすいゾーン</p>
              <p>・30以下: 売られすぎで反発を探るゾーン</p>
              <p>・45〜55: 方向感が出にくい中立ゾーン</p>
            </div>
          </section>

          <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5">
            <h3 className="text-lg font-bold text-white mb-2">出来高って何？（初心者向け）</h3>
            <p className="text-sm text-slate-300 leading-relaxed">
              出来高は「その日に売買が成立した株数」です。値動きだけでなく、どれだけ参加者がいるかを見ます。
              この一覧では、当日の出来高を20日平均と比べて活発度を表示しています。
            </p>
            <div className="mt-3 text-xs text-slate-400 space-y-1">
              <p>・1.5x以上: 普段よりかなり注目されている状態</p>
              <p>・0.8x〜1.1x: だいたい平常運転</p>
              <p>・0.8x未満: 参加者が少なく値動きの信頼度に注意</p>
            </div>
          </section>

          <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 lg:col-span-2">
            <h3 className="text-lg font-bold text-white mb-2">アルゴリズムで「買い」になる条件（現行ロジック）</h3>
            <p className="text-sm text-slate-300 leading-relaxed">
              このシステムは、過去の値動き・テクニカル指標・出来高など複数の特徴量から
              「翌営業日に上がる確率（上昇確率）」を算出し、その確率とボラティリティで売買アクションを決めています。
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <div className="rounded-lg border border-slate-700/80 bg-slate-950/40 p-4">
                <h4 className="text-sm font-semibold text-slate-100 mb-2">買い判定のしきい値</h4>
                <div className="space-y-2 text-xs text-slate-300">
                  <p>
                    <span className="font-semibold text-emerald-300">BUY:</span>{" "}
                    上昇確率 <span className="font-semibold">80%以上</span> かつ
                    ボラティリティ <span className="font-semibold">4%以下</span>
                  </p>
                  <p>
                    <span className="font-semibold text-blue-300">MILD BUY:</span>{" "}
                    上昇確率 <span className="font-semibold">65%以上80%未満</span>
                  </p>
                  <p className="text-slate-400">
                    例外: 上昇確率が80%以上でも、ボラティリティが4%を超えると
                    「BUY」ではなく「MILD BUY」に引き下げます（急変動リスクを抑えるため）。
                  </p>
                </div>
              </div>

              <div className="rounded-lg border border-slate-700/80 bg-slate-950/40 p-4">
                <h4 className="text-sm font-semibold text-slate-100 mb-2">上昇確率に効く主な要素</h4>
                <div className="space-y-1 text-xs text-slate-300">
                  <p>・リターン: 1/2/3/5/10/20日リターンの流れ</p>
                  <p>・トレンド: 移動平均線乖離、短中期クロスの向き</p>
                  <p>・オシレーター: RSI、MACD、ボリンジャーバンド位置</p>
                  <p>・変動率: ATR%、20日ボラティリティ</p>
                  <p>・出来高: 増減率、短期/中期の出来高比率</p>
                  <p>・ローソク足/日柄: 実体やヒゲ、曜日・月初月末要因</p>
                </div>
              </div>
            </div>

            <p className="mt-3 text-xs text-slate-400">
              注: これは現在の実装ルールの説明です。相場急変時は条件を満たしても外れることがあります。
            </p>
          </section>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {Object.keys(data.tickers).map(code => {
                const ticker = data.tickers[code];
                const latestData = ticker.latest_data;
                const latestSignal = ticker.latest_signal;
                const rsiInfo = describeRsi(latestData?.rsi ?? undefined);
                const volumeInfo = describeVolume(
                  latestData?.volume ?? undefined,
                  ticker.avg_volume_20 ?? undefined,
                );

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
                                <div className="flex flex-col items-end gap-2">
                                    <div className={`px-3 py-1 rounded-full text-xs font-bold flex items-center gap-1 ${actionBadgeClass(latestSignal.action)}`}>
                                        <ActionIcon action={latestSignal.action} />
                                        {actionLabel(latestSignal.action)}
                                    </div>
                                    <div className={`px-3 py-1 rounded-full text-xs font-bold border ${confidenceBadgeClass(latestSignal)}`}>
                                        {confidenceLabel(latestSignal)}
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="flex justify-between items-end">
                            <div>
                                <div className="text-xs text-slate-500 uppercase mb-1">現在値</div>
                                <div className="text-2xl font-bold text-white">
                                    {latestData?.close != null ? `¥${latestData.close.toLocaleString()}` : "---"}
                                </div>
                            </div>
                            {latestSignal && (
                                <div className="text-right">
                                    <div className="text-xs text-slate-500 uppercase mb-1">上昇確率（予測値）</div>
                                    <div className={`text-lg font-bold ${probTextClass(latestSignal.prob_up)}`}>
                                        {(latestSignal.prob_up * 100).toFixed(1)}%
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="mt-4 pt-4 border-t border-slate-800/80 space-y-3">
                          <div>
                            <div className="text-xs text-slate-500 uppercase mb-1">RSI (14)</div>
                            <div className="flex items-center justify-between gap-2">
                              <div className="text-sm text-slate-200">
                                {latestData?.rsi != null ? latestData.rsi.toFixed(1) : "---"}
                              </div>
                              <div className={`text-xs font-semibold ${rsiInfo.className}`}>{rsiInfo.label}</div>
                            </div>
                            <p className="text-xs text-slate-400 mt-1">{rsiInfo.note}</p>
                          </div>

                          <div>
                            <div className="text-xs text-slate-500 uppercase mb-1">出来高（20日平均比）</div>
                            <div className="flex items-center justify-between gap-2">
                              <div className="text-sm text-slate-200">
                                {latestData?.volume != null ? latestData.volume.toLocaleString() : "---"}
                              </div>
                              <div className={`text-xs font-semibold ${volumeInfo.className}`}>
                                {volumeInfo.ratioText} / {volumeInfo.label}
                              </div>
                            </div>
                            <p className="text-xs text-slate-400 mt-1">{volumeInfo.note}</p>
                          </div>
                        </div>
                    </Link>
                );
            })}
        </div>
      </div>
    </main>
  );
}
