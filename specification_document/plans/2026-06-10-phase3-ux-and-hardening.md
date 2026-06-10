# Phase 3: 手動トレードUX + 計測完成・運用堅牢化 実装計画

作成日: 2026-06-10 JST

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 0〜2 で構築した計測・品質・ポートフォリオ基盤の成果を、手動トレーダーが毎朝 1 分で意思決定できる形（実績ダッシュボード・日次ダイジェスト通知・堅牢な UI）に仕上げる。同時に、今回の検証で発見した運用バグ（公開ワークフローによる Phase 1/2 JSON の毎日削除）と計測の未完部分（TOPIX ベンチマーク、active mode 配線）を回収する。

**Architecture:** 純粋ロジック（ダイジェスト文面、実績集計、ベンチマーク計算）は `src/digest.py` / `src/performance.py` / `src/db_records.py` に置き standalone テスト。LINE 送信は `src/notifier.py` の共通 `send_line_text()`（リトライ付き）に集約。ダッシュボードは既存の「docs/ 配下 JSON + available=false で非表示」契約を維持して拡張する。

**Tech Stack:** Python 3.13 / uv、psycopg 3、Neon Postgres（既存）、pandas/pyarrow、Next.js 16 + React 19 + Recharts + TailwindCSS 4（既存）。テストは pytest 非依存の standalone runner（既存 `tests/` と同形式）。

**設計の正典:** `specification_document/improvement_roadmap.md` の Phase 3（§5）と W9。Phase 0/1/2 実装計画は `specification_document/plans/` の各ファイル。

---

## 0. Phase 0〜2 実装確認サマリ（2026-06-10 JST 検証）

### 0.1 確認できたこと

- **Phase 0/1/2 の計画成果物はすべて main に存在**し、計画との突合で欠落なし。
  - Phase 0: `src/db_records.py` / `src/db.py` / `migrations/0001` / `scripts/db_migrate.py` / `scripts/settle_outcomes.py` / outbox フォールバック / `PerformanceCard`
  - Phase 1: `src/labels.py`（TP/SL 優先順位・末尾 H 行除外OK）/ `src/calibration.py`（悪化時 fallback OK）/ `src/macro.py`（`merge_asof(direction="backward")` で未来参照なし）/ `src/model_store.py` / `src/phase1.py` / weekly retrain / drift check
  - Phase 2: `src/universe.py` / `src/cross_section.py`（日付内 `groupby("date").transform` のみ、リーク無し）/ `src/cs_model.py` / `src/portfolio.py` / `src/portfolio_backtest.py` / shadow 検証ツール（`scripts/portfolio_shadow_report.py`）
- **テスト全 16 スイート（約 230 件）pass**（2026-06-10 ローカル実行）。
- **DB 稼働中**（`pg_database_size` 7.74MB、storage_warning なし）。
- **Phase 2 shadow mode は本番で実稼働**: 2026-06-10 の daily で `cs-v1-20260610` により 6 銘柄のポートフォリオ snapshot（gross 0.239、expected_vol 0.091）が生成された（commit `55d1c1c` で確認）。
- enabled universe は 51 銘柄（クロスセクション成立条件 30 以上を満たす）。

### 0.2 発見した課題（本計画で回収する改善点）

| ID | 重大度 | 内容 | 該当箇所 | 回収 |
|---|---|---|---|---|
| **I1** | **P0** | **公開ワークフローが Phase 1/2 の JSON を毎日削除**。`rsync --delete` の exclude リストに `model_quality.json` / `drift_report.json` / `portfolio_latest.json` / `portfolio_backtest.json` / `cs_model_quality.json` が無く、daily commit（55d1c1c）の約 1 分後の Publish commit（2e63ff2）で全削除を確認。実在しない `portfolio_report.json` を除外している（typo と推定）。**結果として Phase 1/2 のダッシュボード機能は本番サイトで一度も表示されていない** | `.github/workflows/daily-publish-dashboard.yml:96-110` | ✅ 完了 (8c232f9) |
| **I2** | P1 | Phase 2 active mode で `signals.target_weight` を反映する配線が未実装（コードコメントで Task 10 へ明示的に deferred）。active 化は env 変更だけでは完結しない | `main.py` `_run_portfolio_snapshot` 付近 | Task 1 |
| **I3** | P1 | `signal_outcomes.benchmark_ret` / `excess_ret` が NULL のまま。Phase 1 で TOPIX 系列（`data/macro/macro_panel.parquet` の `topix` 列）は取得済みなのに settlement に未接続。roadmap Phase 3 の「資産曲線 vs TOPIX」の前提が欠ける | `scripts/settle_outcomes.py` | Task 2 |
| **I4** | P1 | `performance_summary.json` の export が settle **前**（main.py 内）に走るため、実績反映が常に 1 営業日遅れる | `daily-preopen-core.yml` のステップ順 / `src/dashboard.py` | Task 3 |
| **I5** | P2 | LINE 通知にリトライが無い（429/5xx/timeout で欠落）。`06_priority_matrix.md` P2 の未回収項目 | `src/notifier.py:70-81` / `scripts/curation_notify.py` | Task 4 |
| **I6** | P2 | チャートが空データで `Math.min(...[])` → `Infinity` になり描画が壊れる。P2 既知課題 | `web/src/components/StockChart.tsx:197-208` | Task 8 |
| **I7** | P2 | フロントに JSON ランタイム検証が無い（TypeScript 型のみ）。不正 JSON で UI が壊れうる | `web/src/**` の各 fetch | Task 8 |
| **I8** | low | `sector_exposure` に sector 欠損由来の `"null"` キーが出る（2026-06-10 snapshot で実発生、weight 0.032） | `src/portfolio.py:831-838` | Task 5 |
| **I9** | low | enabled 51 銘柄 > `settings.curation.max_universe: 50`。curation/universe ガードの上限と 1 件不整合 | `tickers.yml` | §0.3 運用 |
| **I10** | low | `AGENTS.md` / `README.md` の「frontend は `docs/history_data.json` を読む」記述が現状（`dashboard_index.json` + `tickers/*.json`）と不一致。Phase 0〜2 の成果物も未記載 | `AGENTS.md` ほか | Task 10 |
| **I11** | 観察 | shadow ポートフォリオで複数銘柄の `expected_ret` / `prob_up` が完全同値（score-bucket 較正の粗さ）、gross 0.239 と低め（高ボラ銘柄 + min_weight 切り捨ての複合）。バグではないが shadow 期間中の観察対象 | `src/cs_model.py` 較正 / `src/portfolio.py` | Task 9 |
| **I12** | 観察 | `macro_panel.parquet` の `jgb10y` が NaN（系列取得失敗が継続）。欠損許容設計どおり停止はしないが、レジーム特徴の一部が常時欠損 | `src/macro.py` 系列設定 | §0.3 運用 |

