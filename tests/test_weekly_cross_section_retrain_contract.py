#!/usr/bin/env python3
"""Focused tests for Phase 2 weekly execution-provenance persistence."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.weekly_cross_section_retrain import (  # noqa: E402
    _attach_oos_execution_provenance,
    _oos_benchmark_coverage,
)
from src.execution import (  # noqa: E402
    ENTRY_PRICE_BASIS,
    EXECUTION_CONTRACT_VERSION,
    EXIT_PRICE_BASIS,
)


def test_attach_oos_execution_provenance() -> None:
    dates = pd.bdate_range("2026-03-02", periods=4)
    oos = pd.DataFrame(
        {
            "date": [dates[0], dates[0]],
            "ticker": ["A.JP", "B.JP"],
            "raw_score": [0.8, 0.2],
            "fwd_return": [0.1, -0.1],
        }
    )
    panel = pd.DataFrame(
        {
            "date": [dates[0], dates[0]],
            "ticker": ["A.JP", "B.JP"],
            "market_row_number": [10, 20],
            "market_as_of_date": [dates[0], dates[0]],
            "entry_date": [dates[1], dates[1]],
            "execution_exit_date": [dates[3], dates[3]],
            "entry_price": [100.0, 200.0],
            "execution_exit_price": [110.0, 180.0],
            "execution_contract_version": [EXECUTION_CONTRACT_VERSION] * 2,
            "entry_price_basis": [ENTRY_PRICE_BASIS] * 2,
            "exit_price_basis": [EXIT_PRICE_BASIS] * 2,
        }
    )
    bundle = {"oos_predictions": oos}

    _attach_oos_execution_provenance(bundle, panel)

    attached = bundle["oos_predictions"].set_index("ticker")
    assert attached.loc["A.JP", "entry_price"] == 100.0
    assert attached.loc["B.JP", "execution_exit_price"] == 180.0
    assert attached.loc["A.JP", "entry_date"] == dates[1]
    assert attached.loc["A.JP", "execution_exit_date"] == dates[3]
    assert (attached["execution_contract_version"] == EXECUTION_CONTRACT_VERSION).all()
    assert (attached["entry_price_basis"] == ENTRY_PRICE_BASIS).all()
    assert (attached["exit_price_basis"] == EXIT_PRICE_BASIS).all()


def test_oos_benchmark_coverage_requires_same_basis_open() -> None:
    dates = pd.bdate_range("2026-04-01", periods=3)
    oos = pd.DataFrame(
        {
            "date": [dates[0]],
            "ticker": ["A.JP"],
            "market_as_of_date": [dates[0]],
            "entry_date": [dates[1]],
            "execution_exit_date": [dates[2]],
            "execution_contract_version": [EXECUTION_CONTRACT_VERSION],
        }
    )
    close_only = pd.DataFrame({"date": dates, "topix": [2000.0, 2010.0, 2020.0]})
    missing = _oos_benchmark_coverage(oos, close_only)
    assert missing["available"] is False
    assert missing["available_periods"] == 0
    assert missing["reason"] == "topix_open_unavailable_same_basis"

    same_basis = close_only.assign(topix_open=[1995.0, 2005.0, 2015.0])
    complete = _oos_benchmark_coverage(oos, same_basis)
    assert complete["available"] is True
    assert complete["available_periods"] == complete["total_periods"] == 1
    assert complete["reason"] is None


def main() -> int:
    test_attach_oos_execution_provenance()
    print("PASS test_attach_oos_execution_provenance")
    test_oos_benchmark_coverage_requires_same_basis_open()
    print("PASS test_oos_benchmark_coverage_requires_same_basis_open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
