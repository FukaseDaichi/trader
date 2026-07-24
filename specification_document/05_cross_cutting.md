# データ契約・横断仕様

更新日: 2026-07-24 JST

## 設定ファイル

### `tickers.yml`

```yaml
tickers:
  - code: "7011.JP"
    name: "三菱重工業"
    enabled: true
    source: "manual"
settings:
  max_tickers: null
  curation:
    enabled: true
    max_universe: 50
```

日次予測本体の検証仕様（`src/config.py` `load_tickers()`）:

- `tickers` は配列。各要素は `code` / `name` 非空文字列必須、`enabled` 指定時は boolean（省略時有効）
- ticker code の重複はエラー
- `settings.max_tickers`: `null`/未指定で全件、整数で先頭から制限、`< 1` はエラー

キュレーション用の任意メタ（`source`, `added_on`, `sector`, `combined`, `tech_score`, `fund_score`, root の `watchlist`, `settings.curation`）は日次予測では無視され、`scripts/curation_*` と `scripts/universe_select.py` が読み書きします。**`tickers.yml` を直接編集してよいのは決定論スクリプトのみ**（agent による手編集は禁止）。

### `curation_pool.yml`

AI キュレーションの候補プール（`pool[].code/name/sector`）。`technical_screen.py` と `curation_warmup.py` が読みます。書き手は隔週の決定論 `curation_pool_merge.py` のみ（`ai_ticker_curation/07_pool_refresh.md`）。

### `.env` / 環境変数

すべての環境変数の正典はコメント付き`.env.example`（データソース、KPIゲート、閾値最適化、Phase 0 DB、Phase 1ラベル/モデル/較正/ドリフト、Phase 2 CS/ポートフォリオ、Phase 3通知/実績）。既定値は`src/config.py`。`src.env`経由のfloat設定は有限値のみ有効で、`NaN` / `Infinity` は主要設定ではfail-fast、データ取得などの日次補助設定では警告後に既定値へ縮退する。KPIのcanonical名は`TRADER_KPI_MIN_AVG_DAILY_NET_RETURN`、`TRADER_KPI_MIN_ROUND_TRIPS`、`TRADER_AUTO_THRESHOLD_MIN_ROUND_TRIPS`、objective=`avg_daily_net_return`で、旧`EXPECTANCY`／`TRADES`名は警告付きaliasのみ。`vol_norm`は未対応で、残存envは警告して`triple_barrier`へ縮退する。`main.py`は`.env`なしでも動作し、LINE通知とDB接続をスキップする一方、再送可能なprediction/signalイベントは`data/outbox/`へ保存する。

## ローカルデータ（data/）

| パス | 内容 |
|---|---|
| `data/{code}.parquet` | 有効銘柄の日足 OHLCV。`date` は tz なし datetime。OHLCVが有限な行だけを残し、価格正値・OHLC 関係・異常終値変化を検証済み（警告は attrs → レポート） |
| `data/archive/` | 無効化銘柄の parquet 退避先（削除しない） |
| `data/watchlist/{code}.parquet` | キュレーション候補の warmup データ。gitignore 対象、昇格時に `data/` へ移動 |
| `data/macro/macro_panel.parquet` | マクロ系列パネル（usdjpy/topix/nikkei/nikkei_vi/jgb10y + 派生特徴量）。`update_macro_snapshots.py` が更新。`topix` は TOPIX 連動 ETF（1305）のプロキシ値（1306 は調整後系列にも分割級の不連続が残るため不採用）、`nikkei_vi`/`jgb10y` は取得元がなく無効化（全行 NaN） |
| `data/models/.staging/<run>/<version>/` | Phase 1候補の一時領域。active参照されず、候補合否をレポートへ残した後に削除 |
| `data/models/<version>/` | immutableなPhase 1 schema v3。`manifest.json`、version metadata、全対象銘柄のexact final booster、較正器、feature reference、gate evidenceとchecksum |
| `data/models/active_model.json` | atomic replaceされるPhase 1 active pointer。version、artifact/gate契約、manifest/config hash、git commit、activation provenanceを保持 |
| `data/models/cs-v1-*/` + `data/models/active_cs_model.json` | Phase 2 CS モデルバンドルと active ポインタ |
| `data/outbox/*.jsonl` | DB不通時のprediction/signal/model_registryイベントキュー（ファイル名は生成時刻、安定event_idで冪等、復旧時リプレイ）。registry active再送は現在のfile pointer provenanceと一致する場合だけ適用し、古い待機イベントで巻き戻さない。不良イベントは`data/outbox/dead/`へ隔離 |
| `data/jpx_holidays.json` | JPX 休日キャッシュ（`{"holidays": {...}}` 形式と日付キー直下形式の両対応） |

