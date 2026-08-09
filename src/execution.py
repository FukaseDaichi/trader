"""Shared executable-price contract for labels, backtests, and settlement.

The daily pipeline runs before the JPX open with market data through the prior
session.  A signal therefore cannot enter at ``market_as_of_date``'s close.
Contract v2 enters at the next available market row's open and exits at the
close of the ``horizon_days``-th session after entry.  Missing calendar dates
(weekends and JPX holidays) require no special case: the next observed market
row is the first executable session.

This module is deliberately pure.  It owns the positional/date convention so
label generation, OOS simulation, and DB settlement cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


LEGACY_EXECUTION_CONTRACT_VERSION = "close_to_close_v1"
EXECUTION_CONTRACT_VERSION = "next_session_open_to_close_v2"

MARKET_AS_OF_BASIS = "session_close"
DECISION_TIMING = "next_session_preopen"
ENTRY_PRICE_BASIS = "next_session_open"
EXIT_PRICE_BASIS = "horizon_session_close"
BENCHMARK_BASIS = "unavailable_same_basis"

# The basis actually achieved when a same-basis benchmark IS computable.
# Deliberately NOT the default in execution_contract_metadata(): that dict is
# hashed into the Phase 1 gate contract, so changing it invalidates every saved
# model bundle.  Consumers that hold a real benchmark override the exported
# "benchmark_basis" with this value (see src/performance.py and
# scripts/settle_outcomes.py).
SAME_BASIS_BENCHMARK = f"{ENTRY_PRICE_BASIS}_to_{EXIT_PRICE_BASIS}"


def execution_contract_metadata(
    *, cost_bps: float | None = None, slippage_bps: float | None = None
) -> dict:
    """Return JSON-safe metadata for performance and audit artifacts."""
    metadata = {
        "contract_version": EXECUTION_CONTRACT_VERSION,
        "market_as_of_basis": MARKET_AS_OF_BASIS,
        "decision_timing": DECISION_TIMING,
        "entry_price_basis": ENTRY_PRICE_BASIS,
        "exit_price_basis": EXIT_PRICE_BASIS,
        "benchmark_basis": BENCHMARK_BASIS,
        # Outcomes are objective open-to-close price returns.  Trading costs
        # and configured slippage are charged by the strategy simulator rather
        # than hidden inside the observed market prices.
        "return_basis": "raw_market_price_before_costs",
        "cost_treatment": "strategy_simulation_only",
    }
    if cost_bps is not None:
        metadata["cost_bps_per_side"] = float(cost_bps)
    if slippage_bps is not None:
        metadata["slippage_bps_per_side"] = float(slippage_bps)
    return metadata


def _iso_date(value) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.strftime("%Y-%m-%d")


def _positive_price(value, name: str) -> float:
    price = float(value)
    if not np.isfinite(price) or price <= 0:
        raise ValueError(f"{name} must be a positive finite price")
    return price


@dataclass(frozen=True)
class ExecutionWindow:
    """One signal's executable entry/exit window in positional market data."""

    market_as_of_index: int
    entry_index: int
    exit_index: int
    market_as_of_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float

    @property
    def realized_return(self) -> float:
        return self.exit_price / self.entry_price - 1.0


def resolve_execution_window(
    market_frame: pd.DataFrame,
    market_as_of_index: int,
    horizon_days: int,
) -> ExecutionWindow | None:
    """Resolve v2 entry/exit rows, returning ``None`` until enough data exists.

    ``market_as_of_index`` is the last row available to the pre-open decision.
    For horizon H, entry is row ``i + 1`` open and exit is row ``i + H`` close.
    Thus H=1 means next-session open to that same session's close.
    """
    h = max(1, int(horizon_days))
    i = int(market_as_of_index)
    if i < 0 or i >= len(market_frame):
        raise IndexError("market_as_of_index is outside market_frame")
    required = {"date", "open", "close"}
    missing = required.difference(market_frame.columns)
    if missing:
        raise ValueError(f"market_frame missing execution columns: {sorted(missing)}")

    entry_index = i + 1
    exit_index = i + h
    if exit_index >= len(market_frame):
        return None

    return ExecutionWindow(
        market_as_of_index=i,
        entry_index=entry_index,
        exit_index=exit_index,
        market_as_of_date=_iso_date(market_frame["date"].iloc[i]),
        entry_date=_iso_date(market_frame["date"].iloc[entry_index]),
        exit_date=_iso_date(market_frame["date"].iloc[exit_index]),
        entry_price=_positive_price(
            market_frame["open"].iloc[entry_index], "entry open"
        ),
        exit_price=_positive_price(
            market_frame["close"].iloc[exit_index], "exit close"
        ),
    )


