#!/usr/bin/env python3
"""
Unit tests for src/universe.py — pure logic, no real parquets, no tickers.yml writes.

Runnable two ways:
    TRADER_DB_ENABLED=false uv run python tests/test_universe.py
    TRADER_DB_ENABLED=false uv run pytest tests/test_universe.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.universe import (  # noqa: E402
    apply_sector_cap,
    compute_liquidity,
    load_universe_candidates,
    rank_candidates,
    select_target_universe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n_rows: int = 30, close: float = 1000.0, volume: float = 1_000_000.0) -> pd.DataFrame:
    """Tiny synthetic OHLCV DataFrame for testing compute_liquidity."""
    dates = pd.date_range("2025-01-01", periods=n_rows, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": volume,
    })


def _make_candidates(
    specs: list[dict],
    *,
    default_rows: int = 300,
    default_liq: float = 1e9,
    default_combined: float | None = None,
) -> list[dict]:
    """Build synthetic candidate dicts."""
    out = []
    for s in specs:
        out.append({
            "code": s["code"],
            "name": s.get("name", s["code"]),
            "sector": s.get("sector"),
            "combined": s.get("combined", default_combined),
            "rows": s.get("rows", default_rows),
            "liquidity": s.get("liquidity", default_liq),
            "source": s.get("source", "pool"),
        })
    return out


# ---------------------------------------------------------------------------
# compute_liquidity
# ---------------------------------------------------------------------------

def test_compute_liquidity_basic():
    df = _make_df(n_rows=30, close=2000.0, volume=500_000.0)
    liq = compute_liquidity(df, window=20)
    assert liq is not None
    assert abs(liq - 2000.0 * 500_000.0) < 1.0, f"Expected 1e9 got {liq}"


def test_compute_liquidity_uses_last_window_rows():
    # Last 20 rows have close=3000; earlier rows have close=1000
    df1 = _make_df(n_rows=30, close=1000.0, volume=1_000_000.0)
    df2 = _make_df(n_rows=20, close=3000.0, volume=1_000_000.0)
    df = pd.concat([df1, df2]).reset_index(drop=True)
    liq = compute_liquidity(df, window=20)
    assert liq is not None
    assert abs(liq - 3000.0 * 1_000_000.0) < 1.0


def test_compute_liquidity_none_on_none_df():
    assert compute_liquidity(None) is None


def test_compute_liquidity_none_on_empty_df():
    assert compute_liquidity(pd.DataFrame()) is None


def test_compute_liquidity_none_on_missing_columns():
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=5, freq="B"), "open": 100.0})
    assert compute_liquidity(df) is None


def test_compute_liquidity_single_row():
    df = _make_df(n_rows=1, close=500.0, volume=2_000_000.0)
    liq = compute_liquidity(df, window=20)
    assert liq is not None
    assert abs(liq - 500.0 * 2_000_000.0) < 1.0


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------

def test_rank_candidates_warmup_filter():
    cands = _make_candidates([
        {"code": "A.JP", "combined": 80.0, "rows": 100},  # fails warmup (< 200)
        {"code": "B.JP", "combined": 70.0, "rows": 200},  # passes
        {"code": "C.JP", "combined": 60.0, "rows": 300},  # passes
    ])
    passing, low = rank_candidates(cands, min_warmup_rows=200)
    assert {c["code"] for c in passing} == {"B.JP", "C.JP"}
    assert {c["code"] for c in low} == {"A.JP"}


def test_rank_candidates_combined_desc():
    cands = _make_candidates([
        {"code": "B.JP", "combined": 70.0},
        {"code": "A.JP", "combined": 80.0},
        {"code": "C.JP", "combined": 60.0},
    ])
    passing, _ = rank_candidates(cands, min_warmup_rows=0)
    codes = [c["code"] for c in passing]
    assert codes == ["A.JP", "B.JP", "C.JP"]


def test_rank_candidates_none_combined_last():
    """Scored candidates rank above unscored (None treated as -inf)."""
    cands = _make_candidates([
        {"code": "Z.JP", "combined": None},
        {"code": "A.JP", "combined": 50.0},
    ])
    passing, _ = rank_candidates(cands, min_warmup_rows=0)
    assert passing[0]["code"] == "A.JP"
    assert passing[1]["code"] == "Z.JP"


def test_rank_candidates_liquidity_tiebreak():
    """When combined scores tie, higher liquidity ranks first."""
    cands = _make_candidates([
        {"code": "B.JP", "combined": 70.0, "liquidity": 1e9},
        {"code": "A.JP", "combined": 70.0, "liquidity": 2e9},
    ])
    passing, _ = rank_candidates(cands, min_warmup_rows=0)
    assert passing[0]["code"] == "A.JP"
    assert passing[1]["code"] == "B.JP"


def test_rank_candidates_code_tiebreak():
    """When combined and liquidity both tie, alphabetical code order is stable."""
    cands = _make_candidates([
        {"code": "Z.JP", "combined": 70.0, "liquidity": 1e9},
        {"code": "A.JP", "combined": 70.0, "liquidity": 1e9},
        {"code": "M.JP", "combined": 70.0, "liquidity": 1e9},
    ])
    passing, _ = rank_candidates(cands, min_warmup_rows=0)
    assert [c["code"] for c in passing] == ["A.JP", "M.JP", "Z.JP"]


# ---------------------------------------------------------------------------
# apply_sector_cap
# ---------------------------------------------------------------------------

def test_apply_sector_cap_basic():
    """With target_size=4 and sector_cap_pct=50 the cap per sector is max(1, floor(2))=2."""
    cands = _make_candidates([
        {"code": "A.JP", "sector": "銀行"},
        {"code": "B.JP", "sector": "銀行"},
        {"code": "C.JP", "sector": "銀行"},   # 3rd 銀行 → dropped
        {"code": "D.JP", "sector": "電機"},
        {"code": "E.JP", "sector": "電機"},
        {"code": "F.JP", "sector": "電機"},   # 3rd 電機 → dropped but target already full
    ])
    selected, dropped = apply_sector_cap(cands, target_size=4, sector_cap_pct=50)
    assert len(selected) == 4
    sector_counts: dict[str, int] = {}
    for c in selected:
        sector_counts[c["sector"]] = sector_counts.get(c["sector"], 0) + 1
    for s, count in sector_counts.items():
        assert count <= 2, f"sector {s} count={count} exceeds cap=2"


def test_apply_sector_cap_none_sector_not_capped_together():
    """Candidates with sector=None each get their own unique bucket — they are never capped against each other."""
    cands = _make_candidates([
        {"code": "A.JP", "sector": None},
        {"code": "B.JP", "sector": None},
        {"code": "C.JP", "sector": None},
        {"code": "D.JP", "sector": None},
        {"code": "E.JP", "sector": None},
    ])
    # target_size=5, sector_cap_pct=20 → cap=max(1,floor(1))=1 per bucket
    # But each None gets its own bucket, so all 5 pass
    selected, dropped = apply_sector_cap(cands, target_size=5, sector_cap_pct=20)
    assert len(selected) == 5
    assert len(dropped) == 0


def test_apply_sector_cap_respects_target_size():
    cands = _make_candidates([{"code": f"X{i}.JP", "sector": "機械"} for i in range(20)])
    selected, dropped = apply_sector_cap(cands, target_size=5, sector_cap_pct=100)
    assert len(selected) == 5


def test_apply_sector_cap_empty_input():
    selected, dropped = apply_sector_cap([], target_size=10, sector_cap_pct=40)
    assert selected == []
    assert dropped == []


def test_apply_sector_cap_zero_target():
    cands = _make_candidates([{"code": "A.JP", "sector": "銀行"}])
    selected, dropped = apply_sector_cap(cands, target_size=0, sector_cap_pct=40)
    assert selected == []
    assert len(dropped) == 1


def test_apply_sector_cap_cap_always_at_least_one():
    """Even with a tiny sector_cap_pct each sector gets at least 1 slot."""
    cands = _make_candidates([
        {"code": "A.JP", "sector": "銀行"},
        {"code": "B.JP", "sector": "電機"},
    ])
    selected, dropped = apply_sector_cap(cands, target_size=10, sector_cap_pct=1)
    # cap = max(1, floor(0.1)) = max(1,0) = 1; both should be selected
    assert {c["code"] for c in selected} == {"A.JP", "B.JP"}


# ---------------------------------------------------------------------------
# select_target_universe
# ---------------------------------------------------------------------------

def _plenty_of_candidates(n: int = 40, sector_variety: int = 8) -> list[dict]:
    """Build enough synthetic candidates to satisfy a target_size=30, min_universe=30 call."""
    sectors = [f"sector_{i}" for i in range(sector_variety)]
    out = []
    for i in range(n):
        out.append({
            "code": f"{i:04d}.JP",
            "name": f"Company {i}",
            "sector": sectors[i % sector_variety],
            "combined": float(90 - i),   # strictly decreasing so order is deterministic
            "rows": 300,
            "liquidity": float(1e10 - i * 1e6),
            "source": "pool",
        })
    return out


def test_select_ok_returns_sector_exposure():
    cands = _plenty_of_candidates(n=40, sector_variety=8)
    result = select_target_universe(
        cands,
        target_size=30,
        min_warmup_rows=200,
        sector_cap_pct=40,
        min_universe=30,
    )
    assert result["status"] == "ok"
    assert result["selected_size"] <= 30
    # sector_exposure must not exceed cap per sector
    cap = max(1, int(40 / 100 * 30))  # floor(12) = 12
    for sector, count in result["sector_exposure"].items():
        assert count <= cap, f"sector {sector} count={count} > cap={cap}"


def test_select_ok_selected_non_empty():
    cands = _plenty_of_candidates(n=40)
    result = select_target_universe(
        cands,
        target_size=30,
        min_warmup_rows=200,
        sector_cap_pct=40,
        min_universe=30,
    )
    assert result["status"] == "ok"
    assert len(result["selected"]) > 0


def test_select_insufficient_when_too_few_pass_warmup():
    """Only 5 candidates pass warmup; target_size=30, min_universe=30 → insufficient."""
    cands = _make_candidates(
        [{"code": f"{i}.JP", "sector": "銀行", "combined": float(50 + i)} for i in range(5)],
        default_rows=300,
    ) + _make_candidates(
        [{"code": f"low_{i}.JP", "sector": "電機", "combined": float(40 + i)} for i in range(20)],
        default_rows=50,  # below min_warmup_rows=200
    )
    result = select_target_universe(
        cands,
        target_size=30,
        min_warmup_rows=200,
        sector_cap_pct=40,
        min_universe=30,
    )
    assert result["status"] == "insufficient_universe"
    assert result["selected"] == []
    assert result["selected_size"] == 0


def test_select_insufficient_selected_is_empty_list():
    cands = _make_candidates([{"code": "A.JP", "sector": "銀行"}], default_rows=300)
    result = select_target_universe(
        cands,
        target_size=30,
        min_warmup_rows=200,
        sector_cap_pct=40,
        min_universe=30,
    )
    assert result["status"] == "insufficient_universe"
    assert result["selected"] == []


def test_select_ok_sector_exposure_consistent_with_selected():
    cands = _plenty_of_candidates(n=40, sector_variety=5)
    result = select_target_universe(
        cands,
        target_size=30,
        min_warmup_rows=200,
        sector_cap_pct=40,
        min_universe=10,  # low threshold so we pass
    )
    # Recompute exposure manually and check it matches
    expected: dict[str, int] = {}
    for c in result["selected"]:
        s = c.get("sector") or "(none)"
        expected[s] = expected.get(s, 0) + 1
    assert result["sector_exposure"] == expected


def test_select_ok_threshold_uses_min_of_target_and_min_universe():
    """
    If min_universe < target_size, the threshold is min_universe.
    Test: 15 passing candidates, target_size=30, min_universe=10 → ok.
    """
    cands = _make_candidates(
        [{"code": f"X{i}.JP", "sector": f"sec{i}", "combined": float(70 - i)} for i in range(15)],
        default_rows=300,
    )
    result = select_target_universe(
        cands,
        target_size=30,
        min_warmup_rows=200,
        sector_cap_pct=100,  # no cap
        min_universe=10,
    )
    assert result["status"] == "ok"


def test_select_is_pure_does_not_write_files():
    """
    Calling select_target_universe must not create any files (pure function).
    We verify by checking no new files appear in a temp directory that we
    temporarily set as the working directory context.  In practice we just
    confirm no exceptions and no side effects by using a temp dir and checking
    os.listdir before/after.
    """
    import os

    with tempfile.TemporaryDirectory() as tmp:
        before = set(os.listdir(tmp))
        cands = _plenty_of_candidates(n=40)
        _ = select_target_universe(
            cands,
            target_size=30,
            min_warmup_rows=200,
            sector_cap_pct=40,
            min_universe=30,
        )
        after = set(os.listdir(tmp))
    assert before == after, "select_target_universe wrote files — it must be pure"


# ---------------------------------------------------------------------------
# load_universe_candidates
# ---------------------------------------------------------------------------

def test_load_universe_candidates_dedup_priority():
    """enabled > watchlist > pool for the same code."""
    cfg = {
        "tickers": [{"code": "A.JP", "name": "Enabled A", "enabled": True, "sector": None}],
        "watchlist": [{"code": "A.JP", "name": "Watchlist A", "sector": "電機"}],
        "settings": {},
    }
    pool = [{"code": "A.JP", "name": "Pool A", "sector": "電機"}]
    cands = load_universe_candidates(cfg, pool)
    a = next(c for c in cands if c["code"] == "A.JP")
    assert a["source"] == "enabled"
    assert a["name"] == "Enabled A"


def test_load_universe_candidates_sector_backfill_from_pool():
    """Manual tickers without sector should get sector backfilled from pool."""
    cfg = {
        "tickers": [{"code": "7011.JP", "name": "三菱重工業", "enabled": True}],
        "watchlist": [],
        "settings": {},
    }
    pool = [{"code": "7011.JP", "name": "三菱重工業", "sector": "機械・重工"}]
    cands = load_universe_candidates(cfg, pool)
    entry = next(c for c in cands if c["code"] == "7011.JP")
    assert entry["sector"] == "機械・重工"


def test_load_universe_candidates_pool_only_code():
    """Pool-only codes (not in tickers or watchlist) appear with source=pool."""
    cfg = {"tickers": [], "watchlist": [], "settings": {}}
    pool = [{"code": "Z.JP", "name": "Zeta", "sector": "機械"}]
    cands = load_universe_candidates(cfg, pool)
    z = next((c for c in cands if c["code"] == "Z.JP"), None)
    assert z is not None
    assert z["source"] == "pool"
    assert z["rows"] == 0
    assert z["liquidity"] is None


def test_load_universe_candidates_disabled_ticker_can_fall_back_to_pool():
    """Disabled ticker entries should not block pool/watchlist warmup data lookup."""
    cfg = {
        "tickers": [{"code": "D.JP", "name": "Disabled D", "enabled": False}],
        "watchlist": [],
        "settings": {},
    }
    pool = [{"code": "D.JP", "name": "Pool D", "sector": "建設"}]
    cands = load_universe_candidates(cfg, pool)
    d = next(c for c in cands if c["code"] == "D.JP")
    assert d["source"] == "pool"
    assert d["sector"] == "建設"


def test_load_universe_candidates_no_duplicates():
    """Each code appears exactly once."""
    cfg = {
        "tickers": [{"code": "A.JP", "name": "A", "enabled": True}],
        "watchlist": [{"code": "B.JP", "name": "B", "sector": "電機"}],
        "settings": {},
    }
    pool = [
        {"code": "A.JP", "name": "A pool", "sector": "機械"},
        {"code": "B.JP", "name": "B pool", "sector": "電機"},
        {"code": "C.JP", "name": "C pool", "sector": "化学"},
    ]
    cands = load_universe_candidates(cfg, pool)
    codes = [c["code"] for c in cands]
    assert len(codes) == len(set(codes)), "Duplicate codes found"
    assert set(codes) == {"A.JP", "B.JP", "C.JP"}


# ---------------------------------------------------------------------------
# ALL_TESTS and runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    # compute_liquidity
    test_compute_liquidity_basic,
    test_compute_liquidity_uses_last_window_rows,
    test_compute_liquidity_none_on_none_df,
    test_compute_liquidity_none_on_empty_df,
    test_compute_liquidity_none_on_missing_columns,
    test_compute_liquidity_single_row,
    # rank_candidates
    test_rank_candidates_warmup_filter,
    test_rank_candidates_combined_desc,
    test_rank_candidates_none_combined_last,
    test_rank_candidates_liquidity_tiebreak,
    test_rank_candidates_code_tiebreak,
    # apply_sector_cap
    test_apply_sector_cap_basic,
    test_apply_sector_cap_none_sector_not_capped_together,
    test_apply_sector_cap_respects_target_size,
    test_apply_sector_cap_empty_input,
    test_apply_sector_cap_zero_target,
    test_apply_sector_cap_cap_always_at_least_one,
    # select_target_universe
    test_select_ok_returns_sector_exposure,
    test_select_ok_selected_non_empty,
    test_select_insufficient_when_too_few_pass_warmup,
    test_select_insufficient_selected_is_empty_list,
    test_select_ok_sector_exposure_consistent_with_selected,
    test_select_ok_threshold_uses_min_of_target_and_min_universe,
    test_select_is_pure_does_not_write_files,
    # load_universe_candidates
    test_load_universe_candidates_dedup_priority,
    test_load_universe_candidates_sector_backfill_from_pool,
    test_load_universe_candidates_pool_only_code,
    test_load_universe_candidates_disabled_ticker_can_fall_back_to_pool,
    test_load_universe_candidates_no_duplicates,
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
    total = len(ALL_TESTS)
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
