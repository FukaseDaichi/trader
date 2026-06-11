# データ契約・横断仕様

更新日: 2026-06-11 JST

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

AI キュレーションの候補プール（`pool[].code/name/sector`）。`technical_screen.py` と `curation_warmup.py` が読みます。

### `.env` / 環境変数

すべての環境変数の正典はコメント付き `.env.example`（データソース、KPI ゲート、閾値最適化、Phase 0 DB、Phase 1 ラベル/モデル/較正/ドリフト、Phase 2 CS/ポートフォリオ、Phase 3 通知/実績）。既定値は `src/config.py`。`main.py` は `.env` なしでも動作し、LINE 通知と DB 書き込みはスキップされます。

## ローカルデータ（data/）

| パス | 内容 |
|---|---|
| `data/{code}.parquet` | 有効銘柄の日足 OHLCV。`date` は tz なし datetime、価格正値・OHLC 関係・異常終値変化を検証済み（警告は attrs → レポート） |
| `data/archive/` | 無効化銘柄の parquet 退避先（削除しない） |
| `data/watchlist/{code}.parquet` | キュレーション候補の warmup データ。gitignore 対象、昇格時に `data/` へ移動 |
| `data/macro/macro_panel.parquet` | マクロ系列パネル（usdjpy/topix/nikkei/nikkei_vi/jgb10y + 派生特徴量）。`update_macro_snapshots.py` が更新。`topix` は TOPIX 連動 ETF（1306）のプロキシ値、`nikkei_vi`/`jgb10y` は取得元がなく無効化（全行 NaN）— 経緯は `06_issues_and_backlog.md` #1 |
| `data/models/<version>/` | Phase 1 銘柄別モデルバンドル（booster + 較正器 + メタ） |
| `data/models/active_model.json` | Phase 1 active ポインタ（version, macro_features_enabled 等） |
| `data/models/cs-v1-*/` + `data/models/active_cs_model.json` | Phase 2 CS モデルバンドルと active ポインタ |
| `data/outbox/YYYY-MM-DD.jsonl` | DB 不通時のイベントキュー（event_id で冪等、復旧時リプレイ） |
| `data/jpx_holidays.json` | JPX 休日キャッシュ（`{"holidays": {...}}` 形式と日付キー直下形式の両対応） |

## 計測 DB（Neon Postgres）

接続は `DATABASE_URL`（GitHub Actions Secret / ローカル `.env`）。スキーマは `migrations/0001〜0003`、適用は `scripts/db_migrate.py`（`schema_migrations` で冪等）。

| テーブル | 内容 |
|---|---|
| `tickers` | 銘柄マスタ（tickers.yml 反映） |
| `model_registry` | モデル版管理（kind: per_ticker / cross_sectional、cv_metrics、calibration、active フラグ） |
| `predictions` | モデル生出力（run_date / as_of_date / model_version / horizon / raw_score / prob_up / expected_ret / cs_rank / features_hash） |
| `signals` | 人間向け判断（action / raw_action / conviction / **target_weight** / thresholds / gate_passed / status） |
| `signal_outcomes` | 実現結果台帳（horizon 1/5/10 別行: realized_ret / **benchmark_ret / excess_ret**（TOPIX）/ hit / mae / mfe / exit_reason） |
| `portfolio_snapshots` | 日次目標建玉（positions JSONB / diff / gross / sector_exposure / expected_vol / regime） |
| `macro_snapshots` | マクロ日次スナップショット |
| `model_quality_snapshots` / `drift_reports` | Phase 1 品質・ドリフト履歴 |
| `backtest_runs` / `backtest_equity` | バックテスト結果と資産曲線 |
| `universe_snapshots` | ユニバース選定履歴 |
| `schema_migrations` | migration 適用履歴 |

日付契約: `run_date` は workflow 実行日、`as_of_date` は予測に使った最新価格日。outcome は `as_of_date` 起点で各 horizon を評価します。書き込みは write-through + outbox フォールバックで、**DB の状態が日次シグナル生成に影響することはありません**。容量は `TRADER_DB_STORAGE_WARN_MB`（既定400）超過で警告。

## `docs/` 配下の JSON 契約

「必須」はフロントエンドの動作に必要、「任意」は欠損または `available: false` でカード/セクション非表示。

