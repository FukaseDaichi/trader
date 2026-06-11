/** 日本語向けのゆるい正規化: NFKC(全角/半角) + 小文字化 + カタカナ→ひらがな */
export function normalizeJa(s: string): string {
  return s
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[ァ-ヶ]/g, (ch) =>
      String.fromCharCode(ch.charCodeAt(0) - 0x60),
    );
}

/** 銘柄名 or コードに部分一致するか(「6701」「6701.jp」「とよた」「トヨタ」いずれもヒット) */
export function matchesTicker(query: string, code: string, name: string): boolean {
  const q = normalizeJa(query.trim());
  if (!q) return true;
  return normalizeJa(name).includes(q) || normalizeJa(code).includes(q);
}
