#!/usr/bin/env python3
"""
Unit tests for deterministic curation pool merge logic.

Runnable two ways:
  uv run python tests/test_curation_pool_merge.py
  uv run pytest tests/test_curation_pool_merge.py
"""

from __future__ import annotations

import copy
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import curation_pool_merge as pool_merge  # noqa: E402
from curation_pool_merge import DEFAULT_POOL_SETTINGS, compute_pool_decision  # noqa: E402
from curation_common import write_json  # noqa: E402

TODAY = date(2026, 6, 16)


def make_settings(**overrides):
    s = copy.deepcopy(DEFAULT_POOL_SETTINGS)
    s.update(overrides)
    return s


def pool(codes):
    return [
        {"code": code, "name": code, "sector": sector}
        for code, sector in codes
    ]


def cfg(enabled=None, watchlist=None):
    return {
        "tickers": [{"code": code, "name": code, "enabled": True} for code in (enabled or [])],
        "watchlist": [{"code": code, "name": code} for code in (watchlist or [])],
        "settings": {},
    }


def proposal(candidates):
    return {
        "schema_version": 1,
        "as_of": TODAY.isoformat(),
        "candidates": candidates,
    }


def cand(code, action="add", score=80, sector="S", liquidity=10_000):
    return {
        "code": code,
        "name": code,
        "sector": sector,
        "action_hint": action,
        "fund_score": score,
        "liquidity_jpy": liquidity,
    }


def decision(prop, current_pool, settings=None, enabled=None, liquidity=None, cooldown=None):
    return compute_pool_decision(
        prop,
        current_pool,
        cfg(enabled=enabled),
        settings or make_settings(liquidity_floor_jpy=1),
        TODAY,
        liquidity_lookup=liquidity or {},
        cooldown_codes=cooldown or set(),
    )


def test_grow_adds_up_to_cap_and_target():
    current = pool([("A", "X"), ("B", "Y")])
    prop = proposal([
        cand("C", score=90, sector="Z"),
        cand("D", score=88, sector="W"),
        cand("E", score=86, sector="V"),
    ])
    s = make_settings(pool_target_size=4, pool_max_size=10, max_adds_per_run=3, liquidity_floor_jpy=1)
    d = decision(prop, current, s, liquidity={"C": 100, "D": 100, "E": 100})
    assert d["mode"] == "grow"
    assert d["changes"]["added"] == ["C", "D"]
    assert d["pool_after"] == ["A", "B", "C", "D"]


def test_replace_mode_drops_disabled_never_changes_pool():
    current = pool([("A", "X"), ("B", "Y")])
    prop = proposal([cand("C", score=90, sector="Z"), cand("B", action="drop", score=20, sector="Y")])
    s = make_settings(pool_target_size=2, max_adds_per_run=1, max_drops_per_run=0, liquidity_floor_jpy=1)
    d = decision(prop, current, s, liquidity={"C": 100})
    assert d["mode"] == "replace"
    assert d["changes"]["added"] == []
    assert d["changes"]["dropped"] == []
    assert d["pool_after"] == ["A", "B"]
    c = next(r for r in d["ranking"] if r["code"] == "C")
    assert c["reason"] == "replace_mode_drops_disabled"


def test_replace_mode_pairs_add_with_drop_hint():
    current = pool([("A", "X"), ("B", "Y")])
    prop = proposal([cand("C", score=90, sector="Z"), cand("B", action="drop", score=20, sector="Y")])
    s = make_settings(pool_target_size=2, max_adds_per_run=1, max_drops_per_run=1, liquidity_floor_jpy=1)
    d = decision(prop, current, s, liquidity={"C": 100, "B": 100})
    assert d["changes"]["added"] == ["C"]
    assert d["changes"]["dropped"] == ["B"]
    assert d["pool_after"] == ["A", "C"]


def test_enabled_names_are_never_dropped():
    current = pool([("A", "X"), ("B", "Y")])
    prop = proposal([cand("C", score=90, sector="Z"), cand("B", action="drop", score=20, sector="Y")])
    s = make_settings(pool_target_size=2, max_adds_per_run=1, max_drops_per_run=1, liquidity_floor_jpy=1)
    d = decision(prop, current, s, enabled=["B"], liquidity={"C": 100, "B": 100})
    assert d["changes"]["added"] == []
    assert d["changes"]["dropped"] == []
    b = next(r for r in d["ranking"] if r["code"] == "B")
    assert b["reason"] == "enabled_protected"


