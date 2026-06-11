"use client";

import { useEffect, useRef, useState } from "react";
import { GLOSSARY } from "../lib/glossary";

interface TermProps {
  k: string;
  children?: React.ReactNode;
  className?: string;
}

/**
 * 用語ツールチップ。点線下線の言葉をタップ/ホバーすると噛み砕いた解説を出す。
 * 注意: <Link> の内側では使わない(タップが遷移と衝突するため)。
 */
export default function Term({ k, children, className = "" }: TermProps) {
  const entry = GLOSSARY[k];
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [shiftX, setShiftX] = useState(0);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: Event) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // 画面端で見切れないよう水平方向をクランプ
  useEffect(() => {
    if (!open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShiftX(0);
      return;
    }
    const el = popRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const pad = 12;
    if (rect.left < pad) {
      setShiftX(pad - rect.left);
    } else if (rect.right > window.innerWidth - pad) {
      setShiftX(window.innerWidth - pad - rect.right);
    }
  }, [open]);

  if (!entry) return <span className={className}>{children}</span>;

  return (
    <span ref={wrapRef} className={`relative inline-block ${className}`}>
      <button
        type="button"
        className="cursor-help border-b border-dotted border-slate-500 text-left text-inherit hover:border-slate-300"
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        {children ?? entry.term}
      </button>
      {open && (
        <div
          ref={popRef}
          role="tooltip"
          style={{ marginLeft: shiftX }}
          className="absolute left-1/2 top-full z-50 mt-2 w-72 -translate-x-1/2 rounded-xl border border-slate-600 bg-slate-800 p-3 text-left font-normal normal-case shadow-xl"
        >
          <div className="text-sm font-bold text-slate-100">{entry.term}</div>
          <p className="mt-1 text-xs leading-relaxed text-slate-300">{entry.short}</p>
          {entry.analogy && (
            <p className="mt-2 text-xs leading-relaxed text-slate-400">💡 {entry.analogy}</p>
          )}
          {entry.formal && (
            <p className="mt-2 border-t border-slate-700 pt-2 text-xs text-slate-500">
              正式名: {entry.formal}
            </p>
          )}
        </div>
      )}
    </span>
  );
}
