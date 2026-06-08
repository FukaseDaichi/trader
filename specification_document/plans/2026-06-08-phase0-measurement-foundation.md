# Phase 0: 計測基盤（DB＋実現結果台帳）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 既存の日次予測パイプラインを一切壊さずに、出したシグナルとその実現結果（1/5/10日）を Neon Postgres に貯め始め、ダッシュボードに実トラックレコード（実現的中率・平均リターン・簡易資産曲線）を表示する。

**Architecture:** 純粋ロジック（行マッピング・決済計算・集計）を `src/db_records.py` に分離して standalone テストし、psycopg の I/O は `src/db.py` に隔離する。DB 書き込みは write-through、DB 不通時は `data/outbox/*.jsonl` にキューして次回リプレイ（`event_id` で冪等）。`main.py` は DB 例外を握りつぶし、通知・ダッシュボード出力を止めない。決済は `scripts/settle_outcomes.py` が parquet から前向きリターンを再計算して埋める。

**Tech Stack:** Python 3.13 / uv、psycopg 3（`psycopg[binary]`）、Neon Postgres（Free）、pandas/pyarrow（既存）、Next.js 16 + React 19 + TypeScript（既存フロント）。テストは pytest 非依存の standalone runner（既存 `tests/test_curation_merge.py` と同形式）。

**設計の正典:** `specification_document/improvement_roadmap.md` の §4（DB設計）と §5 Phase 0（0A〜0F）。本計画はそれを実装手順へ落としたもの。

---

## 実装前の前提（手動・一回限り）

これらはコードではなく運用準備。Task 1 着手前に完了させること。

1. **フィーチャーブランチを作成**（現在 `main`）:
   ```bash
   git checkout -b feat/phase0-measurement-foundation
   ```
2. **Neon プロジェクト作成**: https://neon.com でプロジェクトを 1 つ作成し、接続文字列（`postgresql://...?sslmode=require`）を控える。region はレイテンシ非依存なので任意。
3. **GitHub Secret 登録**: リポジトリ Settings → Secrets and variables → Actions に `DATABASE_URL` を追加（Neon の接続文字列）。
4. **ローカル `.env`**: ローカル検証用に `.env` へ `DATABASE_URL=...` と `TRADER_DB_ENABLED=true` を追記（`.env` は gitignore 済み）。

> **commit 規約**: 全 commit はこのブランチ上で行い、本リポジトリ慣習に従いメッセージ末尾に `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` を付ける。

---

## File Structure（作成/変更するファイルと責務）

| ファイル | 区分 | 責務 |
|---|---|---|
| `pyproject.toml` | 変更 | `psycopg[binary]` 依存追加 |
| `.env.example` | 変更 | DB 関連 env のドキュメント |
| `src/db_records.py` | 新規 | **純粋ロジック**: signal→行マッピング、`event_id`、決済計算、実績集計。psycopg 非依存・I/O なし |
| `src/db.py` | 新規 | **psycopg I/O 隔離**: 接続・upsert・outbox フォールバック・決済対象取得・DBサイズ |
| `migrations/0001_phase0_schema.sql` | 新規 | スキーマ DDL（roadmap §4.3 準拠） |
| `scripts/db_migrate.py` | 新規 | 冪等 migration ランナー＋ tickers / legacy モデル seed |
| `scripts/settle_outcomes.py` | 新規 | parquet から 1/5/10 日実現リターンを計算し `signal_outcomes` を埋める |
| `main.py` | 変更 | signals 生成後に `db.record_run()` を try/except で呼ぶ |
| `src/dashboard.py` | 変更 | `docs/performance_summary.json` を DB 由来で追加出力（DB 不通なら縮退） |
| `.github/workflows/daily-preopen-core.yml` | 変更 | DB env を渡し、`main.py` 後に settle ステップ追加 |
| `web/src/types/index.ts` | 変更 | `PerformanceSummary` 型追加 |
| `web/src/components/PerformanceCard.tsx` | 新規 | 実績タイル（`performance_summary.json` を fetch、欠損時は非表示） |
| `web/src/app/page.tsx` | 変更 | ヘッダ下に `<PerformanceCard />` を 1 行差し込み |
| `tests/test_db_records.py` | 新規 | `src/db_records.py` の standalone ユニットテスト |

**契約となるデータ形（全タスク共通）:**

`performance_summary.json`（DB 正常時）:
```json
{
  "available": true,
  "generated_at": "2026-06-08 06:05:00",
  "as_of": "2026-06-08",
  "n_long_signals": 42,
  "horizons": {
    "1": {"count": 40, "hit_rate": 0.55, "avg_return": 0.0012},
    "5": {"count": 35, "hit_rate": 0.58, "avg_return": 0.006},
    "10": {"count": 30, "hit_rate": 0.60, "avg_return": 0.011}
  },
  "equity_curve": [{"date": "2026-05-01", "equity": 1.0, "daily_return": 0.0, "n": 3}],
  "db_size_mb": 12.3,
  "storage_warning": false
}
```
DB 不通時: `{"available": false, "reason": "db_unreachable", "generated_at": "..."}`。

---

## Task 1: 依存追加と環境変数のドキュメント

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`

- [ ] **Step 1: psycopg を依存に追加**

Run:
```bash
uv add "psycopg[binary]>=3.2"
```
Expected: `pyproject.toml` の `dependencies` に `psycopg[binary]>=3.2` が追加され、`uv.lock` が更新される。

- [ ] **Step 2: 追加されたことを確認**

Run:
```bash
uv run python -c "import psycopg; from psycopg.rows import dict_row; print('psycopg', psycopg.__version__)"
```
Expected: `psycopg 3.x.x` が出力される（エラーなし）。

- [ ] **Step 3: `.env.example` に DB 設定を追記**

`.env.example` の末尾に以下を追記する:
```bash
# --- Phase 0: 計測基盤（Neon Postgres）---
# Neon の接続文字列。未設定なら DB 書き込みは skip され、従来どおり JSON だけ出力する。
DATABASE_URL=
# DB 書き込みの有効/無効（false で完全に無効化）。
TRADER_DB_ENABLED=true
# DB 不通時のフォールバックキュー出力先。
TRADER_DB_FALLBACK_DIR=data/outbox
# DB 接続タイムアウト秒。
TRADER_DB_WRITE_TIMEOUT_SEC=15
# DB サイズ警告のしきい値（MB）。超過で performance_summary.json に警告フラグ。
TRADER_DB_STORAGE_WARN_MB=400
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock .env.example
git commit -m "feat(phase0): add psycopg dependency and DB env docs"
```

---

## Task 2: `db_records` — signal→DB行マッピングと event_id（純粋）

**Files:**
- Create: `src/db_records.py`
- Test: `tests/test_db_records.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_db_records.py` を新規作成:
```python
#!/usr/bin/env python3
"""
Unit tests for src/db_records.py (pure logic, no DB / no network).

Runnable two ways:
  uv run python tests/test_db_records.py     # standalone
  uv run pytest tests/test_db_records.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db_records import (  # noqa: E402
    make_event_id,
    signal_to_prediction_row,
    signal_to_signal_row,
)

OK_SIGNAL = {
    "ticker": "7011.JP",
    "name": "三菱重工業",
    "date": "2026-06-05",
    "close": 4586.0,
    "prob_up": 0.72,
    "action": "MILD_BUY",
    "raw_action": "MILD_BUY",
    "gate_passed": True,
    "status": "ok",
    "thresholds": {"buy": 0.8, "mild_buy": 0.65, "mild_sell": 0.25, "sell": 0.1, "volatility_limit": 0.04},
    "limit_price": None,
    "stop_loss": None,
    "reason": "やや上昇傾向 (上昇確率 72%)",
}

FAILED_SIGNAL = {
    "ticker": "9999.JP",
    "name": "失敗銘柄",
    "date": "2026-06-08",
    "close": None,
    "prob_up": None,
    "action": "HOLD",
    "raw_action": "HOLD",
    "gate_passed": False,
    "status": "failed",
}


def test_event_id_is_stable_and_namespaced():
    assert make_event_id("2026-06-08", "7011.JP", "sig") == "2026-06-08:7011.JP:sig"
    assert make_event_id("2026-06-08", "7011.JP", "pred") != make_event_id("2026-06-08", "7011.JP", "sig")


def test_prediction_row_for_ok_signal():
    row = signal_to_prediction_row(OK_SIGNAL, run_date="2026-06-08")
    assert row is not None
    assert row["run_date"] == "2026-06-08"
    assert row["as_of_date"] == "2026-06-05"      # signal['date'] = latest price date
    assert row["ticker"] == "7011.JP"
    assert row["model_version"] == "legacy-daily-v0"
    assert row["horizon_days"] == 1               # legacy model predicts next-day
    assert abs(row["prob_up"] - 0.72) < 1e-9
    assert abs(row["raw_score"] - 0.72) < 1e-9


def test_prediction_row_is_none_when_prob_missing():
    assert signal_to_prediction_row(FAILED_SIGNAL, run_date="2026-06-08") is None


def test_signal_row_maps_core_fields():
    row = signal_to_signal_row(OK_SIGNAL, run_date="2026-06-08")
    assert row["run_date"] == "2026-06-08"
    assert row["as_of_date"] == "2026-06-05"
    assert row["ticker"] == "7011.JP"
    assert row["action"] == "MILD_BUY"
    assert row["gate_passed"] is True
    assert row["status"] == "ok"
    assert abs(row["conviction"] - 0.72) < 1e-9
    assert row["target_weight"] is None           # Phase 2
    assert row["thresholds"]["buy"] == 0.8


def test_signal_row_for_failed_signal():
    row = signal_to_signal_row(FAILED_SIGNAL, run_date="2026-06-08")
    assert row["ticker"] == "9999.JP"
    assert row["action"] == "HOLD"
    assert row["gate_passed"] is False
    assert row["status"] == "failed"
    assert row["conviction"] is None


ALL_TESTS = [
    test_event_id_is_stable_and_namespaced,
    test_prediction_row_for_ok_signal,
    test_prediction_row_is_none_when_prob_missing,
    test_signal_row_maps_core_fields,
    test_signal_row_for_failed_signal,
]


def main() -> int:
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run python tests/test_db_records.py`
Expected: `ModuleNotFoundError: No module named 'src.db_records'`（import 失敗）。

- [ ] **Step 3: 最小実装を書く**

`src/db_records.py` を新規作成:
```python
"""
Pure record-mapping and analytics logic for the Phase 0 measurement layer.

