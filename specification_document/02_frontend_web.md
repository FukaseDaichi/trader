# フロントエンド仕様

更新日: 2026-07-24 JST

## 技術スタック

- Next.js 16.1.2 / React 19.2.3 / TypeScript
- Tailwind CSS 4、Recharts 3、lucide-react、date-fns、clsx
- 静的エクスポート: `output: "export"`。GitHub Pages の `/trader` 配下で動かすため、本番ビルドは `NEXT_PUBLIC_BASE_PATH=/trader`
- 日本語 UI・ダークテーマ

## 表示規約

- **売買色**: 買い=赤系（`red`/`orange`）、売り=青系（`blue`/`cyan`）。**緑（`emerald`）は健全性専用**（成績テスト合格・モデル健康診断「良好」・active モードピル）。例外として StockChart の MA60 線とそのトグル/凡例（`#4ade80` = green-400）はチャート用パレットとして許容
- **アクション表示名**: `BUY`=買い / `MILD_BUY`=やや買い / `HOLD`=**様子見** / `MILD_SELL`=やや売り / `SELL`=売り。表示トークン（ラベル・バッジ色・行ティント・バー色・カード枠）は `lib/signal.ts` の `ACTION_STYLE` が単一ソース
- **ヒーロー掲載条件**: `gate_passed && action !== "HOLD"`（成績テスト合格の売買サインのみ）。0件なら「今日は様子見の日」
- **専門用語**: 本文・列ヘッダの用語は `<Term k="...">` ツールチップで説明（辞書は `lib/glossary.ts`、22語）。`<Term>` は `<Link>` の内側に置かない（タップ競合）

## データ取得

静的 JSON をクライアント側で fetch します。`src/lib/fetchJson.ts` の `fetchJson<T>(path, isValid)` が共通入口で、ランタイム型ガードに通らないJSONは`null`扱いになります。HTTPエラー・parse失敗・ガード不合格は各画面のエラー表示またはカード単位の非表示へ縮退します。必須2契約のガードは画面が無条件参照する最小フィールド、任意契約の共通ガードはトップレベル`available`だけを検査します。

| 画面/部品 | 読み込み先 | 契約 |
|---|---|---|
| ホーム `app/page.tsx` | `dashboard_index.json` | 必須 |
| 銘柄詳細 `app/stocks/[ticker]/StockDetailContent.tsx` | `tickers/{code}.json` + `signal_outcomes_recent.json` | 必須 + 任意（結果列のみ縮退） |
| `SiteHeader`（MoodPill） | `curation/macro_latest.json` | 任意（欠損でピル非表示） |
| `PerformanceCard` / `PerformanceHeadline` | `performance_summary.json` | 任意 |
| `ModelQualityCard` | `model_quality.json` | 任意 |
| `PortfolioCard` | `portfolio_latest.json` | 任意 |
| `PerformanceDetail` | `performance_detail.json` + `signal_outcomes_recent.json` | 任意（「データ蓄積中」表示に縮退） |

任意契約はファイル欠損または `available: false` でセクション単位の非表示・縮退表示になります。

## 画面構成

### ホーム（`/`）

「3秒で今日の売買候補がわかる」結論ファースト構成（案A）:

1. `SiteHeader`: タイトル+ナビ（ホーム/成績）+ 市場ムードピル（`market_bias`: risk_on=前向き(赤) / neutral=中立 / risk_off=慎重(青)、クリックで要約ポップ）+ 更新日時
2. `TodayHero`: 今日のAI判断。成績テスト合格の買い候補（赤枠）/売り候補（青枠）チップを確率順に表示。0件なら「今日は様子見の日」
3. `StockExplorer`: 検索（銘柄名・コード、かな/カナ/NFKC 正規化）+ 絞り込みチップ（すべて/買い/売り/様子見、件数つき）+ 並び替え（おすすめ/確率高低/前日比/コード）+ 全銘柄リスト（株価・前日比・判断バッジ・確率バー・過熱感）。md 以上はテーブル行、モバイルは2行コンパクト行
4. `PerformanceCard`: スリム成績カード（的中率/通算リターン/サイン回数 + `/performance` 導線）
5. `PortfolioCard`: AIのおすすめ配分（提案モード/自動反映中ピル、配分テーブル）
6. `GlossaryAccordion`: 用語集アコーディオン
7. `SiteFooter`: 免責 + 週次レポート（GitHub `reports/`）・銘柄入替ログ（`curation/decision_latest.json`）リンク

### 成績ページ（`/performance`）

