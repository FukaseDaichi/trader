#!/usr/bin/env python3
"""
Unit tests for the deterministic curation merge decision logic.

Runnable two ways:
  uv run python tests/test_curation_merge.py     # standalone
  uv run pytest tests/test_curation_merge.py      # if pytest is available

No network or filesystem writes — compute_decision is a pure function.
"""

from __future__ import annotations

import copy
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from curation_common import DEFAULT_CURATION_SETTINGS  # noqa: E402
from curation_merge import compute_decision  # noqa: E402

TODAY = date(2026, 6, 6)


def make_settings(**overrides):
    s = copy.deepcopy(DEFAULT_CURATION_SETTINGS)
    s.update(overrides)
    return s


def cand(code, score, sector="S", rows=400, warmup_ok=None, name=None):
    return {
        "code": code,
        "name": name or code,
        "sector": sector,
        "score": score,
        "rows_available": rows,
        "warmup_ok": (rows >= 200) if warmup_ok is None else warmup_ok,
    }


def tech_report(cands, as_of=TODAY):
    return {"as_of": as_of.isoformat(), "candidates": cands}


def fund_report(cands, as_of=TODAY):
    return {"as_of": as_of.isoformat(), "candidates": cands}


def cfg_with(enabled, watchlist=None, disabled=None):
    tickers = [{"code": c, "name": c, "enabled": True} for c in enabled]
    for code, disabled_on in (disabled or {}).items():
        tickers.append(
            {"code": code, "name": code, "enabled": False, "disabled_on": disabled_on}
        )
    return {"tickers": tickers, "watchlist": watchlist or [], "settings": {}}


# ---------------------------------------------------------------------------


def test_no_fundamental_is_conservative():
    cfg = cfg_with(["A", "B"])
    s = make_settings()
    tech = tech_report([cand("A", 60), cand("B", 60), cand("C", 95, sector="T")])
    d = compute_decision(tech, None, cfg, s, TODAY)
    assert d["conservative"] is True
    assert d["universe_after"] == d["universe_before"] == ["A", "B"]
    assert d["changes"]["promoted"] == []


def test_stale_fundamental_is_conservative():
    cfg = cfg_with(["A", "B"])
    s = make_settings(max_fundamental_age_days=14)
    old = TODAY - timedelta(days=30)
    tech = tech_report([cand("A", 60), cand("B", 60), cand("C", 95, sector="T")])
    fund = fund_report([cand("C", 95, sector="T")], as_of=old)
    d = compute_decision(tech, fund, cfg, s, TODAY)
    assert d["conservative"] is True
    assert d["universe_after"] == ["A", "B"]


def test_add_fills_empty_slots():
    cfg = cfg_with(["A", "B"])
    s = make_settings(max_universe=5, max_daily_adds=2, max_daily_swaps=2)
    tech = tech_report(
        [
            cand("A", 55, sector="A"),
            cand("B", 55, sector="B"),
            cand("C", 80, sector="C"),
            cand("D", 78, sector="D"),
            cand("E", 60, sector="E"),
        ]
    )
    fund = fund_report(
        [
            cand("A", 55, sector="A"),
            cand("B", 55, sector="B"),
            cand("C", 80, sector="C"),
            cand("D", 78, sector="D"),
            cand("E", 60, sector="E"),
        ]
    )
    d = compute_decision(tech, fund, cfg, s, TODAY)
    assert d["conservative"] is False
    assert set(d["changes"]["promoted_add"]) == {"C", "D"}
    assert "E" in d["changes"]["watchlist"]  # 60 >= keep_floor but < min_combined
    assert set(d["universe_after"]) == {"A", "B", "C", "D"}


def test_swap_replaces_worst_below_floor():
    cfg = cfg_with(["A", "B", "C"])
    s = make_settings(max_universe=3, max_daily_swaps=2, min_gap=5, keep_floor=50)
    cands = [
        cand("A", 30, sector="A"),
        cand("B", 60, sector="B"),
        cand("C", 70, sector="C"),
        cand("X", 85, sector="X"),
    ]
    d = compute_decision(tech_report(cands), fund_report(cands), cfg, s, TODAY)
    assert d["changes"]["promoted_swap"] == ["X"]
    assert d["changes"]["demoted"] == ["A"]
    assert set(d["universe_after"]) == {"B", "C", "X"}


def test_warmup_gate_blocks_promotion():
    cfg = cfg_with(["A", "B"])
    s = make_settings(max_universe=5, min_warmup_rows=200)
    cands = [
        cand("A", 55, sector="A"),
        cand("B", 55, sector="B"),
        cand("C", 95, sector="C", rows=50),
    ]  # not enough history
    d = compute_decision(tech_report(cands), fund_report(cands), cfg, s, TODAY)
    assert "C" not in d["changes"]["promoted"]
    watch = {w["code"]: w for w in d["new_watchlist"]}
    assert watch.get("C", {}).get("status") == "warming"


def test_cooldown_blocks_repromotion():
    disabled_on = (TODAY - timedelta(days=2)).isoformat()
    cfg = cfg_with(["A", "B"], disabled={"C": disabled_on})
    s = make_settings(max_universe=5, cooldown_days=5)
    cands = [
        cand("A", 55, sector="A"),
        cand("B", 55, sector="B"),
        cand("C", 95, sector="C"),
    ]
    d = compute_decision(tech_report(cands), fund_report(cands), cfg, s, TODAY)
    assert "C" not in d["changes"]["promoted"]


def test_daily_add_cap():
    cfg = cfg_with(["A"])
    s = make_settings(max_universe=10, max_daily_adds=1)
    cands = [
        cand("A", 55, sector="A"),
        cand("C", 90, sector="C"),
        cand("D", 88, sector="D"),
    ]
    d = compute_decision(tech_report(cands), fund_report(cands), cfg, s, TODAY)
    assert len(d["changes"]["promoted_add"]) == 1
    assert d["changes"]["promoted_add"] == ["C"]  # highest combined first


def test_sector_cap_blocks_overweight():
    cfg = cfg_with(["A", "B"])  # A sector X, B sector Y
    s = make_settings(max_universe=5, sector_cap_pct=40, max_daily_adds=2)
    cands = [
        cand("A", 55, sector="X"),
        cand("B", 55, sector="Y"),
        cand("C", 90, sector="X"),
    ]  # adding C makes X = 2/3 > 40%
    d = compute_decision(tech_report(cands), fund_report(cands), cfg, s, TODAY)
    assert "C" not in d["changes"]["promoted"]
    assert "C" in d["changes"]["watchlist"]


ALL_TESTS = [
    test_no_fundamental_is_conservative,
    test_stale_fundamental_is_conservative,
    test_add_fills_empty_slots,
    test_swap_replaces_worst_below_floor,
    test_warmup_gate_blocks_promotion,
    test_cooldown_blocks_repromotion,
    test_daily_add_cap,
    test_sector_cap_blocks_overweight,
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