Phase 1の`feature_schema_hash`は列名と順序の契約である。同じ列名のまま特徴量計算の意味を変える場合はartifact schema versionを上げて再学習し、旧版をruntime互換とみなさない。

## 計測 DB（Neon Postgres）

接続は `DATABASE_URL`（GitHub Actions Secret / ローカル `.env`）。スキーマは `migrations/0001〜0004`、適用は `scripts/db_migrate.py`（`schema_migrations` で冪等）。

| テーブル | 内容 |
|---|---|
| `tickers` | 銘柄マスタ（tickers.yml 反映） |
| `model_registry` | モデル版管理（kind: per_ticker / cross_sectional、cv_metrics、calibration、activeフラグ）。active切替はkind単位で、Phase 1登録がCS activeを解除しない |
| `predictions` | モデル生出力（run_date / as_of_date / model_version / horizon / raw_score / prob_up / expected_ret / cs_rank / features_hash） |
| `signals` | 人間向け判断（action / raw_action / conviction / **target_weight** / thresholds / gate_passed / status） |
| `signal_outcomes` | 実現結果台帳（horizon 1/5/10 別行）。`market_as_of_date`、実約定 `entry_date`、entry/exit priceとbasis、`contract_version`、benchmark basis、realized_ret / benchmark_ret / excess_ret / hit / mae / mfe / exit_reason |
| `portfolio_snapshots` | 日次目標建玉（positions JSONB / diff / gross / sector_exposure / expected_vol / regime） |
| `macro_snapshots` | マクロ日次スナップショット |
| `model_quality_snapshots` / `drift_reports` | Phase 1 品質・ドリフト履歴 |
| `backtest_runs` / `backtest_equity` | バックテスト結果と資産曲線 |
| `universe_snapshots` | ユニバース選定履歴 |
| `schema_migrations` | migration 適用履歴 |

日付契約: `run_date`はworkflow実行日、`as_of_date` / `market_as_of_date`は予測に使った最後の市場日、`entry_date`はその次に実在する市場行（最初に売買可能な営業日）、`eval_date`はentryを1日目としたH営業日目。`next_session_open_to_close_v2`の価格基準はentry日のopen→eval日のcloseである。migration 0004は既存行を`close_to_close_v1`と明示し、再決済でv2へ置換する。v2のTOPIXは同じentry openがないためbenchmarkをNULLとし、欠損を0や1倍として補完しない。書き込みはwrite-through + outboxフォールバックで、**DBの状態が日次シグナル生成に影響することはありません**。

## `docs/` 配下の JSON 契約

「必須」はフロントエンドの動作に必要、「任意」は欠損または `available: false` でカード/セクション非表示。