def add_execution_columns(
    market_frame: pd.DataFrame, horizon_days: int
) -> pd.DataFrame:
    """Add vectorized v2 dates/prices/returns without changing row order.

    ``entry_session_return`` is next-open to next-close for a newly opened
    sleeve. ``continuation_session_return`` is current-close to next-close for
    sleeves already held at the prior close.  Keeping both prevents overnight
    returns from disappearing in an overlapping-cohort simulation.
    """
    h = max(1, int(horizon_days))
    required = {"date", "open", "close"}
    missing = required.difference(market_frame.columns)
    if missing:
        raise ValueError(f"market_frame missing execution columns: {sorted(missing)}")

    out = market_frame.copy()
    out["market_row_number"] = np.arange(len(out), dtype="int64")
    date = pd.to_datetime(out["date"], errors="coerce")
    open_price = pd.to_numeric(out["open"], errors="coerce").where(lambda s: s > 0)
    close = pd.to_numeric(out["close"], errors="coerce").where(lambda s: s > 0)

    out["market_as_of_date"] = date
    out["entry_date"] = date.shift(-1)
    out["execution_exit_date"] = date.shift(-h)
    out["entry_price"] = open_price.shift(-1)
    out["execution_exit_price"] = close.shift(-h)
    out["fwd_return"] = out["execution_exit_price"] / out["entry_price"] - 1.0
    out["entry_session_return"] = close.shift(-1) / open_price.shift(-1) - 1.0
    out["continuation_session_return"] = close.shift(-1) / close - 1.0
    path_returns: list[list[float] | None] = []
    path_dates: list[list[pd.Timestamp] | None] = []
    path_market_rows: list[list[int] | None] = []
    for i in range(len(out)):
        if i + h >= len(out):
            path_returns.append(None)
            path_dates.append(None)
            path_market_rows.append(None)
            continue
        returns = [float(close.iloc[i + 1] / open_price.iloc[i + 1] - 1.0)]
        returns.extend(
            float(close.iloc[j] / close.iloc[j - 1] - 1.0)
            for j in range(i + 2, i + h + 1)
        )
        path_returns.append(returns)
        path_dates.append([pd.Timestamp(date.iloc[j]) for j in range(i + 1, i + h + 1)])
        path_market_rows.append(list(range(i + 1, i + h + 1)))
    out["execution_path_returns"] = path_returns
    out["execution_path_dates"] = path_dates
    out["execution_path_market_rows"] = path_market_rows
    out["execution_contract_version"] = EXECUTION_CONTRACT_VERSION
    return out


def first_barrier_touch(
    market_frame: pd.DataFrame,
    *,
    entry_index: int,
    exit_index: int,
    take_profit: float,
    stop_loss: float,
) -> tuple[int | None, str | None]:
    """Return the first touched barrier with explicit overnight-gap handling.

    At each session, the open is evaluated before the intraday high/low.  A gap
    through a barrier therefore resolves at that session's open.  If both
    intraday barriers are visible in one OHLC bar, the long-side conservative
    rule assumes the stop was touched first.
    """
    required = {"open", "high", "low"}
    missing = required.difference(market_frame.columns)
    if missing:
        raise ValueError(f"market_frame missing barrier columns: {sorted(missing)}")

    tp = float(take_profit)
    sl = float(stop_loss)
    for j in range(int(entry_index), int(exit_index) + 1):
        session_open = pd.to_numeric(
            pd.Series([market_frame["open"].iloc[j]]), errors="coerce"
        ).iloc[0]
        if np.isfinite(session_open):
            if session_open <= sl:
                return j, "sl_gap"
            if session_open >= tp:
                return j, "tp_gap"

        high = pd.to_numeric(
            pd.Series([market_frame["high"].iloc[j]]), errors="coerce"
        ).iloc[0]
        low = pd.to_numeric(
            pd.Series([market_frame["low"].iloc[j]]), errors="coerce"
        ).iloc[0]
        touch_tp = np.isfinite(high) and high >= tp
        touch_sl = np.isfinite(low) and low <= sl
        if touch_tp and touch_sl:
            return j, "sl"
        if touch_tp:
            return j, "tp"
        if touch_sl:
            return j, "sl"
    return None, None
