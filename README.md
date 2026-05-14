# AI株式トレーダー

日本株の株価データを毎日取得し、LightGBMで翌営業日の上昇確率を推定するシステムです。予測結果はKPIゲートで検証し、基準未達なら売買シグナルを`HOLD`に強制します。成果物は`docs/`にJSONと静的ダッシュボードとして出力されます。

このREADMEは2026-05-14時点のソースコードを正として更新しています。

## 公開ダッシュボード

デプロイ後の画面は以下で確認できます。

- [AI株式トレーダー 公開ダッシュボード](https://fukasedaichi.github.io/trader/)

GitHub Pagesは`main`ブランチの`/docs`を公開元にします。Next.jsの本番ビルドは`/trader`ベースパスで静的エクスポートされるため、公開URLは必ず上記の末尾スラッシュ付きURLを使ってください。

## 現行機能

- `tickers.yml`の有効銘柄を監視対象にする
- Stooqから日足OHLCVを取得し、鮮度不足または取得失敗時はyfinanceへフォールバックする
- JPX休日キャッシュを使ってデータ鮮度とGitHub Actions営業日実行を判定する
- 35個の価格・出来高・テクニカル特徴量を生成する
- LightGBMをwalk-forward方式で学習し、翌日の上昇確率を推定する
- OOSバックテストでCAGR、最大ドローダウン、Sharpe、期待値、取引回数を評価する
- 自動閾値探索で銘柄ごとのBUY/MILD_BUY/MILD_SELL/SELL閾値を調整する
- KPIゲート未達または予測失敗時は、通知可能な売買アクションを`HOLD`へ落とす
- `docs/state.json`、`docs/dashboard_index.json`、`docs/tickers/*.json`、`docs/backtest_report.json`を出力する
- LINE Messaging APIで、KPIゲートを通過した非`HOLD`シグナルのみ通知する
- Next.js静的エクスポートを`docs/`に配置し、GitHub Pagesで表示する

## 構成

| 領域 | 主なファイル | 役割 |
|---|---|---|
| 日次ジョブ | `main.py` | データ更新、特徴量、KPIゲート、予測、通知、ダッシュボード更新 |
| 設定 | `src/config.py`, `tickers.yml` | 銘柄、環境変数、パス、KPI設定 |
| データ取得 | `src/data_loader.py` | Stooq/yfinance取得、鮮度判定、parquet同期 |
| 特徴量・モデル | `src/model.py` | テクニカル特徴量、LightGBM学習・推論 |
| KPIゲート | `src/backtest.py` | OOS予測、売買シミュレーション、閾値最適化、レポート |
| シグナル | `src/predictor.py` | 上昇確率から5段階アクションへ変換 |
| 通知 | `src/notifier.py` | LINE Push API通知 |
| ダッシュボード出力 | `src/dashboard.py` | state/index/ticker JSON生成、`web/public`同期 |
| 補助スクリプト | `scripts/*.py` | 営業日判定、監視、監査、ローテ更新、ストレステスト |
| フロントエンド | `web/` | Next.js 16 + React 19 + Rechartsの静的ダッシュボード |
| 公開成果物 | `docs/` | GitHub Pages公開ディレクトリ |

## セットアップ

Python側は`uv`で管理します。

```bash
uv sync
```

フロントエンドをローカルで動かす場合:

```bash
npm install --prefix web
npm run dev --prefix web
```

`web/public/dashboard_index.json`と`web/public/tickers/*.json`は、`main.py`実行時に`docs/`から同期されます。最新データがない状態では画面にデータ読み込みエラーが出ます。

## ローカル実行

通知なしで日次ジョブを実行するだけなら、LINE環境変数は不要です。

```bash
uv run python main.py
```

実行時の流れ:

1. `tickers.yml`の有効銘柄を読み込む
2. `data/*.parquet`から無効銘柄のファイルを削除する
3. 各銘柄の株価データを更新する
4. 特徴量を生成する
5. KPIゲートを実行する
6. LightGBMで上昇確率を推定する
7. シグナルを生成し、ゲート未達なら`HOLD`へ強制する
8. ゲート通過かつ非`HOLD`の場合のみLINE通知する
9. `docs/backtest_report.json`とダッシュボードJSONを更新する

## 環境変数

| 変数 | 用途 | 既定値 |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Push API token | 未設定 |
| `LINE_USER_ID` | LINE通知先ユーザーID | 未設定 |
| `TRADER_DASHBOARD_URL` | 通知に載せるダッシュボードURL | `https://fukasedaichi.github.io/trader/` |
| `RUN_DATE_JST` | `state.json`へ記録する日付を上書き | 当日JST |
| `TRADER_YF_FALLBACK_ENABLED` | yfinanceフォールバック有効化 | `true` |
| `TRADER_DATA_STALE_OPEN_DAYS` | 鮮度遅れを許容する営業日数 | `0` |
| `TRADER_DATA_HTTP_TIMEOUT_SEC` | データ取得HTTPタイムアウト秒 | `20` |
| `TRADER_KPI_GATE_ENABLED` | KPIゲート有効化 | `true` |
| `TRADER_BT_VALIDATION_YEARS` | 評価に使う直近年数 | `4` |
| `TRADER_BT_VAL_SIZE` | 1 foldの検証日数 | `60` |
| `TRADER_BT_PURGE_GAP` | train/validation間のギャップ | `5` |
| `TRADER_BT_FOLDS` | walk-forward fold数 | `3` |
| `TRADER_BT_MIN_TRAIN_ROWS` | 最小学習行数 | `200` |
| `TRADER_BT_COST_BPS` | 片道コストbps | `10.0` |
| `TRADER_BT_SLIPPAGE_BPS` | 片道スリッページbps | `5.0` |
| `TRADER_BT_ALLOW_SHORT` | バックテストでショート評価を許可 | `false` |
| `TRADER_KPI_MIN_CAGR` | KPIゲート最小CAGR | `0.03` |
| `TRADER_KPI_MAX_DRAWDOWN` | KPIゲート最大ドローダウン許容 | `0.25` |
| `TRADER_KPI_MIN_EXPECTANCY` | KPIゲート最小期待値 | `0.0001` |
| `TRADER_KPI_MIN_SHARPE` | KPIゲート最小Sharpe | `0.20` |
| `TRADER_KPI_MIN_TRADES` | KPIゲート最小取引回数 | `10` |
| `TRADER_AUTO_THRESHOLD_ENABLED` | 自動閾値探索 | `true` |
| `TRADER_AUTO_THRESHOLD_OBJECTIVE` | 閾値探索目的関数 | `expectancy` |
| `TRADER_AUTO_THRESHOLD_MIN_TRADES` | 閾値探索の最小取引回数 | `8` |
| `TRADER_AUTO_THRESHOLD_MIN_GAP` | 閾値間の最小差 | `0.05` |

## 銘柄設定

`tickers.yml`で監視銘柄を管理します。`settings.max_tickers`が`null`または未指定なら、`enabled: true`の全銘柄を処理します。

```yaml
tickers:
  - code: "7011.JP"
    name: "三菱重工業"
    enabled: true
settings:
  max_tickers: null
```

銘柄を変更したら、`uv run python main.py`を実行して`data/`と`docs/`を更新してください。`data/*.parquet`のうち有効銘柄に含まれないファイルは、`main.py`実行時に削除されます。

## 出力ファイル

| ファイル | 内容 |
|---|---|
| `data/{ticker}.parquet` | 銘柄別OHLCV履歴 |
| `data/jpx_holidays.json` | JPX営業日判定用の休日キャッシュ |
| `docs/state.json` | 直近30日分のシグナル履歴 |
| `docs/dashboard_index.json` | 一覧画面向けの銘柄サマリ |
| `docs/tickers/{ticker}.json` | 銘柄詳細画面向けの価格・シグナルデータ |
| `docs/backtest_report.json` | 日次KPIゲート結果 |
| `docs/monthly_audit.json` | 月次KPI監査 |
| `docs/universe_refresh_report.json` | 現在の有効ユニバースの週次スナップショット |
| `docs/rotating_refresh_report.json` | 夜間ローテ更新結果 |
| `docs/feature_precompute_report.json` | 特徴量事前計算レポート |
| `docs/stress_test_report.json` | 高コスト前提の四半期ストレステスト |

`docs/history_data.json`は現行の主要データ契約ではありません。存在する場合、`src/dashboard.py`やpublish workflowが削除します。

## フロントエンド

開発サーバー:

```bash
npm run dev --prefix web
```

GitHub Pages向け静的ビルド:

```bash
npm run build:prod --prefix web
```

`build:prod`は`NEXT_PUBLIC_BASE_PATH=/trader`を付けてNext.jsを静的エクスポートします。GitHub Actionsでは`web/out/`を`docs/`へ同期します。

## push後に画面を更新する手順

`tickers.yml`やフロントエンドをpushしただけでは、公開画面のJSONと静的HTMLがすぐ更新されない場合があります。手動で最新化する場合は以下を実行してください。

1. GitHub Actionsの`Daily Preopen Core`を`Run workflow`で実行し、`main.py`で`data/`と`docs/`のJSONを更新する。
2. JPX休業日で`Daily Preopen Core`がスキップされる場合や、銘柄変更を即時反映したい場合は、ローカルで`uv run python main.py`を実行し、生成された`data/`と`docs/`をpushする。
3. `Daily Publish Dashboard`が自動起動して`web/out/`を`docs/`へ同期することを確認する。自動起動しない場合は、同workflowを手動実行し、`force_publish`を`true`にする。
4. Actions完了後、GitHub Pagesの反映を数分待ち、[公開ダッシュボード](https://fukasedaichi.github.io/trader/)を開く。
5. 古い表示が残る場合は、ブラウザで強制再読み込みするかキャッシュを削除する。

## GitHub Actions

GitHub Pages公開には、リポジトリ設定でPagesの公開元を`main`ブランチの`/docs`にしてください。LINE通知を使う場合はActions secretsに以下を設定します。

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_USER_ID`

主なワークフロー:

| Workflow | JST | 役割 |
|---|---:|---|
| `Daily Preopen Core` | 平日 06:00 | 営業日なら`main.py`を実行 |
| `Daily Preopen Retry` | 平日 06:20/06:40 | 当日未更新なら再実行 |
| `Daily Publish Dashboard` | core/retry成功後 | `web/out`を`docs/`へ同期 |
| `Daily Watchdog` | 平日 12:30 | 日次成果物の鮮度と整合性を検証 |
| `Weekly Model Retrain` | 土曜 08:00 | 通知なしで`main.py`を週次実行 |
| `Weekly Universe Refresh` | 日曜 07:00 | 有効銘柄のスナップショットレポート |
| `Monthly Calendar Sync` | 毎月1日 09:15 | JPX休日キャッシュ更新 |
| `Monthly Full Audit` | 第1日曜 09:00 | 月次KPI監査 |
| `Nightly Rotating Refresh` | 平日 19:30 | 有効銘柄を分割して夜間更新 |
| `Nightly Feature Precompute` | 平日 20:00 | 特徴量ファイル生成とレポート |
| `Quarterly Stress Test` | 四半期初日 10:00 | 高コスト前提のKPI確認 |

## 銘柄選定スキル

このリポジトリには、`tickers.yml`を更新するための`jp-stock-ticker-curation`スキルがあります。依頼例:

```text
jp-stock-ticker-curation を使って、最新情報で有望な日本株を選んで tickers.yml を更新して
```

スキルは企業IRや決算資料などの一次情報を優先し、業績モメンタム、ガイダンス、還元方針、バリュエーション、セクター分散を見て`tickers.yml`を更新します。

## 注意

このシステムは投資助言ではありません。モデルは過去データに基づく確率推定であり、将来の利益を保証しません。実運用では、売買コスト、スリッページ、流動性、決算イベント、急変時の約定リスクを別途確認してください。
