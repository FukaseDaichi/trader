# Pythonバックエンド仕様

更新日: 2026-07-20 JST

## モジュールマップ

| モジュール | 責務 |
|---|---|
| `main.py` | 日次処理のオーケストレーション。銘柄単位の障害分離と run 全体の縮退運転 |
| `src/config.py` | env 読み込みと設定 dict の組み立て（`get_*_config()` 群）。`tickers.yml` 検証 |
| `src/data_loader.py` | OHLCV 取得（Stooq → yfinance フォールバック）、検証、parquet 保存、無効銘柄の退避 |
| `src/model.py` | 34 テクニカル特徴量、`build_feature_frame()`、purged internal validation付きPhase 1学習。`train_and_predict()`は互換helperとして残るが日次配信経路では不使用 |
| `src/macro.py` | マクロパネル（USD/JPY・TOPIX・日経・日経VI・JGB10y）と 11 マクロ特徴量 |
| `src/execution.py` | 約定契約 `next_session_open_to_close_v2`。判断可能日、翌営業日寄付き、H営業日目終値を横断的に解決 |
| `src/labels.py` | ラベル生成（`triple_barrier` / `binary_1d`）と `effective_horizon()` |
| `src/calibration.py` | isotonic 較正、Brier、reliability ビン |
| `src/backtest.py` | 銘柄別 KPI ゲート（walk-forward OOS + コスト/スリッページ + 閾値自動最適化）と `evaluate_portfolio_kpi_gate()` |
| `src/model_store.py` | Phase 1 artifact schema v3、exact-candidate証跡、manifest/checksum、staging/atomic active化、runtime互換性検証 |
| `src/phase1.py` | exact candidateの学習・較正・tuning/holdoutゲート証跡と、保存済み／ephemeralバンドル推論 |
| `src/predictor.py` | `prob_up` → 5 段階アクション + ボラティリティガード + ロングのATR出口プラン |
| `src/universe.py` | Phase 2 ユニバース選定ロジック（流動性・セクター上限・決定論） |
| `src/cross_section.py` | クロスセクション・パネル構築（日付内 z-score/ランク正規化） |
| `src/cs_model.py` | クロスセクション LightGBM ランカの学習・推論・較正 |
| `src/portfolio.py` | 目標建玉の構築（逆ボラ・キャップ・ボラターゲット・ヒステリシス）、`merge_target_weights()`、`read_portfolio_gate()` |
| `src/portfolio_backtest.py` | ポートフォリオ walk-forward バックテストとレポート出力 |
| `src/portfolio_shadow.py` | Phase 1 vs Phase 2 の shadow 比較（純粋ロジック） |
| `src/db.py` | Neon Postgres アクセス層。write-through、outbox リプレイ、各種 fetch/upsert |
| `src/db_records.py` | signal/prediction/outcome の行マッピング（純粋関数、`compute_benchmark_ret()` 含む） |
| `src/notifier.py` | LINE 通知。`send_line_text()`（リトライ付き共通送信）と個別シグナル通知 |
| `src/digest.py` | 日次ダイジェスト（ATR利確/損切/期限を含む）・週次サマリの文面組み立て（純粋ロジック） |
| `src/performance.py` | 実績集計（資産曲線・ドローダウン・rolling 指標・reliability・直近結果） |
| `src/dashboard.py` | `docs/` への JSON エクスポート一式 |

## 実行入口（main.py）

`tickers.yml` の `enabled: true` 銘柄を対象に、以下の順で実行します。

**銘柄ループ前:**

1. `sync_data_files()`: 無効銘柄のトップレベル `data/*.parquet` を `data/archive/` へ退避（削除しない）
2. Phase 1 推論コンテキストの構築: `get_model_runtime_config()`（`TRADER_MODEL_MODE`）、ラベル設定、`macro.load_macro_panel()`、`model_store.read_active_model()`。active pointer、schema v3 artifact/gate契約、manifest/checksum、候補合格、version metadataを`validate_runtime_active_phase1()`で検証し、1つでも不一致なら保存モデルを使わない