This module has NO database or network dependency on purpose, so it can be
unit-tested standalone (see tests/test_db_records.py). The psycopg I/O lives
in src/db.py and imports from here.
"""

from __future__ import annotations

LEGACY_MODEL_VERSION = "legacy-daily-v0"
LEGACY_PREDICTION_HORIZON = 1  # the legacy model predicts next-day direction

# Outcome horizons we evaluate every signal at (independent of the model's horizon).
OUTCOME_HORIZONS = (1, 5, 10)

LONG_ACTIONS = {"BUY", "MILD_BUY"}
AVOID_ACTIONS = {"SELL", "MILD_SELL"}


def make_event_id(run_date: str, ticker: str, event_type: str) -> str:
    """Stable, idempotent key for the outbox fallback queue."""
    return f"{run_date}:{ticker}:{event_type}"


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def signal_to_prediction_row(signal: dict, run_date: str,
                             model_version: str = LEGACY_MODEL_VERSION,
                             horizon_days: int = LEGACY_PREDICTION_HORIZON) -> dict | None:
    """
    Map a daily signal to a `predictions` row. Returns None when there is no
    probability to record (e.g. failed tickers), so we don't store empty rows.
    """
    prob_up = _as_float(signal.get("prob_up"))
    if prob_up is None:
        return None

    return {
        "run_date": run_date,
        "as_of_date": signal.get("date"),
        "ticker": signal.get("ticker"),
        "model_version": model_version,
        "horizon_days": int(horizon_days),
        "raw_score": prob_up,
        "prob_up": prob_up,
        "expected_ret": None,   # Phase 1 (regression head)
        "cs_rank": None,        # Phase 2 (cross-sectional)
        "features_hash": None,  # Phase 1 (reproducibility)
    }


def signal_to_signal_row(signal: dict, run_date: str) -> dict:
    """Map a daily signal to a `signals` row (one per run_date/ticker)."""
    prob_up = _as_float(signal.get("prob_up"))
    return {
        "run_date": run_date,
        "as_of_date": signal.get("date"),
        "ticker": signal.get("ticker"),
        "action": signal.get("action", "HOLD"),
        "raw_action": signal.get("raw_action"),
        "conviction": prob_up,            # calibrated in Phase 1
        "target_weight": None,            # Phase 2 (portfolio)
        "thresholds": signal.get("thresholds"),
        "gate_passed": bool(signal.get("gate_passed", False)),
        "limit_price": _as_float(signal.get("limit_price")),
        "stop_loss": _as_float(signal.get("stop_loss")),
        "reason": signal.get("reason"),
        "status": signal.get("status", "ok"),
    }
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run python tests/test_db_records.py`
Expected: `5/5 passed`。

- [ ] **Step 5: Commit**

```bash
git add src/db_records.py tests/test_db_records.py
git commit -m "feat(phase0): pure signal-to-row mapping and event_id"
```

---

## Task 3: `db_records.compute_outcome` — 実現結果の計算（純粋）

**Files:**
- Modify: `src/db_records.py`
- Test: `tests/test_db_records.py`

- [ ] **Step 1: 失敗するテストを追加**

`tests/test_db_records.py` の import に `compute_outcome` を追加:
```python
from src.db_records import (  # noqa: E402
    compute_outcome,
    make_event_id,
    signal_to_prediction_row,
    signal_to_signal_row,
)
```
そして `ALL_TESTS` の定義の前に以下のテストを追加:
```python
def test_outcome_long_profit():
    # entry 100, exit 110, path high 112 / low 99
    o = compute_outcome("BUY", entry_close=100.0, exit_close=110.0,
                        path_highs=[105.0, 112.0], path_lows=[99.0, 108.0])
    assert abs(o["realized_ret"] - 0.10) < 1e-9
    assert o["hit"] is True
    assert abs(o["mfe"] - 0.12) < 1e-9     # 112/100 - 1
    assert abs(o["mae"] - (-0.01)) < 1e-9  # 99/100 - 1
    assert o["exit_reason"] == "time"


def test_outcome_long_loss_is_not_hit():
    o = compute_outcome("MILD_BUY", entry_close=100.0, exit_close=95.0,
                        path_highs=[101.0], path_lows=[94.0])
    assert abs(o["realized_ret"] - (-0.05)) < 1e-9
    assert o["hit"] is False


def test_outcome_avoid_hit_when_price_falls():
    # SELL/avoid: "hit" means avoiding was correct, i.e. price fell.
    o = compute_outcome("SELL", entry_close=100.0, exit_close=90.0,
                        path_highs=[101.0], path_lows=[89.0])
    assert o["hit"] is True
    o2 = compute_outcome("MILD_SELL", entry_close=100.0, exit_close=105.0,
                         path_highs=[106.0], path_lows=[100.0])
    assert o2["hit"] is False


def test_outcome_hold_has_no_hit():
    o = compute_outcome("HOLD", entry_close=100.0, exit_close=101.0,
                        path_highs=[102.0], path_lows=[100.0])
    assert o["hit"] is None
    assert abs(o["realized_ret"] - 0.01) < 1e-9


def test_outcome_rejects_bad_entry():
    raised = False
    try:
        compute_outcome("BUY", entry_close=0.0, exit_close=100.0, path_highs=[], path_lows=[])
    except ValueError:
        raised = True
    assert raised is True


def test_outcome_empty_path_uses_realized():
    o = compute_outcome("BUY", entry_close=100.0, exit_close=103.0, path_highs=[], path_lows=[])
    assert abs(o["mfe"] - 0.03) < 1e-9
    assert abs(o["mae"] - 0.03) < 1e-9
```
`ALL_TESTS` に追加:
```python
ALL_TESTS = [
    test_event_id_is_stable_and_namespaced,
    test_prediction_row_for_ok_signal,
    test_prediction_row_is_none_when_prob_missing,
    test_signal_row_maps_core_fields,
    test_signal_row_for_failed_signal,
    test_outcome_long_profit,
    test_outcome_long_loss_is_not_hit,
    test_outcome_avoid_hit_when_price_falls,
    test_outcome_hold_has_no_hit,
    test_outcome_rejects_bad_entry,
    test_outcome_empty_path_uses_realized,
]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run python tests/test_db_records.py`
Expected: `ImportError: cannot import name 'compute_outcome'`。

- [ ] **Step 3: 実装を追加**

`src/db_records.py` の末尾に追加:
```python
def compute_outcome(action: str, entry_close: float, exit_close: float,
                    path_highs, path_lows) -> dict:
    """
    Compute the realized outcome of a single signal at one horizon.

    - realized_ret: raw stock return entry->exit (objective fact, sign-agnostic).
    - hit: directional correctness given the action's STANCE
        * long  (BUY/MILD_BUY)  -> hit if price rose
        * avoid (SELL/MILD_SELL)-> hit if price fell (avoiding a loss was correct)
        * HOLD / unknown        -> None (no directional claim)
    - mfe/mae: max favorable / adverse excursion vs entry over the holding path.
    - exit_reason: always "time" in Phase 0 (triple-barrier TP/SL is Phase 1).
    """
    entry = float(entry_close)
    if entry <= 0:
        raise ValueError("entry_close must be positive")

    realized_ret = float(exit_close) / entry - 1.0

    highs = [float(h) for h in (path_highs or [])]
    lows = [float(low) for low in (path_lows or [])]
    mfe = (max(highs) / entry - 1.0) if highs else realized_ret
    mae = (min(lows) / entry - 1.0) if lows else realized_ret

    if action in LONG_ACTIONS:
        hit = realized_ret > 0
    elif action in AVOID_ACTIONS:
        hit = realized_ret < 0
    else:
        hit = None

    return {
        "realized_ret": realized_ret,
        "hit": hit,
        "mae": mae,
        "mfe": mfe,
        "exit_reason": "time",
    }
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run python tests/test_db_records.py`
Expected: `11/11 passed`。

- [ ] **Step 5: Commit**

```bash
git add src/db_records.py tests/test_db_records.py
git commit -m "feat(phase0): compute_outcome (realized return / hit / MAE / MFE)"
```

---

## Task 4: `db_records.summarize_performance` — 実績集計（純粋）

**Files:**
- Modify: `src/db_records.py`
- Test: `tests/test_db_records.py`

- [ ] **Step 1: 失敗するテストを追加**

import に `summarize_performance` を追加し、テストを追加:
```python
def _row(entry_date, action, horizon, ret, hit):
    return {"entry_date": entry_date, "action": action,
            "horizon_days": horizon, "realized_ret": ret, "hit": hit}


def test_summary_hit_rate_and_curve():
    rows = [
        _row("2026-05-01", "BUY", 1, 0.02, True),
        _row("2026-05-01", "MILD_BUY", 1, -0.01, False),  # same day -> averaged
        _row("2026-05-02", "BUY", 1, 0.03, True),
        _row("2026-05-01", "BUY", 5, 0.05, True),
        _row("2026-05-01", "SELL", 1, -0.04, True),       # avoid hit, excluded from curve
    ]
    s = summarize_performance(rows, curve_horizon=1)

    # 1d long hit-rate: 3 long 1d rows (0.02 T, -0.01 F, 0.03 T) -> 2/3
    assert s["horizons"]["1"]["count"] == 3
    assert abs(s["horizons"]["1"]["hit_rate"] - (2.0 / 3.0)) < 1e-9

    # equity curve: day1 mean(0.02,-0.01)=0.005 ; day2 = 0.03
    curve = s["equity_curve"]
    assert [p["date"] for p in curve] == ["2026-05-01", "2026-05-02"]
    assert abs(curve[0]["equity"] - 1.005) < 1e-9
    assert abs(curve[1]["equity"] - 1.005 * 1.03) < 1e-9
    assert s["n_long_signals"] == 4   # BUY/MILD_BUY rows across all horizons


def test_summary_handles_empty():
    s = summarize_performance([], curve_horizon=1)
    assert s["n_long_signals"] == 0
    assert s["equity_curve"] == []
    assert s["horizons"]["5"]["count"] == 0
    assert s["horizons"]["5"]["hit_rate"] is None
```
`ALL_TESTS` に `test_summary_hit_rate_and_curve` と `test_summary_handles_empty` を追加。

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run python tests/test_db_records.py`
Expected: `ImportError: cannot import name 'summarize_performance'`。

- [ ] **Step 3: 実装を追加**

`src/db_records.py` の末尾に追加:
```python
def _mean(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def summarize_performance(rows, curve_horizon: int = 1) -> dict:
    """
    Aggregate joined (signals x signal_outcomes) rows into a dashboard summary.

    Each row: {entry_date, action, horizon_days, realized_ret, hit}.
    Hit-rate and the equity curve use LONG actions only (BUY / MILD_BUY);
    the long-only equity curve compounds the per-day mean realized return at
    `curve_horizon` (default 1 day).
    """
    long_rows = [r for r in rows if r.get("action") in LONG_ACTIONS]

    horizons = {}
    for h in OUTCOME_HORIZONS:
        h_rows = [r for r in long_rows if int(r.get("horizon_days", 0)) == h]
        rets = [r.get("realized_ret") for r in h_rows if r.get("realized_ret") is not None]
        hits = [r.get("hit") for r in h_rows if r.get("hit") is not None]
        horizons[str(h)] = {
            "count": len(rets),
            "hit_rate": (sum(1 for x in hits if x) / len(hits)) if hits else None,
            "avg_return": _mean(rets),
        }

    # Equity curve: group curve_horizon long rows by entry_date, compound daily means.
    by_date: dict[str, list[float]] = {}
    for r in long_rows:
        if int(r.get("horizon_days", 0)) != curve_horizon:
            continue
        ret = r.get("realized_ret")
        if ret is None or not r.get("entry_date"):
            continue
        by_date.setdefault(str(r["entry_date"]), []).append(float(ret))

    equity_curve = []
    equity = 1.0
    for d in sorted(by_date):
        daily_return = _mean(by_date[d]) or 0.0
        equity *= (1.0 + daily_return)
        equity_curve.append({
            "date": d,
            "equity": equity,
            "daily_return": daily_return,
            "n": len(by_date[d]),
        })

    return {
        "n_long_signals": len(long_rows),
        "horizons": horizons,
        "equity_curve": equity_curve,
    }
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run python tests/test_db_records.py`
Expected: `13/13 passed`。

- [ ] **Step 5: Commit**

```bash
git add src/db_records.py tests/test_db_records.py
git commit -m "feat(phase0): summarize_performance (hit-rate + long-only equity curve)"
```

---

## Task 5: スキーマ migration ファイルと冪等ランナー

**Files:**
- Create: `migrations/0001_phase0_schema.sql`
- Create: `scripts/db_migrate.py`

- [ ] **Step 1: スキーマ DDL を作成**

`migrations/0001_phase0_schema.sql` を新規作成（roadmap §4.3 準拠、`IF NOT EXISTS` で安全化）:
```sql
-- Phase 0 schema. Full roadmap §4.3 schema; Phase 0 only writes
-- tickers / model_registry / predictions / signals / signal_outcomes.

CREATE TABLE IF NOT EXISTS tickers (
  code        TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  sector      TEXT,
  enabled     BOOLEAN NOT NULL DEFAULT TRUE,
  source      TEXT,
  added_on    DATE,
  disabled_on DATE
);

CREATE TABLE IF NOT EXISTS model_registry (
  version      TEXT PRIMARY KEY,
  trained_at   TIMESTAMPTZ NOT NULL,
  kind         TEXT NOT NULL,
  universe     JSONB NOT NULL,
  feature_set  JSONB NOT NULL,
  params       JSONB NOT NULL,
  cv_metrics   JSONB NOT NULL,
  calibration  JSONB,
  artifact_uri TEXT,
  active       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS predictions (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,
  as_of_date    DATE NOT NULL,
  ticker        TEXT NOT NULL REFERENCES tickers(code),
  model_version TEXT NOT NULL REFERENCES model_registry(version),
  horizon_days  INT  NOT NULL,
  raw_score     DOUBLE PRECISION,
  prob_up       DOUBLE PRECISION,
  expected_ret  DOUBLE PRECISION,
  cs_rank       INT,
  features_hash TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_date, ticker, model_version, horizon_days)
);

CREATE TABLE IF NOT EXISTS signals (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,
  as_of_date    DATE NOT NULL,
  ticker        TEXT NOT NULL REFERENCES tickers(code),
  prediction_id BIGINT REFERENCES predictions(id),
  action        TEXT NOT NULL,
  raw_action    TEXT,
  conviction    DOUBLE PRECISION,
  target_weight DOUBLE PRECISION,
  thresholds    JSONB,
  gate_passed   BOOLEAN NOT NULL,
  limit_price   DOUBLE PRECISION,
  stop_loss     DOUBLE PRECISION,
  reason        TEXT,
  status        TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_date, ticker)
);

CREATE TABLE IF NOT EXISTS signal_outcomes (
  signal_id      BIGINT NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  horizon_days   INT NOT NULL,
  entry_date     DATE NOT NULL,
  eval_date      DATE NOT NULL,
  entry_close    DOUBLE PRECISION,
  exit_close     DOUBLE PRECISION,
  realized_ret   DOUBLE PRECISION,
  benchmark_ret  DOUBLE PRECISION,
  excess_ret     DOUBLE PRECISION,
  hit            BOOLEAN,
  mae            DOUBLE PRECISION,
  mfe            DOUBLE PRECISION,
  exit_reason    TEXT,
  PRIMARY KEY (signal_id, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_signals_as_of ON signals (as_of_date);
CREATE INDEX IF NOT EXISTS idx_predictions_run ON predictions (run_date);
```

- [ ] **Step 2: 冪等 migration ランナーを作成**

`scripts/db_migrate.py` を新規作成:
```python
#!/usr/bin/env python3
"""
Idempotent migration runner for the Phase 0 measurement layer.

