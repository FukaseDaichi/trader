# フロントエンド仕様

更新日: 2026-05-03 JST

## 技術スタック

- Next.js 16.1
- React 19.2
- TypeScript
- Tailwind CSS 4
- Recharts
- lucide-react
- 静的エクスポート: `output: "export"`

本番ビルドでは`NEXT_PUBLIC_BASE_PATH=/trader`を指定し、GitHub Pagesの`/trader`配下で動くようにします。

## データ取得

フロントエンドは静的JSONをクライアント側でfetchします。

| 画面 | 読み込み先 |
|---|---|
| 一覧画面 `web/src/app/page.tsx` | `${basePath}/dashboard_index.json` |
| 銘柄詳細 `web/src/app/stocks/[ticker]/StockDetailContent.tsx` | `${basePath}/tickers/{ticker}.json` |

`basePath`は`process.env.NEXT_PUBLIC_BASE_PATH`から取得します。

## 一覧画面

`page.tsx`は`DashboardIndexData`を読み込み、監視銘柄一覧を表示します。

表示内容:

- 銘柄名とコード
- 最新終値
- 最新シグナル
- KPIゲート由来の自信度
- 上昇確率
- RSI(14)の状態説明
- 出来高の20日平均比
- RSI、出来高、アルゴリズム条件の初心者向け説明

注意: 一覧画面のアルゴリズム説明には固定閾値が書かれていますが、バックエンドは銘柄ごとの自動閾値最適化を行うため、説明と実シグナル閾値が一致しない場合があります。

## 銘柄詳細画面

`stocks/[ticker]/page.tsx`は静的エクスポート用に`generateStaticParams()`を実装しています。

生成順:

1. `../docs/dashboard_index.json`からticker一覧を読む
2. 読めない場合は`../tickers.yml`を正規表現で読み、`code`を抽出
3. それも失敗した場合は空配列

`StockDetailContent.tsx`は銘柄別JSONをfetchして以下を表示します。

- ローソク足チャート
- 終値ライン
- MA5/MA20/MA60トグル
- 出来高サブチャート
- RSIサブチャート
- 最新予測カード
- シグナル履歴

## コンポーネント

| ファイル | 役割 |
|---|---|
| `components/StockChart.tsx` | Rechartsで価格、出来高、RSIを描画 |
| `components/SignalCard.tsx` | 最新シグナルと自信度を表示 |
| `lib/signal.ts` | アクション表記、色、信頼度ラベルの共通関数 |
| `types/index.ts` | ダッシュボードJSONのTypeScript型 |

## ビルド

開発:

```bash
npm run dev --prefix web
```

通常ビルド:

```bash
npm run build --prefix web
```

GitHub Pages向け:

```bash
npm run build:prod --prefix web
```

`build:prod`は`cross-env NEXT_PUBLIC_BASE_PATH=/trader next build`です。

## 現行制約

- JSONレスポンスのランタイムバリデーションは未実装
- エラーバウンダリ、専用404、専用loadingページは未実装
- チャートのアクセシビリティ対応は限定的
- チャート入力が空の場合の描画ガードは十分ではない
- `tailwind-merge`は依存にあるが現時点では使われていない
