# フロントエンド仕様

更新日: 2026-06-11 JST

## 技術スタック

- Next.js 16.1.2 / React 19.2.3 / TypeScript
- Tailwind CSS 4、Recharts 3、lucide-react
- 静的エクスポート: `output: "export"`。GitHub Pages の `/trader` 配下で動かすため、本番ビルドは `NEXT_PUBLIC_BASE_PATH=/trader`
- 日本語 UI・ダークテーマ。**赤=上昇系、青=下落系**（日本株配色規約）

## データ取得

静的 JSON をクライアント側で fetch します。`src/lib/fetchJson.ts` の `fetchJson<T>(path, isValid)` が共通入口で、**ランタイム型ガードに通らない JSON は null 扱い**（=カード非表示）になります。HTTP エラー・parse 失敗・検証失敗のどれでも UI 全体は壊れません。

| 画面/部品 | 読み込み先 | 契約 |
|---|---|---|
| 一覧 `app/page.tsx` | `dashboard_index.json` | 必須 |
| 銘柄詳細 `app/stocks/[ticker]/StockDetailContent.tsx` | `tickers/{code}.json` | 必須 |
| `RegimeBanner` | `curation/macro_latest.json` | 任意（欠損でバナー非表示） |
| `PerformanceCard` | `performance_summary.json` | 任意 |
| `ModelQualityCard` | `model_quality.json` | 任意 |
| `PortfolioCard` | `portfolio_latest.json` | 任意 |
| 実績ページ `app/performance/page.tsx`（`PerformanceDetail`） | `performance_detail.json` + `signal_outcomes_recent.json` | 任意（「データ蓄積中」表示に縮退） |

任意契約はファイル欠損または `available: false` でセクション単位の非表示・縮退表示になります。

## 画面構成

### 一覧画面（`/`）

- ヘッダ直下に `RegimeBanner`: `market_bias`（risk_on=赤 / neutral=slate / risk_off=青）と as_of を表示し、週次レポート（GitHub `reports/`）とキュレーション決定ログ（`curation/decision_latest.json`）へのリンクを持つ
- `PerformanceCard`（実現実績サマリ + `/performance` への導線）、`ModelQualityCard`、`PortfolioCard`（目標建玉・セクター露出・警告）
- 銘柄カード: 最新終値、シグナル、KPI ゲート由来の自信度、上昇確率、RSI、出来高比、実際に使った銘柄別閾値の説明

### 実績ページ（`/performance`）

`PerformanceDetail`（client component）が表示:

- 資産曲線: 戦略=赤 vs TOPIX=slate の `LineChart`
- ドローダウン `AreaChart`（青）
- 信頼性（較正）: `mean_prob` vs `frac_up` の `BarChart` + Brier スコア
- 直近結果テーブル: 日付/銘柄/action/conviction/実現/超過/hit/MAE/MFE（hit は赤/青バッジ）

### 銘柄詳細（`/stocks/[ticker]`）

`generateStaticParams()` は `../docs/dashboard_index.json` → 失敗時 `../tickers.yml` 正規表現 → 空配列の順でフォールバック。ローソク足 + MA トグル + 出来高 + RSI チャート、最新予測カード、使用閾値、シグナル履歴。

## コンポーネント

| ファイル | 役割 |
|---|---|
| `components/StockChart.tsx` | 価格・出来高・RSI 描画。**空データガード実装済み**（価格が 1 件もなければ「価格データがありません」プレースホルダ） |
| `components/SignalCard.tsx` | 最新シグナルと自信度 |
| `components/PerformanceCard.tsx` | 実績サマリタイル + 詳細導線 |
| `components/PerformanceDetail.tsx` | 実績ページ本体 |
| `components/ModelQualityCard.tsx` | Phase 1 モデル品質・ドリフト警告 |
| `components/PortfolioCard.tsx` | 目標建玉・diff・セクター露出 |
| `components/RegimeBanner.tsx` | マクロレジームバナー + レポート/決定ログ導線 |
| `lib/fetchJson.ts` | fetch + ランタイム型ガード共通化（`isAvailablePayload` 等） |
| `lib/signal.ts` | アクション表記・色・信頼度ラベル |
| `types/index.ts` | ダッシュボード JSON の TypeScript 型（`PerformanceDetail` / `SignalOutcomeRow` 含む） |

## ビルド

```bash
npm run dev --prefix web          # 開発
npm run build --prefix web        # 通常ビルド
npm run build:prod --prefix web   # GitHub Pages 向け（NEXT_PUBLIC_BASE_PATH=/trader）
npm run lint --prefix web
```

## 現行制約

- エラーバウンダリ、専用 404、専用 loading ページは未実装
- チャートのアクセシビリティ対応は限定的
- `tailwind-merge` は依存にあるが未使用