### 0.3 運用チェックリスト（コード変更不要、Phase 3 着手前後に確認）

- [ ] `scripts/backfill_state_signals.py` を本番 DB に対して実行済みか確認する（未実行なら 1 回実行。idempotent）。`performance_summary.json` の `n_long_signals` が 0 のままなのは、settle 順序（I4）と actionable シグナルの少なさ、または backfill 未実行の複合と推定される。
- [ ] 2026-06-13（土）の `weekly-model-retrain.yml` 初回実走で `per-ticker-v1-*` artifact と `active_model.json` が commit されることを確認する（成功すると翌営業日から日次が Phase 1 推論に切り替わり、`model_quality.json` が available になる）。
- [ ] `tickers.yml` の enabled 51 銘柄が次回 curation/universe 実行で 50 以下に収束するか確認する（I9）。収束しない場合は `scripts/universe_select.py --target-size 50 --apply` で是正。
- [ ] `src/macro.py` の JGB10y シンボル設定を見直す（I12。取得不能なら系列を無効化してログを静かにする選択も可）。

---

## 1. Phase 3 の方針

### 実装範囲

roadmap Phase 3（実績ダッシュボード / 通知強化 W9 / チャート堅牢化）に加え、その前提となる残件を含める。

1. 公開ワークフロー修正と再発防止テスト（I1）— ✅ **完了（2026-06-10, commit 8c232f9）**。
2. Phase 2 active mode の完了（I2）と shadow→active 判定の運用整備。
3. 計測の完成: TOPIX ベンチマーク決済（I3）と settle 後 export（I4）。
4. 通知強化: LINE リトライ（I5）、日次ポートフォリオ・ダイジェスト、週次パフォーマンスサマリ。
5. 実績ダッシュボード: 資産曲線 vs TOPIX、ドローダウン、信頼性（較正）、個別シグナル結果履歴。
6. UI 堅牢化: チャート空データガード（I6）、JSON ランタイム検証（I7）。

### 非スコープ（Phase 4 以降へ）

- 約定（fills）記録と提案 vs 実約定の乖離計測。
- 証券会社 CSV / API への発注指示出力、自動執行。
- ダッシュボードの認証・ユーザー管理。
- DB の長期アーカイブ自動化（400MB 警告は既存のまま運用）。

### 互換性ルール

- `docs/state.json` / `docs/dashboard_index.json` / `docs/performance_summary.json` / `docs/portfolio_latest.json` の既存スキーマは壊さない。新情報は**新ファイル**（`performance_detail.json` 等）または後方互換なキー追加で出す。
- 通知の失敗は daily パイプラインを止めない（リトライ後も失敗なら print して継続）。
- web の新カード/新ページは data unavailable で非表示・縮退表示にし、JSON 欠損で壊れない。
- shadow mode の挙動は Task 1 実装後も**一切変えない**（active 配線は `TRADER_PORTFOLIO_MODE=active` かつ KPI ゲート通過時のみ作用）。

### active mode の設計判断（Phase 2 計画 §4 からの意図的変更）

Phase 2 計画は active mode で `signals.action` 自体を portfolio 由来（weight>0 → BUY 等）に書き換えるとしていたが、本計画では **v1 として `target_weight` の反映と理由追記に留め、`action` はモデル由来のまま**とする。理由: (a) 手動トレードの実行指示は「今日の建玉」（digest + dashboard）が担うため action の意味を変える必然性が薄い、(b) 個別通知・履歴・実績台帳の action の意味が途中で変わると Phase 0 計測の連続性が壊れる、(c) 後から portfolio 駆動 action へ進める変更は容易（逆は困難）。action の portfolio 駆動化は active 運用が安定した後に再判断する。

---

## 2. 追加/変更ファイル

