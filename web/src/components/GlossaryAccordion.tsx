"use client";

import { BookOpen } from "lucide-react";
import { GLOSSARY } from "../lib/glossary";

/** ホーム下部の用語集。glossary.ts から自動生成され、Term と説明が二重管理にならない。 */
export default function GlossaryAccordion() {
  const entries = Object.entries(GLOSSARY);
  return (
    <section className="mb-8 rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <h2 className="mb-1 flex items-center gap-2 text-lg font-bold text-white">
        <BookOpen size={18} className="text-slate-400" />
        用語集
      </h2>
      <p className="mb-4 text-xs text-slate-400">
        画面の点線つきの言葉は、タップするとその場でも説明が出ます。
      </p>
      <div className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
        {entries.map(([key, e]) => (
          <details key={key} className="group border-b border-slate-800/80 py-2">
            <summary className="flex cursor-pointer list-none items-center justify-between text-sm text-slate-200 hover:text-white">
              <span>
                {e.term}
                {e.formal && <span className="ml-2 text-xs text-slate-500">{e.formal}</span>}
              </span>
              <span className="text-slate-500 transition-transform group-open:rotate-90">›</span>
            </summary>
            <p className="mt-2 text-xs leading-relaxed text-slate-400">{e.short}</p>
            {e.analogy && (
              <p className="mt-1 text-xs leading-relaxed text-slate-500">💡 {e.analogy}</p>
            )}
          </details>
        ))}
      </div>
    </section>
  );
}
