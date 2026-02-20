# AI株式トレーダー — Stock Prediction & Trading Signal System

日本株（複数銘柄・上限は設定可能）の株価データを毎日取得・学習し、LightGBM で翌日の上昇確率を予測して LINE 通知するシステムです。
React (Next.js) ベースのダッシュボードで、ローソク足チャート・出来高・RSI・移動平均線を可視化できます。

## 特徴

- **自動運用**: GitHub Actions で毎朝 06:00 (JST) に自動実行
- **コスト 0 円**: GitHub Actions (Free tier) + Stooq (Free data)
- **通知**: LINE Messaging API を使用
- **ダッシュボード**: React / Next.js (静的エクスポート) + GitHub Pages で公開
  - ローソク足チャート (日本式: 陽線=赤, 陰線=青)
  - 移動平均線 (MA5 / MA20 / MA60) の表示切替
  - 出来高 (Volume) サブチャート
  - RSI (14) サブチャート
  - 日付範囲セレクター (1ヶ月 / 3ヶ月 / 6ヶ月 / 1年 / 全期間)
  - BUY / SELL / HOLD シグナル表示 & 履歴

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| ML / データ | Python 3.13, LightGBM, Pandas, NumPy |
| データ取得 | Stooq (無料) |
| 通知 | LINE Messaging API |
| フロントエンド | Next.js 16, React 19, TypeScript, TailwindCSS 4, Recharts |
| CI/CD | GitHub Actions |
| ホスティング | GitHub Pages (`docs/`) |

## プロジェクト構成

```
trader/
├── main.py                  # エントリーポイント (日次バッチ)
├── src/
│   ├── config.py            # 設定読み込み
│   ├── data_loader.py       # Stooq からデータ取得
│   ├── model.py             # 特徴量エンジニアリング + LightGBM
│   ├── predictor.py         # シグナル生成ロジック
│   ├── notifier.py          # LINE 通知
│   └── dashboard.py         # ダッシュボードデータ生成
├── web/                     # React / Next.js フロントエンド
│   ├── src/
│   │   ├── app/             # App Router (ページ)
│   │   ├── components/      # StockChart, SignalCard
│   │   └── types/           # TypeScript 型定義
│   └── package.json
├── tickers.yml              # 監視銘柄の設定
├── data/                    # 株価データ (Parquet)
├── docs/                    # GitHub Pages 公開ディレクトリ
│   ├── index.html           # ビルド済みフロントエンド
│   ├── history_data.json    # ダッシュボード用データ
│   └── state.json           # シグナル履歴
└── pyproject.toml           # Python 依存関係
```

## ローカルでの実行方法

### 1. セットアップ

本プロジェクトは `uv` を使用して管理されています。

```powershell
# 依存関係のインストール
uv sync
```

### 2. 環境変数の設定 (通知テスト用)

LINE 通知をテストする場合は、環境変数を設定してください。通知なしで動作確認するだけなら不要です。

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_USER_ID`

### 3. 実行

```powershell
uv run python main.py
```

実行後、`docs/history_data.json` が更新され、ダッシュボードのデータがリフレッシュされます。

## ダッシュボード (Frontend)

`web/` ディレクトリに React / Next.js ベースのダッシュボードがあります。

### 主な機能

- **ローソク足チャート**: 始値・高値・安値・終値を日本式の色分けで表示
- **移動平均線**: MA5 (黄) / MA20 (水色) / MA60 (緑) をトグルで切替
- **出来高チャート**: 陽線・陰線に対応した色分けバーチャート
- **RSI チャート**: 買われすぎ (70) / 売られすぎ (30) ラインを表示
- **コントロールパネル**: 日付範囲セレクター + インジケーター表示切替
- **シグナルカード**: BUY / SELL / HOLD の判定結果を詳細表示
- **シグナル履歴**: 直近30日分のシグナル一覧

### 開発環境での実行

1. `uv run python main.py` でデータを生成
2. `web/` に移動: `cd web`
3. 依存関係インストール: `npm install`
4. `docs/history_data.json` を `web/public/` にコピー
5. 開発サーバー起動: `npm run dev`

### 本番ビルド (GitHub Pages)

GitHub Pages 用の静的ファイルは `docs/` に配置されます。フロントエンドのコードを変更した場合は、以下の手順でビルドして `docs/` を更新してください。

```bash
cd web
npm run build
# Windows (PowerShell)
Copy-Item -Recurse -Force out\* ..\docs\
# Mac/Linux
cp -r out/* ../docs/
```

データ更新 (`main.py`) は `docs/history_data.json` のみを更新するため、毎回のフロントエンドビルドは不要です。

## 銘柄の設定

`tickers.yml` で監視する銘柄を管理します（3銘柄以上に対応）。

```yaml
tickers:
  - code: "8306.JP"
    name: "三菱UFJフィナンシャル・グループ"
    enabled: true
  - code: "7974.JP"
    name: "任天堂"
    enabled: true
settings:
  # null または省略: enabled=true の銘柄をすべて処理
  max_tickers: null
```

`max_tickers` に数値を指定すると上限を設定できます（例: `10`）。  
銘柄を変更した場合、`main.py` を実行すればデータ取得からダッシュボード更新まで自動で行われます。フロントエンドの `generateStaticParams` は `docs/history_data.json` から動的にティッカーを読み取るため、手動でのコード変更は不要です。

## GitHub Actions デプロイ手順

1. リポジトリを GitHub に Push
2. **Settings > Secrets and variables > Actions** に以下を設定
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_USER_ID`
3. **Settings > Pages** で `main` ブランチの `/docs` フォルダを公開元に設定
