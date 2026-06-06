# 補助スクリプト仕様

更新日: 2026-06-06 JST

## `scripts/jpx_calendar.py`

JPX 営業日判定と休日キャッシュ同期を行います。

コマンド:

- `is-open`: 指定日または今日 JST が営業日か判定
- `sync`: `data/jpx_holidays.json` を更新

休日ソースは `https://holidays-jp.github.io/api/v1/date.json` です。加えて 1 月 2 日、1 月 3 日、12 月 31 日を取引所休業日として補完します。

`is-open` はリモート取得に失敗した場合、ローカルキャッシュまたは年末年始補完のみで判定を継続します。`--github-output` 指定時は `is_open`、`market_reason`、`target_date` を `GITHUB_OUTPUT` へ書きます。

## `scripts/run_guard.py`

`docs/state.json` を読み、当日 JST の履歴エントリがあるか判定します。

コマンド:

- `needs-core-run`: 当日エントリがなければ `needs_run=true`
- `has-today-update`: 当日エントリがあれば `has_today_update=true`

GitHub Actions では `--github-output` で結果を `GITHUB_OUTPUT` へ書きます。

## `scripts/workflow_watchdog.py`

日次成果物の健全性を検証します。

確認項目:

- `docs/state.json` に当日エントリがある
- `docs/dashboard_index.json` が存在し、当日更新で、enabled 銘柄を含む
- `docs/tickers/{code}.json` が存在し、`data` 配列を持つ
- ticker JSON 合計サイズが上限以下
- `docs/backtest_report.json` の `entries` が enabled 銘柄数以上

失敗時は exit code 1 を返します。GitHub Actions 側では失敗時に GitHub Issue を作成します。

## `scripts/weekly_model_retrain.py`

週次メンテナンス用に、データ更新、特徴量生成、KPI ゲート評価、学習可否確認を実行し、`docs/weekly_retrain_report.json` を出力します。

重要な点:

- `state.json` は更新しない
- `dashboard_index.json` と ticker JSON は更新しない
- LINE 通知は行わない
- 銘柄単位の失敗はレポートへ記録し、他銘柄の処理を継続する

## `scripts/universe_refresh.py`

現在の有効銘柄をスナップショットし、`docs/universe_refresh_report.json` を出力します。

出力:

- universe size
- active ticker list
- 銘柄別のデータ有無、行数、最新日

現状は候補銘柄を探索して `tickers.yml` を更新する処理ではありません。AI 銘柄キュレーションの候補管理は `curation_pool.yml` と `scripts/curation_*` が担当します。

## `scripts/rotating_refresh.py`

有効銘柄を `--buckets` で分割し、JST 曜日に対応するバケットだけ `update_data()` します。

既定:

- output: `docs/rotating_refresh_report.json`
- buckets: `5`

一部銘柄で例外が出た場合は `failed` に記録し、exit code 1 を返します。

## `scripts/feature_precompute.py`

有効銘柄の特徴量を `data/features/{code}.parquet` へ保存し、`docs/feature_precompute_report.json` を出力します。

現行の重要な制約:

- `data/features/*.parquet` は GitHub Actions で commit されない
- `main.py` や他スクリプトはこの事前計算結果を読まない
- そのため、現時点ではレポート作成以上の効果はありません

## `scripts/monthly_audit.py`

全有効銘柄に対して `evaluate_kpi_gate()` を実行し、`docs/monthly_audit.json` を出力します。

集計:

- total / ok / passed / failed ticker count
- 平均 CAGR
- 平均最大ドローダウン
- 平均 Sharpe
- 平均期待値
- 平均 turnover

## `scripts/stress_test.py`

通常の KPI 設定をコピーし、`--cost-bps` と `--slippage-bps` だけを高コスト前提に変えて再評価します。

既定:

- output: `docs/stress_test_report.json`
- cost: `20.0 bps`
- slippage: `10.0 bps`

日次 `backtest_report.json` と違い、現行の stress test entries には `thresholds` と `threshold_optimization` は出力されません。

## AI銘柄キュレーション共通

`scripts/curation_common.py` は AI 銘柄キュレーション用の共通ヘルパです。

- 主要パス: `docs/curation/`, `reports/`, `data/watchlist/`, `curation_pool.yml`
- `tickers.yml` の読み書き
- `settings.curation` の既定値 merge
- JST 時刻ヘルパ
- JSON 読み書き

