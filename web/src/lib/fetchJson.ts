import { DashboardIndexData, TickerDetailData } from "../types";

export async function fetchJson<T>(
  path: string,
  isValid: (v: unknown) => v is T,
): Promise<T | null> {
  try {
    const res = await fetch(path);
    if (!res.ok) {
      console.warn(`[fetchJson] ${path}: HTTP ${res.status}`);
      return null;
    }
    const data: unknown = await res.json();
    if (!isValid(data)) {
      console.warn(`[fetchJson] ${path}: invalid payload shape`);
      return null;
    }
    return data;
  } catch (err) {
    console.warn(`[fetchJson] ${path}: fetch/parse failed`, err);
    return null;
  }
}

export function isAvailablePayload(v: unknown): v is { available: boolean } {
  return typeof v === "object" && v !== null &&
    typeof (v as { available?: unknown }).available === "boolean";
}

// Minimal structural guards for the two REQUIRED data contracts. They check
// just the fields the pages dereference unconditionally, so a malformed
// export surfaces as the explicit error state instead of a render crash.
export function isDashboardIndex(v: unknown): v is DashboardIndexData {
  if (typeof v !== "object" || v === null) return false;
  const o = v as { last_update?: unknown; tickers?: unknown };
  return (
    typeof o.last_update === "string" &&
    typeof o.tickers === "object" &&
    o.tickers !== null &&
    !Array.isArray(o.tickers)
  );
}

export function isTickerDetail(v: unknown): v is TickerDetailData {
  if (typeof v !== "object" || v === null) return false;
  const o = v as { ticker?: unknown; data?: unknown; signals?: unknown };
  return (
    typeof o.ticker === "string" &&
    Array.isArray(o.data) &&
    Array.isArray(o.signals)
  );
}
