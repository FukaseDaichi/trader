# Settlement-Side Same-Basis TOPIX Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill `signal_outcomes.benchmark_ret` / `excess_ret` for contract-v2 rows using the same-basis TOPIX benchmark (`topix_open[entry_date]` → `topix[eval_date]` close), both at settle time and via a rewritten `--refill-benchmark` backfill, so `/performance` can finally show TOPIX comparison.

**Architecture:** A pure function in `src/db_records.py` computes the open→close benchmark return from two date-keyed dicts. `scripts/settle_outcomes.py` loads `topix_open`+`topix` from the macro panel once per run, computes the benchmark inline during settlement, and reuses the same function in a v2-targeted refill. `src/performance.py` declares the achieved basis in its exported contract when coverage is complete. The legacy v1 close-to-close refill path is deleted.

**Tech Stack:** Python 3.13 via `uv`, pandas, psycopg (DB layer touched only in two helpers), plain-script tests (no pytest).

## Global Constraints

- Spec: `plans/2026-08-10-settlement-same-basis-benchmark-design.md`
- The daily settlement run must never break: macro panel missing/corrupt → benchmark stays NULL, settlement continues (existing degradation pattern with a printed reason).
- Benchmark contract: `benchmark_ret = topix_close[eval_date] / topix_open[entry_date] − 1`, gross (costs are deducted export-side for both strategy and benchmark). `excess_ret = realized_ret − benchmark_ret`.
- Dates match exactly; never forward-fill `topix_open` (contract in `specification_document/05_cross_cutting.md`).
- **Both benchmark dicts are built only from panel rows where `topix_open` AND `topix` are present and positive.** `src/macro.py:353` forward-fills close columns but deliberately never fills opens, so a date carrying a genuine `topix_open` is provably a date the instrument actually traded — and its close from the same source row is genuine too. Keying both dicts off that rule is what stops a stale forward-filled close from becoming an exit price. Same rule as `src/portfolio_backtest.py::_prepare_topix` (line 167). Do NOT build the close dict from the `topix` column alone.
- **NEVER change `execution.BENCHMARK_BASIS`.** Verified empirically: that constant flows through `execution_contract_metadata()` into `model_store.build_phase1_gate_contract()`, whose `gate_contract_sha256` is compared strictly against the stored `data/models/active_model.json`. Changing the string moves the active model's hash from `0f5300ca…e2f0` to `bbb3f965…c218ac`, so `compare_phase1_gate_contract` rejects the saved bundle and every ticker falls back to an ephemeral candidate (different features, different thresholds) until the Saturday retrain — and `drift_check.py` goes `available:false` silently. Instead follow the established override pattern: the base metadata keeps saying `unavailable_same_basis`, and each consumer that actually has a benchmark overrides `benchmark_basis` in its own exported dict (exactly what `src/portfolio_backtest.py:358-361` already does).
- Phase 2 shadow output must stay byte-for-byte identical. `src/portfolio_backtest.py` is NOT modified by this plan.
- Tests are plain Python scripts run with `uv run python tests/test_<name>.py` — keep the existing `ALL_TESTS` + `main()` pattern.
- Plan/spec docs live in `plans/` (repo convention per `AGENTS.md`), NOT under `docs/` — `daily-publish-dashboard.yml` rsyncs `web/out/` over `docs/` with `--delete` and has no `superpowers` exclude, so anything left under `docs/` is destroyed on the next publish.
- Do not edit `tickers.yml` / `curation_pool.yml`.

---

### Task 1: Same-basis `compute_benchmark_ret` pure function

**Files:**
- Modify: `src/db_records.py:268-277` (replace the close-to-close function)
- Test: `tests/test_db_records.py` (replace the existing `compute_benchmark_ret` test near line 530)

**Interfaces:**
- Produces: `compute_benchmark_ret(open_by_date: dict, close_by_date: dict, entry_date: str, eval_date: str) -> float | None` — consumed by Tasks 2 and 3.

- [ ] **Step 1: Rewrite the test block for the new signature**

