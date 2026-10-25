#!/usr/bin/env python3
"""Unit tests for OHLCV boundary validation and the Stooq circuit breaker."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.data_loader as dl  # noqa: E402
from src.data_loader import _validate_ohlcv  # noqa: E402

STOOQ_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-10-15,100,102,99,101,1000\n"
    "2026-10-16,101,103,100,102,1100\n"
)


def test_nonfinite_ohlcv_rows_are_rejected():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-07-21", periods=3, freq="D"),
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, float("inf")],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, float("inf"), 102.0],
            "volume": [1000.0, 1100.0, 1200.0],
        }
    )

    validated = _validate_ohlcv(frame, ticker_code="9999.JP", source="test")

    assert len(validated) == 1
    assert validated.iloc[0]["date"] == pd.Timestamp("2026-07-21")
    assert "invalid_non_finite_ohlcv:2" in validated.attrs["validation_warnings"]


def test_normalize_ohlcv_pins_date_to_ns():
    # yfinance under pandas 3 indexes dates as datetime64[s]; the persisted
    # parquet schema and downstream merge_asof keys stay on [ns].
    index = pd.DatetimeIndex(
        pd.to_datetime(["2026-10-15", "2026-10-16"]).as_unit("s"), name="Date"
    )
    yf_like = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000, 1100],
        },
        index=index,
    )
    for raw in (yf_like.reset_index(), pd.read_csv(io.StringIO(STOOQ_CSV))):
        normalized = dl._normalize_ohlcv(raw)
        assert str(normalized["date"].dtype) == "datetime64[ns]"
        assert normalized["date"].tolist() == [
            pd.Timestamp("2026-10-15"),
            pd.Timestamp("2026-10-16"),
        ]


def _ok_response():
    response = MagicMock()
    response.text = STOOQ_CSV
    response.raise_for_status.return_value = None
    return response


def _connect_timeout(*_args, **_kwargs):
    raise requests.exceptions.ConnectTimeout("Connection to stooq.com timed out.")


def test_stooq_circuit_opens_after_consecutive_connect_failures():
    dl.reset_stooq_circuit()
    try:
        with (
            patch.dict(os.environ, {"TRADER_STOOQ_CIRCUIT_BREAKER_THRESHOLD": "3"}),
            patch.object(dl.requests, "get", side_effect=_connect_timeout) as get,
        ):
            results = [dl.download_stooq_data(f"{i}.JP") for i in range(50)]

        assert all(result is None for result in results)
        assert get.call_count == 3
    finally:
        dl.reset_stooq_circuit()


def test_stooq_half_open_probe_after_cooldown():
    dl.reset_stooq_circuit()
    clock = [1000.0]
    outcomes = iter(
        [_connect_timeout, _connect_timeout, _connect_timeout]
        + [lambda *_a, **_k: _ok_response()] * 2
    )
    env = {
        "TRADER_STOOQ_CIRCUIT_BREAKER_THRESHOLD": "1",
        "TRADER_STOOQ_CIRCUIT_BREAKER_COOLDOWN_SEC": "300",
    }
    try:
        with (
            patch.dict(os.environ, env),
            patch.object(dl.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(
                dl.requests, "get", side_effect=lambda *a, **k: next(outcomes)(*a, **k)
            ) as get,
        ):
            assert dl.download_stooq_data("A.JP") is None  # opens
            assert dl.download_stooq_data("B.JP") is None  # skipped
            assert get.call_count == 1

            clock[0] += 301
            assert dl.download_stooq_data("C.JP") is None  # failed probe
            assert get.call_count == 2
            clock[0] += 100
            assert dl.download_stooq_data("D.JP") is None  # cooldown restarted
            assert get.call_count == 2

            clock[0] += 201
            assert dl.download_stooq_data("E.JP") is None  # failed probe again
            assert get.call_count == 3

            clock[0] += 301
            assert dl.download_stooq_data("F.JP") is not None  # probe succeeds
            assert dl.download_stooq_data("G.JP") is not None  # closed again
            assert get.call_count == 5
    finally:
        dl.reset_stooq_circuit()


def test_stooq_success_resets_consecutive_failure_count():
    dl.reset_stooq_circuit()
    responses = [
        _connect_timeout,
        _connect_timeout,
        lambda *_a, **_k: _ok_response(),
        _connect_timeout,
        _connect_timeout,
        lambda *_a, **_k: _ok_response(),
    ]
    calls = iter(responses)
    try:
        with (
            patch.dict(os.environ, {"TRADER_STOOQ_CIRCUIT_BREAKER_THRESHOLD": "3"}),
            patch.object(
                dl.requests, "get", side_effect=lambda *a, **k: next(calls)(*a, **k)
            ) as get,
        ):
            results = [dl.download_stooq_data("7011.JP") for _ in range(6)]

        assert get.call_count == 6
        assert results[2] is not None and len(results[2]) == 2
        assert results[5] is not None
    finally:
        dl.reset_stooq_circuit()


def test_stooq_http_error_does_not_trip_circuit():
    dl.reset_stooq_circuit()
    response = MagicMock()
    response.raise_for_status.side_effect = requests.exceptions.HTTPError("503")
    try:
        with (
            patch.dict(os.environ, {"TRADER_STOOQ_CIRCUIT_BREAKER_THRESHOLD": "2"}),
            patch.object(dl.requests, "get", return_value=response) as get,
        ):
            for _ in range(5):
                assert dl.download_stooq_data("7011.JP") is None

        assert get.call_count == 5
    finally:
        dl.reset_stooq_circuit()


def test_stooq_circuit_disabled_with_zero_threshold():
    dl.reset_stooq_circuit()
    try:
        with (
            patch.dict(os.environ, {"TRADER_STOOQ_CIRCUIT_BREAKER_THRESHOLD": "0"}),
            patch.object(dl.requests, "get", side_effect=_connect_timeout) as get,
        ):
            for _ in range(10):
                dl.download_stooq_data("7011.JP")

        assert get.call_count == 10
    finally:
        dl.reset_stooq_circuit()


def test_open_circuit_falls_through_to_yfinance():
    dl.reset_stooq_circuit()
    yf_frame = dl._normalize_ohlcv(pd.read_csv(io.StringIO(STOOQ_CSV)))
    fresh = {
        "is_stale": False,
        "latest_date": None,
        "required_latest_date": None,
        "open_day_gap": 0,
    }
    try:
        with (
            patch.dict(
                os.environ,
                {
                    "TRADER_STOOQ_CIRCUIT_BREAKER_THRESHOLD": "1",
                    "TRADER_YF_FALLBACK_ENABLED": "true",
                },
            ),
            patch.object(dl.requests, "get", side_effect=_connect_timeout) as get,
            patch.object(dl, "download_yfinance_data", return_value=yf_frame) as yf,
            patch.object(dl, "_assess_freshness", return_value=fresh),
        ):
            for code in ("7011.JP", "6501.JP", "6701.JP"):
                df, source, _ = dl._download_with_fallback(code)
                assert source == "yfinance"
                assert df is yf_frame

        assert get.call_count == 1
        assert yf.call_count == 3
    finally:
        dl.reset_stooq_circuit()


ALL_TESTS = [
    test_nonfinite_ohlcv_rows_are_rejected,
    test_normalize_ohlcv_pins_date_to_ns,
    test_stooq_circuit_opens_after_consecutive_connect_failures,
    test_stooq_half_open_probe_after_cooldown,
    test_stooq_success_resets_consecutive_failure_count,
    test_stooq_http_error_does_not_trip_circuit,
    test_stooq_circuit_disabled_with_zero_threshold,
    test_open_circuit_falls_through_to_yfinance,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
