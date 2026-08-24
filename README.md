# AI株式トレーダー

日本株の株価データを毎日取得し、LightGBMで上昇確率を推定して5段階の売買シグナル（`BUY`/`MILD_BUY`/`HOLD`/`MILD_SELL`/`SELL`）を生成するシステムです。シグナルはKPIゲートで検証し、基準未達なら`HOLD`に強制します。成果物は`docs/`にJSONと静的ダッシュボードとして出力され、GitHub Pagesで公開されます。

日次シグナルに加えて、以下の4層が稼働しています。

- **Phase 0（計測基盤）**: 予測・シグナルと、判断日の次の営業日寄付き→1/5/10営業日目終値の契約版付き実現リターンをNeon Postgresへ記録し、実績を`docs/performance_summary.json`として公開
- **Phase 1（シグナル品質）**: 5営業日ホライズンのトリプルバリアラベル、isotonic較正、マクロ/レジーム特徴量、exact-candidate holdoutゲート、schema v3保存モデルのatomic切替、ドリフト監視
- **Phase 2（ポートフォリオ・シャドー運用中）**: ユニバース全体を1つのクロスセクショナルモデルで予測し、リスク制約付きロングオンリーの目標ポートフォリオを毎日提案（シャドー中はPhase 1のシグナル・通知に影響しない）
- **Phase 3（手動トレードUX・運用堅牢化）**: トリプルバリアと同じ幅を使うATR出口プラン、v2約定・非重複cohort・往復コスト後の実績ページ（同一basis TOPIXがある場合だけ比較）と個別結果履歴（`/performance`）、朝のダイジェスト通知と週次実績サマリ（LINEリトライ付き）、fail-closedなactive mode配線

このREADMEは2026-08-02時点のソースコードを正として更新しています。Phase 0〜3はすべて実装済みです。現行仕様は`specification_document/`（起点は`README.md`）、未完了の運用移行・既知課題・今後の実装予定は`specification_document/06_issues_and_backlog.md`を参照してください。

## 公開ダッシュボード

デプロイ後の画面は以下で確認できます。

