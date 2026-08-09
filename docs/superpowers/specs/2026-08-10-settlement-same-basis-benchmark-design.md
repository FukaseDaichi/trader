# 決済側 同一basis TOPIXベンチマーク実装 — 設計

日付: 2026-08-10
状態: ユーザー承認済み

## 目的

`signal_outcomes`（v2契約）の `benchmark_ret` / `excess_ret` が常にNULLである状態を解消する。
マクロパネルに実装済みの `topix_open`（1305.T、`topix`終値と同一銘柄・同一basis）を使い、
Phase 2バックテストと同じ「翌営業日寄付き→H営業日目終値」基準のベンチマークを
決済側でも計算する。これにより `performance_summary.json` / `performance_detail.json` /
`signal_outcomes_recent.json` のTOPIX比較が有効化され、2026-08-22の総合判定で
「Phase 1の実測成績が市場比でどうか」を過去に遡って評価できる。

## スコープ

変更対象は決済側のみ:

- `scripts/settle_outcomes.py`
- `src/db_records.py`
- `src/db.py`
- `src/execution.py`
- `tests/test_settle_outcomes.py`（更新＋追加）
- 仕様書（`01_backend_python.md` / `03_cicd_workflows.md` / `04_scripts.md` /
  `05_cross_cutting.md` / `06_issues_and_backlog.md`）

無変更（確認済み）:

- `src/performance.py` — v2契約行のみ集計するため、`benchmark_ret`が埋まれば
  自動でcoverage/equity比較が有効化される。
- フロントエンド `web/src/components/PerformanceDetail.tsx` — 行の
  `benchmark_basis`が`unavailable`系でなく`close_to_close_v1`でもない行が
  揃ったときだけTOPIX列を表示する実装済みロジックをそのまま使う。
- 旧v1（`close_to_close_v1`）のclose-to-close補填は**削除**する（ユーザー了承）。
  v1行は通常運転がv2未決済として段階的に再決済する既存経路で置換される。

## 計算契約

- `benchmark_ret = topix[eval_date](終値) / topix_open[entry_date] − 1`
  （グロス。コスト控除は従来どおりexport側で戦略・ベンチマーク両方に同額適用）
- `excess_ret = realized_ret − benchmark_ret`
- 日付は完全一致のみ。前日埋めしない（`05_cross_cutting.md`の`topix_open`契約）。
- `topix_open[entry_date]`または`topix[eval_date]`が欠損・非有限・非正なら
  `benchmark_ret`/`excess_ret`はNULLのまま決済を続行する。
  後日の`--refill-benchmark`（日次workflowで毎営業日実行される）が自己修復する。

## 変更詳細

### `src/execution.py`

- `BENCHMARK_BASIS = "next_session_open_to_horizon_session_close"` へ変更
  （Phase 2バックテストの`required_basis`と同一文字列）。
- 縮退用に `BENCHMARK_BASIS_UNAVAILABLE = "unavailable_same_basis"` を追加。
- `execution_contract_metadata()`の`benchmark_basis`は新しい`BENCHMARK_BASIS`を返す。

### `src/db_records.py`

- `compute_benchmark_ret(benchmark_by_date, entry_date, eval_date)`（close-to-close）
  を削除し、open→close版の純粋関数に置換:
  `compute_benchmark_ret(open_by_date, close_by_date, entry_date, eval_date) -> float | None`
  - entry側は`open_by_date[entry_date]`、exit側は`close_by_date[eval_date]`。
  - どちらか欠損・0以下・非有限ならNone。

### `scripts/settle_outcomes.py`

- `_load_topix_by_date()`を`topix_open`と`topix`の2列を読むloaderに変更
  （それぞれ日付→float辞書。列欠損・全NaN・読み込み失敗は空辞書＋ログで縮退）。
- 決済ループ内で新`compute_benchmark_ret`をインライン呼び出しし、
  計算できた場合のみ`benchmark_ret`/`excess_ret`を書き、行の`benchmark_basis`は
  値が計算できたときだけ`BENCHMARK_BASIS`、できなければ
  `BENCHMARK_BASIS_UNAVAILABLE`とする。
- loaderは通常決済でも必要になるため、`--refill-benchmark`指定時だけでなく
  常にロードする（失敗しても決済は続行）。
- `--refill-benchmark`をv2（現行契約）の`benchmark_ret IS NULL`行対象に書き換え、
  同じ計算・同じbasis更新で冪等補填する。v1限定の旧経路は削除。

### `src/db.py`

- `fetch_outcomes_missing_benchmark()`: 対象を
  `contract_version = EXECUTION_CONTRACT_VERSION`（v2）へ変更。
- `update_outcome_benchmark()`: `benchmark_basis`も同時に更新するよう引数追加。

## 縮退・冪等性

- DB無効・接続失敗・パネル欠損時の挙動は現行どおり（決済スキップまたは
  benchmark NULLで続行、日次処理は止めない）。
- refillは何度実行しても同じ結果（NULL行のみ対象、値は決定論）。
- デプロイ翌朝: 通常決済で新規行が埋まり、同ランの`--refill-benchmark`で
  2026-05-07以降の既存v2行が一括補填される。

## テスト

`tests/test_settle_outcomes.py`（プレーンPythonスクリプト形式を維持）:

- 新`compute_benchmark_ret`: 正常系、entry open欠損、eval close欠損、非正値。
- 決済行の`benchmark_basis`が「計算成功→same-basisラベル / 失敗→unavailable」。
- 契約メタデータの`benchmark_basis`が新ラベル。
- refill対象がv2 NULL行であること（v1行を触らないこと）。
