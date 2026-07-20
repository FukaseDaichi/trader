"""
Phase 1 label generation (roadmap §6.1).

Pure pandas/numpy logic with NO database/network dependency, so it can be
unit-tested standalone (tests/test_labels.py). The legacy next-day binary
target is reproducible via `label_mode="binary_1d"` for rollback / A-B tests.

Every builder adds three canonical columns under execution contract
``next_session_open_to_close_v2``:

  - `fwd_return`   : executable H-session return,
                     close[t+H]/open[t+1] - 1.
                     Used for realized-return backtests and expected-return
                     estimates (objective fact, independent of the label).
  - `target_class` : 0/1 up-down label used to train the probability head
                     (keeps `prob_up` / action mapping / calibration intact).
  - `target`       : the canonical binary training label. It equals
                     `target_class` for every supported Phase 1 mode.

Rows whose label cannot be computed (the last H rows, or rows missing inputs
such as ATR/volatility) are left as NaN and dropped by build_labelled_frame().
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .execution import add_execution_columns, first_barrier_touch

LABEL_MODES = ("triple_barrier", "binary_1d")


def _normalize_label_mode(label_mode: str) -> str:
    """Normalize Phase 1 mode, preserving safe startup for stale settings."""
    mode = str(label_mode).strip().lower()
    if mode == "vol_norm":
        warnings.warn(
            "Phase 1 label_mode='vol_norm' is no longer supported; "
            "falling back to 'triple_barrier'",
            RuntimeWarning,
            stacklevel=3,
        )
        return "triple_barrier"
    if mode not in LABEL_MODES:
        raise ValueError(
            f"unknown label_mode: {mode!r} (expected one of {LABEL_MODES})"
        )
    return mode


def _binary_from_forward(fwd: pd.Series) -> pd.Series:
    """0/1 where forward return is known, NaN otherwise (no future leakage)."""
    out = pd.Series(np.nan, index=fwd.index, dtype="float64")
    known = fwd.notna()
    out[known] = (fwd[known] > 0).astype("float64")
    return out


def add_forward_return_labels(df: pd.DataFrame, horizon_days: int = 5) -> pd.DataFrame:
    """Executable H-session return + sign(return) binary label."""
    out = add_execution_columns(df, horizon_days)
    out["target_class"] = _binary_from_forward(out["fwd_return"])
    out["target"] = out["target_class"]
    return out


def add_triple_barrier_labels(
    df: pd.DataFrame,
    horizon_days: int = 5,
    tp_atr: float = 1.5,
    sl_atr: float = 1.0,
    atr_col: str = "atr",
) -> pd.DataFrame:
    """
    Triple-barrier label (López de Prado style), aligned with manual TP/SL.

    For each market-as-of row i (a = ATR[i]):
      - executable entry      = open[i+1]
      - take-profit barrier   = entry + tp_atr * a
      - stop-loss barrier     = entry - sl_atr * a
      - time barrier          = close[i+H] (H executable sessions)
    The label is the FIRST barrier touched scanning bars i+1..i+H.  Each
    session's open is checked before its high/low, so an overnight gap through
    a barrier resolves at that open:
      TP first -> 1, SL first -> 0; if neither is touched, time exit uses the
      sign of close[i+H] vs open[i+1]. When TP and SL are touched in the SAME
      bar, we conservatively assume SL first (worst case for a long).

    `fwd_return` is always the fixed executable H-session return (for the
    backtest), independent of where the barrier exit actually happened.
    """
    out = add_execution_columns(df, horizon_days)
    n = len(out)
    h = max(1, int(horizon_days))

    close = pd.to_numeric(out["close"], errors="coerce").to_numpy(dtype="float64")
    entry_price = pd.to_numeric(out["entry_price"], errors="coerce").to_numpy(
        dtype="float64"
    )
    if atr_col in out.columns:
        atr = pd.to_numeric(out[atr_col], errors="coerce").to_numpy(dtype="float64")
    else:
        atr = np.full(n, np.nan)

    labels = np.full(n, np.nan)
    reasons: list[str | None] = [None] * n
    exit_dates: list[str | None] = [None] * n

    for i in range(n):
        entry = entry_price[i]
        a = atr[i]
        if not (np.isfinite(entry) and entry > 0 and np.isfinite(a) and a > 0):
            continue

        if i + h > n - 1:
            continue

        tp_level = entry + tp_atr * a
        sl_level = entry - sl_atr * a
        exit_index, reason = first_barrier_touch(
            out,
            entry_index=i + 1,
            exit_index=i + h,
            take_profit=tp_level,
            stop_loss=sl_level,
        )
        if reason is not None:
            labels[i] = 1.0 if reason.startswith("tp") else 0.0
            reasons[i] = reason
            exit_dates[i] = str(pd.Timestamp(out["date"].iloc[exit_index]).date())
        elif np.isfinite(close[i + h]):
            labels[i] = 1.0 if close[i + h] > entry else 0.0
            reasons[i] = "time"
            exit_dates[i] = str(pd.Timestamp(out["date"].iloc[i + h]).date())

    out["target_class"] = labels
    out["target"] = labels
    out["tb_exit_reason"] = reasons
    out["tb_exit_date"] = exit_dates
    return out


def effective_horizon(config: dict | None) -> int:
    """
    Holding horizon (business days) implied by the label config:
      binary_1d -> 1, triple_barrier -> tb_max_days.
    Used by the horizon-aware backtest and daily inference.
    """
    cfg = config or {}
    mode = _normalize_label_mode(cfg.get("label_mode", "triple_barrier"))
    if mode == "binary_1d":
        return 1
    return max(1, int(cfg.get("tb_max_days", cfg.get("horizon_days", 5))))


def build_labelled_frame(df: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """
    Dispatch to the configured label builder and return a clean labelled frame.

    config keys (see src.config.get_label_config):
      label_mode, horizon_days, tb_tp_atr, tb_sl_atr, tb_max_days

    Rows missing a usable `target`, `target_class`, or `fwd_return` are dropped,
    so the last H rows (unknown forward return) always fall out.
    """
    cfg = config or {}
    mode = _normalize_label_mode(cfg.get("label_mode", "triple_barrier"))
    horizon = max(1, int(cfg.get("horizon_days", 5)))

    out = df.copy()
    if "date" in out.columns:
        out = out.sort_values("date").reset_index(drop=True)
    else:
        out = out.reset_index(drop=True)

    if mode == "binary_1d":
        out = add_forward_return_labels(out, horizon_days=1)
    else:
        out = add_triple_barrier_labels(
            out,
            horizon_days=int(cfg.get("tb_max_days", horizon)),
            tp_atr=float(cfg.get("tb_tp_atr", 1.5)),
            sl_atr=float(cfg.get("tb_sl_atr", 1.0)),
        )

    out = out.dropna(subset=["target", "target_class", "fwd_return"]).reset_index(
        drop=True
    )
    if not out.empty:
        out["target_class"] = out["target_class"].astype(int)
    return out
