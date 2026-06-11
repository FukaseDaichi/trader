"use client";

import SiteHeader from "../../components/SiteHeader";
import SiteFooter from "../../components/SiteFooter";
import PerformanceHeadline from "../../components/PerformanceHeadline";
import PerformanceDetail from "../../components/PerformanceDetail";
import ModelQualityCard from "../../components/ModelQualityCard";

export default function PerformancePage() {
  return (
    <main className="min-h-screen bg-slate-950 p-4 text-slate-200 md:p-8">
      <SiteHeader />
      <div className="mx-auto max-w-7xl">
        <h1 className="mb-4 text-2xl font-bold tracking-tight text-white">AIの成績</h1>
        <PerformanceHeadline />
        <PerformanceDetail />
        <ModelQualityCard />
      </div>
      <SiteFooter />
    </main>
  );
}
