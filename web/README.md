# AI株式トレーダー フロントエンド

`web/`は、リポジトリ直下の`docs/`に生成されるJSONを表示するNext.js静的ダッシュボードです。本番はVercelではなく、`/trader`ベースパスで静的エクスポートしてGitHub Pagesから配信します。

## ローカル開発

リポジトリ直下で実行します。

```bash
npm install --prefix web
npm run dev --prefix web
```

開発サーバーは通常[http://localhost:3000](http://localhost:3000)で開きます。`web/public/dashboard_index.json`と`web/public/tickers/*.json`は`main.py`実行時、またはpublish workflowのビルド前に`docs/`から同期されます。

## 検証と本番ビルド

```bash
npm run lint --prefix web
npm --prefix web exec -- tsc --noEmit --project web/tsconfig.json
npm run build:prod --prefix web
```

`build:prod`は`NEXT_PUBLIC_BASE_PATH=/trader`を設定し、`web/out/`へ静的サイトを出力します。`Daily Publish Dashboard` workflowがこの出力を`docs/`へ同期し、パイプライン生成JSONを除外して保護します。

画面構成、データ契約、表示規約は[フロントエンド仕様](../specification_document/02_frontend_web.md)、システム全体のセットアップは[ルートREADME](../README.md)を参照してください。