1. `PerformanceHeadline`: ヘッドライン3枚（的中率(5日後)・平均リターン(5日後)・通算リターン）
2. `PerformanceDetail`: 往復コスト控除後・非重複cohortの資産の伸び（同一basisが完全な場合だけ市場平均を併記）、一時的な落ち込み（ドローダウン、青）、確率の正直さ（較正、予測のズレ点数=Brier）、最近のサインの結果テーブル（同一basis benchmarkが利用不能なら「市場平均との差」列を隠して理由を表示）
3. `ModelQualityCard`: モデルの健康診断（良好/注意ピル、予測のズレ点数・順位の当たり具合・チェック対象数）

### 銘柄詳細（`/stocks/[ticker]`）

`generateStaticParams()` は `../docs/dashboard_index.json` → 失敗時 `../tickers.yml` 正規表現 → 空配列の順でフォールバック。

- ヘッダ行: 銘柄名・コード・最新終値・前日比 + 判断バッジ + 成績テスト合否ピル
- 左2/3: `StockChart`（ローソク足 + MA トグル + 出来高 + RSI。既定レンジ3ヶ月、RSI 30 ラインは売り側カラー `#06b6d4`）
- 右1/3: `SignalNarrative`（AIのひとこと: action/gate/閾値から日本語文を自動合成。買いサイン時は「買ってよい上限」「撤退ライン」）、`ThresholdGauge`(今日の確率が判断ラインのどこか)、これまでのサインと結果（`signal_outcomes_recent.json` の実現リターン・○×を日付突合で表示）

## コンポーネント

| ファイル | 役割 |
|---|---|
| `components/SiteHeader.tsx` | 共通ヘッダ（ナビ+市場ムードピル） |
| `components/SiteFooter.tsx` | 共通フッタ（免責+レポート/決定ログ導線） |
| `components/TodayHero.tsx` | 今日のAI判断ヒーロー |
| `components/StockExplorer.tsx` | 検索+絞り込み+並び替え+全銘柄リスト |
| `components/SignalNarrative.tsx` | AIのひとこと（シグナル説明文の自動合成） |
| `components/ThresholdGauge.tsx` | 判断ラインゲージ |
| `components/StockChart.tsx` | 価格・出来高・RSI 描画。**空データガード実装済み** |
| `components/PerformanceCard.tsx` | ホーム用スリム成績カード |
| `components/PerformanceHeadline.tsx` | 成績ページのヘッドライン3枚 |
| `components/PerformanceDetail.tsx` | 成績ページ本体（チャート群+結果テーブル） |
| `components/ModelQualityCard.tsx` | モデルの健康診断 |
| `components/PortfolioCard.tsx` | AIのおすすめ配分 |
| `components/Term.tsx` | 用語ツールチップ（クリック/ホバー、Escape・外側タップで閉、画面端クランプ） |
| `components/GlossaryAccordion.tsx` | 用語集アコーディオン |
| `lib/fetchJson.ts` | fetch + ランタイム型ガード共通化（`isAvailablePayload` 等） |
| `lib/signal.ts` | 表示トークン単一ソース（`ACTION_STYLE`・ラベル・色・ゲートピル・価格/前日比フォーマッタ） |
| `lib/glossary.ts` | 用語辞書（22語、コピー確定版） |
| `lib/search.ts` | 検索正規化（NFKC+カナ→かな折りたたみ）と `matchesTicker` |
| `lib/indicators.ts` | やさしい指標ラベル（RSI→過熱感 等） |
| `types/index.ts` | ダッシュボード JSON の TypeScript 型（`PerformanceDetail` / `SignalOutcomeRow` / `prev_close` / `change_pct` 含む） |

`PerformanceCard` / `PerformanceHeadline` / `PerformanceDetail` / 最近結果は、JSONの `execution_contract.contract_version` が現行 `next_session_open_to_close_v2` と一致する場合だけ表示します。deploy直後に残る旧v1静的JSONを新集計と混在させません。

## ビルド

```bash
npm run dev --prefix web          # 開発
npm run build --prefix web        # 通常ビルド
npm run build:prod --prefix web   # GitHub Pages 向け（NEXT_PUBLIC_BASE_PATH=/trader）
npm run lint --prefix web
npm --prefix web exec -- tsc --noEmit --project web/tsconfig.json
```

ユニットテストランナーは導入しない（リポジトリ慣習）。品質ゲートはlint、TypeScript型検査、本番静的ビルドです。

## 現行制約

- エラーバウンダリ、専用 404、専用 loading ページは未実装
- ランタイム型ガードは最小限で、任意JSONのネストした全フィールドまでは検証しない
- チャートのアクセシビリティ対応は限定的
- `tailwind-merge` は依存にあるが未使用
- 検索状態の URL クエリ保存、ライト/ダーク切替はスコープ外
