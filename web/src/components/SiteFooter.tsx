const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

export default function SiteFooter() {
  return (
    <footer className="mx-auto mt-12 flex max-w-7xl flex-col items-start justify-between gap-3 border-t border-slate-800/80 pb-10 pt-6 text-xs text-slate-500 sm:flex-row sm:items-center">
      <p>本サイトはAIによる予測の実験プロジェクトです。投資の最終判断はご自身の責任でお願いします。</p>
      <div className="flex shrink-0 items-center gap-4">
        <a
          href="https://github.com/FukaseDaichi/trader/tree/main/reports"
          target="_blank"
          rel="noopener noreferrer"
          className="underline decoration-dotted hover:text-slate-300"
        >
          週次レポート
        </a>
        <a href={`${basePath}/curation/decision_latest.json`} className="underline decoration-dotted hover:text-slate-300">
          銘柄入替ログ
        </a>
      </div>
    </footer>
  );
}