**銘柄ループ（`_process_ticker`、例外は銘柄単位で捕捉）:**

3. `update_data()` → `load_data()`（60 行未満は failed HOLD）
4. `build_feature_frame()`: 34 テクニカル + 11 マクロ特徴量（マクロはパネル欠損時に自動で省略）
5. `_predict_for_ticker()`: モード別推論と、その**同じcandidate**に保存されたpurged OOSゲート証跡の取得。日次に別の代理モデルを学習して保存モデルへゲートを流用しない。ゲート結果・最適化閾値をbacktest entryへ記録
6. `generate_signal()` + `_attach_confidence_fields()`: 証跡の較正済み確率と閾値から5段階判断を作る。`BUY` / `MILD_BUY`にはラベル設定と同じATR幅の出口プランを付与。ゲート未達、証跡不一致、モデル失敗時は表示用 `action` を `HOLD` へ強制し、注文と誤認されないよう入口/出口価格フィールドをすべて消す（`raw_action` は保持）

**ループ後:**

7. Phase 2 推論 + ポートフォリオ snapshot（`run_phase2_inference` → `_run_portfolio_snapshot`）: `TRADER_PORTFOLIO_ENABLED` のときのみ。`docs/portfolio_latest.json` 出力と `portfolio_snapshots` upsert。失敗・条件未達は `fallback` として理由付き JSON を出し Phase 1 へ影響しない
8. `portfolio.merge_target_weights(signals, snapshot, gate_passed=read_portfolio_gate())`: **active モードかつ snapshot ok かつゲート通過のときだけ** 各シグナルへ `target_weight`（建玉外は 0.0）と理由追記を付与した新リストを返す。shadow / fallback / ゲート未達 / snapshot 無しでは入力をそのまま返す（shadow 完全無変更の保証）。`action` は変更しない
9. 通知ブロック: 個別シグナル通知は**既定で無効**（`TRADER_NOTIFY_PER_TICKER_ENABLED=false`、ダイジェストのみ運用。true にするとゲート通過かつ非 HOLD を 1 件ずつ `send_notification()`、個別失敗は隔離）。続いて `TRADER_NOTIFY_DIGEST_ENABLED`（既定 true）のとき、買い/売り銘柄名とゲート通過ロング最大5銘柄のATR利確/損切/期限を含む `digest.build_daily_digest()` を `send_line_text()` で送信
10. `db.record_run(signals, run_date)`: Phase 0 write-through（merge 後に実行するため `signals.target_weight` が DB に乗る）
11. `write_backtest_report()` → `update_dashboard()`

銘柄単位の失敗は `status: "failed"` の HOLD シグナルと backtest entry として記録され、他銘柄の処理は継続します。通知・Phase 2・DB はそれぞれ try/except で隔離され、どれが失敗しても他を止めません。

## 設定（src/config.py）

モジュールロード時に `BASE_DIR` 等のパス、`.env`、`TICKERS = load_tickers()`、`LINE_CONFIG`、`BACKTEST_GATE_CONFIG` を初期化します。Phase 1〜3 の設定は遅延取得の関数です:

- `get_label_config()`: `TRADER_LABEL_MODE`（`triple_barrier`|`binary_1d`）、`TRADER_TARGET_HORIZON_DAYS`、トリプルバリアの TP/SL ATR 倍率・時間バリア。廃止済みの `vol_norm` が残る環境では日次処理を止めず、警告して `triple_barrier` へフォールバック
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

全モードは `next_session_open_to_close_v2` に従う。特徴量行 `t` は `market_as_of_date` の引けまでに判明した情報であり、エントリーは次に存在する市場行 `open[t+1]`、H営業日の時間出口は `close[t+H]`。したがって固定Hリターンは `close[t+H] / open[t+1] - 1` で、旧 `close[t+H] / close[t] - 1` は新しい学習・成績へ混ぜない。

