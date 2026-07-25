# TOPIX Open (Same-Basis Benchmark) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `topix_open` column to the macro panel so the Phase 2 portfolio backtest can compute a same-basis benchmark return and produce a finite information ratio.

**Architecture:** `src/macro.py` currently slices every fetched series down to `[date, close]`, so the open is discarded at the source. This plan makes the open an opt-in per series (TOPIX only), validates it alongside the close from the same provider response, and carries it into the macro panel as `topix_open` without forward-fill. The consumer side (`src/portfolio_backtest.py`, `scripts/weekly_cross_section_retrain.py`) is already written against this contract and needs no change.

**Tech Stack:** Python 3.13, `uv`, pandas, numpy. Tests are plain Python scripts under `tests/` with an `ALL_TESTS` list and a `main()` runner — no pytest required.

**Spec:** `specification_document/plans/2026-07-26-topix-open-benchmark-design.md`

> **2026-07-26 追記:** Task 1完了後、実データ検証（Task 4）で
> `_open_rejection_reason`の「非有限・非正なら列全体を破棄」ルールが、
> `1305.T`全履歴中の2009年の異常値3件で始値全体を無効化する問題が発覚し、
> 日付単位のNaN化に修正した（コミット`4c56b844`）。以下のTask 1コード
> ブロックはこの修正前の実装を示す。修正の詳細は設計doc
> `2026-07-26-topix-open-benchmark-design.md`の§4.4を参照。

## Global Constraints

- The daily signal run must never break. A failure in the open path degrades to close-only behaviour; it never raises and never aborts the pipeline (AGENTS.md).
- `MACRO_LEVEL_COLS` and `MACRO_FEATURE_COLS` must not change. Phase 1 feature semantics stay identical, so no artifact schema bump and no retraining.
- `latest_snapshot_row()` output keys must not change. The `macro_snapshots` DB table and `src/db.py:1027-1036` stay untouched. No migration.
- The existing `topix` close column keeps its current forward-fill behaviour.
- `topix_open` must never be forward-filled.
- The discontinuity threshold is the existing `_MAX_MARKET_DAILY_MOVE = 0.50`. Do not introduce a new threshold constant.
- Benchmark instrument is `1305.T` / `1305.jp` — the same instrument already backing the `topix` close. Do not add a second TOPIX source.
- No new files under `docs/`, so `daily-publish-dashboard.yml`'s `--exclude` list stays as-is.
- Run tests with `uv run python tests/<file>.py`. Prefix with `TRADER_DB_ENABLED=false` for tests that touch config.

---

### Task 1: Fetch layer carries a validated open (opt-in)

**Files:**
- Modify: `src/macro.py:44-50` (`DEFAULT_MARKET_SERIES`), `src/macro.py:95-192` (`_validated_market_close` → `_validated_market_frame`, `fetch_market_series`)
- Test: `tests/test_macro_features.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `_extreme_move(values: pd.Series) -> tuple[int, float] | None` — first index whose day-over-day move exceeds the limit, with that return.
  - `_open_rejection_reason(frame: pd.DataFrame) -> str | None` — why the frame's `open` column cannot be trusted, or `None`.
  - `_validated_market_frame(df, *, source: str, symbol: str, want_open: bool = False) -> pd.DataFrame | None` — returns `[date, close]`, plus `open` when `want_open` and the provider supplied a trustworthy one.
  - `fetch_market_series(spec: dict) -> pd.DataFrame | None` — unchanged signature; returns an extra `open` column when `spec["open"]` is truthy and the data passes validation.
  - `DEFAULT_MARKET_SERIES["topix"]` gains `"open": True`.

- [ ] **Step 1: Write the failing tests**

Add these seven tests to `tests/test_macro_features.py`, immediately after `test_fetch_market_series_extreme_yfinance_move_is_unavailable` (currently ending at line 294):

```python
def test_only_topix_opts_into_open_levels():
    """Open levels exist for the same-basis benchmark only. Every other series
    stays close-only so its fetch path is byte-for-byte unchanged."""
    assert DEFAULT_MARKET_SERIES["topix"].get("open") is True
    for key, spec in DEFAULT_MARKET_SERIES.items():
        if key != "topix":
            assert not spec.get("open"), key


def test_fetch_market_series_open_opt_in_carries_open_column():
    idx = pd.date_range("2026-01-01", periods=4, freq="D", name="Date")
    raw = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, 103.0],
            "Close": [101.0, 102.0, 103.0, 104.0],
        },
        index=idx,
    )
    with_open, _ = _with_fake_yf(
        {"max": raw}, {"stooq": None, "yfinance": "1305.T", "open": True}
    )
    assert with_open is not None
    assert list(with_open.columns) == ["date", "close", "open"]
    assert with_open["open"].tolist() == [100.0, 101.0, 102.0, 103.0]

    without_open, _ = _with_fake_yf({"max": raw}, {"stooq": None, "yfinance": "1305.T"})
    assert without_open is not None
    assert list(without_open.columns) == ["date", "close"]