Applies any .sql file in migrations/ not yet recorded in schema_migrations,
then seeds tickers (from tickers.yml) and the legacy model_registry row.

Usage:
  uv run python scripts/db_migrate.py
  uv run python scripts/db_migrate.py --dry-run

Exits 0 (no-op) when DB is disabled or DATABASE_URL is unset.
Exits non-zero on a real connection / SQL error so bootstrap failures surface.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from scripts.curation_common import load_tickers_config, now_jst_iso  # noqa: E402

MIGRATIONS_DIR = ROOT / "migrations"


def _ensure_migrations_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    conn.commit()


def _applied_versions(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {r[0] for r in cur.fetchall()}


def _apply_pending(conn, dry_run: bool) -> list[str]:
    applied = _applied_versions(conn)
    pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.stem not in applied)
    done = []
    for path in pending:
        sql = path.read_text(encoding="utf-8")
        print(f"Applying migration {path.stem}{' (dry-run)' if dry_run else ''}...")
        if dry_run:
            continue
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
            )
        conn.commit()
        done.append(path.stem)
    return done


def _seed_tickers(conn) -> int:
    cfg = load_tickers_config()
    rows = []
    for t in cfg.get("tickers", []):
        if not isinstance(t, dict) or not t.get("code"):
            continue
        rows.append((
            t["code"], t.get("name") or t["code"], t.get("sector"),
            bool(t.get("enabled", True)), t.get("source"),
            t.get("added_on"), t.get("disabled_on"),
        ))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO tickers (code, name, sector, enabled, source, added_on, disabled_on)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (code) DO UPDATE SET"
            "  name=EXCLUDED.name, sector=EXCLUDED.sector, enabled=EXCLUDED.enabled,"
            "  source=EXCLUDED.source, added_on=EXCLUDED.added_on, disabled_on=EXCLUDED.disabled_on",
            rows,
        )
    conn.commit()
    return len(rows)