| ファイル | 区分 | 責務 |
|---|---|---|
| `.github/workflows/daily-publish-dashboard.yml` | 変更 | ✅ 完了 — rsync exclude に Phase 1/2/3 JSON を追加、`portfolio_report.json` を削除 |
| `tests/test_publish_workflow.py` | 新規 | ✅ 完了 — docs/ 直下に書く JSON が publish の exclude に揃っているかの再発防止ガード |
| `src/portfolio.py` | 変更 | sector 欠損を「その他」へ正規化（I8）、`merge_target_weights()` 追加 |
| `main.py` | 変更 | 通知の後段化、active mode の target_weight 反映、digest 送信 |
| `scripts/portfolio_shadow_report.py` | 変更 | `active_ready`（bool）と判定根拠を report に追加 |
| `src/db_records.py` | 変更 | `compute_benchmark_ret()` 追加 |
| `scripts/settle_outcomes.py` | 変更 | TOPIX ベンチマーク埋め、`--refill-benchmark`、settle 後の summary/detail 再 export |
| `src/db.py` | 変更 | `fetch_outcome_detail_rows()` / `fetch_outcomes_missing_benchmark()` / `update_outcome_benchmark()` 追加 |
| `src/performance.py` | 新規 | **純粋ロジック**: 資産曲線(戦略/TOPIX)・ドローダウン・rolling 指標・信頼性ビン・直近結果テーブルの組み立て |
| `src/dashboard.py` | 変更 | `export_performance_detail()` / `export_signal_outcomes_recent()` 追加 |
| `src/notifier.py` | 変更 | `send_line_text()`（リトライ付き共通送信）へ集約、`_should_retry()` / `_backoff_seconds()` |
| `scripts/curation_notify.py` | 変更 | 送信を `notifier.send_line_text()` 経由に変更 |
| `src/digest.py` | 新規 | **純粋ロジック**: 日次ダイジェスト/週次サマリの文面組み立て |
| `scripts/weekly_performance_notify.py` | 新規 | 週次実績サマリを DB から集計して LINE 送信 |
| `.github/workflows/weekly-fundamental-report.yml` | 変更 | 週次実績サマリ通知ステップ追加 |
| `.github/workflows/daily-preopen-core.yml` | 変更 | Phase 3 env 追加（digest/retry） |
| `web/src/lib/fetchJson.ts` | 新規 | fetch + ランタイム型ガード共通化 |
| `web/src/components/StockChart.tsx` | 変更 | 空データガード（I6） |
| `web/src/components/PerformanceCard.tsx` | 変更 | `/performance` への導線追加、fetchJson 化 |
| `web/src/components/PortfolioCard.tsx` | 変更 | セクター露出・警告表示、fetchJson 化 |
| `web/src/app/performance/page.tsx` | 新規 | 実績ページ（資産曲線 vs TOPIX / DD / 信頼性 / 結果履歴） |
| `web/src/components/PerformanceDetail.tsx` | 新規 | 実績ページの client コンポーネント |
| `web/src/types/index.ts` | 変更 | `PerformanceDetail` / `SignalOutcomeRow` 型追加 |
| `tests/test_digest.py` | 新規 | digest 文面の standalone テスト |
| `tests/test_performance.py` | 新規 | performance 集計の standalone テスト |
| `tests/test_notifier_retry.py` | 新規 | リトライ判定/バックオフの standalone テスト |
| `tests/test_db_records.py` | 変更 | `compute_benchmark_ret` のテスト追加 |
| `.env.example` | 変更 | Phase 3 env 追記 |
| `AGENTS.md` / `README.md` | 変更 | データ契約の現行化（I10）、Phase 3 成果物の記載 |

---

## 3. 環境変数

`.env.example` に追加する。

```bash
# --- Phase 3: 通知・実績UX ---
# LINE 送信リトライ（429/5xx/接続エラーのみ対象。4xx は即時失敗）
TRADER_NOTIFY_RETRY_MAX=3
TRADER_NOTIFY_RETRY_BASE_SEC=1.0
# 日次ダイジェスト（1通で建玉+差分+レジーム+直近実績）
TRADER_NOTIFY_DIGEST_ENABLED=true
# 個別シグナル通知（LINE 無料枠 200通/月 対策で将来 false 運用を想定）
TRADER_NOTIFY_PER_TICKER_ENABLED=true
# 実績詳細 export の対象期間と信頼性ビン数
TRADER_PERF_HISTORY_DAYS=180
TRADER_PERF_RELIABILITY_BINS=10
```

> **LINE 無料枠の注意:** 無料プランの push は月 200 通。51 銘柄体制で actionable シグナルが増えると個別通知だけで枠を圧迫しうる。digest 導入後は `TRADER_NOTIFY_PER_TICKER_ENABLED=false`（digest のみ運用）への切替を推奨選択肢として残す。

---

## 4. データ契約

### `docs/performance_detail.json`（新規）

```json
{
  "available": true,
  "generated_at": "2026-06-24 06:20:00",
  "as_of": "2026-06-24",
  "horizon_days": 5,
  "history_days": 180,
  "equity_curve": [
    {"date": "2026-06-10", "strategy": 1.004, "benchmark": 1.002, "n": 3}
  ],
  "drawdown_curve": [
    {"date": "2026-06-10", "drawdown": -0.012}
  ],
  "rolling": {
    "hit_rate_20d": 0.58,
    "avg_return_20d": 0.004,
    "excess_return_20d": 0.002,
    "sharpe_60d": 0.85
  },
  "reliability": {
    "brier": 0.24,
    "bins": [
      {"bin_low": 0.5, "bin_high": 0.6, "mean_prob": 0.55, "frac_up": 0.52, "count": 18}
    ]
  }
}
```

DB 不通・サンプル不足時は `{"available": false, "reason": "...", "generated_at": "..."}`。
`equity_curve.benchmark` は TOPIX 同期間複利（benchmark_ret 欠損日は前日値キャリー）。

### `docs/signal_outcomes_recent.json`（新規）

```json
{
  "available": true,
  "generated_at": "2026-06-24 06:20:00",
  "rows": [
    {
      "entry_date": "2026-06-16", "ticker": "7011.JP", "name": "三菱重工業",
      "action": "MILD_BUY", "conviction": 0.66, "horizon_days": 5,
      "realized_ret": 0.021, "benchmark_ret": 0.008, "excess_ret": 0.013,
      "hit": true, "mae": -0.011, "mfe": 0.034, "exit_reason": "time"
    }
  ]
}
```

直近 `TRADER_PERF_HISTORY_DAYS` 日・horizon=5 を新しい順、最大 200 行。

### 日次ダイジェスト（LINE テキスト、`src/digest.py` が生成）

```text
📊 朝のダイジェスト (2026-06-24)
レジーム: 中立 / ドル円 160.4
──────────
🧺 今日の建玉 [shadow / cs-v1-20260620]
グロス 24% ・想定ボラ 9.1%
新規: ディスコ 3.6%
継続: 日産自 5.5% / 大林組 5.2% ほか3
手仕舞い: なし
──────────
📨 個別シグナル: 買い1 / やや買い2 / 売り0
🎯 直近実績(5日): 的中 58% (n=35) / 平均 +0.6%
詳細: https://fukasedaichi.github.io/trader/
```

- portfolio unavailable 時は建玉ブロックを「本日のポートフォリオ提案なし (理由)」に縮退。
- 実績未蓄積（n=0）時は実績行を「実績蓄積中」にする。
- active mode では `[active]` と表示し、建玉行に diff 種別（新規/増/減/手仕舞い）を必ず含める。

### 週次サマリ（LINE テキスト）

```text
📈 週間実績 (6/16〜6/20)
シグナル: 12件 (買い系9 / 売り系3)
的中率(5日): 61% / 平均 +0.8% / 対TOPIX +0.3%
ベスト: ディスコ +4.2% ・ワースト: 日産自 -2.1%
建玉回転: 新規3 / 手仕舞い2
レポート: https://github.com/FukaseDaichi/trader/blob/main/reports/weekly_2026-06-20.md
```