| ファイル | 区分 | 生成元 | 内容 |
|---|---|---|---|
| `state.json` | 必須(内部) | `main.py` | シグナル履歴（最大30日、1日1エントリ、同日再実行は置換、`RUN_DATE_JST` で上書き可） |
| `dashboard_index.json` | 必須 | `main.py` | 一覧画面用インデックス（銘柄ごとの latest_data / latest_signal / rows / prev_close / change_pct） |
| `tickers/{code}.json` | 必須 | `main.py` | 銘柄詳細（`data` 最大500行: date/OHLCV/ma_5/ma_20/ma_60/rsi + シグナル履歴） |
| `backtest_report.json` | 内部 | `main.py` | 実際に推論した保存済み／ephemeral candidateのgate evidence投影（entries[].model_version/gate_evidence_sha256/passed/metrics_tuning/metrics_holdout/thresholds/threshold_optimization） |
| `performance_summary.json` | 任意 | `main.py` + settle | 実現的中率・平均リターン・DB 容量警告 |
| `performance_detail.json` | 任意 | settle / `main.py` | equity_curve（strategy/benchmark）・drawdown・rolling・reliability（契約は下記） |
| `signal_outcomes_recent.json` | 任意 | settle / `main.py` | 直近実現結果（最大200行） |
| `model_quality.json` | 任意 | `main.py` / 週次再学習 | runtime互換性・manifest検証済みPhase 1モデル品質 + ドリフトoverlay。不整合はunavailable |
| `drift_report.json` | 内部 | `drift_check.py` | runtime互換性・manifest検証後のsignal-linked Phase 1 IC/Brier/PSIドリフト |
| `portfolio_latest.json` | 任意 | `main.py` | 今日の目標建玉（positions / diff_summary / gross / expected_vol / mode / model_version） |
| `portfolio_backtest.json` | 内部 | 週次 CS 再学習 | v2実行provenance検証済み・非重複期間のポートフォリオwalk-forward結果。旧book全決済＋新book全建て（最終決済を含む）のコスト後strategy/TOPIXと、除外期間の`data_quality`、CS `model_version`を持つ。`read_portfolio_gate()`は当日snapshotとのexact versionも含めactive可否判定に読む |
| `portfolio_shadow_report.json` | 内部 | 週次 | signal-linked Phase 1とsnapshot exact-version Phase 2のv2 paired比較 + provenance + `active_readiness` |
| `cs_model_quality.json` | 内部 | 週次 CS 再学習 | CS モデル品質 + ポートフォリオゲート結果 |
| `weekly_retrain_report.json` | 内部 | 週次再学習 | 銘柄別学習結果 |
| `curation/*.json` | 内部/任意 | キュレーション | technical/fundamental/decision/warmup/macro_latest/pool_candidates/pool_decision（スキーマは `ai_ticker_curation/04_data_contracts.md` が正） |
| `monthly_audit.json` ほか監査系 | 内部 | 各スクリプト | 監査レポート |

`dashboard_index.json` の前日比フィールド（optional）:

- `tickers.{code}.prev_close` (number|null, optional): 前営業日終値。データ2日分未満なら null。
- `tickers.{code}.change_pct` (number|null, optional): 前日比 (last/prev - 1)。フロントは欠如時に前日比表示を隠す。

### Signal オブジェクト（state.json / dashboard_index / tickers JSON 共通）

```json
{
  "ticker": "7011.JP", "name": "三菱重工業", "date": "2026-07-17",
  "close": 4586.0, "prob_up": 0.72,
  "action": "MILD_BUY", "raw_action": "MILD_BUY",
  "gate_passed": true, "status": "ok",
  "confidence_label": "自信あり", "confidence_reason": "過去検証で基準通過",
  "reason": "やや上昇傾向 (上昇確率 72%)",
  "thresholds": {"buy": 0.8, "mild_buy": 0.65, "mild_sell": 0.25, "sell": 0.1, "volatility_limit": 0.04},
  "threshold_optimization": {},
  "model_version": "per-ticker-v1-20260720T080000-abc123def456-1a2b3c4d", "horizon_days": 5,
  "raw_score": 0.61, "expected_ret": 0.012, "features_hash": "…",
  "artifact_schema_version": 3, "feature_schema_hash": "…",
  "execution_contract_version": "next_session_open_to_close_v2",
  "gate_evidence_sha256": "…", "model_bundle_sha256": "…",
  "limit_price": null, "stop_loss": 4486,
  "take_profit_price": 4736, "stop_price": 4486,
  "take_profit_pct": 0.0327, "stop_pct": -0.0218,
  "time_exit_days": 5,
  "exit_plan": {
    "take_profit_price": 4736, "stop_price": 4486,
    "take_profit_pct": 0.0327, "stop_pct": -0.0218,
    "time_exit_days": 5, "atr": 100.0,
    "tp_atr_mult": 1.5, "sl_atr_mult": 1.0
  }
}
```

- `action` は `BUY` / `MILD_BUY` / `HOLD` / `MILD_SELL` / `SELL`。KPI ゲート未達時は `raw_action` に元判断を残し `action: "HOLD"`
- Phase 1 provenanceは`model_version` / `horizon_days` / `raw_score` / `expected_ret` / 日次入力`features_hash`に加え、artifact schema、feature schema、label/calibration/execution契約、exact model bundle、gate evidenceの識別子を持つ。処理失敗時はnullになり得る
- `features_hash`はその日の入力値、`feature_schema_hash`は順序付き特徴量列の契約であり、用途が異なる
- ephemeral fallbackの`model_version`はartifact schema、artifact contract hash、gate contract hashから安定生成し、異なるlabel/feature/calibration/KPI/execution設定の観測を同じversionへ混ぜない。exact boosterは`model_bundle_sha256`で識別する
- ゲート通過した `BUY` / `MILD_BUY` は、学習ラベルと同じATR倍率から `exit_plan`（利確・損切・現在値比・時間出口・ATR）を持つ。主要値は直下にも平坦化し、DB互換の `stop_loss` は `stop_price` と同値。ATR欠損時はすべて null
- ゲート未達・モデル失敗でHOLDへ強制した場合、`limit_price` / `stop_loss` / `exit_plan` と全平坦化出口フィールドは誤発注防止のため null に消去される
- **active モード時のみ** `target_weight`（建玉外 0.0）が付き、`reason` 末尾に `／建玉 18% (rank 1)` 形式が追記される。shadow では一切付かない
- 処理失敗時は `status: "failed"`、`prob_up`/`close` 等が null になり得る

