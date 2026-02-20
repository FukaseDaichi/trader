"use client";

import { useState, useMemo } from "react";
import {
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ComposedChart,
  ResponsiveContainer,
  Bar,
  Cell,
  ReferenceLine,
} from "recharts";
import { TickerData } from "../types";
import { format, parseISO } from "date-fns";

interface StockChartProps {
  data: TickerData[];
  tickerName: string;
}

type DateRange = "1m" | "3m" | "6m" | "1y" | "all";

const DATE_RANGE_OPTIONS: { value: DateRange; label: string }[] = [
  { value: "1m", label: "1ヶ月" },
  { value: "3m", label: "3ヶ月" },
  { value: "6m", label: "6ヶ月" },
  { value: "1y", label: "1年" },
  { value: "all", label: "全期間" },
];

const RANGE_TO_DAYS: Record<DateRange, number> = {
  "1m": 22,
  "3m": 66,
  "6m": 132,
  "1y": 250,
  "all": Infinity,
};

interface IndicatorToggles {
  ma5: boolean;
  ma20: boolean;
  ma60: boolean;
}

// Custom Candlestick shape for Recharts
interface CandlestickProps {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: TickerData;
  low?: number;
  high?: number;
  yAxis?: { scale: (val: number) => number };
}

const Candlestick = (props: CandlestickProps) => {
  const { x, width, payload, yAxis } = props;
  if (!payload || !x || !width || !yAxis?.scale) return null;

  const { open, close, high, low } = payload;
  if (open == null || close == null || high == null || low == null) return null;

  const scale = yAxis.scale;
  const isUp = close >= open;
  const color = isUp ? "#ef4444" : "#3b82f6"; // 赤=陽線, 青=陰線 (日本式)
  const bodyTop = scale(Math.max(open, close));
  const bodyBottom = scale(Math.min(open, close));
  const bodyHeight = Math.max(bodyBottom - bodyTop, 1);
  const wickX = x + width / 2;

  return (
    <g>
      {/* Upper wick */}
      <line
        x1={wickX}
        y1={scale(high)}
        x2={wickX}
        y2={bodyTop}
        stroke={color}
        strokeWidth={1}
      />
      {/* Lower wick */}
      <line
        x1={wickX}
        y1={bodyBottom}
        x2={wickX}
        y2={scale(low)}
        stroke={color}
        strokeWidth={1}
      />
      {/* Body */}
      <rect
        x={x + 1}
        y={bodyTop}
        width={Math.max(width - 2, 1)}
        height={bodyHeight}
        fill={isUp ? color : color}
        stroke={color}
        strokeWidth={1}
      />
    </g>
  );
};

// Custom tooltip
interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: TickerData }>;
  label?: string;
}

const CustomPriceTooltip = ({ active, payload, label }: CustomTooltipProps) => {
  if (!active || !payload || !payload[0]) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs shadow-xl">
      <p className="font-bold text-slate-200 mb-2">{label ? format(parseISO(label), "yyyy-MM-dd") : ""}</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span className="text-slate-400">始値:</span>
        <span className="text-slate-200 text-right">¥{d.open?.toLocaleString()}</span>
        <span className="text-slate-400">高値:</span>
        <span className="text-red-400 text-right">¥{d.high?.toLocaleString()}</span>
        <span className="text-slate-400">安値:</span>
        <span className="text-blue-400 text-right">¥{d.low?.toLocaleString()}</span>
        <span className="text-slate-400">終値:</span>
        <span className="text-slate-200 text-right font-bold">¥{d.close?.toLocaleString()}</span>
        {d.volume != null && (
          <>
            <span className="text-slate-400">出来高:</span>
            <span className="text-slate-200 text-right">{d.volume?.toLocaleString()}</span>
          </>
        )}
        {d.ma_5 != null && (
          <>
            <span className="text-yellow-400">MA5:</span>
            <span className="text-slate-200 text-right">¥{d.ma_5?.toLocaleString()}</span>
          </>
        )}
        {d.ma_20 != null && (
          <>
            <span className="text-sky-400">MA20:</span>
            <span className="text-slate-200 text-right">¥{d.ma_20?.toLocaleString()}</span>
          </>
        )}
        {d.ma_60 != null && (
          <>
            <span className="text-green-400">MA60:</span>
            <span className="text-slate-200 text-right">¥{d.ma_60?.toLocaleString()}</span>
          </>
        )}
      </div>
    </div>
  );
};

