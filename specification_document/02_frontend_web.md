# フロントエンド (web/) 問題一覧

## 1. データフェッチ

### 1.1 [HIGH] fetch に `res.ok` チェック未実装

**箇所**: `page.tsx` L122-124, `StockDetailContent.tsx` L25-27

```typescript
fetch(dataUrl)
  .then((res) => res.json())
```

**問題**: サーバーが 404 や 500 を返した場合でも `.json()` を実行。エラーレスポンスのパースに失敗し、不明瞭なエラーメッセージが表示される。

**修正方針**:
```typescript
fetch(dataUrl)
  .then((res) => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  })
```

---

### 1.2 [MEDIUM] フェッチデータのランタイムバリデーション未実装

**箇所**: `page.tsx` L124, `StockDetailContent.tsx` L27

**問題**: TypeScript の型キャストはコンパイル時のみ有効。JSON の構造が型定義と一致しない場合、ランタイムでクラッシュする。

**修正方針**: `zod` や手動バリデーション関数で JSON 構造を検証。

---

### 1.3 [MEDIUM] データフェッチロジックの重複

**箇所**: `page.tsx`, `StockDetailContent.tsx`

**問題**: 両ファイルが同一の fetch パターンを独立実装。バグ修正が一方にのみ適用されるリスク。

**修正方針**: `useHistoryData()` カスタムフックに共通化。

---

### 1.4 [LOW] キャッシュ戦略の欠如

**問題**: `history_data.json` のフェッチに Cache-Control, ETag, stale-while-revalidate なし。ページ遷移のたびに全データを再取得。

**修正方針**: `useSWR` や `react-query` の導入でキャッシュ・重複排除・再検証を実現。

---

## 2. TypeScript 型定義

### 2.1 [HIGH] OHLCV フィールドの型が実データと不一致

**箇所**: `types/index.ts` L2-6

```typescript
export interface TickerData {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
```

**問題**: 実際のデータは null を含みうる。`StockChart.tsx` L65 では `if (open == null)` のガードが存在し、コンポーネント側は null を想定している。型定義と実装の乖離。

**修正方針**: `number | null` に型修正。

---

### 2.2 [MEDIUM] `signal.ts` の switch 文に default ケース未設定

**箇所**: `signal.ts` 全4関数

**問題**: TypeScript のコンパイル時網羅チェックは有効だが、ランタイムで未知のアクション文字列 (将来のバックエンド変更等) が来た場合、`undefined` を返す。

**修正方針**: 全 switch 文に `default: return "不明";` を追加。

---

## 3. コンポーネント

### 3.1 [HIGH] `StockChart.tsx` — 空配列での Math.min/max がクラッシュ

**箇所**: L183-184

```typescript
const min = Math.min(...prices);
const max = Math.max(...prices);
```

**問題**: `prices` が空配列の場合、`Math.min()` は `Infinity`、`Math.max()` は `-Infinity` を返す。Y軸のドメインが NaN となりチャート描画が破壊される。

**修正方針**:
```typescript
const min = prices.length > 0 ? Math.min(...prices) : 0;
const max = prices.length > 0 ? Math.max(...prices) : 100;
```

---

### 3.2 [MEDIUM] `StockChart.tsx` — 冗長な fill 三項演算子

**箇所**: L101

```typescript
fill={isUp ? color : color}
```

**問題**: 条件に関わらず同一値。陽線/陰線の塗り分けが機能していない。

**修正方針**: 意図確認の上、`fill={isUp ? color : "transparent"}` 等に修正。

---

### 3.3 [MEDIUM] `SignalCard.tsx` — `limit_price` にカンマ区切り未適用

**箇所**: L66-67

```typescript
<span className="text-slate-200 font-mono">¥{signal.limit_price}</span>
```

**問題**: `close` には `.toLocaleString()` を使用しているが、`limit_price` と `stop_loss` にはフォーマットなし。`¥15000` と `¥15,000` が混在する。

**修正方針**: `signal.limit_price?.toLocaleString()` に統一。

---

### 3.4 [LOW] `StockDetailContent.tsx` — 配列インデックスを React key に使用

