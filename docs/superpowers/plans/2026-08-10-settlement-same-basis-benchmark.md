# Settlement-Side Same-Basis TOPIX Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill `signal_outcomes.benchmark_ret` / `excess_ret` for contract-v2 rows using the same-basis TOPIX benchmark (`topix_open[entry_date]` → `topix[eval_date]` close), both at settle time and via a rewritten `--refill-benchmark` backfill.

**Architecture:** A pure function in `src/db_records.py` computes the open→close benchmark return from two date-keyed dicts. `scripts/settle_outcomes.py` loads `topix_open`+`topix` from the macro panel once per run, computes the benchmark inline during settlement, and reuses the same function in a v2-targeted refill. `src/db.py` gains a basis-aware benchmark update. The legacy v1 close-to-close refill path is deleted.

**Tech Stack:** Python 3.13 via `uv`, pandas, psycopg (DB layer untouched except two helpers), plain-script tests (no pytest).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-10-settlement-same-basis-benchmark-design.md`
- The daily settlement run must never break: macro panel missing/corrupt → benchmark stays NULL, settlement continues (existing degradation pattern with a printed reason).
- Benchmark contract: `benchmark_ret = topix_close[eval_date] / topix_open[entry_date] − 1`, gross (costs are deducted export-side for both strategy and benchmark). `excess_ret = realized_ret − benchmark_ret`.
- Dates match exactly; never forward-fill `topix_open` (contract in `specification_document/05_cross_cutting.md`).
- **Both benchmark dicts are built only from panel rows where `topix_open` AND `topix` are present and positive.** `src/macro.py:353` forward-fills close columns but deliberately never fills opens, so a date carrying a genuine `topix_open` is provably a date the instrument actually traded — and its close from the same source row is genuine too. Keying both dicts off that rule is what stops a stale forward-filled close from becoming an exit price. This mirrors `src/portfolio_backtest.py::_topix_panel` (line 182) exactly, so settlement and the Phase 2 backtest cannot drift apart. Do NOT build the close dict from the `topix` column alone.
- New basis label: `"next_session_open_to_horizon_session_close"` (identical string to the Phase 2 backtest's `required_basis`). Degraded label stays `"unavailable_same_basis"`.
- Per-row `benchmark_basis` carries the same-basis label ONLY when a value was computed; otherwise `"unavailable_same_basis"` (the frontend blocks the whole comparison table if any row has an `unavailable*` basis, so this is deliberate fail-closed behavior).
- Tests are plain Python scripts run with `uv run python tests/test_<name>.py` — keep the existing `ALL_TESTS` + `main()` pattern.
- Do not edit `tickers.yml` / `curation_pool.yml` (unrelated, but a repo hard rule).

---

### Task 1: Same-basis `compute_benchmark_ret` pure function

**Files:**
- Modify: `src/db_records.py:268-277` (replace the close-to-close function)
- Test: `tests/test_db_records.py` (replace the 4 existing `compute_benchmark_ret` assertions around lines 530-548)

**Interfaces:**
- Produces: `compute_benchmark_ret(open_by_date: dict, close_by_date: dict, entry_date: str, eval_date: str) -> float | None` — consumed by Task 2 (inline settle) and Task 3 (refill).

- [ ] **Step 1: Rewrite the test block for the new signature**

In `tests/test_db_records.py`, find the existing test function that exercises `compute_benchmark_ret` (search for `compute_benchmark_ret(topix,` near line 534). Replace the whole test function body with:

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

Keep the function registered in the file's `ALL_TESTS` list under its (possibly renamed) name — check how the old test was named and update the list entry.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python tests/test_db_records.py`
Expected: the new test FAILS (old function takes 3 args / computes close-to-close); all other tests still pass.

- [ ] **Step 3: Replace the implementation in `src/db_records.py`**

Replace the existing `compute_benchmark_ret` (lines 268-277) with:

```python
def compute_benchmark_ret(
    open_by_date: dict, close_by_date: dict, entry_date: str, eval_date: str
) -> float | None:
    """Same-basis TOPIX return: entry-session open to eval-session close.

    Mirrors contract v2 (next_session_open_to_horizon_session_close) using the
    macro panel's ``topix_open`` / ``topix`` levels. Returns None when either
    level is missing, non-finite, or non-positive — settlement keeps going
    with NULL and a later --refill-benchmark run self-heals.
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

Add `import math` to the module's imports if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python tests/test_db_records.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/db_records.py tests/test_db_records.py
git commit -m "Replace close-to-close benchmark helper with same-basis open-to-close"
```

---

### Task 2: Contract label + inline benchmark at settle time

**Files:**
- Modify: `src/execution.py:29` (constants)
- Modify: `scripts/settle_outcomes.py` (docstring, loader, `_settle_for_ticker`, `main()` loader call)
- Test: `tests/test_settle_outcomes.py`

**Interfaces:**
- Consumes: `compute_benchmark_ret(open_by_date, close_by_date, entry_date, eval_date)` from Task 1.
- Produces: `BENCHMARK_BASIS = "next_session_open_to_horizon_session_close"` and `BENCHMARK_BASIS_UNAVAILABLE = "unavailable_same_basis"` in `src/execution.py`; `_load_topix_by_date() -> tuple[dict[str, float], dict[str, float]]` (opens, closes) and `_settle_for_ticker(conn, ticker, signals, topix_open_by_date, topix_close_by_date)` in `scripts/settle_outcomes.py`. Task 3 reuses all of these.

- [ ] **Step 1: Update the existing settle test and add a missing-data test**

In `tests/test_settle_outcomes.py`:

1. Change the import line to also pull the labels:

```python
from src.execution import (  # noqa: E402
    BENCHMARK_BASIS,
    BENCHMARK_BASIS_UNAVAILABLE,
    EXECUTION_CONTRACT_VERSION,
)
```

2. In `test_settlement_uses_same_next_open_window_as_labels`, the 4th positional argument of `_settle_for_ticker` becomes two dicts (opens, closes). Pass TOPIX data covering the H=1 window (entry/eval both 2026-01-13) but leave 2026-01-14 out of the opens so the H=2 row (entry 2026-01-13, eval 2026-01-14) still resolves via closes:

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
            {"2026-01-13": 2000.0},
            {"2026-01-13": 2010.0, "2026-01-14": 2040.0},
        )
