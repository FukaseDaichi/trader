# AI株式トレーダー — Stock Prediction & Trading Signal System

日本株（複数銘柄・上限は設定可能）の株価データを毎日取得・学習し、LightGBM で翌日の上昇確率を予測して LINE 通知するシステムです。
React (Next.js) ベースのダッシュボードで、ローソク足チャート・出来高・RSI・移動平均線を可視化できます。

## 特徴

- **自動運用**: GitHub Actions で毎朝 06:00 (JST) に自動実行
- **コスト 0 円**: GitHub Actions (Free tier) + Stooq (Free data)
- **通知**: LINE Messaging API を使用
- **KPIゲート**: コスト/スリッページ込みの簡易バックテストで基準未達なら通知を自動抑止
- **閾値自動最適化**: OOSデータでシグナル閾値を日次更新し、期待値ベースで最適化
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
│   ├── backtest.py          # KPIゲート用バックテスト
│   ├── predictor.py         # シグナル生成ロジック
│   ├── notifier.py          # LINE 通知
│   └── dashboard.py         # ダッシュボードデータ生成
├── web/                     # React / Next.js フロントエンド
│   ├── src/
│   │   ├── app/             # App Router (ページ)
│   │   ├── components/      # StockChart, SignalCard
│   │   └── types/           # TypeScript 型定義
│   └── package.json
├── AGENTS.md                # Codex のスキルトリガー定義
├── scripts/                 # GitHub Actions 補助スクリプト
│   ├── jpx_calendar.py      # JPX 営業日判定 / 休日キャッシュ
│   ├── run_guard.py         # 当日更新ガード
│   ├── workflow_watchdog.py # 日次成果物チェック
│   ├── monthly_audit.py     # 月次監査レポート
│   ├── universe_refresh.py  # 週次ユニバース更新レポート
│   ├── rotating_refresh.py  # 夜間ローテ更新
│   ├── feature_precompute.py# 特徴量事前計算
│   └── stress_test.py       # 四半期ストレステスト
├── .github/workflows/       # CI/CD ワークフロー
│   ├── daily-preopen-core.yml
│   ├── daily-preopen-retry.yml
│   ├── daily-publish-dashboard.yml
│   ├── daily-watchdog.yml
│   ├── weekly-model-retrain.yml
│   ├── weekly-universe-refresh.yml
│   ├── monthly-calendar-sync.yml
│   ├── monthly-full-audit.yml
│   ├── nightly-rotating-refresh.yml
│   ├── nightly-feature-precompute.yml
│   └── quarterly-stress-test.yml
├── skills/                  # リポジトリ同梱の Codex スキル
│   └── jp-stock-ticker-curation/
│       ├── SKILL.md         # 銘柄調査〜tickers.yml更新ワークフロー
│       ├── agents/openai.yaml
│       └── references/selection-framework.md
├── tickers.yml              # 監視銘柄の設定
├── data/                    # 株価データ (Parquet)
├── docs/                    # GitHub Pages 公開ディレクトリ
│   ├── index.html           # ビルド済みフロントエンド
│   ├── history_data.json    # ダッシュボード用データ
│   ├── state.json           # シグナル履歴
│   └── backtest_report.json # KPIゲート結果
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
同時に `docs/backtest_report.json` に銘柄ごとの KPI ゲート結果が出力されます。

### KPIゲートの設定（任意）

通知前に銘柄ごとに OOS バックテストを実行し、基準未達なら `HOLD` に強制します。  
あわせてシグナル閾値（BUY/MILD_BUY/MILD_SELL/SELL）は、同じ OOS データで日次自動最適化できます。  
初心者向けに、まずは次の手順で設定してください。

1. プロジェクト直下（`main.py` と同じ階層）に `.env` ファイルを作る
2. 下の「まずはこの設定」ブロックをそのまま貼る
3. `uv run python main.py` を実行して、`docs/backtest_report.json` を確認する

未指定の変数はデフォルト値が使われます。  
真偽値は `true/false` のほか `1/0`, `yes/no`, `on/off` でも指定できます。

#### まずはこの設定（デフォルト推奨）

```dotenv
TRADER_KPI_GATE_ENABLED=true
TRADER_BT_COST_BPS=10.0
TRADER_BT_SLIPPAGE_BPS=5.0
TRADER_BT_ALLOW_SHORT=false
TRADER_KPI_MIN_CAGR=0.03
TRADER_KPI_MAX_DRAWDOWN=0.25
TRADER_KPI_MIN_EXPECTANCY=0.0001
TRADER_KPI_MIN_SHARPE=0.20
TRADER_KPI_MIN_TRADES=10
TRADER_AUTO_THRESHOLD_ENABLED=true
TRADER_AUTO_THRESHOLD_OBJECTIVE=expectancy
TRADER_AUTO_THRESHOLD_MIN_TRADES=8
TRADER_AUTO_THRESHOLD_MIN_GAP=0.05
```