**箇所**: L93

```typescript
<div key={idx} className="p-3 ...">
```

**修正方針**: `key={entry.date}` を使用。

---

## 4. アクセシビリティ

### 4.1 [MEDIUM] 戻るボタンに aria-label 未設定

**箇所**: `StockDetailContent.tsx` L57-59

```typescript
<Link href="/" className="p-2 ...">
    <ArrowLeft size={24} />
</Link>
```

**問題**: アイコンのみのリンクにテキストや aria-label がなく、スクリーンリーダーで目的が不明。

**修正方針**: `aria-label="ダッシュボードに戻る"` を追加。

---

### 4.2 [MEDIUM] チャートがスクリーンリーダー非対応

**箇所**: `StockChart.tsx` 全体

**問題**: SVG チャートに `aria-label`、`role="img"`、代替テキストなし。

**修正方針**: チャートコンテナに `aria-label` と `role="img"` を追加。

---

### 4.3 [LOW] インジケータートグルに `aria-pressed` 未設定

**箇所**: `StockChart.tsx` トグルボタン群

**修正方針**: 選択状態に応じて `aria-pressed={true/false}` を付与。

---

## 5. CSS / UI

### 5.1 [MEDIUM] ダークテーマの FOUC (白フラッシュ)

**箇所**: `globals.css` L3-6

```css
:root {
  --background: #ffffff;
  --foreground: #171717;
}
```

**問題**: `:root` のデフォルト背景が白。React ハイドレーション前にダークテーマの `bg-slate-950` と白背景が同時に表示される。

**修正方針**: `:root` の `--background` を `#0a0a0a` (slate-950 相当) に変更。

---

### 5.2 [LOW] Geist フォントが未使用

**箇所**: `layout.tsx` L5-13, `globals.css` L25-26

**問題**: `next/font/google` で Geist をロードしているが、`globals.css` が `font-family: Arial` でハードコード上書き。日本語テキストは全てシステムフォントにフォールバック。

**修正方針**: Geist フォントの読み込みを削除し、日本語対応フォントスタック (`"Noto Sans JP", "Hiragino Sans", sans-serif` 等) に統一。

---

### 5.3 [LOW] `tailwind-merge` が未使用依存関係

**箇所**: `package.json`

**問題**: インポートされていない依存関係。

**修正方針**: `npm uninstall tailwind-merge`

---

## 6. Next.js 設定

### 6.1 [MEDIUM] `trailingSlash` 未設定

**箇所**: `next.config.ts`

**問題**: GitHub Pages の静的ホスティングでは `trailingSlash: true` が必要な場合がある。未設定だと `/trader/stocks/1234` で 404 が発生する可能性。

**修正方針**: `trailingSlash: true` を追加して動作検証。

---

### 6.2 [MEDIUM] エラーバウンダリ (`error.tsx`) 未実装

**問題**: ランタイムエラーでページ全体が白画面化する。`error.tsx` がないため、回復手段がない。

**修正方針**: `src/app/error.tsx` と `src/app/stocks/[ticker]/error.tsx` を追加。

---

### 6.3 [LOW] `not-found.tsx` 未実装

**問題**: 存在しないティッカー URL へのアクセスでカスタム 404 ページが表示されない。

---

### 6.4 [LOW] `loading.tsx` 未実装

**問題**: ルートレベルの Suspense バウンダリがなく、ローディング中に何も表示されない。

---

## 7. ハードコード

### 7.1 [LOW] 教育セクションの閾値がハードコード

**箇所**: `page.tsx` L197-198

```tsx
上昇確率 <span>80%以上</span> かつ ボラティリティ <span>4%以下</span>
```

**問題**: バックエンドの閾値変更時にフロントエンドの表示が追従しない。

**修正方針**: 設定 JSON からの動的読み取り、または同期必要な旨のコメント追加。

---

### 7.2 [LOW] `dynamicParams` 未設定

**箇所**: `stocks/[ticker]/page.tsx`

**修正方針**: `export const dynamicParams = false;` を明示的に追加。
