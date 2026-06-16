# Pythonバックエンド仕様

更新日: 2026-06-16 JST

## モジュールマップ

| モジュール | 責務 |
|---|---|
| `main.py` | 日次処理のオーケストレーション。銘柄単位の障害分離と run 全体の縮退運転 |
| `src/config.py` | env 読み込みと設定 dict の組み立て（`get_*_config()` 群）。`tickers.yml` 検証 |
| `src/data_loader.py` | OHLCV 取得（Stooq → yfinance フォールバック）、検証、parquet 保存、無効銘柄の退避 |
| `src/model.py` | 34 テクニカル特徴量、`build_feature_frame()`、legacy 学習 `train_and_predict()` |
| `src/macro.py` | マクロパネル（USD/JPY・TOPIX・日経・日経VI・JGB10y）と 11 マクロ特徴量 |
| `src/labels.py` | ラベル生成（`triple_barrier` / `vol_norm` / `binary_1d`）と `effective_horizon()` |
| `src/calibration.py` | isotonic 較正、Brier、reliability ビン |
| `src/backtest.py` | 銘柄別 KPI ゲート（walk-forward OOS + コスト/スリッページ + 閾値自動最適化）と `evaluate_portfolio_kpi_gate()` |
| `src/model_store.py` | 週次学習モデルの保存/読込（`data/models/` + active ポインタ） |
| `src/phase1.py` | 保存済みバンドルでの日次推論 `predict_ticker()` |
| `src/predictor.py` | `prob_up` → 5 段階アクション + ボラティリティガード |
| `src/universe.py` | Phase 2 ユニバース選定ロジック（流動性・セクター上限・決定論） |
| `src/cross_section.py` | クロスセクション・パネル構築（日付内 z-score/ランク正規化） |
| `src/cs_model.py` | クロスセクション LightGBM ランカの学習・推論・較正 |
| `src/portfolio.py` | 目標建玉の構築（逆ボラ・キャップ・ボラターゲット・ヒステリシス）、`merge_target_weights()`、`read_portfolio_gate()` |
| `src/portfolio_backtest.py` | ポートフォリオ walk-forward バックテストとレポート出力 |
| `src/portfolio_shadow.py` | Phase 1 vs Phase 2 の shadow 比較（純粋ロジック） |
| `src/db.py` | Neon Postgres アクセス層。write-through、outbox リプレイ、各種 fetch/upsert |
| `src/db_records.py` | signal/prediction/outcome の行マッピング（純粋関数、`compute_benchmark_ret()` 含む） |
| `src/notifier.py` | LINE 通知。`send_line_text()`（リトライ付き共通送信）と個別シグナル通知 |
| `src/digest.py` | 日次ダイジェスト・週次サマリの文面組み立て（純粋ロジック） |
| `src/performance.py` | 実績集計（資産曲線・ドローダウン・rolling 指標・reliability・直近結果） |
| `src/dashboard.py` | `docs/` への JSON エクスポート一式 |

## 実行入口（main.py）

`tickers.yml` の `enabled: true` 銘柄を対象に、以下の順で実行します。

**銘柄ループ前:**

1. `sync_data_files()`: 無効銘柄のトップレベル `data/*.parquet` を `data/archive/` へ退避（削除しない）
2. Phase 1 推論コンテキストの構築: `get_model_runtime_config()`（`TRADER_MODEL_MODE`）、ラベル設定、`macro.load_macro_panel()`、`model_store.read_active_model()`。active モデルの `macro_features_enabled` が現行設定と不一致なら保存モデルを使わない

**銘柄ループ（`_process_ticker`、例外は銘柄単位で捕捉）:**

3. `update_data()` → `load_data()`（60 行未満は failed HOLD）
4. `build_feature_frame()`: 34 テクニカル + 11 マクロ特徴量（マクロはパネル欠損時に自動で省略）
5. `evaluate_kpi_gate()`: ラベル設定と整合する horizon-aware walk-forward OOS バックテスト。ゲート結果・最適化閾値を backtest entry に記録
6. `_predict_for_ticker()`: モード別推論（下記「モデル運用」）
7. `generate_signal()` + `_attach_confidence_fields()`: ゲート未達またはモデル失敗時は表示用 `action` を `HOLD` へ強制（`raw_action` は保持）

**ループ後:**

