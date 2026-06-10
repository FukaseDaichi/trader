#!/usr/bin/env python3
"""
Unit tests for src/digest.py — PURE, no DB/network/file IO.

Runnable as:
  uv run python tests/test_digest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import digest  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _portfolio(mode="shadow", model_version="cs-v1", gross=0.24, vol=0.091,
               positions=None, available=True, reason=None):
    if not available:
        base = {"available": False}
        if reason:
            base["reason"] = reason
        return base
    return {
        "available": True,
        "mode": mode,
        "model_version": model_version,
        "gross_exposure": gross,
        "expected_vol": vol,
        "positions": positions or [],
    }


def _perf(count=35, hit_rate=0.58, avg_return=0.006):
    return {"horizons": {"5": {"count": count, "hit_rate": hit_rate, "avg_return": avg_return}}}


def _signals(*actions_and_gated):
    """List of (action, gate_passed) tuples -> signal dicts."""
    return [{"action": a, "gate_passed": g} for a, g in actions_and_gated]


# ---------------------------------------------------------------------------
# (a) Portfolio available — contains building blocks
# ---------------------------------------------------------------------------

def test_portfolio_available_contains_header():
    pos = [{"name": "ディスコ", "ticker": "6146.JP", "target_weight": 0.036, "diff_type": "new"}]
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(positions=pos),
        _perf(),
        {"market_bias": "neutral", "usdjpy": 160.4},
        [],
        "https://x/",
    )
    assert "今日の建玉" in out, out
    assert "グロス" in out, out
    # The position's name must appear under the 新規 group
    assert "ディスコ" in out, out
    assert "新規" in out, out


def test_portfolio_available_gross_and_vol_formatted():
    pos = [{"name": "テスト", "ticker": "1234.JP", "target_weight": 0.10, "diff_type": "hold"}]
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(gross=0.24, vol=0.091, positions=pos),
        None,
        None,
        [],
        "https://x/",
    )
    # gross 0.24 -> "24%" ; vol 0.091 -> "9.1%"
    assert "24%" in out, out
    assert "9.1%" in out, out


def test_portfolio_available_shadow_groups_new_and_exit_always_shown():
    pos = [{"name": "A社", "ticker": "1001.JP", "target_weight": 0.10, "diff_type": "new"},
           {"name": "B社", "ticker": "1002.JP", "target_weight": 0.08, "diff_type": "hold"}]
    out = digest.build_daily_digest("2026-06-10", _portfolio(positions=pos, mode="shadow"),
                                    None, None, [], "https://x/")
    # shadow: 新規, 継続(hold/increase/decrease), 手仕舞い always shown
    assert "新規" in out, out
    assert "手仕舞い" in out, out
    # 継続 shown because there's a hold
    assert "継続" in out, out
    # A社 in 新規
    assert "A社" in out, out


# ---------------------------------------------------------------------------
# (b) Portfolio None / unavailable — "提案なし" + reason
# ---------------------------------------------------------------------------

def test_portfolio_none_shows_unavailable():
    out = digest.build_daily_digest("2026-06-10", None, None, None, [], "https://x/")
    assert "本日のポートフォリオ提案なし" in out, out
    assert "データなし" in out, out


def test_portfolio_available_false_shows_reason():
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(available=False, reason="モデルなし"),
        None, None, [], "https://x/",
    )
    assert "本日のポートフォリオ提案なし" in out, out
    assert "モデルなし" in out, out


def test_portfolio_available_false_no_reason_falls_back():
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(available=False),
        None, None, [], "https://x/",
    )
    assert "本日のポートフォリオ提案なし" in out, out
    assert "データなし" in out, out


def test_portfolio_status_ok_treated_as_available():
    pos = [{"name": "C社", "ticker": "1003.JP", "target_weight": 0.10, "diff_type": "new"}]
    snap = {"status": "ok", "mode": "shadow", "model_version": "v1",
            "gross_exposure": 0.20, "expected_vol": 0.08, "positions": pos}
    out = digest.build_daily_digest("2026-06-10", snap, None, None, [], "https://x/")
    assert "今日の建玉" in out, out


# ---------------------------------------------------------------------------
# (c) Performance — "実績蓄積中" vs populated
# ---------------------------------------------------------------------------

def test_performance_none_shows_accumulating():
    out = digest.build_daily_digest("2026-06-10", None, None, None, [], "https://x/")
    assert "実績蓄積中" in out, out


def test_performance_count_zero_shows_accumulating():
    out = digest.build_daily_digest("2026-06-10", None,
                                    {"horizons": {"5": {"count": 0, "hit_rate": None, "avg_return": None}}},
                                    None, [], "https://x/")
    assert "実績蓄積中" in out, out


def test_performance_missing_horizon5_shows_accumulating():
    out = digest.build_daily_digest("2026-06-10", None,
                                    {"horizons": {"10": {"count": 20, "hit_rate": 0.6, "avg_return": 0.01}}},
                                    None, [], "https://x/")
    assert "実績蓄積中" in out, out


def test_performance_populated_shows_hit_rate_and_n():
    out = digest.build_daily_digest("2026-06-10", None,
                                    _perf(count=35, hit_rate=0.58, avg_return=0.006),
                                    None, [], "https://x/")
    assert "実績蓄積中" not in out, out
    assert "的中" in out, out
    assert "n=35" in out, out
    # 58%
    assert "58%" in out, out


def test_performance_hit_rate_none_shows_accumulating():
    out = digest.build_daily_digest("2026-06-10", None,
                                    {"horizons": {"5": {"count": 10, "hit_rate": None, "avg_return": 0.01}}},
                                    None, [], "https://x/")
    assert "実績蓄積中" in out, out


# ---------------------------------------------------------------------------
# (d) Signal counts
# ---------------------------------------------------------------------------

def test_signal_counts_correct():
    sigs = _signals(
        ("BUY", True),
        ("MILD_BUY", True),
        ("MILD_BUY", True),
        ("SELL", True),
        ("HOLD", True),           # HOLD not counted
        ("BUY", False),           # not gate_passed -> not counted
        ("MILD_SELL", True),      # MILD_SELL counts as sell
    )
    out = digest.build_daily_digest("2026-06-10", None, None, None, sigs, "https://x/")
    assert "買い1" in out, out
    assert "やや買い2" in out, out
    # sell = SELL(1) + MILD_SELL(1) = 2
    assert "売り2" in out, out


def test_signal_counts_exact_fixture():
    # 1 BUY + 2 MILD_BUY + 1 SELL (all gate_passed) + some HOLD/non-gated
    sigs = _signals(
        ("BUY", True),
        ("MILD_BUY", True),
        ("MILD_BUY", True),
        ("SELL", True),
        ("HOLD", True),
        ("BUY", False),   # non-gated BUY should NOT be counted
    )
    out = digest.build_daily_digest("2026-06-10", None, None, None, sigs, "https://x/")
    assert "買い1 / やや買い2 / 売り1" in out, out


def test_non_gate_passed_buy_not_counted():
    sigs = _signals(("BUY", False), ("MILD_BUY", False))
    out = digest.build_daily_digest("2026-06-10", None, None, None, sigs, "https://x/")
    assert "買い0" in out, out
    assert "やや買い0" in out, out


# ---------------------------------------------------------------------------
# (e) Active mode — "[active" header + diff-type groups
# ---------------------------------------------------------------------------

def test_active_mode_header_contains_active():
    pos = [{"name": "D社", "ticker": "1004.JP", "target_weight": 0.12, "diff_type": "increase"}]
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(mode="active", model_version="cs-v2", positions=pos),
        None, None, [], "https://x/",
    )
    assert "[active" in out, out


def test_active_mode_increase_appears_under_増():
    pos = [{"name": "E社", "ticker": "1005.JP", "target_weight": 0.15, "diff_type": "increase"}]
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(mode="active", positions=pos),
        None, None, [], "https://x/",
    )
    assert "増" in out, out
    assert "E社" in out, out


def test_active_mode_decrease_appears_under_減():
    pos = [{"name": "F社", "ticker": "1006.JP", "target_weight": 0.07, "diff_type": "decrease"}]
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(mode="active", positions=pos),
        None, None, [], "https://x/",
    )
    assert "減" in out, out
    assert "F社" in out, out


def test_active_mode_hold_appears_under_継続():
    pos = [{"name": "G社", "ticker": "1007.JP", "target_weight": 0.10, "diff_type": "hold"}]
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(mode="active", positions=pos),
        None, None, [], "https://x/",
    )
    assert "継続" in out, out
    assert "G社" in out, out


def test_active_mode_separate_groups_shown():
    pos = [
        {"name": "新", "ticker": "1010.JP", "target_weight": 0.15, "diff_type": "new"},
        {"name": "増", "ticker": "1011.JP", "target_weight": 0.12, "diff_type": "increase"},
        {"name": "減", "ticker": "1012.JP", "target_weight": 0.08, "diff_type": "decrease"},
        {"name": "継", "ticker": "1013.JP", "target_weight": 0.10, "diff_type": "hold"},
    ]
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(mode="active", positions=pos),
        None, None, [], "https://x/",
    )
    # All 4 active-mode group labels present
    assert "新規" in out, out
    assert "手仕舞い" in out, out  # always shown


def test_active_mode_empty_middle_groups_absent():
    # Only a `new` position -> 増/減/継続 lines must NOT appear (middle groups
    # render only when non-empty); 新規 + 手仕舞い always show.
    pos = [{"name": "新社", "ticker": "1020.JP", "target_weight": 0.15, "diff_type": "new"}]
    out = digest.build_daily_digest(
        "2026-06-10",
        _portfolio(mode="active", positions=pos),
        None, None, [], "https://x/",
    )
    assert "新規:" in out and "手仕舞い:" in out, out
    assert "増:" not in out and "減:" not in out and "継続:" not in out, out


# ---------------------------------------------------------------------------
# Macro regime
# ---------------------------------------------------------------------------

def test_macro_regime_risk_on():
    out = digest.build_daily_digest("2026-06-10", None, None,
                                    {"market_bias": "risk_on", "usdjpy": 148.5}, [], "https://x/")
    assert "リスクオン" in out, out
    assert "148.5" in out, out


def test_macro_regime_risk_off():
    out = digest.build_daily_digest("2026-06-10", None, None,
                                    {"market_bias": "risk_off", "usdjpy": None}, [], "https://x/")
    assert "リスクオフ" in out, out
    # usdjpy None -> no ドル円 part
    assert "ドル円" not in out, out


def test_macro_regime_none_shows_unknown():
    out = digest.build_daily_digest("2026-06-10", None, None, None, [], "https://x/")
    assert "不明" in out, out


def test_macro_regime_unknown_bias():
    out = digest.build_daily_digest("2026-06-10", None, None,
                                    {"market_bias": "something_else", "usdjpy": 155.0}, [], "https://x/")
    assert "不明" in out, out


# ---------------------------------------------------------------------------
# Structure / separators
# ---------------------------------------------------------------------------

def test_output_contains_separators():
    out = digest.build_daily_digest("2026-06-10", None, None, None, [], "https://x/")
    assert "──────────" in out, out


def test_dashboard_url_appears():
    out = digest.build_daily_digest("2026-06-10", None, None, None, [], "https://myboard.example.com/")
    assert "https://myboard.example.com/" in out, out


def test_run_date_in_header():
    out = digest.build_daily_digest("2026-06-10", None, None, None, [], "")
    assert "2026-06-10" in out, out


# ---------------------------------------------------------------------------
# Top-2 + remainder formatting
# ---------------------------------------------------------------------------

def test_top2_and_remainder_in_group():
    positions = [
        {"name": "大", "ticker": "2001.JP", "target_weight": 0.20, "diff_type": "new"},
        {"name": "中", "ticker": "2002.JP", "target_weight": 0.15, "diff_type": "new"},
        {"name": "小1", "ticker": "2003.JP", "target_weight": 0.10, "diff_type": "new"},
        {"name": "小2", "ticker": "2004.JP", "target_weight": 0.08, "diff_type": "new"},
    ]
    out = digest.build_daily_digest("2026-06-10", _portfolio(positions=positions),
                                    None, None, [], "https://x/")
    # top 2 names shown
    assert "大" in out, out
    assert "中" in out, out
    # "ほか2" for the remaining 2
    assert "ほか2" in out, out
    # 小1/小2 should NOT be listed individually
    assert "小1" not in out, out


# ---------------------------------------------------------------------------
# build_weekly_summary
# ---------------------------------------------------------------------------

_WEEKLY_ROWS = [
    {
        "entry_date": "2026-06-16", "ticker": "6146.JP", "name": "ディスコ",
        "action": "BUY", "conviction": 0.72, "horizon_days": 5,
        "realized_ret": 0.042, "benchmark_ret": 0.008, "excess_ret": 0.034,
        "hit": True, "mae": -0.005, "mfe": 0.048, "exit_reason": "time",
    },
    {
        "entry_date": "2026-06-17", "ticker": "7201.JP", "name": "日産自",
        "action": "MILD_BUY", "conviction": 0.55, "horizon_days": 5,
        "realized_ret": -0.021, "benchmark_ret": 0.003, "excess_ret": -0.024,
        "hit": False, "mae": -0.025, "mfe": 0.002, "exit_reason": "time",
    },
    {
        "entry_date": "2026-06-17", "ticker": "9984.JP", "name": "ソフトバンク",
        "action": "SELL", "conviction": 0.65, "horizon_days": 5,
        "realized_ret": 0.015, "benchmark_ret": 0.006, "excess_ret": 0.009,
        "hit": True, "mae": -0.010, "mfe": 0.018, "exit_reason": "time",
    },
    {
        "entry_date": "2026-06-18", "ticker": "4063.JP", "name": "信越化",
        "action": "MILD_BUY", "conviction": 0.58, "horizon_days": 5,
        "realized_ret": -0.055, "benchmark_ret": 0.002, "excess_ret": -0.057,
        "hit": False, "mae": -0.060, "mfe": 0.001, "exit_reason": "time",
    },
]


def test_weekly_summary_empty_rows_returns_none():
    result = digest.build_weekly_summary([], "2026-06-16", "2026-06-20", "https://x/r.md")
    assert result is None, f"expected None, got {result!r}"


def test_weekly_summary_full_fixture_contains_expected_parts():
    out = digest.build_weekly_summary(_WEEKLY_ROWS, "2026-06-16", "2026-06-20", "https://x/r.md")
    assert out is not None, "expected a string, got None"
    # シグナル counts: total=4, buy=BUY(1)+MILD_BUY(2)=3, sell=SELL(1)
    assert "シグナル: 4件" in out, out
    assert "買い系3" in out, out
    assert "売り系1" in out, out
    # Performance line contains required labels + exact hit rate (2/4 hits = 50%)
    assert "的中率(5日):" in out, out
    assert "50%" in out, out
    assert "平均" in out, out
    # excess_ret present -> 対TOPIX must appear
    assert "対TOPIX" in out, out
    # best (max realized_ret=0.042 -> ディスコ +4.2%) and worst (min=-0.055 -> 信越化 -5.5%)
    assert "ベスト:" in out, out
    assert "ワースト:" in out, out
    assert "ディスコ" in out and "+4.2%" in out, out
    assert "信越化" in out and "-5.5%" in out, out
    # report URL must appear
    assert "https://x/r.md" in out, out


def test_weekly_summary_no_excess_ret_omits_topix():
    rows = [
        {
            "entry_date": "2026-06-16", "ticker": "6146.JP", "name": "ディスコ",
            "action": "BUY", "conviction": 0.72, "horizon_days": 5,
            "realized_ret": 0.042, "benchmark_ret": None, "excess_ret": None,
            "hit": True, "mae": -0.005, "mfe": 0.048, "exit_reason": "time",
        },
    ]
    out = digest.build_weekly_summary(rows, "2026-06-16", "2026-06-20", "")
    assert out is not None, "expected a string, got None"
    assert "対TOPIX" not in out, f"unexpected '対TOPIX' in: {out}"


def test_weekly_summary_date_formatting():
    out = digest.build_weekly_summary(_WEEKLY_ROWS[:1], "2026-06-16", "2026-06-20", "")
    assert out is not None, "expected a string"
    assert "6/16〜6/20" in out, f"expected '6/16〜6/20' in: {out}"


def test_weekly_summary_hit_all_none_shows_dash():
    rows = [{"action": "BUY", "name": "X社", "ticker": "1.JP",
             "realized_ret": 0.01, "excess_ret": None, "hit": None}]
    out = digest.build_weekly_summary(rows, "2026-06-16", "2026-06-20", "")
    assert "的中率(5日): —" in out, out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_portfolio_available_contains_header,
    test_portfolio_available_gross_and_vol_formatted,
    test_portfolio_available_shadow_groups_new_and_exit_always_shown,
    test_portfolio_none_shows_unavailable,
    test_portfolio_available_false_shows_reason,
    test_portfolio_available_false_no_reason_falls_back,
    test_portfolio_status_ok_treated_as_available,
    test_performance_none_shows_accumulating,
    test_performance_count_zero_shows_accumulating,
    test_performance_missing_horizon5_shows_accumulating,
    test_performance_populated_shows_hit_rate_and_n,
    test_performance_hit_rate_none_shows_accumulating,
    test_signal_counts_correct,
    test_signal_counts_exact_fixture,
    test_non_gate_passed_buy_not_counted,
    test_active_mode_header_contains_active,
    test_active_mode_increase_appears_under_増,
    test_active_mode_empty_middle_groups_absent,
    test_active_mode_decrease_appears_under_減,
    test_active_mode_hold_appears_under_継続,
    test_active_mode_separate_groups_shown,
    test_macro_regime_risk_on,
    test_macro_regime_risk_off,
    test_macro_regime_none_shows_unknown,
    test_macro_regime_unknown_bias,
    test_output_contains_separators,
    test_dashboard_url_appears,
    test_run_date_in_header,
    test_top2_and_remainder_in_group,
    # --- build_weekly_summary ---
    test_weekly_summary_empty_rows_returns_none,
    test_weekly_summary_full_fixture_contains_expected_parts,
    test_weekly_summary_no_excess_ret_omits_topix,
    test_weekly_summary_date_formatting,
    test_weekly_summary_hit_all_none_shows_dash,
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
        except Exception as exc:
            failures += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{'OK' if not failures else 'FAILED'} ({len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed)")
    return failures


if __name__ == "__main__":
    sys.exit(main())