#### 各変数の意味と調整の目安

`bps` はベーシスポイントで、`1 bps = 0.01%` です（例: `10.0 bps = 0.10%`）。

| 環境変数 | 何を決めるか | 既定値 | どう調整するか（初心者向け） |
|---|---|---:|---|
| `TRADER_KPI_GATE_ENABLED` | KPIゲート自体を有効化するか | `true` | まずは `true` 推奨。学習/通知の動作確認だけ急ぐなら一時的に `false`。 |
| `TRADER_BT_COST_BPS` | 片道の売買コスト（手数料など） | `10.0` | 実運用でコストが高いほど大きめに。迷ったら `10.0` のままで開始。 |
| `TRADER_BT_SLIPPAGE_BPS` | 片道のスリッページ（約定ズレ） | `5.0` | 小型株中心・寄り付き成行中心なら大きめに。迷ったら `5.0`。 |
| `TRADER_BT_ALLOW_SHORT` | 売り（ショート）シグナルも成績評価に使うか | `false` | 現物中心なら `false`。信用取引も前提で検証したいなら `true`。 |
| `TRADER_KPI_MIN_CAGR` | 年率リターン（CAGR）の最低ライン | `0.03` | 厳しくするなら上げる（例 `0.05`）、通しやすくするなら下げる（例 `0.01`）。 |
| `TRADER_KPI_MAX_DRAWDOWN` | 最大ドローダウン許容値（絶対値） | `0.25` | リスクを抑えたいなら下げる（例 `0.20`）。通しやすくするなら上げる（例 `0.35`）。 |
| `TRADER_KPI_MIN_EXPECTANCY` | 売買が発生した日の平均損益（1日あたり）の最低ライン | `0.0001` | ノイズ除去を強めるなら上げる（例 `0.0005`）。まずは既定値で十分。 |
| `TRADER_KPI_MIN_SHARPE` | シャープレシオの最低ライン | `0.20` | 安定性重視なら上げる（例 `0.50`）。通過率重視なら下げる（例 `0.10`）。 |
| `TRADER_KPI_MIN_TRADES` | 評価対象期間で必要な最小売買回数 | `10` | サンプル不足を避けるなら上げる（例 `20`）。銘柄数が少ないなら下げる（例 `5`）。 |
| `TRADER_AUTO_THRESHOLD_ENABLED` | シグナル閾値の自動最適化を使うか | `true` | 固定閾値に戻したい時だけ `false`。通常は `true` 推奨。 |
| `TRADER_AUTO_THRESHOLD_OBJECTIVE` | 閾値探索で最大化する指標 | `expectancy` | `expectancy`（標準）/`cagr`/`sharpe`/`net_return` を選択。 |
| `TRADER_AUTO_THRESHOLD_MIN_TRADES` | 閾値探索で必要な最小売買回数 | `8` | 少なすぎる閾値を避けたいなら上げる（例 `12`）。 |
| `TRADER_AUTO_THRESHOLD_MIN_GAP` | 閾値同士の最小間隔 | `0.05` | HOLD帯を広げたいなら上げる、シグナル頻度を増やすなら下げる。 |

#### どの値から触るべきか

最初は `TRADER_BT_COST_BPS` と `TRADER_BT_SLIPPAGE_BPS` を実態に合わせるのが優先です。  
次に、ゲートが厳しすぎる/緩すぎると感じたら `TRADER_KPI_MIN_CAGR` と `TRADER_KPI_MAX_DRAWDOWN` を微調整してください。  
閾値最適化を使う場合は、まず `TRADER_AUTO_THRESHOLD_OBJECTIVE=expectancy` のまま運用し、必要時のみ目的関数を変えるのが安全です。

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
銘柄を変更した場合、`main.py` 実行でデータ更新と `history_data.json` 更新は自動で行われます。`data/` 配下の不要な `*.parquet`（`tickers.yml` の有効銘柄に含まれないもの）は実行時に自動削除されます。  
`/stocks/[ticker]` は静的生成ページのため、ローカル運用では `web` の本番ビルド（`npm run build:prod`）と `docs/` への反映が必要です。GitHub Actions の日次ジョブでは、このビルドと同期を自動実行します。

## Codex スキルで銘柄選定を自動化する