def test_liquidity_floor_rejects_add():
    current = pool([("A", "X")])
    prop = proposal([cand("C", score=90, sector="Z")])
    s = make_settings(pool_target_size=3, liquidity_floor_jpy=1_000)
    d = decision(prop, current, s, liquidity={"C": 100})
    assert d["changes"]["added"] == []
    c = next(r for r in d["ranking"] if r["code"] == "C")
    assert c["reason"] == "liquidity_below_floor"


def test_missing_local_liquidity_rejects_add():
    current = pool([("A", "X")])
    prop = proposal([cand("C", score=90, sector="Z", liquidity=1_000_000)])
    s = make_settings(pool_target_size=3, liquidity_floor_jpy=1)
    d = decision(prop, current, s, liquidity={})
    c = next(r for r in d["ranking"] if r["code"] == "C")
    assert c["reason"] == "missing_local_liquidity"


def test_sector_cap_rejects_add():
    current = pool([("A", "X"), ("B", "Y")])
    prop = proposal([cand("C", score=90, sector="X")])
    s = make_settings(pool_target_size=4, pool_sector_cap_pct=40, liquidity_floor_jpy=1)
    d = decision(prop, current, s, liquidity={"C": 100})
    c = next(r for r in d["ranking"] if r["code"] == "C")
    assert c["reason"] == "sector_cap"


def test_cooldown_rejects_reentry():
    current = pool([("A", "X")])
    prop = proposal([cand("C", score=90, sector="Z")])
    s = make_settings(pool_target_size=3, liquidity_floor_jpy=1)
    d = decision(prop, current, s, liquidity={"C": 100}, cooldown={"C"})
    c = next(r for r in d["ranking"] if r["code"] == "C")
    assert c["reason"] == "pool_cooldown"


def test_malformed_proposal_is_noop():
    current = pool([("A", "X"), ("B", "Y")])
    d = decision({"bad": True}, current, make_settings(pool_target_size=4), liquidity={})
    assert d["changes"] == {"added": [], "dropped": []}
    assert d["pool_after"] == ["A", "B"]


def test_cleanup_removes_only_out_of_scope_warmup_files():
    old_watchlist = pool_merge.WATCHLIST_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pool_merge.WATCHLIST_DIR = Path(tmp)
            for code in ["A", "B", "C", "D"]:
                (pool_merge.WATCHLIST_DIR / f"{code}.parquet").write_bytes(b"x" * 3)
            result = pool_merge._cleanup_warmup_files({"A", "C"}, apply=True)
            remaining = sorted(p.stem for p in pool_merge.WATCHLIST_DIR.glob("*.parquet"))
            removed = sorted(item["code"] for item in result["warmup_files_removed"])
            assert remaining == ["A", "C"]
            assert removed == ["B", "D"]
            assert result["warmup_bytes_removed"] == 6
    finally:
        pool_merge.WATCHLIST_DIR = old_watchlist


def test_cadence_guard_respects_days_and_force():
    old_curation_dir = pool_merge.CURATION_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pool_merge.CURATION_DIR = Path(tmp)
            settings = make_settings(cadence_days=14)
            assert pool_merge.should_run_pool_refresh(settings, TODAY)["due"] is True
            write_json(
                pool_merge.CURATION_DIR / "pool_decision_latest.json",
                {"date": (TODAY - timedelta(days=7)).isoformat()},
            )
            assert pool_merge.should_run_pool_refresh(settings, TODAY)["due"] is False
            assert pool_merge.should_run_pool_refresh(settings, TODAY, force=True)["due"] is True
            write_json(
                pool_merge.CURATION_DIR / "pool_decision_latest.json",
                {"date": (TODAY - timedelta(days=1)).isoformat(), "proposal_valid": False},
            )
            invalid = pool_merge.should_run_pool_refresh(settings, TODAY)
            assert invalid["due"] is True
            assert invalid["reason"] == "previous_proposal_invalid"
            write_json(
                pool_merge.CURATION_DIR / "pool_decision_latest.json",
                {"date": (TODAY - timedelta(days=14)).isoformat()},
            )
            assert pool_merge.should_run_pool_refresh(settings, TODAY)["due"] is True
    finally:
        pool_merge.CURATION_DIR = old_curation_dir


ALL_TESTS = [
    test_grow_adds_up_to_cap_and_target,
    test_replace_mode_drops_disabled_never_changes_pool,
    test_replace_mode_pairs_add_with_drop_hint,
    test_enabled_names_are_never_dropped,
    test_liquidity_floor_rejects_add,
    test_missing_local_liquidity_rejects_add,
    test_sector_cap_rejects_add,
    test_cooldown_rejects_reentry,
    test_malformed_proposal_is_noop,
    test_cleanup_removes_only_out_of_scope_warmup_files,
    test_cadence_guard_respects_days_and_force,
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