```

Replace the three trailing benchmark assertions with:

```python
    # H=1: same-basis TOPIX open(entry 01-13) -> close(eval 01-13).
    assert abs(one_day["benchmark_ret"] - (2010.0 / 2000.0 - 1.0)) < 1e-12
    assert abs(
        one_day["excess_ret"]
        - ((80.0 / 120.0 - 1.0) - (2010.0 / 2000.0 - 1.0))
    ) < 1e-12
    assert one_day["benchmark_basis"] == BENCHMARK_BASIS
    assert BENCHMARK_BASIS == "next_session_open_to_horizon_session_close"
    # H=2 also has entry open 01-13 and eval close 01-14 available.
    two_day = captured[1][2]
    assert abs(two_day["benchmark_ret"] - (2040.0 / 2000.0 - 1.0)) < 1e-12
    assert two_day["benchmark_basis"] == BENCHMARK_BASIS
```

3. In `test_settlement_falls_back_to_archived_inactive_ticker_data`, replace the 4th argument `{}` with `{}, {}` and append these assertions at the end (missing TOPIX data must degrade, not break):

```python
    assert captured[0][2]["benchmark_ret"] is None
    assert captured[0][2]["excess_ret"] is None
    assert captured[0][2]["benchmark_basis"] == BENCHMARK_BASIS_UNAVAILABLE
```

4. Add a loader test locking the forward-fill guard (register it in `ALL_TESTS`). `src/macro.py` forward-fills `topix` but never `topix_open`, so a date whose open is NaN carries a stale carried-forward close that must never become an exit price:

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
    assert "2026-01-14" not in closes
    assert opens["2026-01-13"] == 2000.0
    assert closes["2026-01-15"] == 2070.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python tests/test_settle_outcomes.py`
Expected: ERROR (import of `BENCHMARK_BASIS_UNAVAILABLE` fails).

- [ ] **Step 3: Update `src/execution.py` constants**

Replace line 29:

```python
BENCHMARK_BASIS = "next_session_open_to_horizon_session_close"
BENCHMARK_BASIS_UNAVAILABLE = "unavailable_same_basis"
```

`execution_contract_metadata()` already emits `"benchmark_basis": BENCHMARK_BASIS` — no further change there.

- [ ] **Step 4: Update `scripts/settle_outcomes.py`**

1. Rewrite the module docstring paragraph about the macro panel (lines 17-23) to:

```
The macro panel carries a same-basis TOPIX-proxy open (topix_open, same
instrument and adjustment basis as the topix close column). Settlement
computes the contract-v2 same-basis benchmark inline
(topix_open[entry_date] -> topix[eval_date] close, gross), and
--refill-benchmark idempotently backfills v2 rows whose benchmark is still
NULL (e.g. the panel lagged on settle day). Rows settle with NULL benchmark
and benchmark_basis=unavailable_same_basis when either level is missing.
```

