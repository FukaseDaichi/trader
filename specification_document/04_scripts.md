# 補助スクリプト仕様

更新日: 2026-05-03 JST

## `scripts/jpx_calendar.py`

JPX営業日判定と休日キャッシュ同期を行います。

コマンド:

- `is-open`: 指定日または今日JSTが営業日か判定
- `sync`: `data/jpx_holidays.json`を更新

休日ソースは`https://holidays-jp.github.io/api/v1/date.json`です。加えて1月2日、1月3日、12月31日を取引所休業日として補完します。

`is-open`はリモート取得に失敗した場合、ローカルキャッシュまたは年末年始補完のみで判定を継続します。

## `scripts/run_guard.py`

`docs/state.json`を読み、当日JSTの履歴エントリがあるか判定します。

コマンド:

- `needs-core-run`: 当日エントリがなければ`needs_run=true`
- `has-today-update`: 当日エントリがあれば`has_today_update=true`

GitHub Actionsでは`--github-output`で結果を`GITHUB_OUTPUT`へ書きます。

## `scripts/workflow_watchdog.py`

日次成果物の健全性を検証します。

確認項目:

- `docs/state.json`に当日エントリがある
- `docs/dashboard_index.json`が存在し、当日更新で、enabled銘柄を含む
- `docs/tickers/{code}.json`が存在し、`data`配列を持つ
- ticker JSON合計サイズが上限以下
- `docs/backtest_report.json`のentriesがenabled銘柄数以上

失敗時はexit code 1を返しますが、現時点では外部通知は行いません。

## `scripts/universe_refresh.py`

現在の有効銘柄をスナップショットし、`docs/universe_refresh_report.json`を出力します。

出力:

- universe size
- active ticker list
- 銘柄別のデータ有無、行数、最新日

現状は候補銘柄を探索して`tickers.yml`を更新する処理ではありません。

## `scripts/rotating_refresh.py`

有効銘柄を`--buckets`で分割し、JST曜日に対応するバケットだけ`update_data()`します。

既定:

- output: `docs/rotating_refresh_report.json`
- buckets: `5`

一部銘柄で例外が出た場合は`failed`に記録し、exit code 1を返します。

## `scripts/feature_precompute.py`

有効銘柄の特徴量を`data/features/{code}.parquet`へ保存し、`docs/feature_precompute_report.json`を出力します。

現行の重要な制約:

- `data/features/*.parquet`はGitHub Actionsでcommitされない
- `main.py`や他スクリプトはこの事前計算結果を読まない
- そのため、現時点ではレポート作成以上の効果はありません

## `scripts/monthly_audit.py`

全有効銘柄に対して`evaluate_kpi_gate()`を実行し、`docs/monthly_audit.json`を出力します。

集計:

- total / ok / passed / failed ticker count
- 平均CAGR
- 平均最大ドローダウン
- 平均Sharpe
- 平均期待値
- 平均turnover

## `scripts/stress_test.py`

通常のKPI設定をコピーし、`--cost-bps`と`--slippage-bps`だけを高コスト前提に変えて再評価します。

既定:

- output: `docs/stress_test_report.json`
- cost: `20.0 bps`
- slippage: `10.0 bps`

## 実装上の共通点

多くのスクリプトは`ROOT_DIR`を`sys.path`へ追加して、リポジトリルート外からでも`src.*`をimportできるようにしています。レポートの`generated_at`は複数スクリプトでtimezone naiveな`datetime.now()`です。
