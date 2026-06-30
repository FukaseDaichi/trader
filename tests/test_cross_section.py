#!/usr/bin/env python3
"""
Unit tests for src/cross_section.py — cross-sectional panel builder.

Runnable two ways:
  TRADER_DB_ENABLED=false uv run python tests/test_cross_section.py
  uv run pytest tests/test_cross_section.py

All tests use SYNTHETIC data; no real parquet or network access.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.cross_section import (  # noqa: E402
    CS_BASE_FEATURES,
    SECTOR_REL_FEATURES,
    build_cs_panel,
    cross_sectional_feature_cols,
    drop_small_date_groups,
)
from src.macro import MACRO_FEATURE_COLS  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: synthetic OHLCV generation
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 80, seed: int = 0, base_price: float = 1000.0) -> pd.DataFrame:
    """Return a synthetic OHLCV DataFrame with n rows starting 2020-01-01."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n, freq="B")
    close = base_price * np.cumprod(1.0 + rng.normal(0.0005, 0.015, size=n))
    high = close * (1.0 + rng.uniform(0.0, 0.02, size=n))
    low = close * (1.0 - rng.uniform(0.0, 0.02, size=n))
    open_ = close * (1.0 + rng.normal(0.0, 0.005, size=n))
    volume = rng.integers(100_000, 1_000_000, size=n).astype(float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def _make_tickers_data(
    n_tickers: int = 6,
    n_rows: int = 80,
    sectors: list[str | None] | None = None,
) -> list[tuple[dict, pd.DataFrame]]:
    """Build a list of (ticker_info, ohlcv) tuples with synthetic data."""
    out = []
    for i in range(n_tickers):
        code = f"T{i:04d}.JP"
        sector = sectors[i] if sectors and i < len(sectors) else f"Sector{i % 3}"
        ticker_info = {"code": code, "name": f"Stock {i}", "sector": sector}
        ohlcv = _make_ohlcv(n=n_rows, seed=i * 13, base_price=1000.0 + i * 100.0)
        out.append((ticker_info, ohlcv))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

ALL_TESTS: list[tuple[str, object]] = []


def _test(fn):
    ALL_TESTS.append((fn.__name__, fn))
    return fn


@_test
def test_no_future_leakage_cs_features():
    """
    Critical: within-date cs features on date D must be identical whether
    computed on the full panel or on data truncated to dates <= D.
    """
    n_tickers = 6
    n_rows = 60
    tickers_data = _make_tickers_data(n_tickers=n_tickers, n_rows=n_rows)

    # Build full panel (no labels — only features needed).
    full_panel = build_cs_panel(
        tickers_data, macro_panel=None, macro_enabled=False, with_labels=False
    )
    assert not full_panel.empty, "full_panel must not be empty"

    dates_sorted = sorted(full_panel["date"].unique())
    # Pick a middle date (roughly 40% through available dates).
    mid_idx = len(dates_sorted) // 2
    D = dates_sorted[mid_idx]

    full_rows_D = full_panel[full_panel["date"] == D].copy()
    assert len(full_rows_D) > 0, "No rows for date D in full panel"

    # Rebuild truncated: keep only dates <= D for every ticker.
    truncated_data = []
    for ticker_info, ohlcv in tickers_data:
        ohlcv_trunc = ohlcv[ohlcv["date"] <= D].copy()
        if not ohlcv_trunc.empty:
            truncated_data.append((ticker_info, ohlcv_trunc))

    trunc_panel = build_cs_panel(
        truncated_data, macro_panel=None, macro_enabled=False, with_labels=False
    )
    assert not trunc_panel.empty, "truncated panel must not be empty"
    trunc_rows_D = trunc_panel[trunc_panel["date"] == D].copy()
    assert len(trunc_rows_D) > 0, "No rows for date D in truncated panel"

    # Compare cs features for the same tickers on date D.
    check_cols = ["cs_z_return_5d", "cs_rank_return_5d"]
    full_rows_D = full_rows_D.set_index("ticker")
    trunc_rows_D = trunc_rows_D.set_index("ticker")

    common_tickers = full_rows_D.index.intersection(trunc_rows_D.index)
    assert len(common_tickers) > 0, (
        "No common tickers between full and truncated panels on D"
    )

    for col in check_cols:
        if col not in full_rows_D.columns or col not in trunc_rows_D.columns:
            continue
        for t in common_tickers:
            full_val = full_rows_D.loc[t, col]
            trunc_val = trunc_rows_D.loc[t, col]
            # Both NaN -> OK; otherwise must be close.
            if pd.isna(full_val) and pd.isna(trunc_val):
                continue
            assert abs(full_val - trunc_val) < 1e-9, (
                f"Future leakage detected! col={col} ticker={t} date={D} "
                f"full={full_val} trunc={trunc_val}"
            )


@_test
def test_within_date_zscore_sanity():
    """Within a date with >1 finite values: mean of cs_z ≈ 0, ranks in (0,1]."""
    tickers_data = _make_tickers_data(n_tickers=8, n_rows=80)
    panel = build_cs_panel(
        tickers_data, macro_panel=None, macro_enabled=False, with_labels=False
    )

    # Pick a date with plenty of rows.
    date_counts = panel.groupby("date").size()
    big_dates = date_counts[date_counts >= 6].index
    assert len(big_dates) > 0, "No date with >=6 rows found"
    D = big_dates[0]

    rows = panel[panel["date"] == D]

    # Check z-score mean ≈ 0.
    for f in CS_BASE_FEATURES:
        z_col = f"cs_z_{f}"
        r_col = f"cs_rank_{f}"
        if z_col not in rows.columns:
            continue
        z_vals = rows[z_col].dropna()
        if len(z_vals) > 1:
            assert abs(z_vals.mean()) < 1e-9, (
                f"cs_z mean not ~0 for {f} on date {D}: {z_vals.mean()}"
            )
        r_vals = (
            rows[r_col].dropna() if r_col in rows.columns else pd.Series(dtype=float)
        )
        if len(r_vals) > 0:
            assert (r_vals > 0).all() and (r_vals <= 1.0 + 1e-9).all(), (
                f"cs_rank_{f} out of (0,1] range: min={r_vals.min()}, max={r_vals.max()}"
            )


@_test
def test_labels_fwd_return_and_target_up():
    """
    fwd_return is NaN for the last H rows of each ticker;
    equals hand-computed forward return for an interior row;
    target_up matches sign; target_rank_bucket in {0,1,2,3,4} where defined.
    """
    n_tickers = 5
    n_rows = 60
    H = 5
    tickers_data = _make_tickers_data(n_tickers=n_tickers, n_rows=n_rows)

    panel = build_cs_panel(
        tickers_data,
        macro_panel=None,
        macro_enabled=False,
        with_labels=True,
        label_config={"label_horizon_days": H},
    )

    for code, grp in panel.groupby("ticker"):
        grp = grp.sort_values("date").reset_index(drop=True)
        n = len(grp)

        # Last H rows should be NaN.
        last_h = grp.tail(H)
        assert last_h["fwd_return"].isna().all(), (
            f"Ticker {code}: last {H} fwd_return rows should be NaN"
        )

        # Interior row: verify against hand computation.
        if n > H + 5:
            idx = n // 2
            row = grp.iloc[idx]
            fut_row = grp.iloc[idx + H]
            expected = fut_row["close"] / row["close"] - 1.0
            actual = row["fwd_return"]
            assert abs(actual - expected) < 1e-9, (
                f"Ticker {code} idx={idx}: fwd_return={actual} expected={expected}"
            )

            # target_up matches sign.
            if actual > 0:
                assert row["target_up"] == 1.0
            else:
                assert row["target_up"] == 0.0

    # target_rank_bucket: 0..4 where defined, NaN where fwd_return is NaN.
    defined = panel["fwd_return"].notna()
    buckets = panel.loc[defined, "target_rank_bucket"]
    assert buckets.notna().all(), (
        "target_rank_bucket must not be NaN where fwd_return is defined"
    )
    valid = buckets.dropna()
    assert ((valid >= 0) & (valid <= 4)).all(), (
        f"target_rank_bucket out of [0,4]: {valid.unique()}"
    )
    # NaN where fwd_return is NaN.
    nan_fwd = panel["fwd_return"].isna()
    assert panel.loc[nan_fwd, "target_rank_bucket"].isna().all()


@_test
def test_drop_small_date_groups():
    """drop_small_date_groups removes dates with < min_names tickers, keeps the rest."""
    tickers_data = _make_tickers_data(n_tickers=8, n_rows=60)
    panel = build_cs_panel(
        tickers_data, macro_panel=None, macro_enabled=False, with_labels=False
    )

    # Inject a synthetic date with only 2 tickers.
    fake_rows = panel[panel["date"] == panel["date"].iloc[0]].head(2).copy()
    fake_date = pd.Timestamp("2019-01-02")
    fake_rows["date"] = fake_date
    panel_with_small = pd.concat([panel, fake_rows], ignore_index=True)

    min_names = 5
    filtered = drop_small_date_groups(panel_with_small, min_names=min_names)

    # The fake date with 2 rows must be gone.
    assert fake_date not in filtered["date"].values, "Small date group was not dropped"

    # Dates with >= min_names tickers must be retained.
    big_dates = panel.groupby("date").size()
    big_dates = big_dates[big_dates >= min_names].index
    for d in big_dates:
        assert d in filtered["date"].values, (
            f"Date {d} with enough tickers was incorrectly dropped"
        )


@_test
def test_robustness_none_sector_and_no_macro():
    """
    Panel builds without error when some tickers have sector=None and
    macro_panel is None. CS features must still be present (macro cols may be NaN).
    """
    sectors = [None, "Tech", None, "Finance", None, "Energy"]
    tickers_data = _make_tickers_data(n_tickers=6, n_rows=70, sectors=sectors)

    # Should not raise.
    panel = build_cs_panel(
        tickers_data, macro_panel=None, macro_enabled=True, with_labels=False
    )

    assert not panel.empty
    # CS features present.
    for f in CS_BASE_FEATURES:
        z_col = f"cs_z_{f}"
        r_col = f"cs_rank_{f}"
        assert z_col in panel.columns, f"Missing {z_col}"
        assert r_col in panel.columns, f"Missing {r_col}"

    # Sector rank columns present (even though some sectors are None).
    for f in SECTOR_REL_FEATURES:
        col = f"sect_rank_{f}"
        assert col in panel.columns, f"Missing {col}"

    # Macro cols present but all NaN (macro_panel=None).
    for col in MACRO_FEATURE_COLS:
        assert col in panel.columns, f"Missing macro col {col}"
        # Values may be NaN; that is expected.


@_test
def test_cross_sectional_feature_cols():
    """
    cross_sectional_feature_cols returns the right schema;
    all cs_* / sect_* names exist as columns after build_cs_panel.
    """
    cols_no_macro = cross_sectional_feature_cols(macro_enabled=False)
    cols_with_macro = cross_sectional_feature_cols(macro_enabled=True)

    # Macro cols excluded when macro_enabled=False.
    for mc in MACRO_FEATURE_COLS:
        assert mc not in cols_no_macro, f"{mc} should not be in cols_no_macro"

    # Macro cols included when macro_enabled=True.
    for mc in MACRO_FEATURE_COLS:
        assert mc in cols_with_macro, f"{mc} missing from cols_with_macro"

    # All cs_z_, cs_rank_, sect_rank_ cols present in both.
    for f in CS_BASE_FEATURES:
        assert f"cs_z_{f}" in cols_no_macro
        assert f"cs_rank_{f}" in cols_no_macro
    for f in SECTOR_REL_FEATURES:
        assert f"sect_rank_{f}" in cols_no_macro
    assert "cs_rank_turnover" in cols_no_macro

    # Build a panel and verify all cs_* / sect_* names exist as actual columns.
    tickers_data = _make_tickers_data(n_tickers=6, n_rows=70)
    panel = build_cs_panel(
        tickers_data, macro_panel=None, macro_enabled=False, with_labels=False
    )
    cs_only = [c for c in cols_no_macro if c.startswith("cs_") or c.startswith("sect_")]
    for col in cs_only:
        assert col in panel.columns, f"Expected column {col!r} not in panel"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    passed = 0
    failed = 0
    errored = 0
    total = len(ALL_TESTS)

    for name, fn in ALL_TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            errored += 1

    print(f"\n{passed}/{total} passed", end="")
    if failed:
        print(f"  {failed} failed", end="")
    if errored:
        print(f"  {errored} errored", end="")
    print()

    # Return 0 only if all passed.
    return 0 if (failed == 0 and errored == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