def test_fetch_market_series_open_opt_in_tolerates_missing_open():
    """A provider that returns no open at all must still yield the close."""
    idx = pd.date_range("2026-01-01", periods=3, freq="D", name="Date")
    raw = pd.DataFrame({"Close": [100.0, 101.0, 102.0]}, index=idx)
    result, _ = _with_fake_yf(
        {"max": raw}, {"stooq": None, "yfinance": "1305.T", "open": True}
    )
    assert result is not None
    assert list(result.columns) == ["date", "close"]


def test_fetch_market_series_partial_missing_open_is_retained_as_nan():
    """A gap on one date is not a reason to discard the whole open series;
    exact-date consumers drop that date and report incomplete coverage."""
    idx = pd.date_range("2026-01-01", periods=4, freq="D", name="Date")
    raw = pd.DataFrame(
        {
            "Open": [100.0, np.nan, 102.0, 103.0],
            "Close": [101.0, 102.0, 103.0, 104.0],
        },
        index=idx,
    )
    result, _ = _with_fake_yf(
        {"max": raw}, {"stooq": None, "yfinance": "1305.T", "open": True}
    )
    assert result is not None
    assert "open" in result.columns
    assert bool(result["open"].isna().iloc[1])
    assert result["close"].tolist() == [101.0, 102.0, 103.0, 104.0]


def test_fetch_market_series_extreme_open_move_drops_open_keeps_close():
    """A split-scale jump inside the open series poisons benchmark returns.
    Drop the open only — the close still feeds every macro feature."""
    idx = pd.date_range("2026-03-27", periods=4, freq="D", name="Date")
    raw = pd.DataFrame(
        {
            "Open": [400.0, 40.0, 40.5, 41.0],
            "Close": [40.2, 40.4, 40.6, 41.2],
        },
        index=idx,
    )
    result, _ = _with_fake_yf(
        {"max": raw}, {"stooq": None, "yfinance": "1305.T", "open": True}
    )
    assert result is not None
    assert list(result.columns) == ["date", "close"]
    assert result["close"].tolist() == [40.2, 40.4, 40.6, 41.2]


def test_fetch_market_series_open_close_basis_mismatch_drops_open():
    """Close adjusted for a 10:1 split, open left raw. Both series are
    internally continuous, so only the intraday open-to-close ratio exposes
    the basis mismatch."""
    idx = pd.date_range("2026-03-27", periods=4, freq="D", name="Date")
    raw = pd.DataFrame(
        {
            "Open": [400.0, 402.0, 404.0, 406.0],
            "Close": [40.1, 40.3, 40.5, 40.7],
        },
        index=idx,
    )
    result, _ = _with_fake_yf(
        {"max": raw}, {"stooq": None, "yfinance": "1305.T", "open": True}
    )
    assert result is not None
    assert list(result.columns) == ["date", "close"]


def test_fetch_market_series_extreme_close_move_still_rejects_whole_series():
    """Close-side failure keeps its existing meaning: the whole series is
    unusable, regardless of how sound the open looks."""
    idx = pd.date_range("2026-03-27", periods=4, freq="D", name="Date")
    raw = pd.DataFrame(
        {
            "Open": [40.0, 40.5, 41.0, 41.5],
            "Close": [400.0, 40.0, 41.0, 420.0],
        },
        index=idx,
    )
    result, fake = _with_fake_yf(
        {"max": raw}, {"stooq": None, "yfinance": "1305.T", "open": True}
    )
    assert result is None
    assert fake.calls == ["max"]
```

Register them in `ALL_TESTS` (currently at line 325) by inserting these entries directly after `test_fetch_market_series_extreme_yfinance_move_is_unavailable,`:

```python
    test_only_topix_opts_into_open_levels,
    test_fetch_market_series_open_opt_in_carries_open_column,
    test_fetch_market_series_open_opt_in_tolerates_missing_open,
    test_fetch_market_series_partial_missing_open_is_retained_as_nan,
    test_fetch_market_series_extreme_open_move_drops_open_keeps_close,
    test_fetch_market_series_open_close_basis_mismatch_drops_open,
    test_fetch_market_series_extreme_close_move_still_rejects_whole_series,
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python tests/test_macro_features.py`

Expected: exactly three of the new tests FAIL, and everything else PASSES.

FAIL:
- `test_only_topix_opts_into_open_levels` — the `"open"` key does not exist yet.
- `test_fetch_market_series_open_opt_in_carries_open_column` — the second assertion on `["date", "close", "open"]`; the current fetch always returns close-only.
- `test_fetch_market_series_partial_missing_open_is_retained_as_nan` — `"open" in result.columns` is false.

PASS already (they are regression guards, pinning behaviour that must hold both before and after this change): `test_fetch_market_series_open_opt_in_tolerates_missing_open`, `test_fetch_market_series_extreme_open_move_drops_open_keeps_close`, `test_fetch_market_series_open_close_basis_mismatch_drops_open`, `test_fetch_market_series_extreme_close_move_still_rejects_whole_series`. The first three assert the close-only outcome, which the old code reaches by never fetching an open and the new code reaches by rejecting a bad one — the same observable result for opposite reasons, which is exactly what makes them useful once the open path exists.

All 15 pre-existing tests still PASS. Expect `19/22 passed`.

- [ ] **Step 3: Add the open opt-in flag**

In `src/macro.py`, replace the `topix` entry of `DEFAULT_MARKET_SERIES`:

```python
    "topix": {"stooq": "1305.jp", "yfinance": "1305.T", "open": True},
