# Pythonバックエンド仕様

更新日: 2026-05-14 JST

## 実行入口

`main.py`が日次処理の入口です。処理単位は`tickers.yml`の`enabled: true`銘柄です。

実行順:

1. `src.config.TICKERS`から有効銘柄を取得
2. `src.data_loader.sync_data_files()`で無効銘柄の`data/*.parquet`を`data/archive/`へ退避
3. 銘柄ごとに`update_data()`で日足データを更新
4. `load_data()`でparquetを読み込み
5. `add_features()`で特徴量を生成
6. `evaluate_kpi_gate()`でKPIゲートと閾値最適化を実行
7. `train_and_predict()`で上昇確率を推定
8. `generate_signal()`で5段階アクションを作成
9. `_attach_confidence_fields()`でゲート結果を反映し、未達なら`HOLD`へ強制
10. ゲート通過時のみ`send_notification()`を実行
11. `write_backtest_report()`と`update_dashboard()`で成果物を出力

## 設定

`src/config.py`は以下をモジュールロード時に初期化します。

- `BASE_DIR`, `DATA_DIR`, `DOCS_DIR`, `TICKERS_FILE`, `STATE_FILE`
- `.env`の読み込み
- `TICKERS = load_tickers()`
- `LINE_CONFIG = get_line_config()`
- `BACKTEST_GATE_CONFIG = get_backtest_gate_config()`

`load_tickers()`は`tickers.yml`の`enabled`銘柄を抽出し、`settings.max_tickers`が整数なら先頭から制限します。`max_tickers`が`null`または未指定なら全件処理します。

## データ取得

`src/data_loader.py`が担当します。

- 必須列: `date`, `open`, `high`, `low`, `close`, `volume`
- Stooq URL: `https://stooq.com/q/d/l/?s={ticker_code}&i=d`
- yfinanceシンボル: `NNNN.JP`を`NNNN.T`へ変換
- HTTPタイムアウト: `TRADER_DATA_HTTP_TIMEOUT_SEC`、既定20秒
- 鮮度判定: `data/jpx_holidays.json`を使い、JSTの直近完了営業日と最新データ日を比較
- フォールバック: Stooq失敗または鮮度不足時、`TRADER_YF_FALLBACK_ENABLED=true`ならyfinanceを試す
- 保存: 既存parquetと新規データを結合し、`date`重複は後勝ちで保存
- 検証: 正値、OHLC関係、異常な終値変化を検証し、異常行は除外または警告としてレポートへ残す

`sync_data_files(active_ticker_codes)`は、`data/*.parquet`のうち有効銘柄に含まれないものを削除せず、`data/archive/`へ移動します。

## 特徴量

`src/model.py`の`add_features()`が、価格・出来高・テクニカル指標・カレンダーから特徴量を生成します。学習に使う列は`FEATURE_COLS`で定義された34列です。ダッシュボード向けには`ma_5`、`ma_20`、`ma_60`、`rsi`なども保持します。

- リターン: 1/2/3/5/10/20日
- 移動平均: MA5/10/20/60と乖離率
- MAクロス: MA5/20、MA20/60
- RSIとRSI変化
- MACD、signal、histogram、histogram変化
- Bollinger Bandの%位置とbandwidth
- ATR%と20日ボラティリティ
- 出来高変化、5日/20日出来高比率
- ローソク足実体・上ヒゲ・下ヒゲ
- 曜日、月、月末、月初
- 連騰/連落ストリーク
- 寄り付きギャップ
- 20日高値安値内の価格位置

`dropna=True`が既定で、学習・ゲートでは欠損行を落とします。ダッシュボード出力では`dropna=False`を使い、チャート継続性を優先します。

## モデル

`train_and_predict()`はLightGBMの二値分類です。

- 目的変数: 翌日の終値が当日終値より高いか
- 期間: 既定で直近4年
- walk-forward CV: 既定3 fold
- validation size: 60営業日相当
- purge gap: 5行
- 最小学習行数: 200
- 予測値: foldモデルと最終モデルの平均
- 学習不可時: `(None, 0.5)`を返す

## KPIゲート

`src/backtest.py`が担当します。

1. OOS予測をwalk-forwardで収集
2. OOSを閾値チューニング用とholdout評価用に時系列分割
3. 閾値グリッドから目的関数最大の組み合わせを選択
4. コスト/スリッページ込みで売買シミュレーション
5. `trades`, `cagr`, `expectancy`, `max_drawdown`, `sharpe`でゲート判定

既定の基本閾値は`BUY=0.80`, `MILD_BUY=0.65`, `MILD_SELL=0.25`, `SELL=0.10`, `volatility_limit=0.04`です。ただしKPIゲートの自動閾値探索が有効な場合、銘柄ごとの実シグナルには最適化後の閾値が使われます。

## シグナル

`src/predictor.py`の`generate_signal()`が以下のアクションを返します。

- `BUY`
- `MILD_BUY`
- `HOLD`
- `MILD_SELL`
- `SELL`

`main.py`はKPIゲート未達またはモデル失敗時に、`raw_action`を保持したまま表示用`action`を`HOLD`へ変更し、`confidence_label`を`自信なし`にします。銘柄単位の処理失敗時も日次処理全体は継続し、失敗銘柄は`status: "failed"`の`HOLD`シグナルと`backtest_report.json`エントリとして記録します。

シグナルには実際に判定で使った`thresholds`と`threshold_optimization`を含めます。

## 通知

`src/notifier.py`はLINE Messaging API v3を使います。

- `HOLD`は通知しない
- `LINE_CHANNEL_ACCESS_TOKEN`と`LINE_USER_ID`が未設定なら通知をスキップ
- `TRADER_DASHBOARD_URL`があれば銘柄詳細ページURLを本文に追加

## ダッシュボード出力

`src/dashboard.py`は以下を出力します。

- `docs/state.json`
- `docs/dashboard_index.json`
- `docs/tickers/{code}.json`

また、`web/public/`が存在する場合は`dashboard_index.json`と`ticker` JSONを同期します。`docs/history_data.json`や`web/public/history_data.json`は現行契約ではないため、存在すれば削除されます。
