"""Single home for environment-variable parsing.

Call sites pick an ``invalid`` policy so the legacy per-module semantics stay
intact:
- "raise": startup-critical config fails fast (src/config.py style)
- "default": runtime knobs warn and fall back to the default
  (src/data_loader.py style)
- "false" (bool only): unrecognized values mean False (predicate style used
  by src/db.py and main.py)
"""

from __future__ import annotations

import math
import os

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def get_env_str(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    return raw if raw not in (None, "") else default


def get_env_int(name: str, default: int, *, invalid: str = "default") -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return int(default)
    try:
        return int(raw)
    except ValueError as e:
        if invalid == "raise":
            raise ValueError(f"{name} must be an integer value") from e
        print(f"Invalid {name}={raw!r}. Falling back to {default}.")
        return int(default)


def get_env_float(name: str, default: float, *, invalid: str = "default") -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return float(default)
    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError
        return value
    except ValueError as e:
        if invalid == "raise":
            raise ValueError(f"{name} must be a finite float value") from e
        print(f"Invalid {name}={raw!r}. Falling back to {default}.")
        return float(default)


def get_env_bool(name: str, default: bool, *, invalid: str = "default") -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return bool(default)
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    if invalid == "raise":
        raise ValueError(f"{name} must be a boolean value (true/false)")
    if invalid == "false":
        return False
    print(f"Invalid {name}={raw!r}. Falling back to {default}.")
    return bool(default)
