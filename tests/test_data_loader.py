#!/usr/bin/env python3
"""Unit tests for OHLCV boundary validation and the Stooq circuit breaker."""

from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.data_loader as dl  # noqa: E402
from src.data_loader import _validate_ohlcv  # noqa: E402
from src.timeutil import JST  # noqa: E402

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


def test_normalize_ohlcv_drops_dates_ns_cannot_hold():
    # pandas 3 parses a pre-1677 date at [us]; it must drop that row (as
    # pandas 2's errors="coerce" did), not fail the whole download.
    csv = STOOQ_CSV + "1600-01-01,100,102,99,101,1000\n"
    normalized = dl._normalize_ohlcv(pd.read_csv(io.StringIO(csv)))
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


def _bars(dates, volumes=None):
    n = len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates).astype("datetime64[ns]"),
            "open": [100.0 + i for i in range(n)],
            "high": [102.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [101.0 + i for i in range(n)],
            "volume": volumes or [1000.0] * n,
        }
    )


def test_intraday_drops_todays_unconfirmed_bar():
    # 2026-11-13 10:45 JST: the late daily run saw today's half-formed bar.
    frame = _bars(["2026-11-11", "2026-11-12", "2026-11-13"])
    now = datetime(2026, 11, 13, 10, 45, tzinfo=JST)

    out = dl.drop_unconfirmed_bars(frame, now=now, ticker_code="7203.JP")

    assert out["date"].tolist() == [
        pd.Timestamp("2026-11-11"),
        pd.Timestamp("2026-11-12"),
    ]
    # Input frame is not mutated.
    assert len(frame) == 3


def test_bar_kept_once_session_is_confirmed():
    frame = _bars(["2026-11-12", "2026-11-13"])
    for now in (
        datetime(2026, 11, 13, 16, 0, tzinfo=JST),
        datetime(2026, 11, 13, 23, 59, tzinfo=JST),
        datetime(2026, 11, 14, 6, 0, tzinfo=JST),  # normal pre-open run
    ):
        out = dl.drop_unconfirmed_bars(frame, now=now)
        assert out is frame, now


def test_confirmation_cutoff_uses_jst_wall_clock():
    frame = _bars(["2026-11-12", "2026-11-13"])
    # 06:59 UTC == 15:59 JST (still unconfirmed); 07:00 UTC == 16:00 JST.
    before = dl.drop_unconfirmed_bars(
        frame, now=datetime(2026, 11, 13, 6, 59, tzinfo=UTC)
    )
    after = dl.drop_unconfirmed_bars(
        frame, now=datetime(2026, 11, 13, 7, 0, tzinfo=UTC)
    )
    assert before["date"].max() == pd.Timestamp("2026-11-12")
    assert after["date"].max() == pd.Timestamp("2026-11-13")
    # A naive datetime is read as JST.
    naive = dl.drop_unconfirmed_bars(frame, now=datetime(2026, 11, 13, 15, 0))
    assert naive["date"].max() == pd.Timestamp("2026-11-12")


def test_future_dated_bar_is_always_dropped():
    frame = _bars(["2026-11-13", "2026-11-16"])
    out = dl.drop_unconfirmed_bars(frame, now=datetime(2026, 11, 13, 20, 0, tzinfo=JST))
    assert out["date"].tolist() == [pd.Timestamp("2026-11-13")]


def test_update_data_does_not_persist_unconfirmed_bar():
    on_disk = _bars(["2026-11-10", "2026-11-11", "2026-11-12"])
    # Provider returns a revised 11/12 plus today's partial 11/13 bar.
    fetched = _bars(["2026-11-12", "2026-11-13"], volumes=[19336700.0, 6629900.0])
    fresh = {"is_stale": False}
    now = datetime(2026, 11, 13, 10, 45, tzinfo=JST)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "7203.JP.parquet"
        on_disk.to_parquet(path)
        with (
            patch.object(
                dl, "_download_with_fallback", return_value=(fetched, "stooq", fresh)
            ),
            patch.object(dl, "_now_jst", return_value=now),
        ):
            returned = dl.update_data("7203.JP", dest_dir=tmp)

        saved = pd.read_parquet(path)

    expected_dates = [
        pd.Timestamp("2026-11-10"),
        pd.Timestamp("2026-11-11"),
        pd.Timestamp("2026-11-12"),
    ]
    assert returned["date"].tolist() == expected_dates
    assert saved["date"].tolist() == expected_dates
    # The confirmed bar the provider revised is still taken from the download.
    assert saved["volume"].iloc[-1] == 19336700.0


def test_load_data_ignores_persisted_unconfirmed_bar():
    # A parquet committed by an earlier intraday run still holds today's bar.
    persisted = _bars(["2026-11-11", "2026-11-12", "2026-11-13"])
    with tempfile.TemporaryDirectory() as tmp:
        persisted.to_parquet(Path(tmp) / "7203.JP.parquet")
        with patch.object(dl, "DATA_DIR", Path(tmp)):
            with patch.object(
                dl, "_now_jst", return_value=datetime(2026, 11, 13, 12, 0, tzinfo=JST)
            ):
                intraday = dl.load_data("7203.JP")
            with patch.object(
                dl, "_now_jst", return_value=datetime(2026, 11, 14, 6, 0, tzinfo=JST)
            ):
                next_morning = dl.load_data("7203.JP")

    assert intraday["date"].max() == pd.Timestamp("2026-11-12")
    assert next_morning["date"].max() == pd.Timestamp("2026-11-13")


ALL_TESTS = [
    test_nonfinite_ohlcv_rows_are_rejected,
    test_normalize_ohlcv_pins_date_to_ns,
    test_normalize_ohlcv_drops_dates_ns_cannot_hold,
    test_stooq_circuit_opens_after_consecutive_connect_failures,
    test_stooq_half_open_probe_after_cooldown,
    test_stooq_success_resets_consecutive_failure_count,
    test_stooq_http_error_does_not_trip_circuit,
    test_stooq_circuit_disabled_with_zero_threshold,
    test_open_circuit_falls_through_to_yfinance,
    test_intraday_drops_todays_unconfirmed_bar,
    test_bar_kept_once_session_is_confirmed,
    test_confirmation_cutoff_uses_jst_wall_clock,
    test_future_dated_bar_is_always_dropped,
    test_update_data_does_not_persist_unconfirmed_bar,
    test_load_data_ignores_persisted_unconfirmed_bar,
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