### `performance_detail.json` の契約

```json
{
  "available": true, "generated_at": "2026-06-24 06:20:00",
  "as_of": "2026-06-24", "horizon_days": 5, "history_days": 180,
  "execution_contract": {"contract_version": "next_session_open_to_close_v2", "entry_price_basis": "next_session_open", "exit_price_basis": "horizon_session_close"},
  "accounting_method": {"name": "non_overlapping_cohorts_v1", "selection": "eval_date_non_overlap", "fallback_reason": null, "overlapping_horizon_returns_compounded": false, "return_basis": "net_after_entry_exit_costs", "cost_bps_per_side": 10.0, "slippage_bps_per_side": 5.0, "round_trip_cost_rate": 0.003},
  "benchmark_coverage": {"selected_cohorts": 3, "available_cohorts": 0, "coverage_ratio": 0.0, "reason": "unavailable_same_basis"},
  "equity_curve": [{"entry_date": "2026-06-10", "date": "2026-06-17", "strategy": 1.004, "benchmark": null, "period_return": 0.004, "n": 3}],
  "drawdown_curve": [{"date": "2026-06-10", "drawdown": -0.012}],
  "rolling": {"hit_rate_20d": 0.58, "avg_return_20d": 0.004, "excess_return_20d": 0.002, "sharpe_60d": 0.85},
  "reliability": {"brier": 0.24, "bins": [{"bin_low": 0.5, "bin_high": 0.6, "mean_prob": 0.55, "frac_up": 0.52, "count": 18}]}
}
```

`equity_curve` はentry/eval期間が重ならないcohortだけを全資本で逐次運用した系列で、毎日の重複H日リターンを直接複利しない。戦略と比較可能なTOPIXの両方から、KPIバックテストと同じ片道cost+slippageをentry/exitの両側で控除する（raw値は `gross_period_return` / `gross_benchmark_return` に保持）。`eval_date` が無い、またはentryより前で不正な互換入力はH個ごとのstrideへ縮退し、`fallback_reason`を出す。hit rate・平均H日return等は重複サンプルを許すコスト前シグナル品質指標であり、資産曲線とは分離する。60日Sharpeだけは資産曲線と同じ非重複・コスト後cohort系列を使う。v2のTOPIX同基準benchmarkは現在取得不能なため `benchmark: null` とcoverage理由を出し、欠損を1.0で持ち回らない。reliabilityは`signals.prediction_id`へ直接紐付くPhase 1確率を優先し、IDがないlegacy行だけconvictionへfallbackする。互換契約内でversion横断し、provenanceにsource別件数・model versions・fallback・除外理由を持つ。DB不通・サンプル不足の`available: false`成果物にも、現行`execution_contract`・`accounting_method`・benchmark coverageを残す。

## `reports/weekly_YYYY-MM-DD.md`

週次レポート。`reports/` は publish の rsync 対象外で、LINE には GitHub blob URL を通知します。

## 横断的な注意

- `docs/history_data.json` は廃止済み契約。`src/dashboard.py` と publish workflow が存在すれば削除する
- `web/public/` はローカル開発用同期先。公開元は `docs/`
- **`docs/` 直下に新しいデータファイルを追加したら publish workflow の `--exclude` へ追加**（`tests/test_publish_workflow.py` が検査）
- `state.json` の `last_update` は JST。監査・再学習・バックテスト系レポートの `generated_at` は `+09:00` 付き JST ISO 8601
- テストは pytest 非依存の standalone スクリプト（`uv run python tests/test_<name>.py`）。DB 不要で全件実行できる
- `main.py` をローカル実行すると `docs/` / `web/public/`（git 管理対象）と `data/outbox/` が書き換わるため、コミット前に `git checkout -- docs/ web/public/` 等での復元に注意
