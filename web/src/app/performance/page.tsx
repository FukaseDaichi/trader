import Link from "next/link";
import PerformanceDetail from "../../components/PerformanceDetail";

export default function PerformancePage() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-200 p-4 md:p-8">
      <header className="max-w-7xl mx-auto mb-8 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <Link href="/" className="text-blue-400 text-sm hover:underline mb-2 inline-block">
            ← ホーム
          </Link>
          <h1 className="text-3xl font-bold text-white tracking-tight">実績トラックレコード</h1>
        </div>
      </header>
      <div className="max-w-7xl mx-auto">
        <PerformanceDetail />
      </div>
    </main>
  );
}