---

## 5. 実装タスク

### Task 0: 公開ワークフロー修正（I1）— ✅ 完了（2026-06-10 / commit 8c232f9）

`.github/workflows/daily-publish-dashboard.yml` の rsync `--delete` exclude に、毎日の Publish コミットで消えていた docs/ 直下の JSON を追加し、typo の `portfolio_report.json` を削除した。再発防止に `tests/test_publish_workflow.py` を新規追加。背景の詳細は §0.2 I1 を参照。

- 追加した exclude: `model_quality.json` / `drift_report.json` / `portfolio_latest.json` / `portfolio_backtest.json` / `cs_model_quality.json` / `portfolio_shadow_report.json` / `performance_detail.json` / `signal_outcomes_recent.json`（後 2 つは Task 3 実装前だが先行登録）。
- 検証: `uv run python tests/test_publish_workflow.py` → 3/3 pass、既存 16 スイート非回帰、workflow YAML パース OK。

**残（merge 後・運用確認）:** `workflow_dispatch` で `Daily Publish Dashboard` を `force_publish=true` 実行し、`docs/portfolio_latest.json` 等が残ること・`https://fukasedaichi.github.io/trader/portfolio_latest.json` が 200 を返すことを確認。

---

### Task 1: Phase 2 active mode の完了（I2）+ 通知の後段化

**目的:** `TRADER_PORTFOLIO_MODE=active` を「env 変更だけで完結する」状態にする。同時に、通知を per-ticker ループから後段へ移し、Task 5（digest）の土台を作る。

**Files:**
- Modify: `src/portfolio.py`（`merge_target_weights` 追加）
- Modify: `main.py`
- Modify: `scripts/portfolio_shadow_report.py`（`active_ready`）
- Test: `tests/test_portfolio.py`（追記）

- [ ] **Step 1: 純粋ロジック `merge_target_weights()` をテストファースト**

`tests/test_portfolio.py` に追加するテスト（要旨）:

```python
def test_merge_target_weights_active_ok():
    signals = [
        {"ticker": "7011.JP", "action": "BUY", "reason": "r1"},
        {"ticker": "9999.JP", "action": "HOLD", "reason": "r2"},
    ]
    snapshot = {"status": "ok", "mode": "active",
                "positions": [{"ticker": "7011.JP", "target_weight": 0.18}]}
    out = portfolio.merge_target_weights(signals, snapshot, gate_passed=True)
    assert out[0]["target_weight"] == 0.18
    assert "建玉" in out[0]["reason"]          # 理由に建玉情報を追記
    assert out[1]["target_weight"] == 0.0      # 建玉外は明示的に 0
    assert out[1]["action"] == "HOLD"          # action は変更しない


def test_merge_target_weights_noop_on_shadow_or_gate_fail():
    signals = [{"ticker": "7011.JP", "action": "BUY", "reason": "r"}]
    shadow = {"status": "ok", "mode": "shadow", "positions": [
        {"ticker": "7011.JP", "target_weight": 0.18}]}
    assert "target_weight" not in portfolio.merge_target_weights(
        signals, shadow, gate_passed=True)[0]
    active = {**shadow, "mode": "active"}
    assert "target_weight" not in portfolio.merge_target_weights(
        signals, active, gate_passed=False)[0]
```

実装は `src/portfolio.py` に追加: `merge_target_weights(signals, snapshot, gate_passed)` — `mode=="active"` かつ `status=="ok"` かつ `gate_passed` のときのみ、各 signal に `target_weight`（建玉外は 0.0）を付与し、`reason` 末尾へ `／建玉 18% (rank 1)` 形式を追記して**新しいリストを返す**。それ以外は入力をそのまま返す（shadow 完全無変更の保証）。

あわせて `src/db_records.py:122` の `signal_to_signal_row()` が `target_weight` を `None` 固定にしている点を `"target_weight": _as_float(signal.get("target_weight"))` に変更する（現状のままでは merge した値が DB に届かない）。`tests/test_db_records.py` の `test_signal_row_maps_core_fields` は「キー無し→None」を維持しつつ、`target_weight: 0.18` 入りの signal が透過することを確認するテストを追加する。

- [ ] **Step 2: KPI ゲート判定の取得**

active 反映可否の `gate_passed` は `docs/portfolio_backtest.json`（weekly が出力、`gate.passed` キー）から読む薄いヘルパ `portfolio.read_portfolio_gate(path=DOCS/"portfolio_backtest.json") -> bool` を追加。ファイル欠損・available=false は False。

- [ ] **Step 3: main.py の通知後段化と active 配線**

`main.py` を次の順序に再構成する:

1. per-ticker ループ（`_process_ticker` から **通知呼び出しを除去**。signal 生成と backtest entry のみ）
2. Phase 2 inference + `_run_portfolio_snapshot`（既存。snapshot を戻り値で受ける）
3. `signals = portfolio.merge_target_weights(signals, snapshot, gate_passed=portfolio.read_portfolio_gate())`（snapshot が None なら no-op）
4. **通知ブロック**: `TRADER_NOTIFY_PER_TICKER_ENABLED` のとき従来どおり gate_passed かつ非 HOLD の signal を `send_notification()`。続けて Task 5 の digest（実装前は skip）
5. `db.record_run(signals, run_date)`（target_weight が DB の `signals.target_weight` に乗る — `signal_to_signal_row` は既存実装で `target_weight` を拾う）
6. `update_dashboard(signals)` / `write_backtest_report(...)`

通知・phase2・DB は従来どおり try/except で隔離し、どれが失敗しても他を止めない。

- [ ] **Step 4: shadow 非回帰の確認**

Run: `RUN_DATE_JST=2026-06-10 TRADER_DB_ENABLED=false TRADER_PORTFOLIO_ENABLED=true TRADER_PORTFOLIO_MODE=shadow uv run python main.py`
Expected: 完走し、`docs/state.json` の各 signal に `target_weight` が**含まれない**こと（shadow 無変更）。LINE 未設定なら通知 skip ログ。