In `tests/test_db_records.py`, find the test exercising `compute_benchmark_ret` (search for `compute_benchmark_ret(topix,` near line 534). Replace the whole test function with:

```python
def test_compute_benchmark_ret_same_basis_open_to_close():
    opens = {"2026-06-09": 2900.0, "2026-06-16": 2910.0}
    closes = {"2026-06-09": 2905.0, "2026-06-16": 2958.0}

    r = compute_benchmark_ret(opens, closes, "2026-06-09", "2026-06-16")
    assert r is not None
    assert abs(r - (2958.0 / 2900.0 - 1.0)) < 1e-12

    # eval close missing -> None
    assert compute_benchmark_ret(opens, {"2026-06-09": 2905.0}, "2026-06-09", "2026-06-16") is None
    # entry open missing -> None
    assert compute_benchmark_ret({}, closes, "2026-06-09", "2026-06-16") is None
    # zero / non-positive levels -> None (data error, not a 0% return)
    assert compute_benchmark_ret({"a": 0.0}, {"b": 2958.0}, "a", "b") is None
    assert compute_benchmark_ret({"a": 2900.0}, {"b": -1.0}, "a", "b") is None
    # non-finite levels -> None
    assert compute_benchmark_ret({"a": float("nan")}, {"b": 2958.0}, "a", "b") is None
```

Update the entry in that file's `ALL_TESTS` list to the new function name (the old name is registered there).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python tests/test_db_records.py`
Expected: the new test FAILS (old function takes 3 args and computes close-to-close); other tests still pass.

- [ ] **Step 3: Replace the implementation in `src/db_records.py`**

Replace the existing `compute_benchmark_ret` (lines 268-277) with:

```python
def compute_benchmark_ret(
    open_by_date: dict, close_by_date: dict, entry_date: str, eval_date: str
) -> float | None:
    """Same-basis TOPIX return: entry-session open to eval-session close.

    Mirrors contract v2 using the macro panel's ``topix_open`` / ``topix``
    levels.  Returns None when either level is missing, non-finite, or
    non-positive — settlement keeps going with NULL and a later
    --refill-benchmark run self-heals.
    """
    entry = open_by_date.get(str(entry_date))
    exit_ = close_by_date.get(str(eval_date))
    if entry is None or exit_ is None:
        return None
    entry = float(entry)
    exit_ = float(exit_)
    if not (math.isfinite(entry) and math.isfinite(exit_)):
        return None
    if entry <= 0 or exit_ <= 0:
        return None
    return exit_ / entry - 1.0
```

Add `import math` to the module imports if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python tests/test_db_records.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/db_records.py tests/test_db_records.py
git commit -m "Replace close-to-close benchmark helper with same-basis open-to-close"
```

---

### Task 2: Settlement + refill in one commit

Tasks 2 and 3 of the earlier draft are deliberately merged. Splitting them creates an intermediate commit whose `--refill-benchmark` still filters on `close_to_close_v1` while computing v2-basis numbers — and the daily workflows run `--refill-benchmark` every trading morning (`daily-preopen-core.yml:96`, `daily-preopen-retry.yml:117`), so that intermediate state would write permanently-wrong rows into the production DB that no later run can repair (they stop matching `benchmark_ret IS NULL`).

**Files:**
- Modify: `src/execution.py` (ADD a constant; do not change `BENCHMARK_BASIS`)
- Modify: `scripts/settle_outcomes.py` (docstring, loader, `_settle_for_ticker`, refill, `main()`)
- Modify: `src/db.py:969-1006` (`fetch_outcomes_missing_benchmark`, `update_outcome_benchmark`)
- Test: `tests/test_settle_outcomes.py`, `tests/test_reliability_db.py:471-478`

**Interfaces:**
- Consumes: `compute_benchmark_ret(open_by_date, close_by_date, entry_date, eval_date)` from Task 1.
- Produces: `SAME_BASIS_BENCHMARK` in `src/execution.py`; `_load_topix_by_date() -> tuple[dict[str, float], dict[str, float]]`; `_settle_for_ticker(conn, ticker, signals, topix_open_by_date, topix_close_by_date)`; `_refill_v2_benchmarks(conn, topix_open_by_date, topix_close_by_date) -> tuple[int, int]`; `db.update_outcome_benchmark(conn, signal_id, horizon_days, benchmark_ret, excess_ret, benchmark_basis)`. Task 3 consumes none of these (docs only).

- [ ] **Step 1: Write the failing tests**

In `tests/test_settle_outcomes.py`:

1. Extend the imports:

```python
from src.execution import (  # noqa: E402
    BENCHMARK_BASIS,
    EXECUTION_CONTRACT_VERSION,
    SAME_BASIS_BENCHMARK,
)
```

2. In `test_settlement_uses_same_next_open_window_as_labels`, the 4th positional argument becomes two dicts. Real panels always key both dicts identically, so the fixture does too:

```python
        count = settle_outcomes._settle_for_ticker(
            object(),
            "7011.JP",
            [
                {
                    "signal_id": 7,
                    "as_of_date": "2026-01-09",
                    "action": "BUY",
                    "missing_horizons": [1, 2],
                }
            ],
            {"2026-01-13": 2000.0, "2026-01-14": 2030.0},
            {"2026-01-13": 2010.0, "2026-01-14": 2040.0},
        )