```

Extend the block comment above `DEFAULT_MARKET_SERIES` by appending this paragraph after the existing `topix:` bullet:

```
#     The topix entry also opts into `open` levels: the Phase 2 backtest
#     compares strategy returns on a next-session-open to horizon-session-close
#     basis, which a close-only series cannot express. No other series needs it.
```

- [ ] **Step 4: Add the shared discontinuity helper**

In `src/macro.py`, insert this function directly above `_validated_market_close` (currently line 95):

```python
def _extreme_move(values: pd.Series) -> tuple[int, float] | None:
    """First index whose day-over-day move breaches the discontinuity limit.

    Returns ``(index, return)`` or ``None`` when the series is continuous.
    NaN gaps propagate as NaN returns and never trigger a false positive.
    """
    returns = values.pct_change(fill_method=None)
    extreme = returns.abs() > _MAX_MARKET_DAILY_MOVE
    if not extreme.any():
        return None
    idx = int(np.flatnonzero(extreme.to_numpy())[0])
    return idx, float(returns.iloc[idx])


def _open_rejection_reason(frame: pd.DataFrame) -> str | None:
    """Why this frame's ``open`` cannot be trusted, or None when it is sound.

    Three failure modes, each of which would otherwise corrupt the same-basis
    benchmark silently: unusable levels, a split-scale jump inside the open
    series, and an open carried on a different adjustment basis than the close.
    The last one leaves both series internally continuous, so only the intraday
    open-to-close ratio exposes it.

    A NaN on some dates is not a failure: those dates simply have no benchmark
    and the consumer reports incomplete coverage instead of inventing a return.
    """
    opens = frame["open"]
    present = opens.notna()
    if not present.any():
        return "no open levels supplied"
    if not np.isfinite(opens[present]).all() or (opens[present] <= 0).any():
        return "non-finite or non-positive open levels"

    bad = _extreme_move(opens)
    if bad is not None:
        idx, ret = bad
        date = frame["date"].iloc[idx].strftime("%Y-%m-%d")
        return (
            f"daily open move {ret:.1%} on {date} exceeds "
            f"{_MAX_MARKET_DAILY_MOVE:.0%}"
        )

    intraday = frame["close"] / opens - 1.0
    extreme = intraday.abs() > _MAX_MARKET_DAILY_MOVE
    if extreme.any():
        idx = int(np.flatnonzero(extreme.to_numpy())[0])
        date = frame["date"].iloc[idx].strftime("%Y-%m-%d")
        return (
            f"open/close basis mismatch: intraday move "
            f"{float(intraday.iloc[idx]):.1%} on {date} exceeds "
            f"{_MAX_MARKET_DAILY_MOVE:.0%}"
        )
    return None