- [ ] **Step 5: `portfolio_shadow_report.py` に active 化判定を追加**

report JSON に以下を追加:

```json
"active_readiness": {
  "active_ready": false,
  "shadow_days": 4,
  "min_shadow_days": 10,
  "portfolio_gate_passed": true,
  "cs_ic_vs_phase1": 0.012,
  "reasons": ["shadow_days 4 < 10"]
}
```

判定（決定論）: `shadow_days >= 10` かつ `portfolio_gate_passed` かつ「CS の daily IC ≥ Phase 1 比 −0.005」。**active への切替自体は従来どおり人間が env を変更する**（roadmap 原則 3）。

- [ ] **Step 6: Commit**

```bash
git add src/portfolio.py main.py scripts/portfolio_shadow_report.py tests/test_portfolio.py
git commit -m "feat(phase3): wire active-mode target weights + post-loop notification"
```

**Acceptance:**
- shadow では signals/通知/JSON が現状と完全一致。
- `TRADER_PORTFOLIO_MODE=active` かつゲート通過時のみ `signals.target_weight` が DB/state に入る。
- `portfolio_shadow_report.json` に `active_readiness` が出る。

---

### Task 2: TOPIX ベンチマーク決済（I3）

**Files:**
- Modify: `src/db_records.py`（純粋関数）
- Modify: `src/db.py` / `scripts/settle_outcomes.py`
- Test: `tests/test_db_records.py`

- [ ] **Step 1: 純粋関数のテストを追加**

```python
def test_benchmark_ret_basic():
    topix = {"2026-06-09": 2900.0, "2026-06-16": 2958.0}
    r = compute_benchmark_ret(topix, "2026-06-09", "2026-06-16")
    assert abs(r - 0.02) < 1e-9

def test_benchmark_ret_missing_date_is_none():
    assert compute_benchmark_ret({"2026-06-09": 2900.0}, "2026-06-09", "2026-06-16") is None
    assert compute_benchmark_ret({}, "2026-06-09", "2026-06-16") is None
```

- [ ] **Step 2: 実装**

`src/db_records.py`:

```python
def compute_benchmark_ret(benchmark_by_date: dict, entry_date: str,
                          eval_date: str) -> float | None:
    """TOPIX close-to-close return over the holding window; None when either
    date is missing from the series (settlement keeps going with NULL)."""
    entry = benchmark_by_date.get(str(entry_date))
    exit_ = benchmark_by_date.get(str(eval_date))
    if not entry or not exit_:
        return None
    return float(exit_) / float(entry) - 1.0
```

- [ ] **Step 3: settle への接続**

`scripts/settle_outcomes.py`:
- 開始時に `data/macro/macro_panel.parquet` から `{date(str): topix}` 辞書を 1 回構築（ファイル欠損・列欠損は空辞書で続行）。
- 各 outcome upsert 前に `benchmark_ret = compute_benchmark_ret(...)`、`excess_ret = realized_ret - benchmark_ret`（どちらか None なら両方 None）。
- `--refill-benchmark` フラグ追加: `db.fetch_outcomes_missing_benchmark(conn)`（`benchmark_ret IS NULL` の settled 行を signal の entry/eval 日付つきで取得）→ 計算できた行だけ `db.update_outcome_benchmark(conn, signal_id, horizon, benchmark_ret, excess_ret)`。再実行 idempotent。

- [ ] **Step 4: workflow（任意・1回）**

merge 後に手動で `uv run python scripts/settle_outcomes.py --as-of 2026-06-XX --refill-benchmark` を 1 回実行し既存 NULL を埋める。

**Acceptance:**
- 新規 settle 行に `benchmark_ret` / `excess_ret` が入る（TOPIX 欠損日は NULL のまま完走）。
- `--refill-benchmark` が idempotent。
- 既存テスト含め `tests/test_db_records.py` 全 pass。

---

### Task 3: 実績エクスポート再構成（I4 + 実績ダッシュボードのデータ層）

**Files:**
- Create: `src/performance.py` / Test: `tests/test_performance.py`
- Modify: `src/db.py`（`fetch_outcome_detail_rows`）
- Modify: `src/dashboard.py`（`export_performance_detail` / `export_signal_outcomes_recent`）
- Modify: `scripts/settle_outcomes.py`（settle 後に summary + detail を再 export）

- [ ] **Step 1: `src/performance.py`（純粋）をテストファーストで実装**

入力は dict 行のリスト（DB 非依存）。提供関数:

```python
build_equity_curves(rows, horizon=1)      # -> [{date, strategy, benchmark, n}]
build_drawdown(curve)                     # -> [{date, drawdown}]
rolling_metrics(rows, window=20)          # -> {hit_rate_20d, avg_return_20d, excess_return_20d,
                                          #     sharpe_60d}  (sharpe は日次平均/標準偏差×√252、60日窓)
build_reliability(pred_rows, n_bins=10)   # -> {brier, bins:[...]} (src/calibration.py の brier_score/reliability_bins を再利用)
build_recent_outcomes(rows, limit=200)    # -> signal_outcomes_recent.json の rows
build_performance_detail(rows, pred_rows, horizon, history_days, n_bins)
```

テスト要点: 戦略/ベンチ曲線が同じ日付軸で揃う、benchmark 欠損日はキャリー、空入力で `available:false` 相当の空構造、drawdown が高値からの下落率で 0 以下。

- [ ] **Step 2: DB 取得**

`src/db.py` に `fetch_outcome_detail_rows(conn, horizon_days=5, history_days=180)` を追加（signals × signal_outcomes join、`entry_date/ticker/action/conviction/realized_ret/benchmark_ret/excess_ret/hit/mae/mfe/exit_reason`。ticker 名は `tickers.name` join）。信頼性は既存 `fetch_prediction_outcomes(conn, model_version, horizon_days)` を再利用。

- [ ] **Step 3: export と呼び出し順の修正（I4）**

- `src/dashboard.py` に `export_performance_detail()` / `export_signal_outcomes_recent()` を追加（performance_summary と同じ best-effort/縮退規約）。
- `scripts/settle_outcomes.py` の決済完了後（DB 有効時のみ）に `dashboard.export_performance_summary()` → `export_performance_detail()` → `export_signal_outcomes_recent()` を呼ぶ。これで **settle 当日の結果が同日 commit に反映**される（main.py 側の export は従来どおり残してよい — 後勝ちで settle 後の値が残る）。