```

Replace the three trailing benchmark assertions with:

```python
    # H=1: same-basis TOPIX open(entry 01-13) -> close(eval 01-13).
    assert abs(one_day["benchmark_ret"] - (2010.0 / 2000.0 - 1.0)) < 1e-12
    assert abs(
        one_day["excess_ret"] - ((80.0 / 120.0 - 1.0) - (2010.0 / 2000.0 - 1.0))
    ) < 1e-12
    assert one_day["benchmark_basis"] == SAME_BASIS_BENCHMARK
    assert SAME_BASIS_BENCHMARK == "next_session_open_to_horizon_session_close"
    # H=2: entry open 01-13 -> eval close 01-14.
    two_day = captured[1][2]
    assert abs(two_day["benchmark_ret"] - (2040.0 / 2000.0 - 1.0)) < 1e-12
    assert two_day["benchmark_basis"] == SAME_BASIS_BENCHMARK
```

3. In `test_settlement_falls_back_to_archived_inactive_ticker_data`, replace the 4th argument `{}` with `{}, {}` and append:

```python
    assert captured[0][2]["benchmark_ret"] is None
    assert captured[0][2]["excess_ret"] is None
    assert captured[0][2]["benchmark_basis"] == BENCHMARK_BASIS
    assert BENCHMARK_BASIS == "unavailable_same_basis"
```

4. Add a loader test locking the forward-fill guard, and a refill test. Register both in `ALL_TESTS`:

```python
def test_loader_excludes_dates_whose_open_is_missing():
    # 2026-01-14 has a forward-filled close but no genuine open -> excluded
    # from BOTH dicts, so it can never supply an entry or an exit price.
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-13", "2026-01-14", "2026-01-15"]),
            "topix_open": [2000.0, float("nan"), 2050.0],
            "topix": [2010.0, 2010.0, 2070.0],
        }
    )
    original_read = settle_outcomes.pd.read_parquet
    try:
        settle_outcomes.pd.read_parquet = lambda path: panel.copy()
        opens, closes = settle_outcomes._load_topix_by_date()
    finally:
        settle_outcomes.pd.read_parquet = original_read

    assert sorted(opens) == ["2026-01-13", "2026-01-15"]
    assert sorted(closes) == ["2026-01-13", "2026-01-15"]
    assert opens["2026-01-13"] == 2000.0
    assert closes["2026-01-15"] == 2070.0


def test_loader_degrades_when_panel_unreadable():
    def _boom(path):
        raise ValueError("corrupt parquet")

    original_read = settle_outcomes.pd.read_parquet
    try:
        settle_outcomes.pd.read_parquet = _boom
        opens, closes = settle_outcomes._load_topix_by_date()
    finally:
        settle_outcomes.pd.read_parquet = original_read

    assert opens == {} and closes == {}