| モード | 内容 |
|---|---|
| `triple_barrier`（既定） | 実約定した翌営業日寄付き価格を基準とする利確 `+TP_ATR×ATR`・損切り `−SL_ATR×ATR`・時間バリア `TB_MAX_DAYS` 営業日。entry当日の高値/安値から判定し、保有後の翌日以降は寄付きギャップを高値/安値より先に判定。同一バーで両方に触れた場合は損切り優先。末尾の未確定 H 行は学習から除外 |
| `binary_1d` | 1営業日二値。rollback用モデル経路でも価格契約はv2（翌営業日寄付き→同日終値）を使う |

`effective_horizon()` がモードに応じた実効ホライズン（主軸 5 営業日）を返し、KPI ゲート・決済・予測の horizon を整合させます。

トリプルバリア学習の水準は実約定寄付きが確定してから定まる。日次シグナルに表示するpre-openのATR出口プランは判断日終値から計算する参考値であり、寄付き約定後の注文価格再計算・fillシミュレーションは未実装の別契約とする。

## モデル運用（Phase 1）

`TRADER_MODEL_MODE` で日次推論の経路が決まります。

- **`auto`（既定）**: runtime契約とmanifest検証に合格した`active_model.json`のschema v3バンドルがあれば`phase1.predict_ticker()`で推論。不在・破損・不一致時は、テクニカル特徴量・較正なしでその場学習するephemeral candidateへフォールバックする。このcandidateも自身のpurged OOS証跡、holdoutゲート、閾値を持ち、`ephemeral-phase1-v<schema>-<artifact contract hash>-<gate contract hash>`形式の安定versionで契約違いを分離する。exact boosterは別途`model_bundle_sha256`で結び付ける
- **`phase1`**: 互換かつ完全な保存済みバンドル必須。無ければ対象銘柄をfailed HOLDとし、日次全体は継続
- **`legacy`**: rollback用に`binary_1d`へ強制し、毎日ephemeral candidateを学習する。価格契約はv2で、旧close-to-closeや別の代理ゲートには戻らない

週次学習は`scripts/weekly_model_retrain.py`（土曜）が行う。時刻・git commit・UUIDを含む一意versionを`data/models/.staging/`へ作成し、全対象銘柄coverage、exact booster／較正器／ゲート証跡、schema v3契約、manifestファイル集合/checksum、前版からのcoverage劣化を検査する。合格時だけimmutableな`data/models/<version>/`へpromoteし、`active_model.json`をatomic replaceする。1銘柄でも失敗、証跡欠損、checksum不一致、pointer更新失敗なら前activeを維持する。

artifact schema v3は、label config、実効H、順序付きfeature columns/hash、macro有無、較正mode/実装ID、execution contract、gate configを保持する。銘柄bundleにはexact booster bundle hash、calibrator hash、tuning/embargo/holdout split、OOS予測hash、KPI schema v2、閾値、判定を自己checksum付き`gate_evidence`として保存する。シグナルには`model_version` / `horizon_days` / `raw_score` / `expected_ret` / 日次入力の`features_hash`に加え、artifact・gate provenanceが付き、主要予測値は`predictions`へ残る。

特徴量列を変えず計算意味だけを変更する場合は`PHASE1_ARTIFACT_SCHEMA_VERSION`を更新し、再学習する。列順/hashだけでは意味変更を検知できないため、schema versionがその互換性境界である。

`src/model.py::train_and_predict()`は古いcaller互換のhelperとして残るが、`main.py`の日次配信・fallback・週次artifact作成では使わない。

## KPIゲート（src/backtest.py）