**Acceptance:**
- daily 実行後、`performance_summary.json` が settle 後の値になる（翌日遅れの解消）。
- `docs/performance_detail.json` / `docs/signal_outcomes_recent.json` が出力され、DB 不通時は available:false。
- `uv run python tests/test_performance.py` pass。

---

### Task 4: LINE リトライ（I5）

**Files:**
- Modify: `src/notifier.py` / `scripts/curation_notify.py`
- Test: `tests/test_notifier_retry.py`

- [ ] **Step 1: リトライ判定の純粋部分をテストファーストで**

```python
def test_should_retry_on_429_and_5xx():
    assert notifier._should_retry(429) is True
    assert notifier._should_retry(503) is True
    assert notifier._should_retry(None) is True   # timeout/connection error
    assert notifier._should_retry(400) is False   # bad request: give up

def test_backoff_grows():
    assert notifier._backoff_seconds(1, base=1.0) == 1.0
    assert notifier._backoff_seconds(2, base=1.0) == 4.0
```

- [ ] **Step 2: `send_line_text()` 実装**

`src/notifier.py` に追加（既存 `send_notification` は文面組み立て後にこれを呼ぶ形へ縮小）:

```python
import os, time

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

def _should_retry(status: int | None) -> bool:
    return status is None or status in RETRYABLE_STATUS

def _backoff_seconds(attempt: int, base: float) -> float:
    return base * (4 ** (attempt - 1))          # 1s, 4s, 16s...

def send_line_text(text: str, *, sleep_fn=time.sleep) -> bool:
    """Push one text message with bounded retry. Returns success bool.
    Never raises — daily pipeline must not die on notification failure."""
    token = LINE_CONFIG["channel_access_token"]
    user_id = LINE_CONFIG["user_id"]
    if not token or not user_id:
        print("LINE configuration missing. Skipping notification.")
        return False
    max_attempts = int(os.environ.get("TRADER_NOTIFY_RETRY_MAX", "3") or 3)
    base = float(os.environ.get("TRADER_NOTIFY_RETRY_BASE_SEC", "1.0") or 1.0)
    configuration = Configuration(access_token=token)
    for attempt in range(1, max_attempts + 1):
        try:
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).push_message(PushMessageRequest(
                    to=user_id, messages=[TextMessage(text=text)]))
            return True
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status", None)
            retry = _should_retry(status) and attempt < max_attempts
            print(f"LINE push failed (attempt {attempt}/{max_attempts}, "
                  f"status={status}): {exc}" + (" — retrying" if retry else ""))
            if not retry:
                return False
            sleep_fn(_backoff_seconds(attempt, base))
    return False
```

- [ ] **Step 3: `scripts/curation_notify.py` の送信部を `notifier.send_line_text(text)` に差し替え**（文面組み立て・persona は現状維持）。

**Acceptance:**
- 429/5xx/接続断で最大 3 回試行、4xx は即時失敗、最終失敗でも例外を投げない。
- `uv run python tests/test_notifier_retry.py` pass。週次通知もリトライ対象になる。

---

### Task 5: 日次ポートフォリオ・ダイジェスト（W9）

**Files:**
- Create: `src/digest.py` / Test: `tests/test_digest.py`
- Modify: `main.py` / `src/portfolio.py`（I8）/ `.github/workflows/daily-preopen-core.yml`

- [ ] **Step 1: I8 修正** — `src/portfolio.py` のセクター集計（831 行付近）で `sec = pos.get("sector") or "その他"` に正規化。`tests/test_portfolio.py` に sector=None ポジションで `"その他"` に集計されるテストを追加。

- [ ] **Step 2: `src/digest.py`（純粋）をテストファーストで実装**

```python
build_daily_digest(run_date, portfolio_latest, performance_summary,
                   macro_regime, signals, dashboard_url) -> str
```

§4 の文面契約どおり。テスト要点: (a) portfolio available 時に建玉行・diff・gross が入る、(b) unavailable 時に縮退文言、(c) 実績 n=0 で「実績蓄積中」、(d) 個別シグナル集計（買い/やや買い/売り件数）が一致、(e) active mode で `[active]` 表示。`macro_regime` は `docs/curation/macro_latest.json` の `market_bias` と `macro_panel` 最新行の `usdjpy` から組み立てる薄い入力 dict とする。

- [ ] **Step 3: main.py へ接続**

Task 1 の通知ブロック内（per-ticker 通知の後）に:

```python
if _env_bool("TRADER_NOTIFY_DIGEST_ENABLED", True):
    try:
        text = digest.build_daily_digest(run_date, portfolio_latest_payload,
                                         performance_payload, macro_regime,
                                         signals, DASHBOARD_URL)
        notifier.send_line_text(text)
    except Exception as e:
        print(f"Digest notification failed (ignored): {type(e).__name__}: {e}")
```

`portfolio_latest_payload` は snapshot（無ければ `docs/portfolio_latest.json` 読み）、`performance_payload` は `docs/performance_summary.json` 読み。ファイル欠損は None で縮退。

- [ ] **Step 4: workflow へ env 追加** — `daily-preopen-core.yml` の `Run prediction script` env に `TRADER_NOTIFY_DIGEST_ENABLED: "true"`, `TRADER_NOTIFY_PER_TICKER_ENABLED: "true"`, `TRADER_NOTIFY_RETRY_MAX: "3"` を追加。

**Acceptance:**
- 毎朝 1 通のダイジェストが届く（portfolio unavailable でも縮退して届く）。
- digest 失敗が daily を止めない。`tests/test_digest.py` pass。

---

### Task 6: 週次パフォーマンスサマリ通知（W9）

**Files:**
- Create: `scripts/weekly_performance_notify.py`
- Modify: `src/digest.py`（`build_weekly_summary`）/ `.github/workflows/weekly-fundamental-report.yml`