def test_refill_targets_v2_null_rows_and_updates_basis():
    updates = []
    original_fetch = settle_outcomes.db.fetch_outcomes_missing_benchmark
    original_update = settle_outcomes.db.update_outcome_benchmark
    try:
        settle_outcomes.db.fetch_outcomes_missing_benchmark = lambda conn: [
            {
                "signal_id": 1,
                "horizon_days": 1,
                "entry_date": "2026-01-13",
                "eval_date": "2026-01-13",
                "realized_ret": 0.02,
            },
            {   # panel has no data for this window -> stays NULL, no update call
                "signal_id": 2,
                "horizon_days": 5,
                "entry_date": "2026-02-02",
                "eval_date": "2026-02-06",
                "realized_ret": -0.01,
            },
        ]
        settle_outcomes.db.update_outcome_benchmark = (
            lambda conn, signal_id, horizon_days, benchmark_ret, excess_ret,
            benchmark_basis: updates.append(
                (signal_id, horizon_days, benchmark_ret, excess_ret, benchmark_basis)
            )
        )
        refilled, scanned = settle_outcomes._refill_v2_benchmarks(
            object(),
            {"2026-01-13": 2000.0},
            {"2026-01-13": 2010.0},
        )
    finally:
        settle_outcomes.db.fetch_outcomes_missing_benchmark = original_fetch
        settle_outcomes.db.update_outcome_benchmark = original_update

    assert (refilled, scanned) == (1, 2)
    assert len(updates) == 1
    signal_id, horizon, benchmark_ret, excess_ret, basis = updates[0]
    assert (signal_id, horizon) == (1, 1)
    assert abs(benchmark_ret - (2010.0 / 2000.0 - 1.0)) < 1e-12
    assert abs(excess_ret - (0.02 - (2010.0 / 2000.0 - 1.0))) < 1e-12
    assert basis == SAME_BASIS_BENCHMARK
```

5. In `tests/test_reliability_db.py`, invert `test_benchmark_refill_query_excludes_current_contract` (lines 471-478) — the refill now targets v2 and must NOT touch legacy rows:

```python
def test_benchmark_refill_query_targets_current_contract():
    cursor = FakeCursor([])
    rows = db.fetch_outcomes_missing_benchmark(FakeConn(cursor))
    sql, params = cursor.executed[0]
    assert rows == []
    assert "contract_version = %s" in sql
    assert params == (EXECUTION_CONTRACT_VERSION,)
    assert params != (LEGACY_EXECUTION_CONTRACT_VERSION,)
```

Update its `ALL_TESTS` entry, and make sure `EXECUTION_CONTRACT_VERSION` is imported in that file (`LEGACY_EXECUTION_CONTRACT_VERSION` already is; keep it for the negative assertion).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python tests/test_settle_outcomes.py; uv run python tests/test_reliability_db.py`
Expected: settle tests ERROR on importing `SAME_BASIS_BENCHMARK`; the reliability test FAILS on the params assertion.

- [ ] **Step 3: Add the derived constant in `src/execution.py`**

Leave line 29 (`BENCHMARK_BASIS = "unavailable_same_basis"`) EXACTLY as is — see Global Constraints. Add below it:

```python
# The basis actually achieved when a same-basis benchmark IS computable.
# Deliberately NOT the default in execution_contract_metadata(): that dict is
# hashed into the Phase 1 gate contract, so changing it invalidates every saved
# model bundle.  Consumers that hold a real benchmark override the exported
# "benchmark_basis" with this value (see src/portfolio_backtest.py).
SAME_BASIS_BENCHMARK = f"{ENTRY_PRICE_BASIS}_to_{EXIT_PRICE_BASIS}"
```

Place it after `EXIT_PRICE_BASIS` is defined so the f-string resolves.

- [ ] **Step 4: Update `scripts/settle_outcomes.py`**

1. Replace the module docstring paragraph about the macro panel (lines 17-23) with:

```
The macro panel carries a same-basis TOPIX-proxy open (topix_open, same
instrument and adjustment basis as the topix close column). Settlement
computes the contract-v2 same-basis benchmark inline
(topix_open[entry_date] -> topix[eval_date] close, gross), and
--refill-benchmark idempotently backfills v2 rows whose benchmark is still
NULL (e.g. the panel lagged on settle day). Rows settle with NULL benchmark
and benchmark_basis=unavailable_same_basis when either level is missing.
```

2. Change the `src.execution` import to bring in `BENCHMARK_BASIS` and `SAME_BASIS_BENCHMARK`, and DROP `LEGACY_EXECUTION_CONTRACT_VERSION` (its only use disappears in sub-step 5 of this same commit).

3. Replace `_load_topix_by_date` with:

```python
def _load_topix_by_date() -> tuple[dict[str, float], dict[str, float]]:
    """Return ({date: topix_open}, {date: topix_close}) from the macro panel.

    Both dicts are keyed only on dates where topix_open AND topix are present
    and positive.  src/macro.py forward-fills close columns but never opens, so
    a genuine open proves the instrument traded that date and its close from the
    same source row is genuine too; keying both sides off that rule keeps a
    stale forward-filled close from becoming an exit price.  Same rule as
    src/portfolio_backtest.py::_prepare_topix, so settlement and the Phase 2
    backtest measure the identical basis.

    Never raises: any problem degrades to empty dicts (benchmark stays NULL).
    """
    macro_path = ROOT / "data" / "macro" / "macro_panel.parquet"
    try:
        df = pd.read_parquet(macro_path)
        required = {"date", "topix_open", "topix"}
        if not required.issubset(df.columns):
            print(
                "macro_panel.parquet lacks same-basis benchmark columns "
                f"{sorted(required.difference(df.columns))}; benchmark stays NULL"
            )
            return {}, {}
        tp = df[["date", "topix_open", "topix"]].copy()
        tp["date"] = pd.to_datetime(tp["date"], errors="coerce")
        tp["topix_open"] = pd.to_numeric(tp["topix_open"], errors="coerce")
        tp["topix"] = pd.to_numeric(tp["topix"], errors="coerce")
        tp = tp.dropna(subset=["date", "topix_open", "topix"])
        tp = tp[(tp["topix_open"] > 0) & (tp["topix"] > 0)]
        tp = tp.sort_values("date").drop_duplicates(subset="date", keep="last")
        if tp.empty:
            print("macro_panel.parquet has no same-basis TOPIX rows; benchmark stays NULL")
            return {}, {}
        keys = tp["date"].dt.strftime("%Y-%m-%d")
        opens = {d: float(v) for d, v in zip(keys, tp["topix_open"])}
        closes = {d: float(v) for d, v in zip(keys, tp["topix"])}
        return opens, closes
    except FileNotFoundError:
        print("macro_panel.parquet not found; TOPIX benchmark will stay NULL")
        return {}, {}
    except Exception as exc:  # noqa: BLE001
        print(f"macro_panel benchmark load error (benchmark stays NULL): {exc}")
        return {}, {}
```

4. Change `_settle_for_ticker`'s signature to `(conn, ticker: str, signals: list[dict], topix_open_by_date: dict, topix_close_by_date: dict)` and replace the `benchmark_ret = None` block (lines 119-126) with:

```python
            benchmark_ret = compute_benchmark_ret(
                topix_open_by_date,
                topix_close_by_date,
                window.entry_date,
                window.exit_date,
            )
            excess_ret = (
                payload["realized_ret"] - benchmark_ret
                if benchmark_ret is not None
                else None
            )
            benchmark_basis = (
                SAME_BASIS_BENCHMARK if benchmark_ret is not None else BENCHMARK_BASIS
            )
```

In the upsert payload, change `"benchmark_basis": BENCHMARK_BASIS` (line 143) to `"benchmark_basis": benchmark_basis`.

5. Add `_refill_v2_benchmarks` above `main()` and delete the old inline refill block entirely:

```python
def _refill_v2_benchmarks(
    conn, topix_open_by_date: dict, topix_close_by_date: dict
) -> tuple[int, int]:
    """Backfill same-basis benchmark for v2 rows settled while data lagged.

    Idempotent: only rows with benchmark_ret still NULL are scanned, and the
    computation is deterministic.  Returns (refilled, scanned).
    """
    missing = db.fetch_outcomes_missing_benchmark(conn)
    refilled = 0
    for row in missing:
        benchmark_ret = compute_benchmark_ret(
            topix_open_by_date,
            topix_close_by_date,
            row["entry_date"],
            row["eval_date"],
        )
        if benchmark_ret is None:
            continue
        db.update_outcome_benchmark(
            conn,
            row["signal_id"],
            row["horizon_days"],
            benchmark_ret,
            row["realized_ret"] - benchmark_ret,
            SAME_BASIS_BENCHMARK,
        )
        refilled += 1
    return refilled, len(missing)
```

6. In `main()`, load the panel unconditionally and rewrite both call sites:

```python
    topix_open_by_date, topix_close_by_date = _load_topix_by_date()
```

```python
            total += _settle_for_ticker(
                conn, ticker, sigs, topix_open_by_date, topix_close_by_date
            )
```

Replace the whole `if args.refill_benchmark and not topix_by_date:` … final `print(f"Refill benchmark: …")` block with:

```python
        if args.refill_benchmark:
            if not topix_open_by_date or not topix_close_by_date:
                print("Refill benchmark: no same-basis TOPIX data; skipping.")
            else:
                refilled, scanned = _refill_v2_benchmarks(
                    conn, topix_open_by_date, topix_close_by_date
                )
                print(f"Refill benchmark: updated {refilled}/{scanned} v2 rows.")
```

7. Update the `--refill-benchmark` argparse help to `"Backfill same-basis benchmark_ret/excess_ret for v2 rows still NULL."`.

- [ ] **Step 5: Update the two db helpers in `src/db.py`**

Replace `fetch_outcomes_missing_benchmark` (lines 969-989):

```python
def fetch_outcomes_missing_benchmark(conn) -> list[dict]:
    """Contract-v2 settled rows whose same-basis benchmark is still NULL."""
    from psycopg.rows import dict_row
    from .execution import EXECUTION_CONTRACT_VERSION

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT signal_id, horizon_days, entry_date, eval_date, realized_ret"
            " FROM signal_outcomes"
            " WHERE benchmark_ret IS NULL AND realized_ret IS NOT NULL"
            "   AND contract_version = %s",
            (EXECUTION_CONTRACT_VERSION,),
        )
        rows = cur.fetchall()
    for r in rows:
        if r.get("entry_date") is not None:
            r["entry_date"] = str(r["entry_date"])
        if r.get("eval_date") is not None:
            r["eval_date"] = str(r["eval_date"])
    return rows
```

Replace `update_outcome_benchmark` (lines 992-1006):

```python
def update_outcome_benchmark(
    conn,
    signal_id: int,
    horizon_days: int,
    benchmark_ret: float | None,
    excess_ret: float | None,
    benchmark_basis: str,
) -> None:
    """Idempotently write benchmark_ret/excess_ret/basis for one settled row."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE signal_outcomes"
            " SET benchmark_ret=%s, excess_ret=%s, benchmark_basis=%s"
            " WHERE signal_id=%s AND horizon_days=%s",
            (benchmark_ret, excess_ret, benchmark_basis, signal_id, horizon_days),
        )
    conn.commit()
```

`upsert_outcome`'s default (`src/db.py:893`) stays `BENCHMARK_BASIS` — it remains the fail-closed `unavailable_same_basis`, which is correct for any caller that does not supply one.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python tests/test_settle_outcomes.py && uv run python tests/test_db_records.py && uv run python tests/test_reliability_db.py`
Expected: all PASS.

- [ ] **Step 7: Verify the Phase 1 gate contract is untouched**

This is the regression that motivated the design. Run:

```bash
uv run python -c "
import json
from src import model_store
d = json.load(open('data/models/active_model.json'))
gc = d['gate_contract']
rebuilt = model_store.build_phase1_gate_contract(gc['gate_config'], d['artifact_contract'])
assert rebuilt['gate_contract_sha256'] == gc['gate_contract_sha256'], 'GATE CONTRACT BROKEN'
print('gate contract intact:', gc['gate_contract_sha256'][:16])
"
```

Expected: `gate contract intact: 0f5300ca82401aeb`. If this fails, the active model would be rejected on the next daily run — stop and revert the `src/execution.py` change.

- [ ] **Step 8: Commit**

```bash
git add src/execution.py scripts/settle_outcomes.py src/db.py tests/test_settle_outcomes.py tests/test_reliability_db.py
git commit -m "Settle and backfill contract-v2 outcomes with same-basis TOPIX benchmark"
```

---

### Task 3: Declare the achieved basis in the performance export

Without this the numbers land in the DB but `/performance` still hides the comparison: `PerformanceDetail.tsx:62-92` treats the exported `execution_contract.benchmark_basis` as authoritative and hides the TOPIX column whenever it starts with `unavailable`.

**Files:**
- Modify: `src/performance.py:541-546` (and the sibling contract dict feeding `signal_outcomes_recent.json`)
- Test: `tests/test_performance.py`

**Interfaces:**
- Consumes: `SAME_BASIS_BENCHMARK` from Task 2.
- Produces: nothing new; only the exported `execution_contract["benchmark_basis"]` value changes when coverage is complete.

- [ ] **Step 1: Write the failing test**

In `tests/test_performance.py`, add (and register in `ALL_TESTS`):

```python
def test_export_declares_same_basis_only_when_coverage_is_complete():
    from src.execution import BENCHMARK_BASIS, SAME_BASIS_BENCHMARK

    # Every cohort has a benchmark -> the export declares the achieved basis.
    full = build_detail_result(all_rows_have_benchmark=True)
    assert full["execution_contract"]["benchmark_basis"] == SAME_BASIS_BENCHMARK

    # A single missing benchmark -> stays fail-closed.
    partial = build_detail_result(all_rows_have_benchmark=False)
    assert partial["execution_contract"]["benchmark_basis"] == BENCHMARK_BASIS
```

Build `full` / `partial` with the file's existing row-fixture helper (the one around line 70 that already takes a `benchmark_ret` and sets `benchmark_basis`), passing a benchmark on every row versus on all-but-one. Follow how the neighbouring tests in this file construct their inputs — reuse their call shape rather than inventing a new one, and name the local helper accordingly.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python tests/test_performance.py`
Expected: the new test FAILS — the export currently always reports `unavailable_same_basis`.

- [ ] **Step 3: Override the basis when coverage is complete**

In `src/performance.py`, after the existing lines 541-546 that build `execution_contract`, add the override driven by the coverage the function already computes (`benchmark_coverage["available"]` is True only when every selected cohort has a same-basis benchmark):

```python
    if benchmark_coverage.get("available"):
        execution_contract["benchmark_basis"] = SAME_BASIS_BENCHMARK
```