2. Extend the import from `src.execution` with `BENCHMARK_BASIS_UNAVAILABLE` and drop `LEGACY_EXECUTION_CONTRACT_VERSION` (Task 3 deletes its last use; if executing tasks in order, drop it in Task 3 instead — imports must stay consistent with usage at every commit).

3. Replace `_load_topix_by_date` with a two-column loader:

```python
def _load_topix_by_date() -> tuple[dict[str, float], dict[str, float]]:
    """Return ({date: topix_open}, {date: topix_close}) from the macro panel.

    Both dicts are keyed only on dates where topix_open AND topix are present
    and positive.  src/macro.py forward-fills close columns but never opens, so
    a genuine open proves the instrument traded that date and its close from the
    same source row is genuine too; keying both sides off that rule keeps a
    stale forward-filled close from becoming an exit price.  Same rule as
    src/portfolio_backtest.py::_topix_panel, so settlement and the Phase 2
    backtest measure the identical basis.

    Degrades to empty dicts (benchmark stays NULL) on any read problem.
    """
    macro_path = ROOT / "data" / "macro" / "macro_panel.parquet"
    try:
        df = pd.read_parquet(macro_path)
    except FileNotFoundError:
        print("macro_panel.parquet not found; TOPIX benchmark will stay NULL")
        return {}, {}
    except Exception as exc:  # noqa: BLE001
        print(f"macro_panel.parquet read error (benchmark stays NULL): {exc}")
        return {}, {}

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
    tp = tp.drop_duplicates(subset="date", keep="last")
    if tp.empty:
        print("macro_panel.parquet has no same-basis TOPIX rows; benchmark stays NULL")
        return {}, {}

    keys = pd.to_datetime(tp["date"]).dt.strftime("%Y-%m-%d")
    opens = {d: float(v) for d, v in zip(keys, tp["topix_open"])}
    closes = {d: float(v) for d, v in zip(keys, tp["topix"])}
    return opens, closes
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
                BENCHMARK_BASIS
                if benchmark_ret is not None
                else BENCHMARK_BASIS_UNAVAILABLE
            )
```

and pass `"benchmark_basis": benchmark_basis` in the upsert payload instead of the constant import used today (line 143 currently passes `BENCHMARK_BASIS`).

5. In `main()`, load the panel unconditionally (it is now needed by every settle run, not just refill):

```python
    topix_open_by_date, topix_close_by_date = _load_topix_by_date()
```

and update the per-ticker call:

```python
            total += _settle_for_ticker(
                conn, ticker, sigs, topix_open_by_date, topix_close_by_date
            )
```