- [ ] `src/digest.py` に `build_weekly_summary(rows, week_start, week_end, report_url) -> str | None` を追加（§4 契約。rows は `fetch_outcome_detail_rows` の出力。actionable 0 件なら None=送信しない）。テストを `tests/test_digest.py` に追加。
- [ ] `scripts/weekly_performance_notify.py`: DB から直近 7 日分 outcome を取得 → 文面生成 → `notifier.send_line_text()`。DB 不通・データ無しは exit 0 no-op。
- [ ] `weekly-fundamental-report.yml` の LINE 通知ステップの後に `continue-on-error: true` で追加（env: `DATABASE_URL`, `TRADER_DB_ENABLED`, LINE secrets）。

**Acceptance:** 土曜の週次 workflow 後、実績があれば週間サマリが 1 通届く。実績ゼロ・DB 不通では送信されず workflow も落ちない。

---

### Task 7: ダッシュボード実績ページ

**Files:**
- Create: `web/src/app/performance/page.tsx` / `web/src/components/PerformanceDetail.tsx`
- Modify: `web/src/types/index.ts` / `web/src/components/PerformanceCard.tsx`

- [ ] `types/index.ts` に `PerformanceDetail` / `SignalOutcomeRow`（§4 契約どおり）を追加。
- [ ] `PerformanceDetail.tsx`（client component）: `fetchJson` で `performance_detail.json` と `signal_outcomes_recent.json` を取得し、
  - 資産曲線: Recharts `LineChart`（strategy=red, benchmark=slate。日本株色規約: 上昇系は赤）
  - ドローダウン: `AreaChart`（blue）
  - 信頼性: `mean_prob` vs `frac_up` の `BarChart` + 対角参照線、Brier 表示
  - 結果履歴: テーブル（日付/銘柄/action/conviction/実現/超過/hit/MAE/MFE）。hit は赤/青バッジ
  - すべて `available:false` または行ゼロでセクション単位の「データ蓄積中」表示
- [ ] `app/performance/page.tsx`: ヘッダ + `<PerformanceDetail />`。`PerformanceCard` に `Link href="/performance"`（「詳細 →」）を追加。
- [ ] **レジーム・バナーとレポート導線**（roadmap Phase 3 §1 の未実装解消）: `page.tsx` ヘッダ直下に小バナーを追加。`{basePath}/curation/macro_latest.json` を `fetchJson` で読み、`market_bias`（risk_on=赤 / neutral=slate / risk_off=青）と `as_of` を表示。バナー右端に「週次レポート」（`https://github.com/FukaseDaichi/trader/tree/main/reports`）と「キュレーション決定ログ」（`{basePath}/curation/decision_latest.json`）へのリンクを置く。JSON 欠損時はバナーごと非表示。
- [ ] Run: `npm run lint --prefix web && npm run build:prod --prefix web` Expected: PASS（静的 export に `/performance` が含まれる）。

**Acceptance:** 実績ページで資産曲線 vs TOPIX・DD・較正・結果履歴が見える。データ未蓄積でも壊れない。lint/build pass。

---

### Task 8: チャート堅牢化（I6）+ JSON ランタイム検証（I7）

**Files:**
- Create: `web/src/lib/fetchJson.ts`
- Modify: `web/src/components/StockChart.tsx` / `PerformanceCard.tsx` / `ModelQualityCard.tsx` / `PortfolioCard.tsx`

- [ ] **Step 1: StockChart 空データガード**

`web/src/components/StockChart.tsx` の min/max 計算（197 行付近）を置換:

```tsx
  const { minPrice, maxPrice, hasPriceData } = useMemo(() => {
    const prices: number[] = [];
    filteredData.forEach((d) => {
      if (d.high != null) prices.push(d.high);
      if (d.low != null) prices.push(d.low);
      if (d.close != null) prices.push(d.close);
    });
    if (prices.length === 0) {
      return { minPrice: 0, maxPrice: 1, hasPriceData: false };
    }
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const margin = (max - min) * 0.05;
    return { minPrice: min - margin, maxPrice: max + margin, hasPriceData: true };
  }, [filteredData]);
```

そして return 直前に:

```tsx
  if (!hasPriceData) {
    return (
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-8 text-center">
        <p className="text-slate-200 font-semibold">{tickerName}</p>
        <p className="text-slate-400 text-sm mt-2">価格データがありません</p>
      </div>
    );
  }
```

- [ ] **Step 2: `web/src/lib/fetchJson.ts`**

```ts
export async function fetchJson<T>(
  path: string,
  isValid: (v: unknown) => v is T,
): Promise<T | null> {
  try {
    const res = await fetch(path);
    if (!res.ok) return null;
    const data: unknown = await res.json();
    return isValid(data) ? data : null;
  } catch {
    return null;
  }
}

export function isAvailablePayload(v: unknown): v is { available: boolean } {
  return typeof v === "object" && v !== null &&
    typeof (v as { available?: unknown }).available === "boolean";
}
```

- [ ] **Step 3:** 4 カード（Performance/ModelQuality/Portfolio/PerformanceDetail）の fetch を `fetchJson(path, isAvailablePayload)` ベースに置換（不正 JSON → null → 非表示）。`page.tsx` の `dashboard_index.json` fetch も `res.ok` チェックを確認。
- [ ] Run: `npm run lint --prefix web && npm run build:prod --prefix web` Expected: PASS。

**Acceptance:** 空配列データの銘柄でチャートがプレースホルダ表示になる。壊れた JSON でもカードが安全に消えるだけで UI 全体は描画される。

---

### Task 9: shadow 検証と active 化の運用（Phase 2 Task 10 の接続）

コード変更は Task 1 で完了済み。ここは運用ゲートの明文化。

- [ ] shadow 開始は 2026-06-10。**10 営業日経過（目安 2026-06-24）以降**に `docs/portfolio_shadow_report.json` の `active_readiness` を確認する。
- [ ] 確認項目: `active_ready: true` / `portfolio_gate_passed: true` / CS daily IC ≥ Phase 1 比 −0.005 / 観察項目 I11（expected_ret の bucket 同値・gross 低位）が意思決定を阻害しないか。
- [ ] 満たしたら `.github/workflows/daily-preopen-core.yml` の `TRADER_PORTFOLIO_MODE` を `"active"` へ変更（1 行 PR）。ロールバックは同じ 1 行を `"shadow"` に戻すだけ。
- [ ] active 化後 1 週間は digest の建玉と DB `signals.target_weight` の一致を毎朝確認。