1. walk-forward で OOS 予測を収集（ラベル設定と同じ horizon）。各モデルのearly stopping用validationは外部OOSより前のtrain pool内に別途切り出し、内部trainとの間にも実効purge gap（設定値とHの大きい方）を置く。外部OOSは予測だけに使い、学習・round数選択には渡さない
2. OOS を閾値チューニング用と holdout 用に時系列分割し、その間に実効ホライズン H 以上の判断行を embargo する。分離できない場合はチューニングせず固定閾値で全OOSをholdout評価
3. 閾値グリッドから全シミュレーション日を使う目的関数（既定 `avg_daily_net_return`）最大の組を選択（`TRADER_AUTO_THRESHOLD_*`）。`auto_threshold_min_round_trips` を満たす候補が無い場合は疎な最良候補を採用せず既定閾値へ戻し、その候補は診断情報にだけ残す
4. 毎日のシグナルへ `1/H` 資本のsleeveを割り当て、最大gross 1.0で日次mark-to-market。新規sleeve初日は翌日寄付き→終値、既存sleeveは前日終値→当日終値（overnightを含む）。入口・時間出口の両側にコスト/スリッページを課す
5. `round_trips` / `cagr` / `avg_daily_net_return` / `max_drawdown` / `sharpe` でゲート判定。必須値が欠損・NaN・Infなら閾値比較を通さず`*_unavailable`でfail-closeし、未達銘柄と同様に表示`HOLD`へ強制

指標schema v2は、売買発生日数 `turnover_days`、完結した建玉エピソード数 `round_trips`、独立シグナル数 `signal_cohorts`、全シミュレーション日の日次純リターン平均 `avg_daily_net_return`、完結エピソードごとの複利純損益平均 `expectancy_per_trade` を分離します。互換フィールド `trades` / `expectancy` / `turnover` はそれぞれ `round_trips` / `expectancy_per_trade` / `avg_daily_turnover` の非推奨aliasで、意味は `metrics_semantics` に明示します。

既定の基本閾値は `BUY=0.80` / `MILD_BUY=0.65` / `MILD_SELL=0.25` / `SELL=0.10` / `volatility_limit=0.04`。ゲート有効時は銘柄ごとの最適化閾値が実シグナルに使われます。`TRADER_KPI_GATE_ENABLED=false` では `skipped: true` の通過扱い。

ポートフォリオ単位のゲート `evaluate_portfolio_kpi_gate()`（Sharpe / MaxDD / 情報比 / 回転率、`TRADER_PORTFOLIO_BACKTEST_*`）は週次のクロスセクション再学習時に評価され、結果は `docs/cs_model_quality.json` に加えて `docs/portfolio_backtest.json` の `gate: {passed, failures}` にも書き出されます。`portfolio.read_portfolio_gate()` は、`available=true`、明示的な `gate.passed=true`（failuresなし）、現行 `next_session_open_to_close_v2`、net-vs-net、完全な同一basis benchmark coverage、バックテストと当日snapshotのCS `model_version`一致がすべて整合するときだけactiveを許可します。gateなし・availabilityだけの旧レポート、close-to-close契約、別versionの旧レポートはfail-closedです。

ポートフォリオのwalk-forwardは、各cross sectionの全行でv2契約・market-as-of・entry・exitが共通かを検証し、不整合期間と選択/保有銘柄の`fwd_return`欠損期間を`data_quality.exclusions`へ理由付きで除外します。有効期間が2未満なら`insufficient`です。H日窓は非重複なので同一銘柄でも前bookと次bookの差分相殺はせず、前book全決済＋次book全建てをturnoverに数え、最終bookの決済も必ず課金します。TOPIXも同じfull exit/entry方式でコスト・スリッページ控除後にし、戦略net対benchmark netでIR等を算出します。コスト前は`gross_period_return` / `gross_benchmark_return`に保持します。必須ゲート指標がNULL/NaN/Infなら閾値比較を通さず、指標別のunavailable理由でfail-closedです。

## シグナルとATR出口プラン（src/predictor.py）