8. Phase 2 推論 + ポートフォリオ snapshot（`run_phase2_inference` → `_run_portfolio_snapshot`）: `TRADER_PORTFOLIO_ENABLED` のときのみ。`docs/portfolio_latest.json` 出力と `portfolio_snapshots` upsert。失敗・条件未達は `fallback` として理由付き JSON を出し Phase 1 へ影響しない
9. `portfolio.merge_target_weights(signals, snapshot, gate_passed=read_portfolio_gate())`: **active モードかつ snapshot ok かつゲート通過のときだけ** 各シグナルへ `target_weight`（建玉外は 0.0）と理由追記を付与した新リストを返す。shadow / fallback / ゲート未達 / snapshot 無しでは入力をそのまま返す（shadow 完全無変更の保証）。`action` は変更しない
10. 通知ブロック: 個別シグナル通知は**既定で無効**（`TRADER_NOTIFY_PER_TICKER_ENABLED=false`、ダイジェストのみ運用。true にするとゲート通過かつ非 HOLD を 1 件ずつ `send_notification()`、個別失敗は隔離）。続いて `TRADER_NOTIFY_DIGEST_ENABLED`（既定 true）のとき `digest.build_daily_digest()` を `send_line_text()` で送信
11. `db.record_run(signals, run_date)`: Phase 0 write-through（merge 後に実行するため `signals.target_weight` が DB に乗る）
12. `write_backtest_report()` → `update_dashboard()`

銘柄単位の失敗は `status: "failed"` の HOLD シグナルと backtest entry として記録され、他銘柄の処理は継続します。通知・Phase 2・DB はそれぞれ try/except で隔離され、どれが失敗しても他を止めません。

## 設定（src/config.py）

モジュールロード時に `BASE_DIR` 等のパス、`.env`、`TICKERS = load_tickers()`、`LINE_CONFIG`、`BACKTEST_GATE_CONFIG` を初期化します。Phase 1〜3 の設定は遅延取得の関数です:

- `get_label_config()`: `TRADER_LABEL_MODE`（`triple_barrier`|`vol_norm`|`binary_1d`）、`TRADER_TARGET_HORIZON_DAYS`、トリプルバリアの TP/SL ATR 倍率・時間バリア
- `get_model_runtime_config()`: `TRADER_MODEL_MODE`（`auto`|`phase1`|`legacy`）、較正モード、マクロ特徴量フラグ、モデル保存先
- `get_cross_section_config()` / `get_portfolio_config()`: Phase 2 の最小ユニバース・top_n・リスクキャップ・バックテストゲート閾値など

`load_tickers()` は `tickers.yml` を検証してから `enabled` 銘柄を抽出します（`code`/`name` 非空必須、`enabled` boolean、code 重複エラー、`settings.max_tickers` の件数制限）。`settings.curation` や `watchlist` はキュレーション用で、日次予測本体は無視します。

全環境変数の正典はコメント付きの `.env.example` です（既定値は `src/config.py`）。

## データ取得（src/data_loader.py）

- 必須列: `date`, `open`, `high`, `low`, `close`, `volume`
- Stooq URL: `https://stooq.com/q/d/l/?s={ticker_code}&i=d`、yfinance は `NNNN.JP` → `NNNN.T`
- 鮮度判定: `data/jpx_holidays.json` で JST の直近完了営業日と比較（`TRADER_DATA_STALE_OPEN_DAYS`、既定 0）
- フォールバック: Stooq 失敗または鮮度不足時、`TRADER_YF_FALLBACK_ENABLED=true` なら yfinance
- 検証: 正値、OHLC 関係、異常な終値変化。警告は DataFrame attrs 経由でレポートの `data_validation_warnings` へ
- `update_data(dest_dir=...)` で任意ディレクトリへ保存可能（キュレーション warmup は `data/watchlist/`）

## 特徴量

- **テクニカル 34 列**（`src/model.py` `FEATURE_COLS`）: リターン(1〜20日)、MA5/10/20/60 と乖離・クロス、RSI、MACD、Bollinger、ATR%・20日ボラ、出来高比率、ローソク足形状、カレンダー、ストリーク、ギャップ、20日高安レンジ内位置
- **マクロ 11 列**（`src/macro.py` `MACRO_FEATURE_COLS`）: USD/JPY リターン/ボラ、TOPIX・日経のトレンド/リターン、日経VI、JGB10y、リスクバイアススコアなど。`data/macro/macro_panel.parquet` を `merge_asof(direction="backward")` で結合（未来参照なし）。パネル欠損・列欠損は該当特徴量を NaN として処理を継続