Leave the `--refill-benchmark` block compiling against the old helpers for now if needed — but since `topix_by_date` no longer exists, the cleanest order is to stub the refill block to use the new dicts minimally; Task 3 rewrites it fully. To keep this commit green, replace the refill block's `topix_by_date` references with `topix_close_by_date` and its `compute_benchmark_ret(topix_by_date, ...)` call with `compute_benchmark_ret(topix_open_by_date, topix_close_by_date, ...)` (the v1-filter logic is deleted in Task 3).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python tests/test_settle_outcomes.py && uv run python tests/test_db_records.py`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/execution.py scripts/settle_outcomes.py tests/test_settle_outcomes.py
git commit -m "Settle contract-v2 outcomes with inline same-basis TOPIX benchmark"
```

---

### Task 3: v2 refill backfill (and delete the legacy v1 path)

**Files:**
- Modify: `src/db.py:969-1006` (`fetch_outcomes_missing_benchmark`, `update_outcome_benchmark`)
- Modify: `scripts/settle_outcomes.py` (refactor refill into `_refill_v2_benchmarks`, delete v1 logic)
- Test: `tests/test_settle_outcomes.py`

**Interfaces:**
- Consumes: `compute_benchmark_ret`, `BENCHMARK_BASIS`, `_load_topix_by_date` from Tasks 1-2.
- Produces: `_refill_v2_benchmarks(conn, topix_open_by_date: dict, topix_close_by_date: dict) -> tuple[int, int]` (refilled, scanned) in `scripts/settle_outcomes.py`; `db.update_outcome_benchmark(conn, signal_id, horizon_days, benchmark_ret, excess_ret, benchmark_basis)`.

- [ ] **Step 1: Write the failing refill test**

Append to `tests/test_settle_outcomes.py` (and register in `ALL_TESTS`):

```python
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
    assert basis == BENCHMARK_BASIS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python tests/test_settle_outcomes.py`
Expected: FAIL/ERROR — `_refill_v2_benchmarks` does not exist.

- [ ] **Step 3: Update the two db helpers in `src/db.py`**

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

- [ ] **Step 4: Rewrite the refill in `scripts/settle_outcomes.py`**

1. Add the function (above `main()`):

```python
def _refill_v2_benchmarks(
    conn, topix_open_by_date: dict, topix_close_by_date: dict
) -> tuple[int, int]:
    """Backfill same-basis benchmark for v2 rows settled while data lagged.

    Idempotent: only rows with benchmark_ret still NULL are scanned, and the
    computation is deterministic. Returns (refilled, scanned).
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
        excess_ret = row["realized_ret"] - benchmark_ret
        db.update_outcome_benchmark(
            conn,
            row["signal_id"],
            row["horizon_days"],
            benchmark_ret,
            excess_ret,
            BENCHMARK_BASIS,
        )
        refilled += 1
    return refilled, len(missing)
```

2. In `main()`, replace the whole `--refill-benchmark` block (the `if args.refill_benchmark and not topix_by_date:` ... `print(f"Refill benchmark: ...")` section) with:

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

3. Import `BENCHMARK_BASIS` from `src.execution` in this script and remove the now-unused `LEGACY_EXECUTION_CONTRACT_VERSION` import. Also update the `--refill-benchmark` argparse help text to `"Backfill same-basis benchmark_ret/excess_ret for v2 rows still NULL."`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python tests/test_settle_outcomes.py && uv run python tests/test_db_records.py && uv run python tests/test_performance.py && uv run python tests/test_reliability_db.py`
Expected: all PASS (the last two prove the export layer is unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/db.py scripts/settle_outcomes.py tests/test_settle_outcomes.py
git commit -m "Backfill v2 same-basis benchmark via --refill-benchmark; drop v1 path"
```

---

### Task 4: Spec docs, backlog, full test sweep, safety review

**Files:**
- Modify: `specification_document/01_backend_python.md:164` (v2 benchmark NULL statement)
- Modify: `specification_document/03_cicd_workflows.md:32,36` (settle step description)
- Modify: `specification_document/04_scripts.md:33` (settle_outcomes contract)
- Modify: `specification_document/05_cross_cutting.md:74,159` (date/JSON contract notes)
- Modify: `specification_document/06_issues_and_backlog.md` (backlog table row)

**Interfaces:** none (documentation + verification only).

- [ ] **Step 1: Update the five spec documents**

Each file states some variant of "v2のTOPIX同基準benchmarkは作れないためNULL". Rewrite those statements to the new reality. Guidance per file (adapt to surrounding Japanese prose, keep each file's voice):

- `01_backend_python.md` line 164: replace the sentence claiming TOPIXパネルは終値しかなくv2と同基準が作れない with: 決済はマクロパネルの`topix_open`（entry日寄付き）→`topix`（eval日終値）で同一basisの`benchmark_ret`/`excess_ret`をグロスで計算し、`benchmark_basis=next_session_open_to_horizon_session_close`を保存する。データ欠損時はNULL＋`unavailable_same_basis`で縮退し、`--refill-benchmark`が冪等補填する。旧v1のclose-to-close補填経路は削除。
- `03_cicd_workflows.md` line 32: the settle step now reads: 決済時に同一basis TOPIX benchmarkをインライン計算し、`--refill-benchmark`はv2のNULL行を冪等補填する。
- `04_scripts.md` line 33: same substance as above; also note `--restate-execution-contract` is unchanged.
- `05_cross_cutting.md` line 74: replace「v2のTOPIXは同じentry openがないためbenchmarkをNULLとし…」with the computed-contract statement (same-basis open→close, exact-date match, no forward fill, NULL+unavailable on gaps). Line 159: update「v2のTOPIX同基準benchmarkは現在取得不能なため `benchmark: null`」to say the benchmark is now computed and `benchmark: null` appears only for genuine data gaps (coverage reasonで報告).
- `06_issues_and_backlog.md`: delete the「決済側の同一basis benchmark」row from the「着手条件が既に揃っているもの」table (completed work lives in git history per that doc's own policy).

- [ ] **Step 2: Full test sweep**

Run every plain-script test:

```bash
for t in tests/test_*.py; do uv run python "$t" > /dev/null 2>&1 || echo "FAIL: $t"; done; echo done
```

Expected: only `done` printed (no FAIL lines). If a test fails, run it individually to see output and fix before proceeding.

- [ ] **Step 3: Dispatch the pipeline-safety-reviewer agent**

Dispatch the repo's `pipeline-safety-reviewer` agent over the diff (`git diff main` scope: `scripts/settle_outcomes.py`, `src/db.py`, `src/db_records.py`, `src/execution.py`). Address any invariant findings (graceful degradation of the daily run is the invariant most at risk here).

- [ ] **Step 4: Commit**

```bash
git add specification_document/
git commit -m "Document settlement-side same-basis TOPIX benchmark"
```
