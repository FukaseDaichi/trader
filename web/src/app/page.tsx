"use client";

import { useEffect, useState } from "react";
import { DashboardIndexData } from "../types";
import SiteHeader from "../components/SiteHeader";
import SiteFooter from "../components/SiteFooter";
import TodayHero from "../components/TodayHero";
import StockExplorer from "../components/StockExplorer";
import PerformanceCard from "../components/PerformanceCard";
import PortfolioCard from "../components/PortfolioCard";
import GlossaryAccordion from "../components/GlossaryAccordion";

export default function Home() {
  const [data, setData] = useState<DashboardIndexData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${basePath}/dashboard_index.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json: DashboardIndexData) => {
        setData(json);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load dashboard index", err);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        読み込み中...
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-red-400">
        データの読み込みに失敗しました。
      </div>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 p-4 text-slate-200 md:p-8">
      <SiteHeader updated={data.last_update} />
      <div className="mx-auto max-w-7xl">
        <TodayHero data={data} />

        <h2 className="mb-4 text-xl font-bold text-white">全銘柄をさがす</h2>
        <StockExplorer data={data} />

        <PerformanceCard />
        <PortfolioCard />
        <GlossaryAccordion />
      </div>
      <SiteFooter />
    </main>
  );
}
