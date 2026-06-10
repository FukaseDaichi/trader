export async function fetchJson<T>(
  path: string,
  isValid: (v: unknown) => v is T,
): Promise<T | null> {
  try {
    const res = await fetch(path);
    if (!res.ok) return null;
    const data: unknown = await res.json();
    return isValid(data) ? data : null;
  } catch {
    return null;
  }
}

export function isAvailablePayload(v: unknown): v is { available: boolean } {
  return typeof v === "object" && v !== null &&
    typeof (v as { available?: unknown }).available === "boolean";
}
