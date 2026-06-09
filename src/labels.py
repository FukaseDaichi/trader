"""
Phase 1 label generation (roadmap §6.1).

Pure pandas/numpy logic with NO database/network dependency, so it can be
unit-tested standalone (tests/test_labels.py). The legacy next-day binary
target is reproducible via `label_mode="binary_1d"` for rollback / A-B tests.

Every builder adds three canonical columns:

  - `fwd_return`   : H-day forward simple return, close[t+H]/close[t] - 1.
                     Used for realized-return backtests and expected-return
                     estimates (objective fact, independent of the label).
  - `target_class` : 0/1 up-down label used to train the probability head
                     (keeps `prob_up` / action mapping / calibration intact).
  - `target`       : the canonical training label for the mode. For binary
                     modes it equals `target_class`; for `vol_norm` it is the
                     continuous volatility-normalized forward return.

Rows whose label cannot be computed (the last H rows, or rows missing inputs
such as ATR/volatility) are left as NaN and dropped by build_labelled_frame().
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LABEL_MODES = ("triple_barrier", "vol_norm", "binary_1d")


def _forward_return(close: pd.Series, horizon_days: int) -> pd.Series:
    return close.shift(-int(horizon_days)) / close - 1.0


def _binary_from_forward(fwd: pd.Series) -> pd.Series:
    """0/1 where forward return is known, NaN otherwise (no future leakage)."""
    out = pd.Series(np.nan, index=fwd.index, dtype="float64")
    known = fwd.notna()
    out[known] = (fwd[known] > 0).astype("float64")
    return out


def add_forward_return_labels(df: pd.DataFrame, horizon_days: int = 5) -> pd.DataFrame:
    """H-day forward return + sign(forward return) binary label."""
    out = df.copy()
    fwd = _forward_return(out["close"], horizon_days)
    out["fwd_return"] = fwd
    out["target_class"] = _binary_from_forward(fwd)
    out["target"] = out["target_class"]
    return out


def add_vol_normalized_labels(
    df: pd.DataFrame, horizon_days: int = 5, vol_col: str = "volatility"
) -> pd.DataFrame:
    """Volatility-normalized forward return regression target (roadmap §6.1)."""
    out = add_forward_return_labels(df, horizon_days)
    if vol_col in out.columns:
        vol = pd.to_numeric(out[vol_col], errors="coerce")
    else:
        vol = pd.Series(np.nan, index=out.index, dtype="float64")
    safe_vol = vol.where(vol > 0)
    out["target_vol_norm"] = out["fwd_return"] / safe_vol
    out["target"] = out["target_vol_norm"]
    # target_class (sign of forward return) is kept for the probability head.
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

    For each entry row i (entry = close[i], a = ATR[i]):
      - take-profit barrier  = entry + tp_atr * a
      - stop-loss barrier     = entry - sl_atr * a
      - time barrier          = horizon_days bars ahead
    The label is the FIRST barrier touched scanning bars i+1..i+H:
      TP first -> 1, SL first -> 0; if neither is touched, time exit uses the
      sign of close[i+H] vs entry. When TP and SL are touched in the SAME bar,
      we conservatively assume SL first (worst case for a long).

    `fwd_return` is always the fixed H-day forward return (for the backtest),
    independent of where the barrier exit actually happened.
    """
    out = df.copy()
    n = len(out)
    h = max(1, int(horizon_days))

    close = pd.to_numeric(out["close"], errors="coerce").to_numpy(dtype="float64")
    high = pd.to_numeric(out["high"], errors="coerce").to_numpy(dtype="float64")
    low = pd.to_numeric(out["low"], errors="coerce").to_numpy(dtype="float64")
    if atr_col in out.columns:
        atr = pd.to_numeric(out[atr_col], errors="coerce").to_numpy(dtype="float64")
    else:
        atr = np.full(n, np.nan)

    labels = np.full(n, np.nan)
    reasons: list[str | None] = [None] * n

    for i in range(n):
        entry = close[i]
        a = atr[i]
        if not (np.isfinite(entry) and entry > 0 and np.isfinite(a) and a > 0):
            continue

        tp_level = entry + tp_atr * a
        sl_level = entry - sl_atr * a
        last = min(i + h, n - 1)

        resolved = False
        for j in range(i + 1, last + 1):
            touch_tp = np.isfinite(high[j]) and high[j] >= tp_level
            touch_sl = np.isfinite(low[j]) and low[j] <= sl_level
            if touch_tp and touch_sl:
                labels[i], reasons[i] = 0.0, "sl"  # same bar: conservative
                resolved = True
                break
            if touch_tp:
                labels[i], reasons[i] = 1.0, "tp"
                resolved = True
                break
            if touch_sl:
                labels[i], reasons[i] = 0.0, "sl"
                resolved = True
                break

        if not resolved and i + h <= n - 1 and np.isfinite(close[i + h]):
            labels[i] = 1.0 if close[i + h] > entry else 0.0
            reasons[i] = "time"
        # otherwise: not enough forward data -> leave NaN

    out["fwd_return"] = _forward_return(out["close"], h)
    out["target_class"] = labels
    out["target"] = labels
    out["tb_exit_reason"] = reasons
    return out


def target_kind(label_mode: str) -> str:
    """'regression' for vol_norm, otherwise 'binary' (probability head)."""
    return "regression" if label_mode == "vol_norm" else "binary"


def effective_horizon(config: dict | None) -> int:
    """
    Holding horizon (business days) implied by the label config:
      binary_1d -> 1, triple_barrier -> tb_max_days, vol_norm -> horizon_days.
    Used by the horizon-aware backtest and daily inference.
    """
    cfg = config or {}
    mode = cfg.get("label_mode", "triple_barrier")
    if mode == "binary_1d":
        return 1
    if mode == "triple_barrier":
        return max(1, int(cfg.get("tb_max_days", cfg.get("horizon_days", 5))))
    return max(1, int(cfg.get("horizon_days", 5)))


def build_labelled_frame(df: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """
    Dispatch to the configured label builder and return a clean labelled frame.

    config keys (see src.config.get_label_config):
      label_mode, horizon_days, tb_tp_atr, tb_sl_atr, tb_max_days, vol_col

    Rows missing a usable `target`, `target_class`, or `fwd_return` are dropped,
    so the last H rows (unknown forward return) always fall out.
    """
    cfg = config or {}
    mode = cfg.get("label_mode", "triple_barrier")
    horizon = max(1, int(cfg.get("horizon_days", 5)))

    out = df.copy()
    if "date" in out.columns:
        out = out.sort_values("date").reset_index(drop=True)
    else:
        out = out.reset_index(drop=True)

    if mode == "binary_1d":
        out = add_forward_return_labels(out, horizon_days=1)
    elif mode == "vol_norm":
        out = add_vol_normalized_labels(
            out, horizon_days=horizon, vol_col=cfg.get("vol_col", "volatility")
        )
    elif mode == "triple_barrier":
        out = add_triple_barrier_labels(
            out,
            horizon_days=int(cfg.get("tb_max_days", horizon)),
            tp_atr=float(cfg.get("tb_tp_atr", 1.5)),
            sl_atr=float(cfg.get("tb_sl_atr", 1.0)),
        )
    else:
        raise ValueError(f"unknown label_mode: {mode!r} (expected one of {LABEL_MODES})")

    out = out.dropna(subset=["target", "target_class", "fwd_return"]).reset_index(drop=True)
    if not out.empty:
        out["target_class"] = out["target_class"].astype(int)
    return out