- `generate_signal()` は生のアクションが `BUY` / `MILD_BUY` のときだけ `build_long_exit_plan()` を呼びます。`SELL` / `MILD_SELL` / `HOLD` にロング出口は付きません。
- 利確価格は `round(close + tb_tp_atr × ATR)`、損切価格は `round(close - tb_sl_atr × ATR)`、時間出口は `tb_max_days` 営業日です。既定値は `+1.5 ATR` / `-1.0 ATR` / 5営業日で、トリプルバリア学習ラベルと同じ `get_label_config()` を使います。
- `exit_plan` は `take_profit_price` / `stop_price` / 両者の現在値比 / `time_exit_days` / ATR / ATR倍率を持ち、主要値はシグナル直下にも平坦化されます。既存DB列との互換のため `stop_loss` は `stop_price` と同じ値です。`limit_price` は強い `BUY` / `SELL` の入口指値目安（現在値から0.5%）で、出口価格とは別です。
- ATRが欠損・NaN・0以下なら `exit_plan: null` に縮退します。ゲート未達またはモデル失敗でHOLDへ強制するときは `_clear_entry_exit_fields()` が `limit_price`、利確、損切、期限をすべて `null` にします。出口プランは手動注文の目安で、自動発注は行いません。

## Phase 2 クロスセクション + ポートフォリオ

- `cross_section.py`: 全銘柄×全日付のパネルを構築し、各特徴量を**日付内で** z-score/ランク正規化（`groupby("date").transform` のみ、リークなし）
- `cs_model.py`: LightGBM ランカ（`lambdarank`、日付 = group）または回帰。週次学習（`scripts/weekly_cross_section_retrain.py`）で `data/models/cs-v1-*/` に保存、`active_cs_model.json` がポインタ
- 日次推論（`main.py` `run_phase2_inference`）: active CS モデル・最小ユニバース（`TRADER_CS_MIN_UNIVERSE=30`）・使用可能データ数を満たすときのみ実行し、`predictions`（`cs_rank` 付き）を DB へ記録。満たさなければ理由付き fallback
- `portfolio.py` `build_portfolio_snapshot()`: スコア上位 `top_n` → 逆ボラ初期ウェイト → 銘柄キャップ（20%）・セクターキャップ（40%）→ ボラターゲット（年率 12%、`risk_off` レジームでグロス半減）→ ヒステリシス（無トレード幅 2%）→ 前日比 diff（new/add/trim/exit）。出力は `docs/portfolio_latest.json` + `portfolio_snapshots`。regime は `main.py` `_load_portfolio_regime()` が `docs/curation/macro_latest.json` の `market_bias` から供給（`risk_on`/`neutral`/`risk_off` 以外は neutral 縮退）
- **shadow 契約**: shadow モードでは Phase 1 のシグナル・通知をバイト単位で変更しない。active 配線は `merge_target_weights()`（上記 main.py 手順 8）のみで、`signals.action` は active でもモデル由来のまま

## 通知（src/notifier.py, src/digest.py）

- `send_line_text(text)`: LINE Messaging API v3 の共通送信。**リトライ付き**（429/5xx/接続エラーのみ対象、4xx は即時失敗。`TRADER_NOTIFY_RETRY_MAX=3`、backoff は `base × 4^(attempt-1)`）。例外を外へ出さない
- 日次ダイジェスト `digest.build_daily_digest()`（**通知の主チャネル**）: 建玉（モード・グロス・想定ボラ・new/継続/手仕舞い）+ 個別シグナル件数 + **アクション別の買い/売り銘柄名リスト**（ゲート通過のみ、各アクション最大4銘柄 + ほかN件）+ **ロングの現値→利確/損切・期限**（BUY優先、最大5銘柄）+ 直近実績 + レジーム（`docs/curation/macro_latest.json` の `market_bias` と USD/JPY）を 1 通に集約。portfolio 不在時は縮退文言
- 個別シグナル通知 `send_notification()`: **既定無効**（LINE 無料枠対策でダイジェストのみ運用）。有効時は HOLD スキップで現在値・上昇確率・入口指値/ATR損切り目安・理由・銘柄ページ URL を 1 銘柄 1 通
- 週次サマリ `digest.build_weekly_summary()`: `scripts/weekly_performance_notify.py` から送信（実績 0 件なら送らない）
- LINE 未設定（token/user_id 空）なら送信スキップ。通知失敗は daily を止めない

