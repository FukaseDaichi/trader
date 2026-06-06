# データ契約・横断仕様

更新日: 2026-06-06 JST

## `tickers.yml`

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
    max_universe: 10
```

日次予測本体の仕様:

- `tickers` は配列
- 各要素は `code`, `name`, `enabled` を持つ
- `code` と `name` は非空文字列必須
- `enabled` 省略時は有効扱い。指定時は boolean 必須
- ticker code の重複はエラー
- `settings` は指定時 mapping 必須
- `settings.max_tickers` が `null` または未指定なら全有効銘柄
- `settings.max_tickers` が整数なら先頭から件数制限
- `max_tickers < 1` はエラー

AI 銘柄キュレーション用の任意メタ:

- ticker item: `source`, `added_on`, `disabled_on`, `sector`, `combined`, `tech_score`, `fund_score`
- root: `watchlist`
- settings: `settings.curation`

これらは `load_tickers()` の日次予測対象抽出では無視されますが、`scripts/curation_*` が読み書きします。

## `curation_pool.yml`

AI キュレーションの日次テクニカルスクリーニング候補プールです。

```yaml
pool:
  - code: "7203.JP"
    name: "トヨタ自動車"
    sector: "自動車"
```

`scripts/technical_screen.py` と `scripts/curation_warmup.py` が読みます。候補は `NNNN.JP` 形式で、`sector` は分散ガードに使われます。

## `data/{ticker}.parquet`

必須列:

- `date`
- `open`
- `high`
- `low`
- `close`
- `volume`

`date` は timezone なし datetime へ正規化されます。価格と出来高は数値化できない行を削除します。さらに、価格の正値、`low <= open/close <= high`、異常な終値変化を検証します。検証警告は `data_validation_warnings` として日次・監査系レポートに残ります。

## `data/watchlist/{ticker}.parquet`

AI キュレーション候補の warmup データです。`scripts/curation_warmup.py` が `update_data(dest_dir=...)` で保存します。

- `.gitignore` 対象で、通常 commit しない
- `sync_data_files()` の退避対象外
- 昇格時に `curation_merge.py` が必要に応じて `data/{ticker}.parquet` へ移動

## `data/jpx_holidays.json`

`jpx_calendar.py sync` と `data_loader.py` の鮮度判定で使います。

対応形式:

```json
{
  "updated_at": "2026-05-03T00:00:00Z",
  "source_url": "https://holidays-jp.github.io/api/v1/date.json",
  "holidays": {
    "2026-01-01": "元日"
  }
}
```

`data_loader.py` と `jpx_calendar.py` は `{"holidays": {...}}` 形式と、日付キー直下の辞書形式の両方を読めます。

## `docs/state.json`

シグナル履歴です。

```json
{
  "last_update": "2026-05-03 06:05:00",
  "history": [
    {
      "date": "2026-05-03",
      "signals": []
    }
  ]
}
```

仕様:

- `history` は最大 30 日
- 1 日 1 エントリ
- 同日再実行時は当日エントリを置換
- `RUN_DATE_JST` で `date` を上書き可能
- `signals` は `tickers.yml` の有効銘柄だけにフィルタされる

## `docs/dashboard_index.json`

一覧画面用の軽量インデックスです。

```json
{
  "last_update": "2026-05-03 06:05:00",
  "tickers": {
    "7011.JP": {
      "ticker": "7011.JP",
      "name": "三菱重工業",
      "latest_data": {},
      "avg_volume_20": 1234567.0,
      "latest_signal": {},
      "data_file": "tickers/7011.JP.json",
      "rows": 500
    }
  }
}
```

## `docs/tickers/{code}.json`

銘柄詳細画面用です。

```json
{
  "last_update": "2026-05-03 06:05:00",
  "ticker": "7011.JP",
  "name": "三菱重工業",
  "latest_signal": {},
  "signals": [],
  "data": []
}
```

`data` は最大 500 行で、列は `date`, `open`, `high`, `low`, `close`, `volume`, `ma_5`, `ma_20`, `ma_60`, `rsi` です。

## Signalオブジェクト

```json
{
  "ticker": "7011.JP",
  "name": "三菱重工業",
  "date": "2026-05-01",
  "close": 4586.0,
  "prob_up": 0.72,
  "action": "HOLD",
  "raw_action": "MILD_BUY",
  "gate_passed": false,
  "status": "ok",
  "confidence_label": "自信なし",
  "confidence_reason": "過去検証で基準未達 (...)",
  "reason": "自信なしのため見送り（過去検証で基準未達）",
  "thresholds": {
    "buy": 0.8,
    "mild_buy": 0.65,
    "mild_sell": 0.25,
    "sell": 0.1,
    "volatility_limit": 0.04
  },
  "threshold_optimization": {},
  "limit_price": null,
  "stop_loss": null
}
```

`action` は `BUY`, `MILD_BUY`, `HOLD`, `MILD_SELL`, `SELL` のいずれかです。KPI ゲート未達時は `raw_action` に予測上のアクションを残し、`action` は `HOLD` になります。

銘柄単位の処理失敗時は `status: "failed"`、`action: "HOLD"` になり、`prob_up` や `close` は `null` になり得ます。失敗シグナルでは `thresholds` と `threshold_optimization` が付かない場合があります。

## `docs/backtest_report.json`

日次 KPI ゲート結果です。

主なフィールド:

- `generated_at`
- `entries[].ticker`
- `entries[].passed`
- `entries[].reason`
- `entries[].failures`
- `entries[].metrics`
- `entries[].metrics_tuning`
- `entries[].metrics_holdout`
- `entries[].thresholds`
- `entries[].threshold_optimization`
- `entries[].status`
- `entries[].data_validation_warnings`

## `docs/curation/*.json`

AI 銘柄キュレーションの作業物・監査ログです。

- `technical_features.json`: `technical_screen.py` の中間特徴量
- `technical_latest.json`, `technical_YYYY-MM-DD.json`: テクニカル候補スコア
- `fundamental_latest.json`, `fundamental_YYYY-MM-DD.json`: 週次ファンダメンタル候補スコア
- `decision_latest.json`, `decision_YYYY-MM-DD.json`: 決定論 merge の監査ログ
- `warmup_report.json`: warmup 結果

詳細スキーマは `ai_ticker_curation/04_data_contracts.md` を正とします。

## `reports/weekly_YYYY-MM-DD.md`

週次ファンダ・テクニカル総合レポートです。`weekly-fundamental-report.yml` のレポートライター agent が生成し、`curation_notify.py` が GitHub blob URL を LINE 通知します。

`reports/` は `docs/` 外にあり、GitHub Pages の publish `rsync --delete` の対象外です。

## 横断的な注意

- `docs/history_data.json` は現行契約ではありません
- `web/public/` はローカル開発用同期先であり、公開元は `docs/`
- `data/features/*.parquet` は生成されますが、現状では主要処理の入力ではなく、workflow の commit 対象でもありません
- 無効化された銘柄のトップレベル `data/*.parquet` は削除せず、`data/archive/` へ移動します
- `state.json` の `last_update` は JST ですが、監査系・バックテスト系レポートの `generated_at` はすべて JST に統一されているわけではありません