- [AI株式トレーダー 公開ダッシュボード](https://fukasedaichi.github.io/trader/)

GitHub Pagesは`main`ブランチの`/docs`を公開元にします。Next.jsの本番ビルドは`/trader`ベースパスで静的エクスポートされるため、公開URLは必ず上記の末尾スラッシュ付きURLを使ってください。

## 現行機能

- `tickers.yml`の有効銘柄（現在約50銘柄）を監視対象にする
- Stooqから日足OHLCVを取得し、非有限値を含む行を除外して価格・OHLC関係を検証する。鮮度不足または取得失敗時はyfinanceへフォールバックする
- JPX休日キャッシュでデータ鮮度とGitHub Actionsの営業日実行を判定する
- テクニカル34個＋マクロ/レジーム11個の特徴量を生成する（USD/JPY、TOPIX、日経平均、日経VI、JGB10年）
- 実際に推論する同一candidateのpurged OOSをtuning/embargo/holdoutへ分離し、日次sleeveのCAGR、最大ドローダウン、Sharpe、平均日次純リターン、往復取引数を評価する。証跡不一致またはKPI未達銘柄は`HOLD`へ強制する
- 銘柄ごとのBUY/MILD_BUY/MILD_SELL/SELL閾値を自動最適化する
- ゲートを通過した`BUY`/`MILD_BUY`に、学習ラベルと同じトリプルバリア幅を使った利確・損切り・時間出口（既定: `+1.5 ATR` / `-1.0 ATR` / 5営業日）を付ける。終値またはATRが欠損・非有限の場合は出口プランだけを省略して日次処理を継続する
- schema v3 manifest/checksum検証済みの週次モデル（isotonic較正付き）で日次推論し、保存モデルが無い銘柄はartifact/gate契約hash付きversionと自身のholdoutゲート証跡を持つephemeral candidateへフォールバックする（`TRADER_MODEL_MODE=auto`）
- 予測・シグナル・実現リターンと週次model registry登録をNeon Postgresへ書き込み、DB不通時は`data/outbox/`のJSONLキューへ退避して次回再送する
- 日次推論と同じruntime artifact契約を検証したうえでIC/Brier/PSIによるモデルドリフト監視を行い、しきい値超過でGitHub Issueを自動起票する
- クロスセクショナルモデル（LightGBM ranker）でユニバース全体を順位付けし、逆ボラ重み＋銘柄/セクター/グロス上限のロングオンリー目標ポートフォリオを`docs/portfolio_latest.json`へ出力する（シャドー運用）
- LINEは朝のダイジェスト1通を主チャネルとし、ゲート通過シグナルの銘柄名とロングのATR出口プランを集約する。銘柄別通知は既定無効で、必要な場合だけ有効化できる
- Next.js静的エクスポートを`docs/`に配置し、GitHub Pagesで表示する（実績・モデル品質・ポートフォリオの各カードはデータがある場合のみ表示）
- AI銘柄キュレーション（自動）で、テクニカル（日次）とファンダメンタル（週次）の分析からガード通過時のみ`tickers.yml`の有効ユニバースを少数入替する
- 隔週（14日）でAIが候補母集団`curation_pool.yml`をファンダ＋流動性で見直し、決定論マージで安全に更新する（add-onlyで段階導入）
- 週次でファンダ＋テクニカルを総合した解説レポート（`reports/`）を生成し、GitHub URLをLINE通知する

## 構成

| 領域 | 主なファイル | 役割 |
|---|---|---|
| 日次ジョブ | `main.py` | データ更新、特徴量、KPIゲート、予測、通知、Phase 0 DB書き込み、Phase 2推論、ダッシュボード更新 |
| 設定 | `src/config.py`, `tickers.yml`, `.env.example` | 銘柄、環境変数、パス、KPI/モデル/ポートフォリオ設定 |
| データ取得 | `src/data_loader.py` | Stooq/yfinance取得、鮮度・OHLCV有限性/異常値検証、parquet同期、無効銘柄の退避 |
| 特徴量・モデル | `src/model.py`, `src/macro.py`, `src/labels.py` | テクニカル/マクロ特徴量、ラベル生成、LightGBM学習・推論 |
| モデル運用 | `src/model_store.py`, `src/phase1.py`, `src/calibration.py` | schema v3 artifact、exact-candidate証跡、manifest/checksum、atomic active化、保存／ephemeral推論、isotonic較正 |
| KPIゲート | `src/backtest.py` | purged OOSのtuning/holdout分離、日次sleeveシミュレーション、horizon重複を除いた独立cohort充足性、閾値最適化、metrics schema v3 |
| シグナル | `src/predictor.py` | 上昇確率から5段階アクションへ変換し、ロングへATR利確・損切り・時間出口を付与（ボラティリティガード付き） |
| 計測DB | `src/db.py`, `src/db_records.py`, `migrations/` | Neon Postgres書き込み、outboxフォールバック、スキーマ |
| ポートフォリオ | `src/universe.py`, `src/cross_section.py`, `src/cs_model.py`, `src/portfolio.py`, `src/portfolio_backtest.py`, `src/portfolio_shadow.py` | ユニバース選定、CSパネル/モデル、目標ウェイト構築、ウォークフォワード検証、シャドー比較 |
| 通知 | `src/notifier.py`, `src/digest.py` | LINE Push API共通送信、朝のダイジェスト・ATR出口目安・週次サマリ |
| ダッシュボード出力 | `src/dashboard.py` | state/index/ticker/performance/model_quality/portfolio JSON生成、`web/public`同期 |
| 補助スクリプト | `scripts/*.py` | 営業日判定、監視、監査、再学習、決済、ドリフト、ローテ更新、ストレステスト |
| フロントエンド | `web/` | Next.js 16 + React 19 + Recharts 3の静的ダッシュボード |
| 公開成果物 | `docs/` | GitHub Pages公開ディレクトリ |
| AI銘柄キュレーション | `scripts/curation_*.py`（`curation_merge`/`curation_pool_merge`/`curation_notify`/`curation_pool_notify`等）, `scripts/technical_screen.py`, `.claude/skills/*`, `curation_pool.yml` | 日次テクニカル・週次マクロ＆ファンダ・隔週プール分析、決定論マージ、週次レポート、LINE通知 |
| commit/push共通 | `.github/scripts/commit-and-push.sh` | 全workflow共通の`git pull --rebase --autostash`＋最大3回リトライ |
| テスト | `tests/test_*.py` | ラベル/較正/CS/ポートフォリオ/DB等の単体テスト（pytest不要の素のPython） |

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

計測DB（Phase 0）を使う場合はNeon Postgresの接続文字列を`DATABASE_URL`に設定し、スキーマを適用します。

```bash
uv run python scripts/db_migrate.py
```

GitHub Actions上では`Manual DB Migrate`ワークフロー（`workflow_dispatch`）からも適用できます（`dry_run`で保留中マイグレーションのプレビュー可）。

`migrations/0004_execution_contract.sql`を初めて適用した後は、過去のactionable signalをv2へ再決済します。

```bash
uv run python scripts/settle_outcomes.py --restate-execution-contract
```

また、旧Phase 1 artifactはschema v3互換性検証で使用不可になるため、`scripts/weekly_model_retrain.py`で新候補を学習・検証してください。詳細な移行順は`specification_document/06_issues_and_backlog.md`にあります。

`DATABASE_URL`未設定でもシステムは動作します（DB書き込みはスキップされ、イベントは`data/outbox/`へ退避されます）。

## ローカル実行

通知なしで日次ジョブを実行するだけなら、LINE環境変数は不要です。

```bash
uv run python main.py
```

実行時の流れ:

1. `tickers.yml`の有効銘柄を読み込む
2. 無効銘柄のparquetを`data/archive/`へ退避する（削除はしない）
3. マクロパネルとactiveモデルポインタを読み込む
4. 銘柄ごとに: データ更新 → 特徴量生成（テクニカル＋マクロ）→ 上昇確率推定（互換性検証済み保存モデル、無ければephemeral candidate）→ 同一candidateのholdoutゲートと閾値 → 5段階シグナル生成。`BUY`/`MILD_BUY`にはATR出口プランを付け、証跡不一致・ゲート未達・モデル失敗時は価格目安を消して`HOLD`へ強制する
5. Phase 2: クロスセクション推論 → 目標ポートフォリオ構築 → `docs/portfolio_latest.json`とDBスナップショット更新（`TRADER_PORTFOLIO_ENABLED=true`時のみ）
6. activeモードかつポートフォリオKPIゲート通過時だけ`target_weight`をシグナルへマージする（shadowでは無変更）
7. ループ後にLINE通知を1回実行する。朝のダイジェストが主チャネルで、ゲート通過ロングの利確・損切り・期限も最大5銘柄まで掲載する。銘柄別通知は既定無効
8. Phase 0: マージ後の予測・シグナルを計測DBへ書き込む（失敗してもジョブは止まらない）
9. `docs/backtest_report.json`とダッシュボードJSON（state/index/tickers/performance/model_quality/portfolio）を更新する

## 環境変数

主要な変数のみ示します。全変数と詳細コメントは`.env.example`が正です。数値の環境変数には有限値を指定し、`NaN`や`Infinity`は使用しません。共通パーサーを使うfloat設定は、起動時に組み立てる主要設定では不正値をエラーにし、データ取得などの日次補助設定では警告して既定値へ戻します。

### 基本・データ取得

| 変数 | 用途 | 既定値 |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` | LINE Push API認証 | 未設定（通知スキップ） |
| `TRADER_DASHBOARD_URL` | 通知に載せるダッシュボードURL | `https://fukasedaichi.github.io/trader/` |
| `RUN_DATE_JST` | 実行日付（JST）の上書き | 当日JST |
| `TRADER_YF_FALLBACK_ENABLED` | yfinanceフォールバック | `true` |
| `TRADER_DATA_STALE_OPEN_DAYS` | 鮮度遅れを許容する営業日数 | `0` |
| `TRADER_DATA_HTTP_TIMEOUT_SEC` | データ取得HTTPタイムアウト秒 | `20` |
| `TRADER_DATA_MAX_DAILY_MOVE` | 日次変動の異常値警告しきい値 | `0.50` |

### KPIゲート・閾値最適化

| 変数 | 用途 | 既定値 |
|---|---|---|
| `TRADER_KPI_GATE_ENABLED` | KPIゲート有効化 | `true` |
| `TRADER_BT_VALIDATION_YEARS` / `TRADER_BT_VAL_SIZE` / `TRADER_BT_PURGE_GAP` / `TRADER_BT_FOLDS` / `TRADER_BT_MIN_TRAIN_ROWS` | walk-forward分割の構成 | `4` / `60` / `5` / `3` / `200` |
| `TRADER_BT_COST_BPS` / `TRADER_BT_SLIPPAGE_BPS` | 片道コスト/スリッページbps | `10.0` / `5.0` |
| `TRADER_BT_ALLOW_SHORT` | ショート評価の許可 | `false` |
| `TRADER_KPI_MIN_CAGR` / `TRADER_KPI_MAX_DRAWDOWN` / `TRADER_KPI_MIN_AVG_DAILY_NET_RETURN` / `TRADER_KPI_MIN_SHARPE` / `TRADER_KPI_MIN_ROUND_TRIPS` | ゲート合格基準 | `0.03` / `0.25` / `0.0001` / `0.20` / `10` |
| `TRADER_AUTO_THRESHOLD_ENABLED` / `TRADER_AUTO_THRESHOLD_OBJECTIVE` / `TRADER_AUTO_THRESHOLD_MIN_ROUND_TRIPS` / `TRADER_AUTO_THRESHOLD_MIN_GAP` | 閾値自動最適化 | `true` / `avg_daily_net_return` / `8` / `0.05` |

旧 `TRADER_KPI_MIN_EXPECTANCY` / `TRADER_KPI_MIN_TRADES` / `TRADER_AUTO_THRESHOLD_MIN_TRADES` と objective `expectancy` は移行用aliasとして警告付きで読めます。新規設定では使用しません。

### Phase 0: 計測DB

| 変数 | 用途 | 既定値 |
|---|---|---|
| `DATABASE_URL` | Neon Postgres接続文字列。未設定ならDB接続をスキップし、prediction/signal等をoutboxへ保存 | 未設定 |
| `TRADER_DB_ENABLED` | DB接続の有効化。`false`時も再送可能なイベントはoutboxへ保存 | `true` |
| `TRADER_DB_FALLBACK_DIR` | DB不通時のJSONLキュー出力先 | `data/outbox` |
| `TRADER_DB_WRITE_TIMEOUT_SEC` | DB接続タイムアウト秒 | `15` |
| `TRADER_DB_STORAGE_WARN_MB` | DBサイズ警告しきい値MB | `400` |

### Phase 1: モデル・ラベル・ドリフト

| 変数 | 用途 | 既定値 |
|---|---|---|
| `TRADER_MODEL_MODE` | `auto`（互換保存モデル→無ければ自身のOOS証跡を持つephemeral candidate）/ `phase1`（互換保存モデル必須）/ `legacy`（毎日ephemeral candidateを学習し、翌日二値ラベルへ強制するrollback用） | `auto` |
| `TRADER_LABEL_MODE` | `triple_barrier` / `binary_1d`（rollback用） | `triple_barrier` |
| `TRADER_TARGET_HORIZON_DAYS` | 予測ホライズン（営業日） | `5` |
| `TRADER_TB_TP_ATR` / `TRADER_TB_SL_ATR` / `TRADER_TB_MAX_DAYS` | トリプルバリアの利確/損切（ATR倍）と時間バリア | `1.5` / `1.0` / `5` |
| `TRADER_CALIBRATION_MODE` | 確率較正（`isotonic` / `none`） | `isotonic` |
| `TRADER_MIN_CALIBRATION_ROWS` | 較正に必要な最小OOS行数 | `60` |
| `TRADER_MACRO_FEATURES_ENABLED` | マクロ特徴量の有効化 | `true` |
| `TRADER_MODEL_DIR` / `TRADER_MODEL_ACTIVE_FILE` | モデルartifact保存先とactiveポインタ | `data/models` / `data/models/active_model.json` |
| `TRADER_DRIFT_MIN_OUTCOMES` / `TRADER_DRIFT_MIN_IC` / `TRADER_DRIFT_MAX_BRIER` / `TRADER_DRIFT_MAX_PSI` | ドリフト検知しきい値 | `30` / `-0.02` / `0.30` / `0.25` |

### Phase 2: クロスセクション・ポートフォリオ

| 変数 | 用途 | 既定値 |
|---|---|---|
| `TRADER_PORTFOLIO_ENABLED` | Phase 2推論の有効化（CIでは`true`） | `false` |
| `TRADER_PORTFOLIO_MODE` | `shadow` / `active` | `shadow` |
| `TRADER_CS_OBJECTIVE` | CSモデルの目的（`ranker` / `regression`） | `ranker` |
| `TRADER_CS_LABEL_HORIZON_DAYS` / `TRADER_CS_TOP_N` / `TRADER_CS_MIN_UNIVERSE` / `TRADER_CS_MIN_DAILY_NAMES` / `TRADER_CS_PANEL_LOOKBACK_YEARS` | CSパネル/推論の構成 | `5` / `8` / `30` / `20` / `5` |
| `TRADER_CS_MODEL_ACTIVE_FILE` | CSモデルのactiveポインタ | `data/models/active_cs_model.json` |
| `TRADER_UNIVERSE_TARGET_SIZE` | ユニバース目標銘柄数 | `40` |
| `TRADER_PORTFOLIO_TARGET_VOL` | 年率目標ボラティリティ | `0.12` |
| `TRADER_PORTFOLIO_MAX_NAME_WEIGHT` / `TRADER_PORTFOLIO_SECTOR_CAP` / `TRADER_PORTFOLIO_MAX_GROSS` / `TRADER_PORTFOLIO_MIN_WEIGHT` | 銘柄/セクター/グロス/最小ウェイト制約 | `0.20` / `0.40` / `1.00` / `0.03` |
| `TRADER_PORTFOLIO_NOTRADE_BAND` / `TRADER_PORTFOLIO_RISK_OFF_GROSS_MULT` / `TRADER_PORTFOLIO_COV_LOOKBACK_DAYS` | ノートレードバンド、リスクオフ時グロス係数、共分散lookback | `0.02` / `0.50` / `60` |
| `TRADER_PORTFOLIO_BACKTEST_MIN_SHARPE` / `TRADER_PORTFOLIO_BACKTEST_MAX_DD` / `TRADER_PORTFOLIO_BACKTEST_MIN_IR` / `TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER` | ポートフォリオKPIゲート | `0.30` / `0.25` / `0.00` / `0.40` |

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

銘柄を変更したら、`uv run python main.py`を実行して`data/`と`docs/`を更新してください。有効銘柄に含まれない`data/*.parquet`は、`main.py`実行時に`data/archive/`へ退避されます（削除はされません）。

`settings.curation`はAI銘柄キュレーション（自動）の動作パラメータです。`load_tickers()`は`tickers`と`settings.max_tickers`のみ参照するため、`watchlist`や`settings.curation`を追加しても日次予測には影響しません。詳細は`specification_document/ai_ticker_curation/`を参照してください。

## 出力ファイル

| ファイル | 内容 |
|---|---|
| `data/{ticker}.parquet` | 銘柄別OHLCV履歴 |
| `data/archive/` | 無効化された銘柄のparquet退避先 |
| `data/jpx_holidays.json` | JPX営業日判定用の休日キャッシュ |
| `data/macro/` | マクロ指標スナップショット（USD/JPY、TOPIX、日経VI、JGB10年など） |
| `data/models/` | immutableな週次artifact、staging、manifest/checksum、activeポインタ（Phase 1 / Phase 2） |
| `data/outbox/` | prediction/signal/model_registryのDB書き込み失敗時JSONLフォールバックキュー |
| `docs/state.json` | 直近30日分のシグナル履歴 |
| `docs/dashboard_index.json` | 一覧画面向けの銘柄サマリ |
| `docs/tickers/{ticker}.json` | 銘柄詳細画面向けの価格・シグナルデータ |
| `docs/backtest_report.json` | 日次KPIゲート結果 |
| `docs/performance_summary.json` | Phase 0: v2実現的中率・コスト前平均リターン・往復コスト後H1運用曲線（DB由来） |
| `docs/performance_detail.json` | Phase 3: 往復コスト後の非重複cohort資産曲線・ドローダウン・rolling・signal-linked較正。TOPIXは同一basis完全coverage時だけ表示 |
| `docs/signal_outcomes_recent.json` | Phase 3: 直近のv2個別シグナル結果（実現リターン・利用可能時のみ超過リターン・的中・MAE/MFE、最大200行） |
| `docs/model_quality.json` | Phase 1: Brier/IC/較正状態・ドリフト警告のサマリ |
| `docs/drift_report.json` | Phase 1: ドリフト監視の詳細 |
| `docs/portfolio_latest.json` | Phase 2: 当日の目標ポートフォリオ（mode=shadow/active、前日比diff付き） |
| `docs/portfolio_backtest.json` | Phase 2: ポートフォリオのウォークフォワード検証とKPIゲート |
| `docs/cs_model_quality.json` | Phase 2: クロスセクションモデルの週次品質レポート |
| `docs/portfolio_shadow_report.json` | Phase 2: Phase 1比較のシャドー検証レポート（週次） |
| `docs/weekly_retrain_report.json` | 週次再学習レポート |
| `docs/monthly_audit.json` | 月次KPI監査 |
| `docs/universe_refresh_report.json` | 有効ユニバースの週次スナップショット |
| `docs/rotating_refresh_report.json` | 夜間ローテ更新結果 |
| `docs/stress_test_report.json` | 高コスト前提の四半期ストレステスト |
| `docs/curation/decision_*.json` | AI銘柄キュレーションの日次判断（監査ログ） |
| `docs/curation/technical_*.json` | テクニカル候補スコア（baseline/agent精査後） |
| `docs/curation/fundamental_latest.json` | 週次ファンダ候補スコア（日次mergeのキャッシュ） |
| `docs/curation/macro_latest.json` | 週次マクロ（金利・為替）レジーム（ファンダ/レポートが消費） |
| `docs/curation/pool_candidates_*.json` | 隔週プールagentの母集団候補提案 |
| `docs/curation/pool_decision_*.json` | 隔週プールマージの決定（監査ログ） |
| `reports/weekly_*.md` | 週次の総合解説レポート（LINE通知対象） |

`data/watchlist/*.parquet`（候補のwarmupデータ）は`.gitignore`対象で、毎回再取得されるためコミットされません。

`docs/history_data.json`は旧データ契約で、存在する場合は`src/dashboard.py`が削除します。

## 計測DB（Phase 0）

Neon Postgresに以下を記録します。スキーマは`migrations/*.sql`、適用は`scripts/db_migrate.py`です。

- `predictions` / `signals`: 日次の予測値とシグナル（モデルバージョン・ホライズン付き）
- `signal_outcomes`: `scripts/settle_outcomes.py`が`next_session_open_to_close_v2`で1/5/10営業日結果を決済し、entry/exit basisとcontract versionを保存
- `model_registry`: 週次学習モデルのバージョン台帳。activeフラグはmodel kind単位
- `macro_snapshots`: マクロ指標の日次スナップショット
- `portfolio_snapshots` / `backtest_runs` / `backtest_equity`: Phase 2のポートフォリオ記録

書き込みはすべてベストエフォートです。DB障害時はイベントが`data/outbox/`へJSONLで退避され、次回接続時に再送（dedup付き）されます。**DB起因で日次シグナルが止まることはありません。**

## モデル運用（Phase 1）

- 週次（土曜）に`scripts/weekly_model_retrain.py`が一意versionをstagingへ作り、全対象銘柄coverage、schema v3 manifest/checksum、exact booster・較正器・gate evidenceを検証します。合格時だけimmutable directoryをpromoteして`active_model.json`をatomic更新し、`model_registry`へ登録します。失敗時は前activeを保持します。
- 日次の`main.py`は`TRADER_MODEL_MODE=auto`で動作し、runtime契約・manifest検証済みactiveモデルがあれば推論のみ、不在・不一致時は同じcandidate自身のpurged OOS証跡を使うその場学習へフォールバックします。`legacy`は`binary_1d`のrollback経路ですが、価格契約はv2のままです。
- ラベルは既定でトリプルバリア（利確1.5ATR/損切1.0ATR/5営業日）、確率はisotonic較正されます。
- `BUY`/`MILD_BUY`の出口プランは同じ設定値から算出します。利確=`現在値 + TP_ATR×ATR`、損切り=`現在値 - SL_ATR×ATR`（1円単位へ丸め）、期限=`TB_MAX_DAYS`です。これは手動注文の目安であり、自動発注は行いません。
- `scripts/drift_check.py`と`docs/model_quality.json`は日次と同じruntime契約・manifestを検証し、互換なactive版だけIC/Brier/PSIを評価します。不一致はunavailableへfail-closeし、しきい値超過時はDaily WatchdogがGitHub Issueを起票します。

## ポートフォリオ提案（Phase 2・シャドー運用中）

- `scripts/universe_select.py`が流動性・セクター上限に基づき30〜50銘柄のユニバースを決定論的に選定します（現在はレポートのみ、`--apply`で反映）。
- 週次に`scripts/weekly_cross_section_retrain.py`がクロスセクショナルLightGBM（既定ranker）を学習し、OOSの日次IC・precision@Nを`docs/cs_model_quality.json`へ出力、`active_cs_model.json`を更新します。
- 日次の`main.py`がCS推論を行い、逆ボラ重み・銘柄20%/セクター40%/グロス100%上限・最小3%・2%ノートレードバンド・リスクオフ時グロス半減の制約で目標ウェイトを構築し、`docs/portfolio_latest.json`とDBへ書き込みます。
- ウォークフォワード検証はv2実行窓が重ならない期間だけを使い、旧book全決済＋新book全建て＋最終決済へ往復コストを課します。KPIゲート（Sharpe≥0.30、MaxDD≤25%、IR≥0、回転率≤40%）は同一basis・コスト後のTOPIX完全coverageと当日snapshotのCS model version一致も要求します。
- 現在は**shadowモード**です。shadow中はPhase 1のシグナル・LINE通知に一切影響しません。現行マクロパネルにはTOPIX openがなく同一basis benchmarkが作れないため、activeゲートは意図的にfail-closeします。active配線は実装済みですが、TOPIX coverage、同一CS version、KPIゲート、`active_readiness`を満たすまで切替禁止です。
- `scripts/portfolio_shadow_report.py`（週次）は、Phase 1のsignal-linked予測、各日snapshotとexact versionが一致するPhase 2予測、v2決済結果、保存weightが揃うpairedデータだけを比較し、provenanceと`active_readiness`を出力します。

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

ホーム画面は`dashboard_index.json`と`tickers/*.json`（必須）に加えて、`performance_summary.json`（実績カード）、`model_quality.json`（モデル品質カード）、`portfolio_latest.json`（今日の建玉カード）を読み込みます。後者3つは存在しない・`available: false`の場合、カードごと非表示になります。

## push後に画面を更新する手順

`tickers.yml`やフロントエンドをpushしただけでは、公開画面のJSONと静的HTMLがすぐ更新されない場合があります。手動で最新化する場合は以下を実行してください。

1. GitHub Actionsの`Daily Preopen Core`を`Run workflow`で実行し、`main.py`で`data/`と`docs/`のJSONを更新する。
2. JPX休業日で`Daily Preopen Core`がスキップされる場合や、銘柄変更を即時反映したい場合は、ローカルで`uv run python main.py`を実行し、生成された`data/`と`docs/`をpushする。
3. `Daily Publish Dashboard`が自動起動して`web/out/`を`docs/`へ同期することを確認する。自動起動しない場合は、同workflowを手動実行し、`force_publish`を`true`にする。
4. Actions完了後、GitHub Pagesの反映を数分待ち、[公開ダッシュボード](https://fukasedaichi.github.io/trader/)を開く。
5. 古い表示が残る場合は、ブラウザで強制再読み込みするかキャッシュを削除する。

なお`Daily Publish Dashboard`のrsyncは`--delete`付きですが、`docs/`配下のデータJSON（state、performance、portfolio、curation等）はexcludeリストで保護されています。

## GitHub Actions

GitHub Pages公開には、リポジトリ設定でPagesの公開元を`main`ブランチの`/docs`にしてください。Actions secretsには以下を設定します。

- `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID`（LINE通知）
- `DATABASE_URL`（Phase 0計測DB。未設定でも動作するが実績計測が無効になる）
- `CLAUDE_CODE_OAUTH_TOKEN`（AI銘柄キュレーション用。Claude Pro/Max契約で`claude setup-token`を実行して発行）
- `TRADER_REPO_SLUG`（variable、任意。週次レポートのGitHub URL生成用。未設定時は`git remote`から導出）

主なワークフロー:

| Workflow | JST | 役割 |
|---|---:|---|
| `Daily Preopen Core` | 平日 06:00 | 営業日ならマクロ更新→`main.py`→実現リターン決済→ドリフト判定→commit |
| `Daily Preopen Retry` | 平日 06:20/06:40 | 当日未更新なら再実行 |
| `Daily Publish Dashboard` | core/retry成功後 | `web/out`を`docs/`へ同期（データJSONはexcludeで保護） |
| `Daily Watchdog` | 平日 12:30 | 成果物の鮮度・整合性とドリフトを検証し、異常時はGitHub Issue起票 |
| `Daily Ticker Curation` | 平日 04:30 | テクニカル分析→ガード付きで`tickers.yml`を少数入替 |
| `Weekly Model Retrain` | 土曜 08:00 | Phase 1銘柄別再学習＋Phase 2 CS再学習＋シャドー検証レポート |
| `Weekly Universe Refresh` | 日曜 07:00 | 有効銘柄のスナップショットレポート |
| `Weekly Fundamental & Report` | 土曜 07:00 | マクロ→ファンダ分析→週次レポート生成→LINE通知 |
| `Monthly Calendar Sync` | 毎月1日 09:15 | JPX休日キャッシュ更新 |
| `Monthly Full Audit` | 第1日曜 09:00 | 月次KPI監査 |
| `Nightly Rotating Refresh` | 平日 19:30 | 有効銘柄を分割して夜間更新 |
| `Quarterly Stress Test` | 四半期初日 10:00 | 高コスト前提のKPI確認 |
| `Manual DB Migrate` | 手動 | `migrations/*.sql`を冪等適用（DB初期化/更新。`dry_run`でプレビュー） |
| `CI Tests` | push / pull request / 手動 | Pythonの全plain-scriptテストとフロントエンドlint・本番静的ビルド |

すべての書き込み系workflowは、commit/pushを共通ヘルパ`.github/scripts/commit-and-push.sh`（`git pull --rebase --autostash`＋最大3回リトライ）に集約しています。

workflowが直接使うJavaScript ActionはNode 24対応版（`actions/checkout@v6`、`actions/setup-node@v6`、`astral-sh/setup-uv@v8.3.2`）です。フロントエンドのビルド対象は引き続きNode 22で、Action自身の実行ランタイムとは独立しています。

## AI銘柄キュレーション（自動）

Claudeをサブスク（`CLAUDE_CODE_OAUTH_TOKEN`）でGitHub Actions上で実行し、トレンド分析から`tickers.yml`の有効ユニバースを自動更新します。詳細仕様は`specification_document/ai_ticker_curation/`にあります。

- **日次**（平日 04:30 JST / `Daily Ticker Curation`）: 候補データのwarmup → `technical_screen.py`の決定論スコア → テクニカルagent（任意精査）→ `curation_merge.py`が「当日テクニカル＋直近週ファンダ（キャッシュ）」を合成し、ガード通過時のみ`tickers.yml`を少数入替。06:00の`Daily Preopen Core`が更新後ユニバースで予測します。
- **週次**（土曜 07:00 JST / `Weekly Fundamental & Report`）: マクロagent（金利・為替・世界情勢／Web一次情報・Opus）が`macro_latest.json`を生成 → ファンダagent（Web一次情報・Opus）がそれを織り込み`fundamental_latest.json`を更新 → レポートagentがギャル投資家文体（口調はギャル・分析はガチ）で「今後2週間以降に伸びそうな銘柄」を中心とした週次解説`reports/weekly_YYYY-MM-DD.md`を生成 → そのGitHub URLをLINE通知します。
- **隔週**（14日ごと / `Weekly Fundamental & Report`内のステップ）: プールagent（ファンダ＋流動性・Sonnet）が`pool_candidates_latest.json`を提案 → 決定論`curation_pool_merge.py`が候補母集団`curation_pool.yml`を更新（rollout phase 1はadd-onlyで`pool_target_size:60`までgrow、到達後はreplace-onlyへ自動切替）→ 変更を`curation_pool_notify.py`でLINE通知。詳細は`specification_document/ai_ticker_curation/07_pool_refresh.md`。

### 安全設計

- 5つのClaude agent（テクニカル/マクロ/ファンダ/レポート/プール）は`docs/curation/*.json`または`reports/*.md`を書くだけで、`tickers.yml`や`curation_pool.yml`の編集や`git push`は行いません。不可逆変更は決定論の`curation_merge.py`（`tickers.yml`）と`curation_pool_merge.py`（`curation_pool.yml`）、共通ヘルパ`commit-and-push.sh`に限定されます。
- ガードレール: tech/fundの両軸必須、`min_combined_to_promote`、`min_gap`、churn上限（`max_daily_swaps`/`max_daily_adds`）、`sector_cap_pct`、`min_warmup_rows`、`cooldown_days`、`max_fundamental_age_days`（ファンダ鮮度切れで新規昇格を停止）。
- 新規候補は`data/watchlist/`（gitignore）で履歴をwarmupし、十分な履歴がある場合のみ昇格します。
- すべての判断は`docs/curation/decision_*.json`に監査ログとして残ります。巻き戻しは`git revert`、緊急停止は`settings.curation.enabled: false`。

### 設定と運用

- パラメータは`tickers.yml`の`settings.curation`で調整します（プールのノブは`settings.curation.pool`、未設定時はコード既定）。
- `Daily Ticker Curation`は`workflow_dispatch`の`apply=false`でdry-run（`tickers.yml`を変更せず`decision_*.json`のみ生成）できます。
- ファンダ未取得の初回は安全側に「現状維持」で動作します。最初に`Weekly Fundamental & Report`を手動実行するとファンダが生成され、以降の日次で昇格が有効になります。
- 決定ロジックの純粋関数`compute_decision()`は`tests/test_curation_merge.py`で検証できます（`uv run python tests/test_curation_merge.py`）。

## 銘柄選定スキル（対話実行）

このリポジトリには、一次情報から候補を調査し、ガードレールを通る変更だけを決定論`curation_merge.py`経由で`tickers.yml`へ反映する`jp-stock-ticker-curation`スキルがあります。依頼例:

```text
jp-stock-ticker-curation を使って、最新情報で有望な日本株を選んで tickers.yml を更新して
```

スキルは企業IRや決算資料などの一次情報を優先し、業績モメンタム、ガイダンス、還元方針、バリュエーション、セクター分散を見て候補JSONを作ります。`tickers.yml`は直接編集せず、現在のテクニカル結果、warmup、churn、セクター、鮮度のガードを満たす候補だけを決定論スクリプトが反映します。条件未達の候補は提案として報告し、安全策を迂回しません。

## 注意

このシステムは投資助言ではありません。モデルは過去データに基づく確率推定であり、将来の利益を保証しません。実運用では、売買コスト、スリッページ、流動性、決算イベント、急変時の約定リスクを別途確認してください。