既定の `settings.curation` は `max_universe=10`、`max_daily_swaps=2`、`max_daily_adds=2`、`min_warmup_rows=200`、`max_fundamental_age_days=14` などです。

## `scripts/curation_warmup.py`

候補 pool と watchlist のうち enabled ではない銘柄を `data/watchlist/` へ取得します。

入力:

- `curation_pool.yml`
- `tickers.yml` の enabled / watchlist

出力:

- `data/watchlist/{code}.parquet`
- `docs/curation/warmup_report.json`

ネットワークや parse 失敗は銘柄単位で処理し、ジョブ全体は継続します。

## `scripts/technical_screen.py`

AI キュレーションの日次テクニカルスクリーニングです。

入力:

- `curation_pool.yml`
- enabled tickers
- `tickers.yml` の watchlist
- `data/{code}.parquet` または `data/watchlist/{code}.parquet`

処理:

- `src.model.add_features(dropna=False)` で特徴量を計算
- MA、リターン、RSI、MACD、出来高比率、20日高値位置などから決定論スコアを 0-100 で算出
- warmup 行数は `settings.curation.min_warmup_rows` を使う

出力:

- `docs/curation/technical_features.json`
- `docs/curation/technical_latest.json`
- `docs/curation/technical_YYYY-MM-DD.json`

Claude の technical agent が失敗しても、この baseline JSON が merge の安全網になります。

## `scripts/curation_merge.py`

AI キュレーションの安全-critical な決定論 merge です。LLM は呼びません。

入力:

- `docs/curation/technical_latest.json`
- `docs/curation/fundamental_latest.json` または `--fundamental`
- `tickers.yml`
- `settings.curation`
- `data/watchlist/*.parquet`

処理:

- tech/fund score を `tech_weight` / `fund_weight` で合成
- 新規昇格は tech と fund の両方があり、`combined >= min_combined_to_promote`、warmup OK、cooldown 外の候補に限定
- ファンダキャッシュ欠落または `max_fundamental_age_days` 超過時は conservative mode になり、ユニバース変更を止める
- `max_universe` 未満なら `max_daily_adds` まで追加
- 満杯時は `max_daily_swaps`、`min_gap`、`keep_floor` を満たす場合だけ入替
- セクター比率は `sector_cap_pct` でガード
- 昇格時は `data/watchlist/{code}.parquet` を `data/{code}.parquet` へ移動

出力:

- `docs/curation/decision_latest.json`
- `docs/curation/decision_YYYY-MM-DD.json`
- `--apply` 時のみ `tickers.yml`

`--dry-run` または `--apply` 未指定では監査ログのみ書きます。

## `scripts/curation_guard.py`

日次キュレーションの冪等ガードです。

コマンド:

- `needs-run`: 当日の `docs/curation/decision_YYYY-MM-DD.json` がなく、`decision_latest.json` も当日でなければ `needs_run=true`

GitHub Actions では `--github-output` で結果を `GITHUB_OUTPUT` へ書きます。

## `scripts/curation_notify.py`

週次レポートの GitHub URL を LINE 通知します。

入力:

- `--report reports/weekly_YYYY-MM-DD.md`
- `settings.curation.report.persona_name`
- `TRADER_REPO_SLUG` または `git remote.origin.url`
- LINE 環境変数

通知本文はカジュアルな女の子ナビ文体で、レポート先頭の `###` 見出しを注目銘柄として取り込みます。LINE 設定がない場合は送信せず、送信予定本文を標準出力に出します。

## `.claude/skills/*`

GitHub Actions の Claude Code Action から `/skill-name` で起動される skill です。

- `.claude/skills/jp-stock-technical-screen/SKILL.md`: `technical_screen.py` の結果を読んで `technical_latest.json` を精査。`tickers.yml` 非編集
- `.claude/skills/jp-stock-fundamental-screen/SKILL.md`: 最新の一次情報を調査し `fundamental_latest.json` と日付版を出力。`tickers.yml` 非編集
- `.claude/skills/weekly-stock-report/SKILL.md`: `fundamental_latest.json`、`technical_latest.json`、直近 decision logs から `reports/weekly_*.md` を生成

## 実装上の共通点

多くのスクリプトは `ROOT_DIR` を `sys.path` へ追加して、リポジトリルート外からでも `src.*` を import できるようにしています。監査系レポートの `generated_at` は複数スクリプトで timezone naive な `datetime.now()` です。一方、AI キュレーション系の `generated_at` は `now_jst_iso()` により `+09:00` 付きです。