`build_feature_frame(df, macro_panel, ticker_info, macro_enabled)` が両者を結合します。学習・ゲートでは `dropna=True`、ダッシュボード出力では `dropna=False`。

## ラベル（src/labels.py）

| モード | 内容 |
|---|---|
| `triple_barrier`（既定） | 利確 `+TP_ATR×ATR`・損切り `−SL_ATR×ATR`・時間バリア `TB_MAX_DAYS` 営業日。最初に触れたバリアでラベル化。末尾の未確定 H 行は学習から除外 |
| `vol_norm` | ボラ正規化した H 日先リターンの回帰 |
| `binary_1d` | 旧来の翌日二値。`TRADER_MODEL_MODE=legacy` の rollback 用 |

`effective_horizon()` がモードに応じた実効ホライズン（主軸 5 営業日）を返し、KPI ゲート・決済・予測の horizon を整合させます。

## モデル運用（Phase 1）

`TRADER_MODEL_MODE` で日次推論の経路が決まります。

- **`auto`（既定）**: `data/models/active_model.json` が指す週次学習済みバンドル（LightGBM + isotonic 較正器）があれば `phase1.predict_ticker()` で推論。バンドルが無い銘柄・ポインタ不在時は legacy 学習へフォールバック
- **`phase1`**: 保存済みバンドル必須。無ければ failed HOLD
- **`legacy`**: 毎日ゼロから `train_and_predict()`（その場合ラベルも `binary_1d` へ強制）。即時 rollback 経路

週次学習は `scripts/weekly_model_retrain.py`（土曜）が行い、銘柄別バンドルを `data/models/<version>/` に保存、`model_registry`（DB）へ版登録し、`active_model.json` を更新します。シグナルには `model_version` / `horizon_days` / `raw_score` / `expected_ret` / `features_hash` が provenance として付き、`predictions` テーブルに残ります。

legacy 学習（`train_and_predict`）: LightGBM 二値分類、直近 4 年、walk-forward 3 fold、purge gap 5 行、最小 200 行、fold 平均で予測。学習不可時は `(None, 0.5)`。

## KPIゲート（src/backtest.py）

1. walk-forward で OOS 予測を収集（ラベル設定と同じ horizon）
2. OOS を閾値チューニング用と holdout 用に時系列分割
3. 閾値グリッドから目的関数（既定 expectancy）最大の組を選択（`TRADER_AUTO_THRESHOLD_*`）
4. コスト/スリッページ込みで売買シミュレーション
5. `trades` / `cagr` / `expectancy` / `max_drawdown` / `sharpe` でゲート判定。未達銘柄は表示 `HOLD` へ強制

既定の基本閾値は `BUY=0.80` / `MILD_BUY=0.65` / `MILD_SELL=0.25` / `SELL=0.10` / `volatility_limit=0.04`。ゲート有効時は銘柄ごとの最適化閾値が実シグナルに使われます。`TRADER_KPI_GATE_ENABLED=false` では `skipped: true` の通過扱い。

ポートフォリオ単位のゲート `evaluate_portfolio_kpi_gate()`（Sharpe / MaxDD / 情報比 / 回転率、`TRADER_PORTFOLIO_BACKTEST_*`）は週次のクロスセクション再学習時に評価され、結果は `docs/cs_model_quality.json` に加えて `docs/portfolio_backtest.json` の `gate: {passed, failures}` にも書き出されます。`portfolio.read_portfolio_gate()` はこの `gate.passed` を優先して参照します（gate キーが無い旧レポートのみ availability にフォールバック）。

## Phase 2 クロスセクション + ポートフォリオ