export default function StockChart({ data, tickerName }: StockChartProps) {
  const [dateRange, setDateRange] = useState<DateRange>("6m");
  const [indicators, setIndicators] = useState<IndicatorToggles>({
    ma5: true,
    ma20: true,
    ma60: true,
  });

  // Slice data based on date range
  const filteredData = useMemo(() => {
    const days = RANGE_TO_DAYS[dateRange];
    if (days === Infinity) return data;
    return data.slice(-days);
  }, [data, dateRange]);

  // Determine min/max for Y-axis scaling
  const { minPrice, maxPrice } = useMemo(() => {
    const prices: number[] = [];
    filteredData.forEach((d) => {
      if (d.high != null) prices.push(d.high);
      if (d.low != null) prices.push(d.low);
      if (d.close != null) prices.push(d.close);
    });
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const margin = (max - min) * 0.05;
    return { minPrice: min - margin, maxPrice: max + margin };
  }, [filteredData]);

  // Volume max for scaling
  const maxVolume = useMemo(() => {
    const vols = filteredData.map((d) => d.volume).filter((v) => v != null);
    return Math.max(...vols, 1);
  }, [filteredData]);

  const toggleIndicator = (key: keyof IndicatorToggles) => {
    setIndicators((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="w-full bg-slate-900 rounded-xl p-4 shadow-lg border border-slate-800">
      {/* Header + Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
        <h3 className="text-xl font-bold text-slate-100">{tickerName}</h3>

        {/* Control Panel */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Date Range Selector */}
          <div className="flex rounded-lg border border-slate-700 overflow-hidden">
            {DATE_RANGE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setDateRange(opt.value)}
                className={`px-3 py-1 text-xs font-medium transition-colors ${
                  dateRange === opt.value
                    ? "bg-blue-600 text-white"
                    : "bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-slate-200"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Indicator Toggles */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => toggleIndicator("ma5")}
              className={`px-2 py-1 text-xs rounded font-medium transition-colors ${
                indicators.ma5
                  ? "bg-yellow-500/20 text-yellow-400 border border-yellow-500/50"
                  : "bg-slate-800 text-slate-500 border border-slate-700"
              }`}
            >
              MA5
            </button>
            <button
              onClick={() => toggleIndicator("ma20")}
              className={`px-2 py-1 text-xs rounded font-medium transition-colors ${
                indicators.ma20
                  ? "bg-sky-500/20 text-sky-400 border border-sky-500/50"
                  : "bg-slate-800 text-slate-500 border border-slate-700"
              }`}
            >
              MA20
            </button>
            <button
              onClick={() => toggleIndicator("ma60")}
              className={`px-2 py-1 text-xs rounded font-medium transition-colors ${
                indicators.ma60
                  ? "bg-green-500/20 text-green-400 border border-green-500/50"
                  : "bg-slate-800 text-slate-500 border border-slate-700"
              }`}
            >
              MA60
            </button>
          </div>
        </div>
      </div>

      {/* === Price Chart (Candlestick) === */}
      <div className="h-[350px]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={filteredData}
            margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
            <XAxis
              dataKey="date"
              stroke="#94a3b8"
              tickFormatter={(str) => {
                try { return format(parseISO(str), "MM/dd"); } catch { return str; }
              }}
              minTickGap={40}
              fontSize={11}
            />
            <YAxis
              yAxisId="price"
              domain={[minPrice, maxPrice]}
              stroke="#94a3b8"
              tickFormatter={(val) => val.toLocaleString()}
              fontSize={11}
            />
            <Tooltip content={<CustomPriceTooltip />} />

            {/* Candlestick via Bar with custom shape */}
            <Bar
              dataKey="high"
              yAxisId="price"
              shape={<Candlestick />}
              isAnimationActive={false}
            />

            {/* Moving Averages */}
            {indicators.ma5 && (
              <Line
                type="monotone"
                dataKey="ma_5"
                yAxisId="price"
                stroke="#facc15"
                dot={false}
                strokeWidth={1.5}
                name="MA5"
                connectNulls
                isAnimationActive={false}
              />
            )}
            {indicators.ma20 && (
              <Line
                type="monotone"
                dataKey="ma_20"
                yAxisId="price"
                stroke="#38bdf8"
                dot={false}
                strokeWidth={1.5}
                name="MA20"
                connectNulls
                isAnimationActive={false}
              />
            )}
            {indicators.ma60 && (
              <Line
                type="monotone"
                dataKey="ma_60"
                yAxisId="price"
                stroke="#4ade80"
                dot={false}
                strokeWidth={1.5}
                name="MA60"
                connectNulls
                isAnimationActive={false}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Legend for MAs */}
      <div className="flex items-center gap-4 mt-2 px-2 text-xs text-slate-400">
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-0.5 bg-red-500 rounded" /> 陽線
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-0.5 bg-blue-500 rounded" /> 陰線
        </span>
        {indicators.ma5 && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-0.5 bg-yellow-400 rounded" /> MA5
          </span>
        )}
        {indicators.ma20 && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-0.5 bg-sky-400 rounded" /> MA20
          </span>
        )}
        {indicators.ma60 && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-0.5 bg-green-400 rounded" /> MA60
          </span>
        )}
      </div>

      {/* === Volume Sub-chart === */}
      <div className="h-[100px] mt-4 border-t border-slate-800 pt-3">
        <h4 className="text-sm font-semibold text-slate-400 mb-1">出来高</h4>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={filteredData} margin={{ top: 0, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
            <XAxis dataKey="date" hide />
            <YAxis
              domain={[0, maxVolume * 1.1]}
              stroke="#94a3b8"
              tickFormatter={(val) => {
                if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(0)}M`;
                if (val >= 1_000) return `${(val / 1_000).toFixed(0)}K`;
                return val.toString();
              }}
              fontSize={10}
            />
            <Tooltip
              contentStyle={{ backgroundColor: "#1e293b", borderColor: "#334155", color: "#f1f5f9" }}
              labelFormatter={(label) => {
                try { return format(parseISO(label), "yyyy-MM-dd"); } catch { return label; }
              }}
              formatter={(value: number | undefined) => [value != null ? value.toLocaleString() : "---", "出来高"]}
            />
            <Bar dataKey="volume" isAnimationActive={false}>
              {filteredData.map((entry, index) => {
                const isUp = entry.close >= entry.open;
                return (
                  <Cell
                    key={`vol-${index}`}
                    fill={isUp ? "rgba(239, 68, 68, 0.5)" : "rgba(59, 130, 246, 0.5)"}
                  />
                );
              })}
            </Bar>
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* === RSI Sub-chart === */}
      <div className="h-[120px] mt-4 border-t border-slate-800 pt-3">
        <h4 className="text-sm font-semibold text-slate-400 mb-1">RSI (14)</h4>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={filteredData} margin={{ top: 0, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
            <XAxis dataKey="date" hide />
            <YAxis domain={[0, 100]} stroke="#94a3b8" ticks={[30, 50, 70]} fontSize={10} />
            <Tooltip
              contentStyle={{ backgroundColor: "#1e293b", borderColor: "#334155", color: "#f1f5f9" }}
              labelFormatter={(label) => {
                try { return format(parseISO(label), "yyyy-MM-dd"); } catch { return label; }
              }}
              formatter={(value: number | undefined) => [value != null ? value.toFixed(1) : "---", "RSI"]}
            />
            <ReferenceLine y={70} stroke="#ef4444" strokeDasharray="3 3" strokeWidth={1} />
            <ReferenceLine y={30} stroke="#22c55e" strokeDasharray="3 3" strokeWidth={1} />
            <Line
              type="monotone"
              dataKey="rsi"
              stroke="#c084fc"
              dot={false}
              strokeWidth={2}
              isAnimationActive={false}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