```

- [ ] **Step 5: Rewrite the validator to carry the open**

In `src/macro.py`, replace the whole `_validated_market_close` function (lines 95-128) with:

```python
def _validated_market_frame(
    df: pd.DataFrame | None,
    *,
    source: str,
    symbol: str,
    want_open: bool = False,
) -> pd.DataFrame | None:
    """Normalize and reject split-scale discontinuities in a market series.

    Returns ``[date, close]``, plus an ``open`` column when the caller asked for
    one and the provider supplied one that passes the same integrity checks.
    A close-side failure rejects the whole series as before. An open-side
    failure drops only the open: the close and every macro feature built from
    it must survive so the daily run never breaks.
    """
    if df is None or df.empty or not {"date", "close"}.issubset(df.columns):
        return None

    cols = ["date", "close"]
    has_open = bool(want_open) and "open" in df.columns
    if has_open:
        cols.append("open")

    out = df[cols].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.tz_localize(None)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    if has_open:
        out["open"] = pd.to_numeric(out["open"], errors="coerce")
    out = (
        out.dropna(subset=["date", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )
    if out.empty or not np.isfinite(out["close"]).all() or (out["close"] <= 0).any():
        print(f"macro: rejected invalid {source} levels for {symbol}")
        return None

    bad = _extreme_move(out["close"])
    if bad is not None:
        bad_idx, bad_return = bad
        bad_date = out["date"].iloc[bad_idx].strftime("%Y-%m-%d")
        print(
            f"macro: rejected {source} series for {symbol}: "
            f"daily move {bad_return:.1%} on {bad_date} exceeds "
            f"{_MAX_MARKET_DAILY_MOVE:.0%}"
        )
        return None

    if has_open:
        reason = _open_rejection_reason(out)
        if reason is not None:
            print(
                f"macro: dropped {source} open for {symbol}: {reason} "
                f"(close retained)"
            )
            out = out.drop(columns=["open"])

    return out
```

- [ ] **Step 6: Thread `want_open` through the fetch**

In `src/macro.py`, replace `fetch_market_series` (lines 131-192) with:

```python
def fetch_market_series(spec: dict) -> pd.DataFrame | None:
    """
    Fetch one series as a [date, close] frame, trying Stooq then yfinance.
    A spec with ``open: True`` also carries an ``open`` column when the provider
    supplies a trustworthy one (the same-basis benchmark needs it).
    Returns None on failure (caller treats the series as unavailable).
    """
    from .data_loader import download_stooq_data

    want_open = bool(spec.get("open"))

    stooq_symbol = spec.get("stooq")
    if stooq_symbol:
        df = download_stooq_data(stooq_symbol)
        if df is not None and not df.empty and "close" in df.columns:
            out = _validated_market_frame(
                df,
                source="Stooq",
                symbol=str(stooq_symbol),
                want_open=want_open,
            )
            if out is not None:
                return out

    yf_symbol = spec.get("yfinance")
    if yf_symbol:
        # period="max" breaks yfinance's range resolution for some symbols,
        # so retry once with a
        # bounded period — 10y of daily closes covers every macro feature.
        for period in ("max", "10y"):
            try:
                import yfinance as yf

                raw = yf.download(
                    yf_symbol,
                    period=period,
                    interval="1d",
                    # Macro levels/returns must be continuous across ETF splits.
                    # With current yfinance this makes Close the adjusted close.
                    auto_adjust=True,
                    progress=False,
                    threads=False,
                )
                if raw is None or raw.empty:
                    continue
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = [
                        c[0] if isinstance(c, tuple) else c for c in raw.columns
                    ]
                raw = raw.reset_index()
                raw.columns = [str(c).lower() for c in raw.columns]
                # Some older yfinance versions and test doubles still return an
                # explicit Adj Close even with auto_adjust=True. Prefer it when
                # present so compatibility never regresses to a raw close.
                # That path can pair an adjusted close with a raw open; the
                # intraday basis check in _open_rejection_reason catches it.
                close_col = "adj close" if "adj close" in raw.columns else "close"
                if close_col in raw.columns and "date" in raw.columns:
                    take = ["date", close_col]
                    if want_open and "open" in raw.columns:
                        take.append("open")
                    out = raw[take].rename(columns={close_col: "close"})
                    out = _validated_market_frame(
                        out,
                        source="yfinance",
                        symbol=str(yf_symbol),
                        want_open=want_open,
                    )
                    if out is not None:
                        return out
                    # An extreme/invalid frame is a provider-integrity failure,
                    # not the empty-response range bug handled by the 10y retry.
                    return None
            except Exception as exc:  # noqa: BLE001
                print(
                    f"macro: yfinance fetch failed for {yf_symbol} "
                    f"(period={period}): {type(exc).__name__}: {exc}"
                )
    return None
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python tests/test_macro_features.py`

Expected: `22/22 passed`.

- [ ] **Step 8: Confirm no stale references to the old validator name**

Run: `grep -rn "_validated_market_close" src/ scripts/ tests/`

Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add src/macro.py tests/test_macro_features.py
git commit -m "Carry a validated TOPIX open through the macro fetch

The Phase 2 backtest measures strategy returns from the next session's open
to the horizon session's close, so a close-only benchmark cannot be compared
on the same basis. src/macro.py sliced every fetched series down to
[date, close], discarding the open at the source.

Series can now opt into open levels; only topix does. The open is taken from
the same provider response as the close, so both share one adjustment factor.
Validation rejects non-positive levels, split-scale jumps in the open series,
and an open carried on a different adjustment basis than the close - the last
detectable only through the intraday open-to-close ratio, since both series
stay internally continuous in that case.

An open-side failure drops the open alone and keeps the close, so macro
features and the daily run are unaffected. Close-side failure keeps its
existing meaning and rejects the whole series.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Panel exposes `topix_open` without forward-fill

**Files:**
- Modify: `src/macro.py:52-53` (level column constants), `src/macro.py:215-231` (`_aligned_levels`), `src/macro.py:234-279` (`build_macro_panel`)
- Test: `tests/test_macro_features.py`

**Interfaces:**
- Consumes: `fetch_market_series` frames from Task 1, which may carry an `open` column.
- Produces:
  - `MACRO_AUX_LEVEL_COLS: list[str]` — `["topix_open"]`. Benchmark-only levels; not features, not part of the DB snapshot row.
  - `build_macro_panel(series_data, qualitative=None) -> pd.DataFrame` — unchanged signature; output columns become `["date"] + MACRO_LEVEL_COLS + MACRO_AUX_LEVEL_COLS + MACRO_FEATURE_COLS`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_macro_features.py`, add `MACRO_AUX_LEVEL_COLS` to the `src.macro` import block (currently lines 21-30), keeping alphabetical order — place it directly after `MACRO_FEATURE_COLS`:

```python
from src.macro import (  # noqa: E402
    DEFAULT_MARKET_SERIES,
    MACRO_AUX_LEVEL_COLS,
    MACRO_FEATURE_COLS,
    MACRO_LEVEL_COLS,
    add_macro_features,
    build_macro_panel,
    encode_market_bias,
    fetch_market_series,
    latest_snapshot_row,
)
```

Add these three tests directly after `test_latest_snapshot_row` (currently ending at line 126):

```python
def test_topix_open_is_not_forward_filled_off_session():
    """USD/JPY trades on JP holidays, so the outer join creates dates on which
    TOPIX did not trade. Forward-filling the open there would pair yesterday's
    open with yesterday's close and invent a benchmark return that every
    downstream check accepts: exact date match, positive levels, no NaN."""
    usd = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "close": [150.0, 150.5, 151.0],
        }
    )
    top = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-07"]),
            "close": [2800.0, 2820.0],
            "open": [2790.0, 2810.0],
        }
    )
    panel = build_macro_panel({"usdjpy": usd, "topix": top}).set_index("date")
    # the off-session date still carries a forward-filled close (unchanged) ...
    assert panel.loc["2026-01-06", "topix"] == 2800.0
    # ... but no open, so exact-date consumers exclude that date
    assert np.isnan(panel.loc["2026-01-06", "topix_open"])
    # real sessions keep their own open
    assert panel.loc["2026-01-05", "topix_open"] == 2790.0
    assert panel.loc["2026-01-07", "topix_open"] == 2810.0


def test_build_macro_panel_always_emits_topix_open_column():
    """The panel's column set must not depend on whether the open was
    available, so downstream readers never branch on schema shape."""
    usd, _ = _series("2026-01-01", 30, 0.1, "usdjpy")
    top, _ = _series("2026-01-01", 30, 1.0, "topix")  # close-only series
    panel = build_macro_panel({"usdjpy": usd, "topix": top})
    expected = ["date"] + MACRO_LEVEL_COLS + MACRO_AUX_LEVEL_COLS + MACRO_FEATURE_COLS
    for col in expected:
        assert col in panel.columns, col
    assert panel["topix_open"].isna().all()
    assert MACRO_AUX_LEVEL_COLS == ["topix_open"]
    # and on the empty-input path
    assert "topix_open" in build_macro_panel({}).columns


def test_latest_snapshot_row_ignores_topix_open():
    """topix_open is benchmark-only. macro_snapshots must not gain a column,
    so no DB migration is implied by this panel change."""
    top = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "close": [2800.0, 2820.0],
            "open": [2790.0, 2810.0],
        }
    )
    panel = build_macro_panel({"topix": top})
    row = latest_snapshot_row(panel)
    assert set(row.keys()) == {
        "date",
        "usdjpy",
        "topix",
        "nikkei",
        "nikkei_vi",
        "jgb10y",
        "market_bias",
        "regime",
    }
    assert row["topix"] == 2820.0
```

Register them in `ALL_TESTS` directly after `test_latest_snapshot_row,`:

```python
    test_topix_open_is_not_forward_filled_off_session,
    test_build_macro_panel_always_emits_topix_open_column,
    test_latest_snapshot_row_ignores_topix_open,
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python tests/test_macro_features.py`

Expected: an `ImportError` on `MACRO_AUX_LEVEL_COLS` — the whole file fails to load. That is the expected first failure.

- [ ] **Step 3: Add the auxiliary level constant**

In `src/macro.py`, directly after the `MACRO_LEVEL_COLS` definition (line 53), add:

```python
# Benchmark-only level columns. These are NOT model features and NOT part of
# the macro_snapshots DB row; they exist so the Phase 2 backtest can measure a
# same-basis benchmark (next-session open -> horizon-session close).
MACRO_AUX_LEVEL_COLS = ["topix_open"]
```

- [ ] **Step 4: Carry the open through the join without forward-filling it**

In `src/macro.py`, replace `_aligned_levels` (lines 215-231) with:

```python
def _aligned_levels(series_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Outer-join each series' close on date, forward-filled, into one frame.

    A series carrying an ``open`` also contributes ``<key>_open``. Only closes
    are forward-filled. A forward-filled open would pair yesterday's open with
    yesterday's close on a date the instrument never traded and manufacture a
    benchmark return that passes every downstream check, so opens stay NaN
    off-session and exact-date consumers exclude those dates.
    """
    panel = None
    close_cols: list[str] = []
    for key, df in series_data.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        take = ["date", "close"]
        renames = {"close": key}
        if "open" in df.columns:
            take.append("open")
            renames["open"] = f"{key}_open"
        col = df[take].copy()
        col["date"] = pd.to_datetime(col["date"]).dt.tz_localize(None)
        col = col.rename(columns=renames).sort_values("date")
        panel = col if panel is None else panel.merge(col, on="date", how="outer")
        close_cols.append(key)
    if panel is None:
        return pd.DataFrame(columns=["date"])
    panel = panel.sort_values("date").reset_index(drop=True)
    for key in close_cols:
        if key in panel.columns:
            panel[key] = panel[key].ffill()
    return panel
```

- [ ] **Step 5: Include the auxiliary column in the panel schema**

In `src/macro.py` `build_macro_panel`, make three edits.

Replace the empty-panel early return (line 243):

```python
    if panel.empty:
        return pd.DataFrame(
            columns=(
                ["date"]
                + MACRO_LEVEL_COLS
                + MACRO_AUX_LEVEL_COLS
                + MACRO_FEATURE_COLS
            )
        )
```

Replace the column-guarantee loop (lines 245-248):

```python
    # Guarantee level columns exist for the snapshot row, and the auxiliary
    # benchmark levels so the panel schema never depends on availability.
    for col in MACRO_LEVEL_COLS + MACRO_AUX_LEVEL_COLS:
        if col not in panel.columns:
            panel[col] = np.nan
```

Replace the output column list (line 278):

```python
    cols = (
        ["date"] + MACRO_LEVEL_COLS + MACRO_AUX_LEVEL_COLS + MACRO_FEATURE_COLS
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python tests/test_macro_features.py`

Expected: `25/25 passed`.

- [ ] **Step 7: Run the Phase 1 regression suites**

Run each and expect every test to pass:

```bash
uv run python tests/test_phase1_artifact_contract.py
TRADER_DB_ENABLED=false uv run python tests/test_cross_section.py
TRADER_DB_ENABLED=false uv run python tests/test_weekly_cross_section_retrain_contract.py
```

Expected: all PASS. These confirm `MACRO_FEATURE_COLS` semantics and the CS contract are untouched, so no Phase 1 schema bump is implied.

- [ ] **Step 8: Commit**

```bash
git add src/macro.py tests/test_macro_features.py
git commit -m "Expose topix_open in the macro panel without forward-fill

The panel now carries topix_open alongside the topix close so the Phase 2
backtest can measure a same-basis benchmark. It is an auxiliary level, not a
feature: MACRO_LEVEL_COLS, MACRO_FEATURE_COLS, and latest_snapshot_row's keys
are unchanged, so Phase 1 needs no schema bump and macro_snapshots needs no
migration.

Opens are deliberately excluded from the forward-fill. USD/JPY trades on JP
holidays, so the outer join produces dates on which TOPIX never traded;
forward-filling there would pair yesterday's open with yesterday's close and
produce a benchmark return with a matching date, positive levels and no NaN -
wrong in a way nothing downstream can detect. Leaving it NaN makes the
consumer drop the date and report incomplete coverage instead.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Producer and consumer agree on the benchmark contract

**Files:**
- Test: `tests/test_portfolio_backtest.py`

**Interfaces:**
- Consumes: `build_macro_panel` from Task 2 (emits `topix_open`); `pbt._prepare_topix(macro_panel) -> pd.DataFrame | None` and `pbt._benchmark_return(topix, entry_date, exit_date) -> float | None`, both already present in `src/portfolio_backtest.py`.
- Produces: no production code. This is the end-to-end guard that the two halves connect.

- [ ] **Step 1: Write the failing test**

In `tests/test_portfolio_backtest.py`, add this test directly after `test_backtest_benchmark_alpha_beta` (currently starting at line 366; insert before the next `def test_`):

```python
def test_macro_panel_with_open_feeds_benchmark_preparation():
    """End-to-end contract: the producer (src.macro.build_macro_panel) and the
    consumer (_prepare_topix / _benchmark_return) must agree on the same-basis
    benchmark columns. Each side is tested in isolation elsewhere; this is the
    test that fails if they drift apart."""
    from src.macro import build_macro_panel

    dates = pd.bdate_range("2026-01-05", periods=10)
    top = pd.DataFrame(
        {
            "date": dates,
            "close": [2800.0 + i * 5 for i in range(10)],
            "open": [2795.0 + i * 5 for i in range(10)],
        }
    )

    panel = build_macro_panel({"topix": top})
    prepared = pbt._prepare_topix(panel)
    assert prepared is not None
    assert list(prepared.columns) == ["date", "topix_open", "topix"]
    assert len(prepared) == 10

    # entry open -> exit close, exactly the v2 execution contract
    ret = pbt._benchmark_return(prepared, dates[1], dates[5])
    expected = (2800.0 + 5 * 5) / (2795.0 + 1 * 5) - 1.0
    assert abs(ret - expected) < 1e-12

    # a close-only panel stays fail-closed rather than substituting a basis
    close_only = build_macro_panel({"topix": top[["date", "close"]]})
    assert pbt._prepare_topix(close_only) is None
```

Register it in `ALL_TESTS` directly after `test_backtest_benchmark_alpha_beta,`:

```python
    test_macro_panel_with_open_feeds_benchmark_preparation,
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `TRADER_DB_ENABLED=false uv run python tests/test_portfolio_backtest.py`

Expected: all tests PASS, including the new one. Tasks 1 and 2 already supply `topix_open`, so this test confirms the connection rather than driving new code. If it fails, the panel and the backtest disagree — fix `src/macro.py`, not the test.

- [ ] **Step 3: Verify it fails without the producer change**

Tasks 1 and 2 are already committed, so there is nothing to stash. Check out the pre-change `src/macro.py` (`HEAD~2` is the commit before Task 1), run the suite, then restore:

```bash
git checkout HEAD~2 -- src/macro.py
TRADER_DB_ENABLED=false uv run python tests/test_portfolio_backtest.py
git checkout HEAD -- src/macro.py
```

Expected: with the old `src/macro.py`, `test_macro_panel_with_open_feeds_benchmark_preparation` FAILS on `assert prepared is not None` while every other test in the file passes. After the restore, re-running gives all PASS. This proves the test exercises the new behaviour rather than passing vacuously.

Confirm the restore landed:

```bash
git status --porcelain src/macro.py
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add tests/test_portfolio_backtest.py
git commit -m "Test that the macro panel actually feeds the benchmark

_prepare_topix and build_macro_panel are tested in separate files, so nothing
caught the case where the panel produced no topix_open at all - which is
exactly the state the repository was in. This test builds a panel through the
real producer and runs it through the real consumer, and asserts the v2
entry-open to exit-close return. It also pins the fail-closed path: a
close-only panel yields None rather than a substituted basis.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Measure the information ratio locally

**Files:**
- Create: `/private/tmp/claude-501/-Users-fukasedaichi-git-trader/0d948e81-b913-42c6-bc2f-3455cad38276/scratchpad/verify_topix_open.py` (throwaway; not committed)
- Touches at runtime: `data/macro/macro_panel.parquet` (regenerated, then restored)

**Interfaces:**
- Consumes: `build_macro_panel` / `load_macro_panel` from Task 2, and the committed `data/models/cs-v1-20260725/oos_predictions.parquet`.
- Produces: measured values for `benchmark_coverage.coverage_ratio`, `metrics.information_ratio`, `metrics.alpha`, `metrics.beta`, `metrics.tracking_error`, and `gate.failures`. Task 5 records these.

This task changes no production code. It answers the question the whole change exists to answer.

- [ ] **Step 1: Back up the committed macro panel**

The next step overwrites a tracked file. Confirm it is clean first so it can be restored exactly.

```bash
git status --porcelain data/macro/macro_panel.parquet
```

Expected: no output (file unmodified).

- [ ] **Step 2: Regenerate the panel with real data**

```bash
TRADER_DB_ENABLED=false uv run python scripts/update_macro_snapshots.py
```

Expected: a line like `macro: fetched topix (N rows)` and `macro: panel saved to .../macro_panel.parquet`. There must be NO `macro: dropped ... open for 1305.T` line — if one appears, read the printed reason and stop; the provider data violates one of the three integrity checks and the design's assumptions need revisiting before measuring anything.

- [ ] **Step 3: Inspect the regenerated panel**

```bash
uv run python -c "
import pandas as pd
from src.macro import load_macro_panel
p = load_macro_panel()
print('columns:', list(p.columns))
w = p[(p['date'] >= '2025-10-21') & (p['date'] <= '2026-07-10')]
print('rows in backtest window:', len(w))
print('topix_open present:', int(w['topix_open'].notna().sum()))
print('topix_open missing:', int(w['topix_open'].isna().sum()))
both = w.dropna(subset=['topix_open', 'topix'])
intraday = both['topix'] / both['topix_open'] - 1.0
print('intraday mean %.5f std %.5f min %.4f max %.4f' % (
    intraday.mean(), intraday.std(), intraday.min(), intraday.max()))
"
```

Expected: `topix_open` is in the column list; the intraday mean is near 0 and the standard deviation is roughly 0.008-0.009, matching the 2026-07-26 measurement in the spec (mean −0.00035, std 0.00855). Missing rows correspond to non-JPX dates carried in by USD/JPY and should be a small minority.

- [ ] **Step 4: Write the measurement script**

Create the script at `/private/tmp/claude-501/-Users-fukasedaichi-git-trader/0d948e81-b913-42c6-bc2f-3455cad38276/scratchpad/verify_topix_open.py`. If that session scratchpad no longer exists, use any directory outside the repository — the script must not be committed.

```python
#!/usr/bin/env python3
"""One-off: measure the Phase 2 benchmark now that topix_open exists.

Offline except for the macro fetch already done in Step 2 — reads the committed
cs-v1-20260725 OOS predictions and data/*.parquet. Not committed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/fukasedaichi/git/trader")
sys.path.insert(0, str(ROOT))

from src.backtest import evaluate_portfolio_kpi_gate, format_portfolio_gate_summary
from src.config import TICKERS, get_cross_section_config, get_portfolio_config
from src.data_loader import load_data
from src.macro import load_macro_panel
from src.portfolio_backtest import run_portfolio_backtest

MODEL_DIR = ROOT / "data" / "models" / "cs-v1-20260725"

macro_panel = load_macro_panel()
oos = pd.read_parquet(MODEL_DIR / "oos_predictions.parquet")

price_frames = {}
sectors = {}
for ticker in TICKERS:
    code = ticker["code"]
    df = load_data(code)
    if df is None or "date" not in df.columns or "close" not in df.columns:
        continue
    price_frames[code] = df[["date", "close"]].copy()
    if ticker.get("sector"):
        sectors[code] = ticker["sector"]

cs_cfg = get_cross_section_config()
portfolio_cfg = get_portfolio_config()
portfolio_cfg["top_n"] = cs_cfg.get("top_n", 8)

result = run_portfolio_backtest(
    oos,
    price_frames,
    macro_panel,
    portfolio_cfg,
    sectors=sectors,
    label_horizon_days=int(cs_cfg["label_horizon_days"]),
    cost_bps=float(portfolio_cfg.get("cost_bps", 10.0)),
    slippage_bps=float(portfolio_cfg.get("slippage_bps", 5.0)),
)
gate = evaluate_portfolio_kpi_gate(result, portfolio_cfg)

metrics = result.get("metrics") or {}
print(
    json.dumps(
        {
            "status": result.get("status"),
            "n_periods": result.get("n_periods"),
            "benchmark_coverage": result.get("benchmark_coverage"),
            "metrics": {
                key: metrics.get(key)
                for key in (
                    "information_ratio",
                    "alpha",
                    "beta",
                    "tracking_error",
                    "sharpe",
                    "cagr",
                    "max_drawdown",
                    "turnover",
                )
            },
            "gate": gate,
        },
        indent=2,
        default=str,
        ensure_ascii=False,
    )
)
print(format_portfolio_gate_summary(gate))
```

- [ ] **Step 5: Run the measurement**

```bash
TRADER_DB_ENABLED=false uv run python /private/tmp/claude-501/-Users-fukasedaichi-git-trader/0d948e81-b913-42c6-bc2f-3455cad38276/scratchpad/verify_topix_open.py
```

Expected: `benchmark_coverage.available` is `true` with `coverage_ratio` of `1.0`; `information_ratio`, `alpha`, `beta` and `tracking_error` are finite numbers instead of `null`; `gate.failures` no longer contains `ir_unavailable_same_basis`. `turnover>0.40` is still expected to be present — that is out of scope and not a failure of this work. Record every printed number.

- [ ] **Step 6: Restore the committed panel**

The regenerated parquet must not be committed; the Monday 06:00 preopen job regenerates and commits it through the normal pipeline.

```bash
git checkout -- data/macro/macro_panel.parquet
git status --porcelain
```

Expected: `git status --porcelain` shows no modification to `data/macro/macro_panel.parquet`.

---

### Task 5: Record the measured outcome

**Files:**
- Modify: `specification_document/06_issues_and_backlog.md`

**Interfaces:**
- Consumes: the numbers printed in Task 4 Step 5.
- Produces: no code. Updates the operational record so the 2026-08-01 decision point has current evidence.

- [ ] **Step 1: Update the status table and the P0 constraint**

In `specification_document/06_issues_and_backlog.md`:

1. Change the `更新日` line at the top to the implementation date.
2. In the `現在地` table, replace the `TOPIX benchmark` row's 状態 and 判断 with the measured `coverage_ratio` and the fact that IR is now computed. Keep the row — it becomes evidence, not a blocker.
3. In the `Phase 2レポート` row, note that the 2026-07-25 baseline was generated with `cs-v1-20260725` and that its gate failed on `ir_unavailable_same_basis` and `turnover>0.40`, and record the locally measured IR.
4. In `### 3. 並行判断 — TOPIX openの方針を決める（期限: 2026-08-01）`, mark option 1 as the chosen and implemented path, citing the design and plan documents by filename. Replace the two-option text with the decision and its date.
5. In `## 継続中のP0制約` → `### Phase 2 active化を禁止する`, remove `ir_unavailable_same_basis` from the reasons and keep the remaining blockers (`turnover>0.40`, `cs_ic_vs_phase1`, Phase 1 gate 0/50) explicit. `TRADER_PORTFOLIO_MODE=shadow` stays.
6. In `### 5. 最初の総合判定（2026-08-22）`, tick the `TOPIX同一basis open系列の契約・履歴・完全coverageを確認` checkbox only if Task 4 measured `coverage_ratio == 1.0`; otherwise leave it unticked and record the measured ratio next to it.

Do not change the 2026-08-01 or 2026-08-22 decision dates, and do not alter `TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER`.

- [ ] **Step 2: Verify no other document contradicts the new state**

Run: `grep -rn "topix_open\|unavailable_same_basis" specification_document/`

Expected: every remaining occurrence either describes the code contract (accurate) or sits in the design/plan documents. Any sentence still claiming the open series does not exist must be updated.

- [ ] **Step 3: Commit**

```bash
git add specification_document/06_issues_and_backlog.md
git commit -m "Record the TOPIX open outcome in the issues backlog

The same-basis benchmark now exists, so ir_unavailable_same_basis is no longer
a P0 blocker and the 2026-08-01 TOPIX decision is settled: option 1, sourcing
the open from the existing 1305 instrument. The remaining active blockers -
turnover above the cap, the negative CS IC versus Phase 1, and Phase 1's 0/50
KPI gate - are recorded explicitly so the 2026-08-22 review is not misread as
closer than it is. TRADER_PORTFOLIO_MODE stays shadow.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verification of the whole change

After Task 5, run the full set of affected suites:

```bash
uv run python tests/test_macro_features.py
TRADER_DB_ENABLED=false uv run python tests/test_portfolio_backtest.py
TRADER_DB_ENABLED=false uv run python tests/test_cross_section.py
TRADER_DB_ENABLED=false uv run python tests/test_weekly_cross_section_retrain_contract.py
uv run python tests/test_phase1_artifact_contract.py
uv run python tests/test_publish_workflow.py
```

Expected: every suite reports all tests passing. `test_publish_workflow.py` confirms no new `docs/` file needs an `--exclude` entry.

Then confirm the working tree carries only source and documentation changes:

```bash
git status --porcelain
```

Expected: clean — no modified parquet files.

## Rollout

- 2026-07-27 (Mon) 06:00 JST: the preopen core job runs `scripts/update_macro_snapshots.py` and commits a panel containing `topix_open` through the normal pipeline.
- 2026-08-01 (Sat) 08:00 JST: the weekly retrain generates the first official `docs/portfolio_backtest.json` with a finite information ratio.
- `TRADER_PORTFOLIO_MODE` stays `shadow`. This change does not make `active_ready` true and must not be treated as progress toward activation beyond restoring the measurement.
