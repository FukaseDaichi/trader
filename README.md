# Stock Prediction & Trading Signal System

日本株（最大 3 銘柄）の株価データを毎日取得・学習し、LightGBM で翌日の上昇確率を予測して LINE 通知するシステムです。

## 特徴

- **自動運用**: GitHub Actions で毎朝 06:00 (JST) に自動実行
- **コスト 0 円**: GitHub Actions (Free tier) + Stooq (Free data)
- **通知**: LINE Messaging API を使用
- **可視化**: GitHub Pages でダッシュボードを公開

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

実行後、`docs/index.html` が更新され、`data/` ディレクトリに `.parquet` ファイルが生成されます。

## 設定

`tickers.yml` で対象銘柄を管理します。

```yaml
tickers:
  - code: "8306.JP"
    name: "Mitsubishi UFJ Financial Group"
    enabled: true
```

## GitHub Actions デプロイ手順

1. リポジトリを GitHub に Push
2. **Settings > Secrets and variables > Actions** に以下を設定
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_USER_ID`
3. **Settings > Pages** で `main` ブランチの `/docs` フォルダを公開元に設定