- `cross_section.py`: 全銘柄×全日付のパネルを構築し、各特徴量を**日付内で** z-score/ランク正規化（`groupby("date").transform` のみ、リークなし）
- `cs_model.py`: LightGBM ランカ（`lambdarank`、日付 = group）または回帰。週次学習（`scripts/weekly_cross_section_retrain.py`）で `data/models/cs-v1-*/` に保存、`active_cs_model.json` がポインタ
- 日次推論（`main.py` `run_phase2_inference`）: active CS モデル・最小ユニバース（`TRADER_CS_MIN_UNIVERSE=30`）・使用可能データ数を満たすときのみ実行し、`predictions`（`cs_rank` 付き）を DB へ記録。満たさなければ理由付き fallback
- `portfolio.py` `build_portfolio_snapshot()`: スコア上位 `top_n` → 逆ボラ初期ウェイト → 銘柄キャップ（20%）・セクターキャップ（40%）→ ボラターゲット（年率 12%、`risk_off` レジームでグロス半減）→ ヒステリシス（無トレード幅 2%）→ 前日比 diff（new/add/trim/exit）。出力は `docs/portfolio_latest.json` + `portfolio_snapshots`。regime は `main.py` `_load_portfolio_regime()` が `docs/curation/macro_latest.json` の `market_bias` から供給（`risk_on`/`neutral`/`risk_off` 以外は neutral 縮退）
- **shadow 契約**: shadow モードでは Phase 1 のシグナル・通知をバイト単位で変更しない。active 配線は `merge_target_weights()`（上記 main.py 手順 9）のみで、`signals.action` は active でもモデル由来のまま

## 通知（src/notifier.py, src/digest.py）

- `send_line_text(text)`: LINE Messaging API v3 の共通送信。**リトライ付き**（429/5xx/接続エラーのみ対象、4xx は即時失敗。`TRADER_NOTIFY_RETRY_MAX=3`、backoff は `base × 4^(attempt-1)`）。例外を外へ出さない
- 日次ダイジェスト `digest.build_daily_digest()`（**通知の主チャネル**）: 建玉（モード・グロス・想定ボラ・new/継続/手仕舞い）+ 個別シグナル件数 + **アクション別の買い/売り銘柄名リスト**（ゲート通過のみ、各アクション最大4銘柄 + ほかN件）+ 直近実績 + レジーム（`docs/curation/macro_latest.json` の `market_bias` と USD/JPY）を 1 通に集約。portfolio 不在時は縮退文言
- 個別シグナル通知 `send_notification()`: **既定無効**（LINE 無料枠対策でダイジェストのみ運用）。有効時は HOLD スキップで現在値・上昇確率・指値/損切り目安・理由・銘柄ページ URL を 1 銘柄 1 通
- 週次サマリ `digest.build_weekly_summary()`: `scripts/weekly_performance_notify.py` から送信（実績 0 件なら送らない）
- LINE 未設定（token/user_id 空）なら送信スキップ。通知失敗は daily を止めない

## 計測（Phase 0: src/db.py, src/db_records.py）

- `record_run()`: signals → `predictions` + `signals` テーブルへ upsert（event_id `run_date:ticker:event_type` で冪等）。接続不可時は `data/outbox/YYYY-MM-DD.jsonl` へキューし、次回成功時に `flush_outbox()` でリプレイ
- `DATABASE_URL` 未設定または `TRADER_DB_ENABLED=false` なら DB 系は全て no-op
- 決済は `scripts/settle_outcomes.py`（`04_scripts.md`）。`db_records.compute_benchmark_ret()` が TOPIX 同期間リターンを計算し `benchmark_ret` / `excess_ret` を埋める（系列欠損時は NULL で継続）
- `db_size_mb()` による容量監視（`TRADER_DB_STORAGE_WARN_MB=400` 超で performance_summary に警告）

## ダッシュボード出力（src/dashboard.py）

| 出力 | 内容 | 契約 |
|---|---|---|
| `docs/state.json` | シグナル履歴（最大30日） | 必須 |
| `docs/dashboard_index.json` | 一覧画面用インデックス | 必須 |
| `docs/tickers/{code}.json` | 銘柄詳細（最大500行 + シグナル履歴） | 必須 |
| `docs/performance_summary.json` | 実現的中率・平均リターン等の小型サマリ | 任意（DB 由来、不通時 `available:false`） |
| `docs/performance_detail.json` | 資産曲線（戦略 vs TOPIX）・DD・rolling・reliability | 任意（同上） |
| `docs/signal_outcomes_recent.json` | 直近の個別シグナル実現結果（最大200行） | 任意（同上） |
| `docs/model_quality.json` | Phase 1 モデル品質 + ドリフト overlay | 任意 |
| `docs/portfolio_latest.json` | 今日の目標建玉 snapshot | 任意 |
| `docs/portfolio_backtest.json` | 週次ポートフォリオ・バックテスト | 任意 |

`web/public/` が存在する場合は開発用に index/tickers JSON を同期します。旧 `docs/history_data.json` は存在すれば削除します。すべて atomic write（tmp → rename）です。
