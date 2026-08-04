# アーキテクチャ図（Excalidraw）

更新日: 2026-08-04 JST

対外発表・レポート向けのシステム構成図。システム内部を知らない人でも
単語ベースで理解できる粒度に簡略化している（内部の環境変数・フォール
バック分岐などは意図的に省略。正確な as-built 仕様は本ディレクトリ上位の
`00_overview.md` 以下を参照）。

## 構成

| ファイル | 内容 |
|---|---|
| `01_system_overview.excalidraw` | 全体像：情報収集 → クラウド自動分析 → 結果配信 |
| `02_daily_pipeline.excalidraw` | 毎朝の売買シグナル生成フロー（6ステップ + HOLD 安全設計） |
| `03_ai_curation.excalidraw` | AI 銘柄キュレーション（AI は提案のみ・確定は決定的スクリプト） |
| `04_schedule.excalidraw` | 運用スケジュール（日次 / 週次 / 月次・四半期、JST） |
| `_preview.html` | 4枚をまとめてブラウザ表示する簡易プレビュー（要ローカルHTTPサーバー、CDN 使用） |

## 編集方法

`.excalidraw` は JSON テキスト。次のどちらかで開いて編集する:

- [excalidraw.com](https://excalidraw.com) → メニュー「開く」からファイルを読み込み
- VS Code 拡張 **Excalidraw**（`pomdtr.excalidraw-editor`）でリポジトリ内のファイルを直接編集

発表資料に貼る場合は Excalidraw から SVG / PNG をエクスポートする。
エクスポート画像をコミットする場合は同名 `.svg` をこのディレクトリに置く。

## プレビュー

```bash
uv run python -m http.server 8321
```

を リポジトリルートで起動し、
`http://localhost:8321/specification_document/diagrams/_preview.html` を開く
（`.claude/launch.json` の `static-repo` 設定でも同じサーバーが起動できる）。
