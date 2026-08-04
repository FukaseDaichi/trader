#!/usr/bin/env python3
"""
Unit tests for src/notifier.py retry helpers and send_line_text().
No network — _push_once is monkeypatched throughout.

Runnable:
  uv run python tests/test_notifier_retry.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.notifier as notifier  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny helper: fake exception with a .status attribute
# ---------------------------------------------------------------------------


class FakeLineError(Exception):
    def __init__(self, status):
        super().__init__(f"fake LINE error (status={status})")
        self.status = status


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


def test_should_retry_on_429_and_5xx():
    assert notifier._should_retry(429) is True
    assert notifier._should_retry(503) is True
    assert notifier._should_retry(None) is True  # timeout/connection error
    assert notifier._should_retry(400) is False  # bad request: give up


def test_backoff_grows():
    assert notifier._backoff_seconds(1, base=1.0) == 1.0
    assert notifier._backoff_seconds(2, base=1.0) == 4.0
    assert notifier._backoff_seconds(3, base=1.0) == 16.0


# ---------------------------------------------------------------------------
# send_line_text retry-loop tests (monkeypatched)
# ---------------------------------------------------------------------------


def _save_env():
    return {
        k: os.environ.pop(k, None)
        for k in ("TRADER_NOTIFY_RETRY_MAX", "TRADER_NOTIFY_RETRY_BASE_SEC")
    }


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_always_retryable_returns_false_sleeps_n_minus_1():
    """_push_once always raises 503 → returns False, sleep called max_attempts-1 times."""
    orig_push = notifier._push_once
    orig_cfg = notifier.LINE_CONFIG
    saved_env = _save_env()
    sleep_calls = []
    try:
        notifier.LINE_CONFIG = {"channel_access_token": "t", "user_id": "u"}
        # default max_attempts=3 → sleep called 2 times
        notifier._push_once = lambda token, user_id, text: (_ for _ in ()).throw(
            FakeLineError(503)
        )
        result = notifier.send_line_text(
            "hello", sleep_fn=lambda s: sleep_calls.append(s)
        )
        assert result is False
        assert len(sleep_calls) == 2  # max_attempts-1 = 3-1 = 2
    finally:
        notifier._push_once = orig_push
        notifier.LINE_CONFIG = orig_cfg
        _restore_env(saved_env)


def test_fast_fail_on_400():
    """_push_once raises 400 → fail fast, returns False, sleep never called."""
    orig_push = notifier._push_once
    orig_cfg = notifier.LINE_CONFIG
    saved_env = _save_env()
    sleep_calls = []
    try:
        notifier.LINE_CONFIG = {"channel_access_token": "t", "user_id": "u"}
        notifier._push_once = lambda token, user_id, text: (_ for _ in ()).throw(
            FakeLineError(400)
        )
        result = notifier.send_line_text(
            "hello", sleep_fn=lambda s: sleep_calls.append(s)
        )
        assert result is False
        assert len(sleep_calls) == 0
    finally:
        notifier._push_once = orig_push
        notifier.LINE_CONFIG = orig_cfg
        _restore_env(saved_env)


def test_retry_once_then_succeed():
    """_push_once raises retryable once then succeeds → returns True, sleep called once."""
    orig_push = notifier._push_once
    orig_cfg = notifier.LINE_CONFIG
    saved_env = _save_env()
    sleep_calls = []
    call_count = [0]
    try:
        notifier.LINE_CONFIG = {"channel_access_token": "t", "user_id": "u"}

        def push_once_flaky(token, user_id, text):
            call_count[0] += 1
            if call_count[0] == 1:
                raise FakeLineError(503)
            # second call succeeds (returns None)

        notifier._push_once = push_once_flaky
        result = notifier.send_line_text(
            "hello", sleep_fn=lambda s: sleep_calls.append(s)
        )
        assert result is True
        assert len(sleep_calls) == 1
    finally:
        notifier._push_once = orig_push
        notifier.LINE_CONFIG = orig_cfg
        _restore_env(saved_env)


def test_bad_env_value_falls_back_without_raising():
    """A non-numeric retry env must fall back to defaults, never raise (pipeline-safe)."""
    orig_push = notifier._push_once
    orig_cfg = notifier.LINE_CONFIG
    saved_env = _save_env()
    sleep_calls = []
    try:
        notifier.LINE_CONFIG = {"channel_access_token": "t", "user_id": "u"}
        os.environ["TRADER_NOTIFY_RETRY_MAX"] = "x"  # invalid int
        os.environ["TRADER_NOTIFY_RETRY_BASE_SEC"] = "y"  # invalid float
        notifier._push_once = lambda token, user_id, text: (_ for _ in ()).throw(
            FakeLineError(503)
        )
        result = notifier.send_line_text(
            "hello", sleep_fn=lambda s: sleep_calls.append(s)
        )
        assert result is False
        assert len(sleep_calls) == 2  # fell back to default max_attempts=3
    finally:
        notifier._push_once = orig_push
        notifier.LINE_CONFIG = orig_cfg
        _restore_env(saved_env)


def test_config_missing_returns_false_no_sleep_no_push():
    """Config missing → returns False immediately, no sleep, no _push_once call."""
    orig_push = notifier._push_once
    orig_cfg = notifier.LINE_CONFIG
    saved_env = _save_env()
    sleep_calls = []
    push_calls = [0]
    try:
        notifier.LINE_CONFIG = {"channel_access_token": None, "user_id": None}

        def push_should_not_be_called(token, user_id, text):
            push_calls[0] += 1

        notifier._push_once = push_should_not_be_called
        result = notifier.send_line_text(
            "hello", sleep_fn=lambda s: sleep_calls.append(s)
        )
        assert result is False
        assert len(sleep_calls) == 0
        assert push_calls[0] == 0
    finally:
        notifier._push_once = orig_push
        notifier.LINE_CONFIG = orig_cfg
        _restore_env(saved_env)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_should_retry_on_429_and_5xx,
    test_backoff_grows,
    test_always_retryable_returns_false_sleeps_n_minus_1,
    test_fast_fail_on_400,
    test_retry_once_then_succeed,
    test_bad_env_value_falls_back_without_raising,
    test_config_missing_returns_false_no_sleep_no_push,
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
