"""Small cross-cutting helpers for the daily pipeline."""

from __future__ import annotations

import traceback


def log_exc(label: str, exc: BaseException) -> None:
    """Print a one-line summary plus the traceback for a swallowed exception.

    The daily pipeline deliberately swallows exceptions so a single failure
    never stops the run; this keeps that behavior while preserving the stack
    trace for debugging in GitHub Actions logs.
    """
    print(f"{label}: {type(exc).__name__}: {exc}")
    traceback.print_exception(exc)