このリポジトリには、`tickers.yml` の更新専用スキル `jp-stock-ticker-curation` を同梱しています。
ユーザーが「ネット調査して有望な日本株を選んで `tickers.yml` を更新して」と依頼したときに、再現可能な手順で実行するためのスキルです。

### できること

- 最新の公開情報をインターネット調査
- 日本株候補をスコアリングして絞り込み
- `tickers.yml` をフォーマット維持で更新
- 変更内容と根拠ソースをセットで報告

### 参照ファイル

- `AGENTS.md`: スキル発火ルール
- `skills/jp-stock-ticker-curation/SKILL.md`: 実行ワークフロー本体
- `skills/jp-stock-ticker-curation/references/selection-framework.md`: 選定基準

### 使い方（Codexへの依頼例）

以下のように依頼するとスキルが発火します。

```text
jp-stock-ticker-curation を使って、最新情報で有望な日本株を選んで tickers.yml を更新して
```

```text
インターネット上の一次情報を調べて、比較的値上がりが見込める日本株で tickers.yml を入れ替えて
```

### 実行フロー（内部的に行う処理）

1. `tickers.yml` の現在フォーマットを確認
2. 企業IR・決算資料などの一次情報を中心に収集
3. 選定基準（業績モメンタム、ガイダンス、バリュエーション、還元方針など）で評価
4. セクター偏りを抑えて最終銘柄を決定
5. `tickers.yml` を更新し、変更点と情報ソースを報告

### 出力イメージ

- 更新ファイルパス
- 採用銘柄一覧（`code` + `name`）
- セクター/テーマ別の簡潔な採用理由
- 参照したソースURL一覧

### 運用上の注意

- 本スキルは投資助言ではありません。最終判断は利用者側で行ってください。
- 市況や企業見通しは短期間で変化します。定期的に再実行してください。
- 一次情報の公開タイミングによっては、直近データが未反映な場合があります。

## GitHub Actions デプロイ手順

