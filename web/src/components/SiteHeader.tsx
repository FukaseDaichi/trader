"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { MacroLatest } from "../types";
import { fetchJson } from "../lib/fetchJson";

function moodToken(bias: string): { text: string; className: string } {
  switch (bias) {
    case "risk_on":
      return { text: "市場ムード: 前向き", className: "bg-red-500/15 text-red-300 border-red-500/40" };
    case "risk_off":
      return { text: "市場ムード: 慎重", className: "bg-blue-500/15 text-blue-300 border-blue-500/40" };
    default:
      return { text: "市場ムード: 中立", className: "bg-slate-800 text-slate-300 border-slate-600" };
  }
}

function MoodPill() {
  const [macro, setMacro] = useState<MacroLatest | null>(null);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetchJson<MacroLatest>(
      `${basePath}/curation/macro_latest.json`,
      (v): v is MacroLatest => typeof v === "object" && v !== null,
    ).then(setMacro);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: Event) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  if (!macro?.market_bias) return null;
  const mood = moodToken(macro.market_bias);

  return (
    <span ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`rounded-full border px-3 py-1 text-xs font-semibold ${mood.className}`}
      >
        {mood.text}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-72 rounded-xl border border-slate-600 bg-slate-800 p-3 shadow-xl">
          <p className="text-xs leading-relaxed text-slate-300">
            {macro.summary ||
              "金利や為替など市場全体の環境から、相場が追い風か向かい風かを週次で判定しています。"}
          </p>
          {macro.as_of && <p className="mt-1 text-xs text-slate-500">基準日: {macro.as_of}</p>}
        </div>
      )}
    </span>
  );
}

export default function SiteHeader({ updated }: { updated?: string }) {
  const pathname = usePathname();
  const navClass = (active: boolean) =>
    `rounded-full px-3 py-1 text-sm transition-colors ${
      active ? "bg-slate-800 text-white" : "text-slate-400 hover:text-white"
    }`;
  return (
    <header className="mx-auto mb-8 flex max-w-7xl flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-2 md:gap-4">
        <Link href="/" className="text-xl font-bold tracking-tight text-white md:text-2xl">
          AI株式トレーダー
        </Link>
        <nav className="flex items-center gap-1">
          <Link href="/" className={navClass(pathname === "/")}>
            ホーム
          </Link>
          <Link href="/performance" className={navClass(pathname.startsWith("/performance"))}>
            成績
          </Link>
        </nav>
      </div>
      <div className="flex items-center gap-3">
        <MoodPill />
        {updated && <span className="text-xs text-slate-500">更新: {updated}</span>}
      </div>
    </header>
  );
}