**Acceptance:** active 化が「report 確認 + env 1 行変更」で完結し、判断根拠が report に記録されている。

---

### Task 10: ドキュメント・設定の現行化（I10）

- [ ] `AGENTS.md`: 「frontend は `docs/history_data.json` を読む」記述を「`docs/dashboard_index.json` + `docs/tickers/*.json`（実績系は `performance_*.json` / `portfolio_*.json`）」へ修正。Phase 0〜3 の成果物（DB 計測、週次学習、CS ポートフォリオ shadow、digest 通知）を Architecture 節へ 1 段落追記。
- [ ] `README.md`: 実績ダッシュボード（/performance）とダイジェスト通知を機能一覧へ追記。
- [ ] `.env.example`: §3 の env 追記（Task 4/5 で実施済みなら確認のみ）。
- [ ] Run: `uv run python tests/test_publish_workflow.py`（ドキュメント変更で壊れないことの確認を兼ねる）。

---

## 6. Verification

DB なしローカルで通すもの:

```bash
uv run python tests/test_publish_workflow.py
uv run python tests/test_notifier_retry.py
uv run python tests/test_digest.py
uv run python tests/test_performance.py
uv run python tests/test_db_records.py
uv run python tests/test_portfolio.py
uv run python tests/test_portfolio_shadow.py
# 既存スイートの非回帰
for f in tests/test_*.py; do uv run python "$f"; done
uv run python -c "import yaml; [yaml.safe_load(open(p)) for p in ['.github/workflows/daily-publish-dashboard.yml','.github/workflows/daily-preopen-core.yml','.github/workflows/weekly-fundamental-report.yml']]; print('workflow YAML OK')"
TRADER_DB_ENABLED=false RUN_DATE_JST=2026-06-10 TRADER_PORTFOLIO_ENABLED=true TRADER_PORTFOLIO_MODE=shadow uv run python main.py
TRADER_DB_ENABLED=false uv run python scripts/settle_outcomes.py --as-of 2026-06-10
TRADER_DB_ENABLED=false uv run python scripts/weekly_performance_notify.py
npm run lint --prefix web
npm run build:prod --prefix web
```

DB あり（staging/production 相当）で通すもの:

```bash
uv run python scripts/settle_outcomes.py --as-of <today> --refill-benchmark
RUN_DATE_JST=<today> uv run python main.py     # digest が 1 通届く
uv run python scripts/weekly_performance_notify.py
# performance_detail.json の equity_curve に benchmark が入ることを確認
```

---

## 7. Acceptance Criteria（Phase 3 全体）

- Publish 後も `model_quality.json` / `portfolio_latest.json` 等が本番サイトで 200 を返す（I1 解消）。
- ダッシュボードで戦略 vs TOPIX の資産曲線・ドローダウン・較正・個別結果履歴が確認できる（roadmap Phase 3 §1）。
- 毎朝 1 通のダイジェスト（建玉 + 差分 + レジーム + 直近実績）が届き、LINE 障害時はリトライされ、最終失敗でも daily は完走する（W9）。
- 週次実績サマリが届く。
- 空データ銘柄でチャートが壊れず、不正 JSON でカードが安全に非表示になる（P2 回収）。
- `signal_outcomes` に TOPIX 超過リターンが蓄積され、settle 当日の実績がその日の JSON に反映される。
- `TRADER_PORTFOLIO_MODE=active` への切替が env 1 行で完結し、shadow の挙動は本計画適用後も不変。
- `docs/state.json` / `docs/dashboard_index.json` の既存契約が壊れない。

---

## 8. Rollback

- 公開 workflow 修正（Task 0）は exclude 追加のみで、戻す理由が生じない（戻すと I1 再発）。
- ダイジェスト/週次通知: `TRADER_NOTIFY_DIGEST_ENABLED=false` / workflow ステップの `continue-on-error` で即無効化。
- 個別通知の戻し: `TRADER_NOTIFY_PER_TICKER_ENABLED=true`（デフォルト）。
- active mode: `TRADER_PORTFOLIO_MODE=shadow` に戻すだけ（Task 1 は shadow 無変更を保証）。
- 実績ページ/カード: JSON が unavailable なら自動で非表示。export を止めれば UI も消える。
- ベンチマーク決済: 失敗時も NULL で継続するため、`macro_panel.parquet` を外せば実質無効化。

---

## 9. Phase 4 へ送るもの

- **fills（約定）記録**: 手動約定の入力経路（リポジトリ内 YAML or LINE Bot 双方向）→ `fills` テーブル → 提案 vs 実約定の乖離計測（roadmap §11.1）。
- 発注指示出力（broker CSV/API、`src/execution.py`）。
- `signals.action` の portfolio 駆動化（§1 の設計判断の再評価）。
- active 化の自動提案（`active_readiness` を Issue 起票に接続）。
- DB アーカイブ自動化（backtest_equity の parquet 退避）、Alembic 導入判断。
- CS 較正の粒度改善（I11: bucket → isotonic 連続化）、ポートフォリオの gross 低位問題の振り返り。

---

## 10. 推奨実装順序と工数感

| 順 | タスク | 工数 | 備考 |
|---|---|---|---|
| 1 | Task 0（公開バグ） | 小 | ✅ **完了 (8c232f9)**。merge 後に force_publish で本番 200 を確認 |
| 2 | Task 2 → Task 3（計測完成） | 中 | 実績データの蓄積は時間が資産。早いほど良い |
| 3 | Task 4（リトライ） | 小 | Task 5/6 の前提 |
| 4 | Task 1（active 配線 + 通知後段化） | 中 | Task 5 の前提 |
| 5 | Task 5（digest）→ Task 6（週次） | 中 | digest は shadow 中から価値が出る |
| 6 | Task 7 → Task 8（web） | 中 | データ蓄積と並行で実装 |
| 7 | Task 9（active 化判断: 2026-06-24 目安）/ Task 10 | 小 | 運用 |