1. リポジトリを GitHub に Push
2. **Settings > Secrets and variables > Actions** に以下を設定
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_USER_ID`
3. **Settings > Pages** で `main` ブランチの `/docs` フォルダを公開元に設定

### 主なワークフロー頻度（実装済み）

- `Daily Preopen Core`: 平日 06:00 JST（本処理）
- `Daily Preopen Retry`: 平日 06:20 / 06:40 JST（再実行）
- `Daily Publish Dashboard`: Core/Retry 成功後に `docs/` 公開物を更新
- `Daily Watchdog`: 平日 12:30 JST（成果物鮮度チェック）
- `Weekly Model Retrain`: 毎週土曜 08:00 JST
- `Weekly Universe Refresh`: 毎週日曜 07:00 JST
- `Monthly Calendar Sync`: 毎月1日 09:15 JST
- `Monthly Full Audit`: 毎月第1日曜 09:00 JST
- `Nightly Rotating Refresh`: 平日 19:30 JST（任意）
- `Nightly Feature Precompute`: 平日 20:00 JST（任意）
- `Quarterly Stress Test`: 四半期初日 10:00 JST（任意）

### 各ワークフローの具体的な動き

1. `daily-preopen-core.yml`（本処理）
   - トリガー: 平日 06:00 JST / `workflow_dispatch`
   - 事前判定: `scripts/jpx_calendar.py is-open` でJPX営業日を判定
   - 休場日: ログを出して終了（予測・通知・コミットは実行しない）
   - 営業日: `uv run python main.py` を実行して予測、KPIゲート判定、通知、`docs/history_data.json` 更新
   - 反映: `data/` と `docs/` を commit/push
   - 排他制御: `concurrency.group = daily-core-main`

2. `daily-preopen-retry.yml`（再実行）
   - トリガー: 平日 06:20 / 06:40 JST / `workflow_dispatch`
   - 事前判定:
     - `jpx_calendar.py is-open` で営業日判定
     - `run_guard.py needs-core-run` で「当日分が既に更新済みか」を確認
   - スキップ条件:
     - 休場日
     - 当日更新済み（`docs/state.json` の当日エントリあり）
   - 実行条件: 営業日かつ未更新時のみ `main.py` を再実行
   - 反映: `data/` と `docs/` を commit/push
   - 排他制御: core と同じ `daily-core-main`（同時実行を防止）

3. `daily-publish-dashboard.yml`（公開物更新）
   - トリガー:
     - `workflow_run`（`Daily Preopen Core` / `Daily Preopen Retry` 成功時）
     - 手動実行（`force_publish` 入力対応）
   - 事前判定: `run_guard.py has-today-update` で当日更新有無を確認
   - スキップ条件: 当日更新がない、かつ `force_publish != true`
   - 主処理:
     - `docs/history_data.json` を `web/public/history_data.json` に同期
     - `npm run build:prod --prefix web`
     - `web/out/` を `docs/` に rsync（`state.json` や監査系jsonは除外）
   - 反映: `docs/` を commit/push
   - 排他制御: `concurrency.group = daily-publish-main`（最新publish優先）

4. `daily-watchdog.yml`（日次監視）
   - トリガー: 平日 12:30 JST / `workflow_dispatch`
   - 事前判定: `jpx_calendar.py is-open`
   - 休場日: 監視をスキップ
   - 営業日: `workflow_watchdog.py` で以下を検証
     - `docs/state.json` の当日更新有無
     - `docs/history_data.json` の整合
     - `docs/backtest_report.json` の存在と件数妥当性
   - 判定: 不整合があればジョブ失敗（アクション履歴から検知可能）

5. `weekly-model-retrain.yml`（週次再学習）
   - トリガー: 毎週土曜 08:00 JST / `workflow_dispatch`
   - 主処理: 通知を無効化した状態で `main.py` を実行し、週次メンテとして再評価
   - 反映: `data/` と `docs/` を commit/push
   - 想定用途: 日次だけでは追従しにくいモデル状態の定期更新

6. `weekly-universe-refresh.yml`（週次ユニバース更新）
   - トリガー: 毎週日曜 07:00 JST / `workflow_dispatch`
   - 主処理: `universe_refresh.py` を実行して `docs/universe_refresh_report.json` を生成
   - 反映: レポートを commit/push
   - 備考: 現在は Phase-1 実装（`tickers.yml` ベースのスナップショット）

7. `monthly-calendar-sync.yml`（休日キャッシュ更新）
   - トリガー: 毎月1日 09:15 JST / `workflow_dispatch`
   - 主処理: `jpx_calendar.py sync` で `data/jpx_holidays.json` を更新
   - 反映: カレンダーファイルを commit/push
   - 目的: 営業日判定の精度維持

8. `monthly-full-audit.yml`（月次監査）
   - トリガー: 毎月第1日曜 09:00 JST / `workflow_dispatch`
   - 主処理: `monthly_audit.py` を実行し `docs/monthly_audit.json` を生成
   - 反映: 監査レポートを commit/push
   - レポート内容: 銘柄別KPIゲート結果、平均CAGR/MaxDD/Sharpe/Expectancy など

9. `nightly-rotating-refresh.yml`（任意: 夜間ローテ更新）
   - トリガー: 平日 19:30 JST / `workflow_dispatch`
   - 主処理: `rotating_refresh.py` が銘柄をバケット分割し、その日の担当バケットのみ更新
   - 反映: `data/` と `docs/rotating_refresh_report.json` を commit/push
   - 目的: 全銘柄フル更新を避けつつ更新負荷を平準化

10. `nightly-feature-precompute.yml`（任意: 特徴量事前計算）
   - トリガー: 平日 20:00 JST / `workflow_dispatch`
   - 主処理: `feature_precompute.py` で特徴量を事前計算し、結果をレポート化
   - 反映: `docs/feature_precompute_report.json` を commit/push
   - 目的: 翌朝処理の前段を軽くするための準備ジョブ

11. `quarterly-stress-test.yml`（任意: 四半期ストレステスト）
   - トリガー: 四半期初日 10:00 JST / `workflow_dispatch`
   - 主処理: `stress_test.py` を高コスト前提で実行し `docs/stress_test_report.json` を生成
   - 反映: レポートを commit/push
   - 目的: コスト悪化シナリオでの耐性確認

### 実運用での推奨有効化順

1. まず有効化: `Daily Preopen Core`, `Daily Preopen Retry`, `Daily Publish Dashboard`, `Daily Watchdog`
2. 次に有効化: `Weekly Model Retrain`, `Weekly Universe Refresh`, `Monthly Calendar Sync`, `Monthly Full Audit`
3. 段階導入: `Nightly Rotating Refresh`, `Nightly Feature Precompute`, `Quarterly Stress Test`

### 補助スクリプト

- `scripts/jpx_calendar.py`: JPX営業日判定・休日キャッシュ更新
- `scripts/run_guard.py`: 「当日更新済みか」の実行ガード
- `scripts/workflow_watchdog.py`: 日次成果物の健全性チェック
- `scripts/monthly_audit.py`: 月次監査レポート出力
- `scripts/universe_refresh.py`: 週次ユニバース更新レポート出力
- `scripts/rotating_refresh.py`: 夜間ローテ更新
- `scripts/feature_precompute.py`: 特徴量事前計算
- `scripts/stress_test.py`: 四半期ストレステスト
