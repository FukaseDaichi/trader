# データ契約・横断仕様

更新日: 2026-05-03 JST

## `tickers.yml`

```yaml
tickers:
  - code: "7011.JP"
    name: "三菱重工業"
    enabled: true
settings:
  max_tickers: null
```

仕様:

- `tickers`は配列
- 各要素は`code`, `name`, `enabled`を持つ想定
- `enabled`省略時は有効扱い
- `settings.max_tickers`が`null`または未指定なら全有効銘柄
- `settings.max_tickers`が整数なら先頭から件数制限
- `max_tickers < 1`はエラー

現状、`code`/`name`必須チェックは明示的には行われず、後段で`KeyError`になる可能性があります。

## `data/{ticker}.parquet`

必須列:

- `date`
- `open`
- `high`
- `low`
- `close`
- `volume`

`date`はtimezoneなしdatetimeへ正規化されます。価格と出来高は数値化できない行を削除します。

## `data/jpx_holidays.json`

`jpx_calendar.py sync`と`data_loader.py`の鮮度判定で使います。

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

`data_loader.py`は`{"holidays": {...}}`形式と、日付キー直下の辞書形式の両方を読めます。

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

- `history`は最大30日
- 1日1エントリ
- 同日再実行時は当日エントリを置換
- `RUN_DATE_JST`で`date`を上書き可能
- `signals`は`tickers.yml`の有効銘柄だけにフィルタされる

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

`data`は最大500行で、列は`date`, `open`, `high`, `low`, `close`, `volume`, `ma_5`, `ma_20`, `ma_60`, `rsi`です。

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
  "confidence_label": "自信なし",
  "confidence_reason": "過去検証で基準未達 (...)",
  "reason": "自信なしのため見送り（過去検証で基準未達）",
  "limit_price": null,
  "stop_loss": null
}
```

`action`は`BUY`, `MILD_BUY`, `HOLD`, `MILD_SELL`, `SELL`のいずれかです。KPIゲート未達時は`raw_action`に予測上のアクションを残し、`action`は`HOLD`になります。

## `docs/backtest_report.json`

日次KPIゲート結果です。

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

## 横断的な注意

- `docs/history_data.json`は現行契約ではありません
- `web/public/`はローカル開発用同期先であり、公開元は`docs/`
- `data/features/*.parquet`は生成されますが、現状では主要処理の入力ではありません
- レポート類の時刻はJSTに統一されていません