Import `SAME_BASIS_BENCHMARK` alongside the existing `execution` imports at line 22. Apply the same override to the contract dict exported with `signal_outcomes_recent.json` — locate it by searching this module for the other `execution_contract_metadata(` call and gate it on that export's own completeness check (every returned row carrying a non-null `benchmark_ret`), never on a bare row count.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python tests/test_performance.py && uv run python tests/test_reliability_db.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/performance.py tests/test_performance.py
git commit -m "Declare same-basis benchmark in performance exports when coverage is complete"
```

---

### Task 4: Docs, workflow comments, full sweep, safety review

**Files:**
- Modify: `specification_document/01_backend_python.md:164`
- Modify: `specification_document/03_cicd_workflows.md:32,36`
- Modify: `specification_document/04_scripts.md:33`
- Modify: `specification_document/05_cross_cutting.md:74,159`
- Modify: `specification_document/06_issues_and_backlog.md` (backlog table row)
- Modify: `.github/workflows/daily-preopen-core.yml:93-95` and `.github/workflows/daily-preopen-retry.yml:115-116` (stale comments)
- Modify: `AGENTS.md:29-31` (Phase 3 description claims benchmark_ret/excess_ret stay NULL)

**Interfaces:** none (documentation + verification only).

- [ ] **Step 1: Update the specification documents**

Each file states some variant of "v2のTOPIX同基準benchmarkは作れないためNULL". Rewrite in each file's own Japanese voice:

- `01_backend_python.md:164`: 決済はマクロパネルの`topix_open`（entry日寄付き）→`topix`（eval日終値）で同一basisの`benchmark_ret`/`excess_ret`をグロス計算し、成功行だけ`benchmark_basis=next_session_open_to_horizon_session_close`を持つ。欠損時はNULL＋`unavailable_same_basis`で縮退し、`--refill-benchmark`がv2のNULL行を冪等補填する。旧v1のclose-to-close補填経路は削除。
- `03_cicd_workflows.md:32,36`: 決済時に同一basis TOPIX benchmarkをインライン計算し、`--refill-benchmark`はv2のNULL行を冪等補填する。
- `04_scripts.md:33`: 同趣旨。`--restate-execution-contract`は不変であることも明記。
- `05_cross_cutting.md:74`: 「v2のTOPIXは同じentry openがないためbenchmarkをNULL」を、同一basis open→close・日付完全一致・前日埋めなし・欠損はNULLのまま、へ差し替え。`:159`: `benchmark: null`は実データ欠損時のみで、coverage理由付きで報告される旨へ更新。
- `06_issues_and_backlog.md`: 「着手条件が既に揃っているもの」表から「決済側の同一basis benchmark」行を削除。

Also note in `01_backend_python.md` (near the execution-contract description) that `execution_contract_metadata()`'s `benchmark_basis` is intentionally the fail-closed value because the dict is hashed into the Phase 1 gate contract, and that consumers holding a real benchmark override it with `SAME_BASIS_BENCHMARK`.

- [ ] **Step 2: Fix the stale workflow comments**

In `daily-preopen-core.yml:93-95` and the matching comment in `daily-preopen-retry.yml:115-116`, replace the "legacy v1 rows only / contract-v2 benchmark fields stay NULL" wording with: `--refill-benchmark` は v2 の同一basis benchmark 未設定行を冪等補填する。Update `AGENTS.md:29-31` the same way (it currently says `benchmark_ret`/`excess_ret` stay NULL because the settlement path does not consume the macro panel's TOPIX open).

- [ ] **Step 3: Full test sweep**

```bash
for t in tests/test_*.py; do uv run python "$t" > /dev/null 2>&1 || echo "FAIL: $t"; done; echo done
```

Expected: only `done`. Investigate and fix any FAIL line individually before proceeding.

- [ ] **Step 4: Confirm Phase 2 shadow output is byte-for-byte unchanged**

`src/portfolio_backtest.py` is not modified, but `src/execution.py` is. Verify nothing moved:

```bash
git stash list > /dev/null; python3 -c "
import json
d = json.load(open('docs/portfolio_backtest.json'))
print('benchmark_basis:', d['execution_contract']['benchmark_basis'])
print('coverage:', d['benchmark_coverage']['coverage_ratio'], d['benchmark_coverage']['available'])
"
```

Expected: `next_session_open_to_horizon_session_close` and `1.0 True` — unchanged from before this work, because Phase 2 already overrode the label itself.

- [ ] **Step 5: Dispatch the pipeline-safety-reviewer agent**

Dispatch `pipeline-safety-reviewer` over the implemented diff (`scripts/settle_outcomes.py`, `src/db.py`, `src/db_records.py`, `src/execution.py`, `src/performance.py`). Address any invariant findings, with graceful degradation of the daily run and the Phase 1 gate-contract hash as the two highest-risk areas.

- [ ] **Step 6: Commit**

```bash
git add specification_document/ .github/workflows/ AGENTS.md
git commit -m "Document settlement-side same-basis TOPIX benchmark"
```