## 計測（Phase 0: src/db.py, src/db_records.py）

- `record_run()`: signals → `predictions` + `signals` テーブルへ upsert（event_id `run_date:ticker:event_type` で冪等）。接続不可時は `data/outbox/YYYY-MM-DD.jsonl` へキューし、次回成功時に `flush_outbox()` でリプレイ
- 週次Phase 1の`model_registry`登録もDB障害時はactive file pointerのprovenanceを含む安定event IDでoutboxへキューする。リプレイ時のactive更新は`kind`単位で、Phase 1登録がCS activeフラグを消さない
- `DATABASE_URL` 未設定または `TRADER_DB_ENABLED=false` なら DB 系は全て no-op
- 決済は `scripts/settle_outcomes.py`（`04_scripts.md`）。`signal_outcomes` は `market_as_of_date`、実際の `entry_date`、価格基準、`contract_version` を保持する。TOPIXパネルは終値しかなくv2と同基準の翌日寄付きが作れないため、v2の `benchmark_ret` / `excess_ret` は NULL、`benchmark_basis=unavailable_same_basis` とする。旧v1だけが close-to-close TOPIX補填対象
- `db_size_mb()` による容量監視（`TRADER_DB_STORAGE_WARN_MB=400` 超で performance_summary に警告）

reliabilityは`signals.prediction_id`から実際にaction生成へ使ったPhase 1予測を直接参照する。IDがないlegacy行だけ`signals.conviction`へfallbackし、現行v2・互換ホライズン内でモデルversionを横断集計する。Phase 2予測や今日のactive registry rowを推測で選ばず、source別件数・fallback数・除外理由を成果物へ残す。

## ダッシュボード出力（src/dashboard.py）

| 出力 | 内容 | 契約 |
|---|---|---|
| `docs/state.json` | シグナル履歴（最大30日） | 必須 |
| `docs/dashboard_index.json` | 一覧画面用インデックス | 必須 |
| `docs/tickers/{code}.json` | 銘柄詳細（最大500行 + シグナル履歴） | 必須 |
| `docs/performance_summary.json` | 実現的中率・コスト前平均リターン・往復コスト控除後H1運用曲線の小型サマリ | 任意（DB 由来、不通時 `available:false`） |
| `docs/performance_detail.json` | 往復コスト控除後の非重複cohort資産曲線・DD・net Sharpe、rawシグナル品質、約定/会計契約・benchmark coverage | 任意（同上） |
| `docs/signal_outcomes_recent.json` | 直近の個別シグナル実現結果（最大200行） | 任意（同上） |
| `docs/model_quality.json` | Phase 1 モデル品質 + ドリフト overlay | 任意 |
| `docs/portfolio_latest.json` | 今日の目標建玉 snapshot | 任意 |
| `docs/portfolio_backtest.json` | 週次ポートフォリオ・バックテスト | 任意 |

`docs/model_quality.json`と`scripts/drift_check.py`も日次推論と同じ`validate_runtime_active_phase1()`を使う。active artifactがruntime契約、manifest、checksum、候補合格のどれかに不整合なら、旧モデルを品質正常と表示せず`available:false`または互換性エラーへfail-closeする。

`web/public/` が存在する場合は開発用に index/tickers JSON を同期します。旧 `docs/history_data.json` は存在すれば削除します。すべて atomic write（tmp → rename）です。