| ファイル | 区分 | 生成元 | 内容 |
|---|---|---|---|
| `state.json` | 必須(内部) | `main.py` | シグナル履歴（最大30日、1日1エントリ、同日再実行は置換、`RUN_DATE_JST` で上書き可） |
| `dashboard_index.json` | 必須 | `main.py` | 一覧画面用インデックス（銘柄ごとの latest_data / latest_signal / rows） |
| `tickers/{code}.json` | 必須 | `main.py` | 銘柄詳細（`data` 最大500行: date/OHLCV/ma_5/ma_20/ma_60/rsi + シグナル履歴） |
| `backtest_report.json` | 内部 | `main.py` | KPI ゲート結果（entries[].passed/metrics/thresholds/threshold_optimization/data_validation_warnings） |
| `performance_summary.json` | 任意 | `main.py` + settle | 実現的中率・平均リターン・DB 容量警告 |
| `performance_detail.json` | 任意 | settle / `main.py` | equity_curve（strategy/benchmark）・drawdown・rolling・reliability（契約は下記） |
| `signal_outcomes_recent.json` | 任意 | settle / `main.py` | 直近実現結果（最大200行） |
| `model_quality.json` | 任意 | `main.py` | Phase 1 モデル品質 + ドリフト overlay |
| `drift_report.json` | 内部 | `drift_check.py` | IC/Brier/PSI ドリフト |
| `portfolio_latest.json` | 任意 | `main.py` | 今日の目標建玉（positions / diff_summary / gross / expected_vol / mode / model_version） |
| `portfolio_backtest.json` | 内部 | 週次 CS 再学習 | ポートフォリオ walk-forward 結果。`read_portfolio_gate()` が active 可否判定に読む |
| `portfolio_shadow_report.json` | 内部 | 週次 | Phase 1 vs 2 比較 + `active_readiness` |
| `cs_model_quality.json` | 内部 | 週次 CS 再学習 | CS モデル品質 + ポートフォリオゲート結果 |
| `weekly_retrain_report.json` | 内部 | 週次再学習 | 銘柄別学習結果 |
| `curation/*.json` | 内部/任意 | キュレーション | technical/fundamental/decision/warmup/macro_latest（スキーマは `ai_ticker_curation/04_data_contracts.md` が正） |
| `monthly_audit.json` ほか監査系 | 内部 | 各スクリプト | 監査レポート |

### Signal オブジェクト（state.json / dashboard_index / tickers JSON 共通）

```json
{
  "ticker": "7011.JP", "name": "三菱重工業", "date": "2026-06-10",
  "close": 4586.0, "prob_up": 0.72,
  "action": "HOLD", "raw_action": "MILD_BUY",
  "gate_passed": false, "status": "ok",
  "confidence_label": "自信なし", "confidence_reason": "過去検証で基準未達 (...)",
  "reason": "自信なしのため見送り（過去検証で基準未達）",
  "thresholds": {"buy": 0.8, "mild_buy": 0.65, "mild_sell": 0.25, "sell": 0.1, "volatility_limit": 0.04},
  "threshold_optimization": {},
  "model_version": "per-ticker-v1-20260613", "horizon_days": 5,
  "raw_score": 0.61, "expected_ret": 0.012, "features_hash": "…",
  "limit_price": null, "stop_loss": null
}
```

- `action` は `BUY` / `MILD_BUY` / `HOLD` / `MILD_SELL` / `SELL`。KPI ゲート未達時は `raw_action` に元判断を残し `action: "HOLD"`
- Phase 1 provenance（`model_version` / `horizon_days` / `raw_score` / `expected_ret` / `features_hash`）は推論経路により null になり得る
- **active モード時のみ** `target_weight`（建玉外 0.0）が付き、`reason` 末尾に `／建玉 18% (rank 1)` 形式が追記される。shadow では一切付かない
- 処理失敗時は `status: "failed"`、`prob_up`/`close` 等が null になり得る

### `performance_detail.json` の契約

```json
{
  "available": true, "generated_at": "2026-06-24 06:20:00",
  "as_of": "2026-06-24", "horizon_days": 5, "history_days": 180,
  "equity_curve": [{"date": "2026-06-10", "strategy": 1.004, "benchmark": 1.002, "n": 3}],
  "drawdown_curve": [{"date": "2026-06-10", "drawdown": -0.012}],
  "rolling": {"hit_rate_20d": 0.58, "avg_return_20d": 0.004, "excess_return_20d": 0.002, "sharpe_60d": 0.85},
  "reliability": {"brier": 0.24, "bins": [{"bin_low": 0.5, "bin_high": 0.6, "mean_prob": 0.55, "frac_up": 0.52, "count": 18}]}
}
```

`benchmark` は TOPIX 同期間複利（欠損日は前日値キャリー）。DB 不通・サンプル不足は `{"available": false, "reason": "..."}`。

## `reports/weekly_YYYY-MM-DD.md`

週次レポート。`reports/` は publish の rsync 対象外で、LINE には GitHub blob URL を通知します。

## 横断的な注意

- `docs/history_data.json` は廃止済み契約。`src/dashboard.py` と publish workflow が存在すれば削除する
- `web/public/` はローカル開発用同期先。公開元は `docs/`
- **`docs/` 直下に新しいデータファイルを追加したら publish workflow の `--exclude` へ追加**（`tests/test_publish_workflow.py` が検査）
- `state.json` の `last_update` は JST。監査系レポートの `generated_at` は一部 naive（JST 未統一）
- テストは pytest 非依存の standalone スクリプト（`uv run python tests/test_<name>.py`）。DB 不要で全件実行できる
- `main.py` をローカル実行すると `docs/` / `web/public/`（git 管理対象）と `data/outbox/` が書き換わるため、コミット前に `git checkout -- docs/ web/public/` 等での復元に注意