def _seed_legacy_model(conn) -> None:
    from psycopg.types.json import Jsonb
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_registry"
            " (version, trained_at, kind, universe, feature_set, params, cv_metrics, artifact_uri, active)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (version) DO NOTHING",
            (
                db.LEGACY_MODEL_VERSION, now_jst_iso(), "per_ticker_legacy_daily",
                Jsonb([]), Jsonb([]), Jsonb({}), Jsonb({}), None, True,
            ),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not db.db_enabled():
        print("DB disabled or DATABASE_URL unset; nothing to migrate.")
        return 0

    conn = db.connect()
    try:
        _ensure_migrations_table(conn)
        done = _apply_pending(conn, dry_run=args.dry_run)
        if args.dry_run:
            print("Dry-run complete.")
            return 0
        n_tickers = _seed_tickers(conn)
        _seed_legacy_model(conn)
        print(f"Migrations applied: {done or '(none pending)'}")
        print(f"Seeded {n_tickers} tickers and legacy model '{db.LEGACY_MODEL_VERSION}'.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

> **Note:** `scripts/db_migrate.py` は Task 6 で実装する `src.db` の `db_enabled()` / `connect()` / `LEGACY_MODEL_VERSION` に依存する。これらは Task 6 完了後に動作する。Task 5 では構文確認のみ行う。

- [ ] **Step 3: 構文を確認（DB なしで import 可能か）**

Run:
```bash
uv run python -c "import ast; ast.parse(open('scripts/db_migrate.py').read()); print('db_migrate.py OK')"
```
Expected: `db_migrate.py OK`。

- [ ] **Step 4: Commit**

```bash
git add migrations/0001_phase0_schema.sql scripts/db_migrate.py
git commit -m "feat(phase0): schema migration and idempotent runner"
```

---

## Task 6: `src/db.py` — psycopg I/O とフォールバック

**Files:**
- Create: `src/db.py`
- Modify: `tests/test_db_records.py`（outbox 冪等の純粋部分のみ）

> **Note:** psycopg を使う関数（`connect` / `record_run` の DB 経路 / `flush_outbox` / `fetch_unsettled` / `upsert_outcome` / `fetch_outcome_rows` / `db_size_mb`）は実 DB が必要なため、ユニットテストではなく Step 5 の手動スモークで検証する。outbox のキュー書き出しと `event_id` 冪等（DB 不要）だけ standalone テストする。

- [ ] **Step 1: `src/db.py` を実装**

`src/db.py` を新規作成:
```python
"""
Phase 0 measurement layer: psycopg I/O, isolated so the daily pipeline never
breaks when the database is unreachable.

Write path is write-through with an on-disk fallback queue (data/outbox/*.jsonl).
Every helper that touches the network is wrapped by callers in try/except;
record_run() itself never raises.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import DATA_DIR
from . import db_records
from .db_records import LEGACY_MODEL_VERSION, OUTCOME_HORIZONS  # re-exported

DEFAULT_FALLBACK_DIR = DATA_DIR / "outbox"


# --- env helpers (mirror src/data_loader.py style) -------------------------

def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    return raw if raw not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def database_url() -> str | None:
    url = _env_str("DATABASE_URL")
    return url or None


def db_enabled() -> bool:
    return _env_bool("TRADER_DB_ENABLED", True) and database_url() is not None


def _fallback_dir() -> Path:
    return Path(_env_str("TRADER_DB_FALLBACK_DIR", str(DEFAULT_FALLBACK_DIR)))


def connect():
    """Open a psycopg connection. Raises on failure (callers handle it)."""
    import psycopg
    timeout = _env_int("TRADER_DB_WRITE_TIMEOUT_SEC", 15)
    return psycopg.connect(database_url(), connect_timeout=timeout)


# --- outbox (pure-ish: filesystem only, no network) ------------------------

def _queue_events(events: list[dict]) -> int:
    if not events:
        return 0
    out_dir = _fallback_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y%m%d%H%M%S")
    path = out_dir / f"{stamp}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return len(events)


def _read_outbox_events() -> list[dict]:
    out_dir = _fallback_dir()
    if not out_dir.exists():
        return []
    events = []
    for path in sorted(out_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _clear_outbox() -> None:
    out_dir = _fallback_dir()
    if not out_dir.exists():
        return
    for path in out_dir.glob("*.jsonl"):
        path.unlink(missing_ok=True)


def _build_events(signals: list[dict], run_date: str) -> list[dict]:
    """Turn daily signals into idempotent outbox events (pred + sig)."""
    events = []
    for s in signals:
        ticker = s.get("ticker")
        if not ticker:
            continue
        pred = db_records.signal_to_prediction_row(s, run_date)
        if pred is not None:
            events.append({
                "event_id": db_records.make_event_id(run_date, ticker, "pred"),
                "kind": "prediction", "row": pred,
            })
        events.append({
            "event_id": db_records.make_event_id(run_date, ticker, "sig"),
            "kind": "signal",
            "row": db_records.signal_to_signal_row(s, run_date),
        })
    return events


# --- upserts ---------------------------------------------------------------

def _upsert_prediction(cur, row: dict) -> None:
    cur.execute(
        "INSERT INTO predictions"
        " (run_date, as_of_date, ticker, model_version, horizon_days,"
        "  raw_score, prob_up, expected_ret, cs_rank, features_hash)"
        " VALUES (%(run_date)s, %(as_of_date)s, %(ticker)s, %(model_version)s,"
        "  %(horizon_days)s, %(raw_score)s, %(prob_up)s, %(expected_ret)s,"
        "  %(cs_rank)s, %(features_hash)s)"
        " ON CONFLICT (run_date, ticker, model_version, horizon_days) DO UPDATE SET"
        "  as_of_date=EXCLUDED.as_of_date, raw_score=EXCLUDED.raw_score,"
        "  prob_up=EXCLUDED.prob_up, expected_ret=EXCLUDED.expected_ret,"
        "  cs_rank=EXCLUDED.cs_rank, features_hash=EXCLUDED.features_hash",
        row,
    )


def _upsert_signal(cur, row: dict) -> None:
    from psycopg.types.json import Jsonb
    params = dict(row)
    params["thresholds"] = Jsonb(row.get("thresholds")) if row.get("thresholds") is not None else None
    cur.execute(
        "INSERT INTO signals"
        " (run_date, as_of_date, ticker, action, raw_action, conviction,"
        "  target_weight, thresholds, gate_passed, limit_price, stop_loss, reason, status)"
        " VALUES (%(run_date)s, %(as_of_date)s, %(ticker)s, %(action)s, %(raw_action)s,"
        "  %(conviction)s, %(target_weight)s, %(thresholds)s, %(gate_passed)s,"
        "  %(limit_price)s, %(stop_loss)s, %(reason)s, %(status)s)"
        " ON CONFLICT (run_date, ticker) DO UPDATE SET"
        "  as_of_date=EXCLUDED.as_of_date, action=EXCLUDED.action,"
        "  raw_action=EXCLUDED.raw_action, conviction=EXCLUDED.conviction,"
        "  target_weight=EXCLUDED.target_weight, thresholds=EXCLUDED.thresholds,"
        "  gate_passed=EXCLUDED.gate_passed, limit_price=EXCLUDED.limit_price,"
        "  stop_loss=EXCLUDED.stop_loss, reason=EXCLUDED.reason, status=EXCLUDED.status",
        params,
    )


def _apply_events(conn, events: list[dict]) -> int:
    """Idempotently upsert a list of outbox events. Dedup by event_id."""
    seen = set()
    applied = 0
    with conn.cursor() as cur:
        for ev in events:
            eid = ev.get("event_id")
            if eid in seen:
                continue
            seen.add(eid)
            if ev.get("kind") == "prediction":
                _upsert_prediction(cur, ev["row"])
            elif ev.get("kind") == "signal":
                _upsert_signal(cur, ev["row"])
            applied += 1
    conn.commit()
    return applied


def flush_outbox(conn) -> int:
    events = _read_outbox_events()
    if not events:
        return 0
    applied = _apply_events(conn, events)
    _clear_outbox()
    return applied


def record_run(signals: list[dict], run_date: str) -> dict:
    """
    Write-through the day's predictions+signals. Never raises.
    On any failure, events are queued to the outbox for the next run.
    """
    events = _build_events(signals, run_date)
    if not db_enabled():
        queued = _queue_events(events)
        return {"ok": False, "reason": "db_disabled", "queued": queued}

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        queued = _queue_events(events)
        return {"ok": False, "reason": f"connect_failed: {type(exc).__name__}", "queued": queued}

    try:
        flushed = flush_outbox(conn)
        applied = _apply_events(conn, events)
        return {"ok": True, "applied": applied, "flushed_backlog": flushed}
    except Exception as exc:  # noqa: BLE001
        queued = _queue_events(events)
        return {"ok": False, "reason": f"write_failed: {type(exc).__name__}", "queued": queued}
    finally:
        conn.close()


# --- settlement support (read) ---------------------------------------------

def fetch_unsettled(conn) -> list[dict]:
    """Actionable signals and which OUTCOME_HORIZONS are still missing."""
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.id AS signal_id, s.ticker, s.as_of_date, s.action,"
            " COALESCE(array_agg(o.horizon_days) FILTER (WHERE o.horizon_days IS NOT NULL), '{}') AS settled"
            " FROM signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id"
            " WHERE s.status = 'ok' AND s.action IN ('BUY','MILD_BUY','SELL','MILD_SELL')"
            " GROUP BY s.id, s.ticker, s.as_of_date, s.action"
        )
        rows = cur.fetchall()
    result = []
    for r in rows:
        settled = set(r["settled"] or [])
        missing = [h for h in OUTCOME_HORIZONS if h not in settled]
        if missing:
            result.append({**r, "missing_horizons": missing})
    return result


def upsert_outcome(conn, signal_id: int, horizon_days: int, payload: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_outcomes"
            " (signal_id, horizon_days, entry_date, eval_date, entry_close, exit_close,"
            "  realized_ret, benchmark_ret, excess_ret, hit, mae, mfe, exit_reason)"
            " VALUES (%(signal_id)s, %(horizon_days)s, %(entry_date)s, %(eval_date)s,"
            "  %(entry_close)s, %(exit_close)s, %(realized_ret)s, %(benchmark_ret)s,"
            "  %(excess_ret)s, %(hit)s, %(mae)s, %(mfe)s, %(exit_reason)s)"
            " ON CONFLICT (signal_id, horizon_days) DO UPDATE SET"
            "  eval_date=EXCLUDED.eval_date, entry_close=EXCLUDED.entry_close,"
            "  exit_close=EXCLUDED.exit_close, realized_ret=EXCLUDED.realized_ret,"
            "  benchmark_ret=EXCLUDED.benchmark_ret, excess_ret=EXCLUDED.excess_ret,"
            "  hit=EXCLUDED.hit, mae=EXCLUDED.mae, mfe=EXCLUDED.mfe, exit_reason=EXCLUDED.exit_reason",
            {"signal_id": signal_id, "horizon_days": horizon_days, **payload},
        )
    conn.commit()


def fetch_outcome_rows(conn) -> list[dict]:
    """Joined rows for summarize_performance()."""
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.as_of_date AS entry_date, s.action, o.horizon_days,"
            " o.realized_ret, o.hit"
            " FROM signal_outcomes o JOIN signals s ON s.id = o.signal_id"
            " WHERE s.action IN ('BUY','MILD_BUY','SELL','MILD_SELL')"
        )
        rows = cur.fetchall()
    # Normalize dates to ISO strings for the pure summarizer.
    for r in rows:
        if r.get("entry_date") is not None:
            r["entry_date"] = str(r["entry_date"])
    return rows


def db_size_mb(conn) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        size_bytes = cur.fetchone()[0]
    return round(size_bytes / (1024 * 1024), 2)
```

- [ ] **Step 2: outbox 冪等の失敗テストを追加**

`tests/test_db_records.py` に、`src.db` の純粋部分（filesystem のみ、network なし）のテストを追加。import セクションの下に追記:
```python
import tempfile  # noqa: E402

import src.db as dbmod  # noqa: E402


def test_outbox_queue_and_dedup(tmp_path_str=None):
    import os
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRADER_DB_FALLBACK_DIR"] = tmp
        os.environ["TRADER_DB_ENABLED"] = "false"  # force fallback path
        os.environ.pop("DATABASE_URL", None)
        try:
            signals = [
                {"ticker": "7011.JP", "date": "2026-06-05", "prob_up": 0.7,
                 "action": "MILD_BUY", "gate_passed": True, "status": "ok"},
                {"ticker": "9999.JP", "date": "2026-06-08", "prob_up": None,
                 "action": "HOLD", "gate_passed": False, "status": "failed"},
            ]
            res = dbmod.record_run(signals, run_date="2026-06-08")
            assert res["ok"] is False
            # 7011 -> pred + sig (2), 9999 -> sig only (1) = 3 events
            assert res["queued"] == 3

            events = dbmod._read_outbox_events()
            ids = {e["event_id"] for e in events}
            assert "2026-06-08:7011.JP:pred" in ids
            assert "2026-06-08:7011.JP:sig" in ids
            assert "2026-06-08:9999.JP:sig" in ids
            assert "2026-06-08:9999.JP:pred" not in ids  # no prob_up -> no prediction
        finally:
            os.environ.pop("TRADER_DB_FALLBACK_DIR", None)
            os.environ.pop("TRADER_DB_ENABLED", None)
```
`ALL_TESTS` に `test_outbox_queue_and_dedup` を追加。

- [ ] **Step 3: テストが失敗することを確認**

Run: `uv run python tests/test_db_records.py`
Expected: 新テストが `FAIL`/`ERROR`（`src/db.py` 未実装、または挙動不一致）。Step 1 実装後に通る想定なので、Step 1→Step 3 の順なら、ここでは「他は PASS、新規だけ要確認」。

> 実行順の注意: Step 1（実装）を先に終えているため、本 Step では `14/14 passed` を確認する。TDD 形式に厳密化したい場合は、Step 2 を Step 1 より前に置き、まず失敗を観測してから Step 1 を適用する。

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run python tests/test_db_records.py`
Expected: `14/14 passed`。

- [ ] **Step 5: 手動スモーク（実 DB、ローカル `.env` に DATABASE_URL がある場合）**

Run:
```bash
uv run python scripts/db_migrate.py
uv run python -c "
from src import db
conn = db.connect()
print('db_size_mb', db.db_size_mb(conn))
print('unsettled', len(db.fetch_unsettled(conn)))
conn.close()
"
```
Expected: migration が適用され（`Seeded N tickers...`）、`db_size_mb` が数値、`unsettled 0`（まだシグナル未投入）。
DATABASE_URL が無い環境では `DB disabled or DATABASE_URL unset; nothing to migrate.` が出ればよい（スキップ）。

- [ ] **Step 6: Commit**

```bash
git add src/db.py tests/test_db_records.py
git commit -m "feat(phase0): psycopg I/O layer with outbox fallback"
```

---

## Task 7: `main.py` に write-through を組み込む

**Files:**
- Modify: `main.py`

- [ ] **Step 1: DB 記録を呼び出す（例外は握りつぶす）**

`main.py` の import 群（既存の `from src.backtest import ...` の下）に追加:
```python
from src import db
```

`main.py` の `main()` 関数内、`for ticker_info in TICKERS:` ループが終わった直後・`report_path = write_backtest_report(backtest_entries)` の**前**に以下を挿入:
```python
    # Phase 0: write-through predictions/signals to the measurement DB.
    # Never let DB issues break the daily run (notification + dashboard).
    try:
        db_result = db.record_run(signals, _run_date_jst())
        print(f"DB record_run: {db_result}")
    except Exception as e:  # defensive: record_run itself should not raise
        print(f"DB record_run unexpected error (ignored): {type(e).__name__}: {e}")
```

- [ ] **Step 2: フォールバック動作を手動確認（DB 無効）**

Run:
```bash
TRADER_DB_ENABLED=false RUN_DATE_JST=2026-06-08 uv run python main.py
```
Expected: 日次処理が最後まで完走し、`DB record_run: {'ok': False, 'reason': 'db_disabled', 'queued': N}` が出力される。`data/outbox/*.jsonl` が生成される。`docs/state.json` / `docs/dashboard_index.json` は従来どおり更新される。

- [ ] **Step 3: outbox の中身を確認**

Run:
```bash
ls data/outbox/ && head -2 data/outbox/*.jsonl
```
Expected: JSONL に `event_id` 付きイベント（`pred`/`sig`）が並ぶ。

- [ ] **Step 4: （DB 有効時）リプレイを確認**

ローカル `.env` に DATABASE_URL がある場合:
```bash
uv run python scripts/db_migrate.py
RUN_DATE_JST=2026-06-08 uv run python main.py
```
Expected: `DB record_run: {'ok': True, 'applied': ..., 'flushed_backlog': N}`。`data/outbox/` が空になる（バックログがリプレイされ削除）。再実行しても重複行が増えない（upsert）。

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(phase0): write-through predictions/signals from daily run"
```

---

## Task 8: `scripts/settle_outcomes.py` — 実現結果の決済

**Files:**
- Create: `scripts/settle_outcomes.py`

- [ ] **Step 1: settle スクリプトを実装**

`scripts/settle_outcomes.py` を新規作成:
```python
#!/usr/bin/env python3
"""
Phase 0 outcome settlement.

For each actionable, not-yet-settled signal in the DB, compute the realized
1/5/10 trading-day forward outcome from the ticker's parquet and upsert into
signal_outcomes. Idempotent: re-running only fills missing (signal, horizon)
pairs that now have enough forward data.

Usage:
  uv run python scripts/settle_outcomes.py
  uv run python scripts/settle_outcomes.py --as-of 2026-06-08

Benchmark (TOPIX) columns are left NULL in Phase 0 (added in Phase 1).
Exits 0 (no-op) when DB is disabled / unreachable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.db_records import compute_outcome  # noqa: E402
from src.data_loader import load_data  # noqa: E402
from scripts.curation_common import today_jst_iso  # noqa: E402


def _settle_for_ticker(conn, ticker: str, signals: list[dict]) -> int:
    df = load_data(ticker)
    if df is None or df.empty or "date" not in df.columns:
        return 0
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    date_to_idx = {d: i for i, d in enumerate(df["date"].tolist())}

    settled = 0
    for sig in signals:
        as_of = str(sig["as_of_date"])
        idx = date_to_idx.get(as_of)
        if idx is None:
            continue  # as_of date not present in price history (e.g. failed signal)
        entry_close = float(df["close"].iloc[idx])
        for h in sig["missing_horizons"]:
            exit_idx = idx + h
            if exit_idx >= len(df):
                continue  # not enough forward data yet; settle on a later run
            exit_close = float(df["close"].iloc[exit_idx])
            path = df.iloc[idx + 1: exit_idx + 1]
            payload = compute_outcome(
                action=sig["action"], entry_close=entry_close, exit_close=exit_close,
                path_highs=path["high"].astype(float).tolist(),
                path_lows=path["low"].astype(float).tolist(),
            )
            db.upsert_outcome(conn, sig["signal_id"], h, {
                "entry_date": as_of,
                "eval_date": df["date"].iloc[exit_idx],
                "entry_close": entry_close,
                "exit_close": exit_close,
                "realized_ret": payload["realized_ret"],
                "benchmark_ret": None,
                "excess_ret": None,
                "hit": payload["hit"],
                "mae": payload["mae"],
                "mfe": payload["mfe"],
                "exit_reason": payload["exit_reason"],
            })
            settled += 1
    return settled


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=today_jst_iso(),
                        help="JST date label (informational; settlement scans all unsettled).")
    args = parser.parse_args()

    if not db.db_enabled():
        print("DB disabled or DATABASE_URL unset; skipping settlement.")
        return 0

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect for settlement (ignored): {type(exc).__name__}: {exc}")
        return 0

    try:
        unsettled = db.fetch_unsettled(conn)
        by_ticker: dict[str, list[dict]] = {}
        for row in unsettled:
            by_ticker.setdefault(row["ticker"], []).append(row)

        total = 0
        for ticker, sigs in by_ticker.items():
            total += _settle_for_ticker(conn, ticker, sigs)
        print(f"Settlement as-of {args.as_of}: filled {total} outcome rows "
              f"across {len(by_ticker)} tickers ({len(unsettled)} unsettled signals scanned).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 構文を確認**

Run:
```bash
uv run python -c "import ast; ast.parse(open('scripts/settle_outcomes.py').read()); print('settle_outcomes.py OK')"
```
Expected: `settle_outcomes.py OK`。

- [ ] **Step 3: 手動スモーク（DB 有効時）**

前提: Task 7 Step 4 で当日シグナルが DB に入っている。古い `as_of_date` のシグナルがあれば 1d outcome が確定する。
Run:
```bash
uv run python scripts/settle_outcomes.py --as-of 2026-06-08
```
Expected: `Settlement as-of 2026-06-08: filled N outcome rows ...`。N は前向きデータが十分なシグナル数（当日分のみだと 0 のこともある＝正常）。
再実行しても、既に埋まった (signal, horizon) は ON CONFLICT で更新のみ（重複なし）。
DATABASE_URL が無ければ `DB disabled ...` でスキップ。

- [ ] **Step 4: Commit**

```bash
git add scripts/settle_outcomes.py
git commit -m "feat(phase0): settle realized 1/5/10d outcomes from parquet"
```

---

## Task 9: `performance_summary.json` のダッシュボード出力

**Files:**
- Modify: `src/dashboard.py`

- [ ] **Step 1: 実績サマリ出力を追加**

`src/dashboard.py` の import 群に追加:
```python
from . import db
from .db_records import summarize_performance
```
ファイル上部の定数（`LEGACY_HISTORY_FILE = ...` の近く）に追加:
```python
PERFORMANCE_FILE = DOCS_DIR / "performance_summary.json"
```
`update_dashboard()` を次のように拡張（`export_dashboard_data()` の後に 1 行追加）:
```python
def update_dashboard(signals):
    """
    Update state.json and export lightweight dashboard JSON assets.
    """
    # 1. Update state.json (history of signals).
    update_state(signals)

    # 2. Export dashboard_index.json + tickers/{code}.json.
    export_dashboard_data()

    # 3. Phase 0: export realized-performance summary from the DB (best-effort).
    export_performance_summary()
```
ファイル末尾（`export_history_data()` の下）に新関数を追加:
```python
def export_performance_summary():
    """
    Write docs/performance_summary.json from the measurement DB. Best-effort:
    if the DB is disabled or unreachable, write an "unavailable" marker and
    keep the previous summary untouched on disk if present.
    """
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    if not db.db_enabled():
        if not PERFORMANCE_FILE.exists():
            _atomic_write_json(PERFORMANCE_FILE, {
                "available": False, "reason": "db_disabled", "generated_at": now_str,
            }, indent=2)
        return

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"performance_summary: DB unreachable ({type(exc).__name__}); leaving file as-is.")
        if not PERFORMANCE_FILE.exists():
            _atomic_write_json(PERFORMANCE_FILE, {
                "available": False, "reason": "db_unreachable", "generated_at": now_str,
            }, indent=2)
        return

    try:
        rows = db.fetch_outcome_rows(conn)
        summary = summarize_performance(rows, curve_horizon=1)
        size_mb = db.db_size_mb(conn)
        warn_mb = float(os.getenv("TRADER_DB_STORAGE_WARN_MB", "400"))
        payload = {
            "available": True,
            "generated_at": now_str,
            "as_of": _resolve_run_date_jst(datetime.now(JST)),
            "n_long_signals": summary["n_long_signals"],
            "horizons": summary["horizons"],
            "equity_curve": summary["equity_curve"],
            "db_size_mb": size_mb,
            "storage_warning": size_mb >= warn_mb,
        }
        if payload["storage_warning"]:
            print(f"WARNING: DB size {size_mb}MB >= {warn_mb}MB threshold.")
        _atomic_write_json(PERFORMANCE_FILE, payload, indent=2)
        print(f"Performance summary exported to {PERFORMANCE_FILE}")
    except Exception as exc:  # noqa: BLE001
        print(f"performance_summary: export failed (ignored): {type(exc).__name__}: {exc}")
    finally:
        conn.close()
```

- [ ] **Step 2: 出力を手動確認**

Run:
```bash
TRADER_DB_ENABLED=false RUN_DATE_JST=2026-06-08 uv run python main.py
cat docs/performance_summary.json
```
Expected（DB 無効時）: `{"available": false, "reason": "db_disabled", "generated_at": "..."}`。日次処理は完走。

DB 有効時:
```bash
RUN_DATE_JST=2026-06-08 uv run python main.py
uv run python scripts/settle_outcomes.py --as-of 2026-06-08
RUN_DATE_JST=2026-06-08 uv run python main.py   # 2回目: settle済みを集計
cat docs/performance_summary.json
```
Expected: `"available": true` と `horizons`/`equity_curve`/`db_size_mb` を含む JSON。

- [ ] **Step 3: Commit**

```bash
git add src/dashboard.py
git commit -m "feat(phase0): export performance_summary.json from measurement DB"
```

---

## Task 10: 日次ワークフローへ DB と settle を組み込む

**Files:**
- Modify: `.github/workflows/daily-preopen-core.yml`

- [ ] **Step 1: 予測ステップに DB env を渡す**

`.github/workflows/daily-preopen-core.yml` の `Run prediction script` ステップの `env:` ブロックに 2 行追加:
```yaml
      - name: Run prediction script
        if: ${{ steps.market.outputs.is_open == 'true' }}
        env:
          LINE_CHANNEL_ACCESS_TOKEN: ${{ secrets.LINE_CHANNEL_ACCESS_TOKEN }}
          LINE_USER_ID: ${{ secrets.LINE_USER_ID }}
          RUN_DATE_JST: ${{ steps.market.outputs.today_jst }}
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          TRADER_DB_ENABLED: "true"
        run: uv run python main.py
```

- [ ] **Step 2: settle ステップを追加（main.py の後、commit の前）**

`Run prediction script` ステップと `Commit and push ...` ステップの間に、次のステップを挿入:
```yaml
      - name: Settle realized outcomes
        if: ${{ steps.market.outputs.is_open == 'true' }}
        continue-on-error: true
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          TRADER_DB_ENABLED: "true"
          RUN_DATE_JST: ${{ steps.market.outputs.today_jst }}
        run: uv run python scripts/settle_outcomes.py --as-of "${{ steps.market.outputs.today_jst }}"
```

> commit ステップは既存のまま（`data/ docs/` を commit）。これにより `data/outbox/*.jsonl`（DB 不通時のみ生成）と `docs/performance_summary.json` が永続化される。

- [ ] **Step 3: YAML 構文を確認**

Run:
```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-preopen-core.yml')); print('workflow YAML OK')"
```
Expected: `workflow YAML OK`。

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily-preopen-core.yml
git commit -m "feat(phase0): wire DB write-through and settlement into daily workflow"
```

---

## Task 11: ダッシュボードに実績タイルを追加（最小）

**Files:**
- Modify: `web/src/types/index.ts`
- Create: `web/src/components/PerformanceCard.tsx`
- Modify: `web/src/app/page.tsx`

- [ ] **Step 1: 型を追加**

`web/src/types/index.ts` の末尾に追加:
```typescript
export interface PerformanceHorizon {
  count: number;
  hit_rate: number | null;
  avg_return: number | null;
}

export interface PerformanceSummary {
  available: boolean;
  reason?: string;
  generated_at: string;
  as_of?: string;
  n_long_signals?: number;
  horizons?: Record<string, PerformanceHorizon>;
  equity_curve?: { date: string; equity: number; daily_return: number; n: number }[];
  db_size_mb?: number;
  storage_warning?: boolean;
}
```

- [ ] **Step 2: タイルコンポーネントを作成**

`web/src/components/PerformanceCard.tsx` を新規作成:
```tsx
"use client";

import { useEffect, useState } from "react";
import { PerformanceSummary } from "../types";

export default function PerformanceCard() {
  const [perf, setPerf] = useState<PerformanceSummary | null>(null);

  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${basePath}/performance_summary.json`)
      .then((res) => (res.ok ? res.json() : null))
      .then((json: PerformanceSummary | null) => setPerf(json))
      .catch(() => setPerf(null));
  }, []);

  // Render nothing until the file is available with data (Phase 0 is best-effort).
  if (!perf || !perf.available || !perf.horizons) return null;

  const h5 = perf.horizons["5"];
  const curve = perf.equity_curve || [];
  const cumReturn = curve.length ? curve[curve.length - 1].equity - 1 : null;

  const pct = (v: number | null | undefined) =>
    v == null ? "---" : `${(v * 100).toFixed(1)}%`;

  return (
    <section className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 mb-8">
      <h3 className="text-lg font-bold text-white mb-1">実績トラックレコード（計測中）</h3>
      <p className="text-xs text-slate-400 mb-4">
        実際に出した買い系シグナル（BUY / やや買い）の実現結果です。サンプルが貯まるほど信頼度が上がります。
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">的中率(5日)</div>
          <div className="text-2xl font-bold text-emerald-300">{pct(h5?.hit_rate)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">平均リターン(5日)</div>
          <div className="text-2xl font-bold text-blue-300">{pct(h5?.avg_return)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">累積(1日複利)</div>
          <div className="text-2xl font-bold text-white">{pct(cumReturn)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase mb-1">サンプル数</div>
          <div className="text-2xl font-bold text-slate-200">{perf.n_long_signals ?? 0}</div>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 3: トップページに差し込む**

`web/src/app/page.tsx` の import 群に追加:
```tsx
import PerformanceCard from "../components/PerformanceCard";
```
`<div className="max-w-7xl mx-auto">` の直後、`<h2 ...>監視銘柄一覧</h2>` の**前**に 1 行差し込む:
```tsx
      <div className="max-w-7xl mx-auto">
        <PerformanceCard />
        <h2 className="text-xl font-bold text-slate-100 mb-6 flex items-center gap-2">
```

- [ ] **Step 4: Lint とビルドで確認**

Run:
```bash
npm run lint --prefix web
npm run build --prefix web
```
Expected: lint エラーなし。ビルドが成功する（`performance_summary.json` 不在でもコンポーネントは null を返すため壊れない）。

- [ ] **Step 5: Commit**

```bash
git add web/src/types/index.ts web/src/components/PerformanceCard.tsx web/src/app/page.tsx
git commit -m "feat(phase0): minimal realized-performance tile on dashboard"
```

---

## 受け入れ基準（Phase 0 完了条件・roadmap §5 Phase0 準拠）

実装完了後、以下を満たすこと:

- [ ] 日次実行後、`signals` と `predictions` に当日分が入り、`run_date` と `as_of_date` が区別されている。
- [ ] 翌営業日以降、`signal_outcomes` に 1d 実現結果が確定する（`settle_outcomes.py`）。
- [ ] DB を停止しても日次シグナル・LINE 通知は従来どおり動く（`TRADER_DB_ENABLED=false` で `main.py` 完走を確認）。
- [ ] outbox に溜まったイベントを DB 復旧後にリプレイでき、重複行が発生しない（`event_id` + ON CONFLICT）。
- [ ] ダッシュボードに「実現的中率」と「平均実現リターン」が表示される（`PerformanceCard`）。
- [ ] `docs/state.json` / `docs/dashboard_index.json` の既存契約が壊れない。
- [ ] DB サイズが警告しきい値以上で `performance_summary.json` の `storage_warning` が `true` になる。

**統合スモーク（最後に通すコマンド列、DB 有効時）:**
```bash
uv run python scripts/db_migrate.py
RUN_DATE_JST=2026-06-08 uv run python main.py
uv run python scripts/settle_outcomes.py --as-of 2026-06-08
RUN_DATE_JST=2026-06-08 uv run python main.py
uv run python tests/test_db_records.py        # 14/14 passed
uv run python tests/test_curation_merge.py    # 既存テスト回帰確認
cat docs/performance_summary.json
```

---

## Self-Review（計画作成者によるチェック・実施済み）

**1. Spec coverage（roadmap §5 Phase 0 の 0A〜0F）:**
- 0A provider/bootstrap → 前提セクション＋Task 1（env/dep）✔
- 0B migration → Task 5（`migrations/0001`＋`db_migrate.py`、`schema_migrations`、tickers/legacy seed）✔
- 0C write-through + outbox → Task 6（`src/db.py`）＋Task 7（`main.py`）✔
- 0D outcome settlement → Task 3（`compute_outcome`）＋Task 8（`settle_outcomes.py`、long/avoid 分離、benchmark NULL）✔
- 0E dashboard export → Task 4（`summarize_performance`）＋Task 9（`performance_summary.json`、DB容量警告）＋Task 11（Web タイル）✔
- 0F workflow → Task 10（env＋settle、continue-on-error、commit は既存）✔

**2. Placeholder scan:** "TBD"/"後で"/"適宜" などの未確定記述なし。各コードステップは完全なコードを含む。✔

**3. Type/name consistency:** `make_event_id` / `signal_to_prediction_row` / `signal_to_signal_row` / `compute_outcome` / `summarize_performance` / `db_enabled` / `connect` / `record_run` / `flush_outbox` / `fetch_unsettled`（`missing_horizons` キー）/ `upsert_outcome` / `fetch_outcome_rows` / `db_size_mb` / `LEGACY_MODEL_VERSION` / `OUTCOME_HORIZONS` を定義タスクと利用タスクで突合し一致を確認。`performance_summary.json` の形（`horizons["5"]` 等）は Task 9 出力と Task 11 消費で一致。✔

**既知の前提・後続フェーズ送り（意図的スコープ外）:**
- TOPIX ベンチマーク（`benchmark_ret`/`excess_ret`）は Phase 1（NULL 固定）。
- `signals.prediction_id` は Phase 0 では NULL（predictions と signals は run_date+ticker で join 可能）。
- 較正・トリプルバリア・モデル永続化は Phase 1。
- 実 DB を要する関数は standalone ユニットテスト対象外（手動スモークで検証）。純粋ロジックのみ TDD。
