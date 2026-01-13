# 実装計画: 株価予測・売買判断通知システム

## 目的

GitHub Actions を使用して毎日無料で（コスト 0 円）実行される、日本株（最大 3 銘柄）の株価予測および売買判断通知システムを構築します。Stooq から過去データを取得し、毎日 LightGBM モデルを学習させ、LINE 経由で売買シグナルを通知します。結果は GitHub Pages のダッシュボードで可視化します。

## ユーザー確認事項

- **対象銘柄**: 設定は `tickers.yml` を使用して管理します。デフォルト: 三菱 UFJ フィナンシャル・グループ (8306.JP)。
- **環境シークレット**: ユーザーは GitHub Secrets に `LINE_CHANNEL_ACCESS_TOKEN` と `LINE_USER_ID` を設定する必要があります。

## 変更内容案

### 設定

#### [NEW] [tickers.yml](file:///c:/Users/119003/git/trader/tickers.yml)

- 対象銘柄を管理する YAML 設定ファイル。

### ソースコード (`src/`)

#### [NEW] [config.py](file:///c:/Users/119003/git/trader/src/config.py)

- 設定と環境変数を読み込みます。

#### [NEW] [data_loader.py](file:///c:/Users/119003/git/trader/src/data_loader.py)

- Stooq からデータをダウンロードし、既存の履歴データとマージする機能。
- `na_values` の処理と日付解析を行います。

#### [NEW] [model.py](file:///c:/Users/119003/git/trader/src/model.py)

- 特徴量エンジニアリング（リターン、移動平均、RSI、ボラティリティ）。
- LightGBM の学習と予測。
- ローリングウィンドウ学習（4 年分）。

#### [NEW] [predictor.py](file:///c:/Users/119003/git/trader/src/predictor.py)

- 確率の閾値（0.62 / 0.38）に基づいて BUY/SELL/HOLD シグナルを判定するロジック。
- 指値の計算。

#### [NEW] [notifier.py](file:///c:/Users/119003/git/trader/src/notifier.py)

- LINE Push メッセージの送信。

#### [NEW] [dashboard.py](file:///c:/Users/119003/git/trader/src/dashboard.py)

- `index.html` の生成と `state.json` の更新。

#### [NEW] [main.py](file:///c:/Users/119003/git/trader/main.py)

- 毎日のワークフローを統括するメインエントリーポイント。

### 自動化

#### [NEW] [.github/workflows/daily_job.yml](file:///c:/Users/119003/git/trader/.github/workflows/daily_job.yml)

- スケジュール: `0 21 * * *` (UTC) -> 06:00 JST。
- 手順: チェックアウト、`uv` のセットアップ、依存関係インストール、`main.py` 実行、コミット＆プッシュ（データ更新）、GitHub Pages へのデプロイ。

### ドキュメント

#### [NEW] [README.md](file:///c:/Users/119003/git/trader/README.md)

- 使用方法とプロジェクト概要。

## 検証計画

### 自動テスト

- スクリプトベースのツールであるため、ローカルでスクリプト (`python main.py`) をドライランモードまたは少量のデータで実行し、クラッシュしないことを確認します。
- `state.json` の構造を確認します。

### 手動検証

- `data/*.parquet` ファイルが作成されているか確認します。
- 確率とシグナル生成のロジック出力ログを確認します。
- **ユーザーアクション**: GitHub へデプロイし、Action が正常に実行され、（シークレット設定後に）LINE 通知が届くかを確認します。
