#!/usr/bin/env python3
"""
Weekly-review gap attribution: why did the portfolio beat / lose to TOPIX?

Reads the official ``docs/portfolio_backtest.json`` (read-only; writes nothing)
and decomposes each rebalance period's net gap versus the same-basis benchmark
into three additive effects:

  exposure_effect   = (gross_exposure - 1) * gross_benchmark_return
                      -> what holding cash instead of the market cost us
  selection_effect  = gross_period_return - gross_exposure * gross_benchmark_return
                      -> what stock picking added vs holding the benchmark
                         at the same exposure
  cost_effect       = benchmark_cost_return - cost_return
                      -> trading-cost difference vs the same-basis benchmark

Identity per period (and for the totals):
  exposure_effect + selection_effect + cost_effect == net_gap
  where net_gap = period_return - benchmark_return.

Because the same-basis benchmark is charged synthetic rebalance costs, the
report also shows ``net_gap_vs_real_benchmark`` = the gap against a zero-cost
buy-and-hold benchmark (exposure_effect + selection_effect - cost_return),
which is the honest "did we beat just buying TOPIX?" number.

This is a standalone diagnostic for the weekly review. It is not part of the
daily pipeline, writes no files, and never touches signals or notifications.

Usage:
  uv run python scripts/performance_attribution.py
  uv run python scripts/performance_attribution.py --last 4
  uv run python scripts/performance_attribution.py --json
  uv run python scripts/performance_attribution.py --input docs/portfolio_backtest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_INPUT = ROOT_DIR / "docs" / "portfolio_backtest.json"

_REQUIRED_FIELDS = (
    "gross_period_return",
    "cost_return",
    "gross_benchmark_return",
    "benchmark_cost_return",
    "gross_exposure",
)

_EFFECT_LABELS = {
    "exposure": "エクスポージャー（現金比率）",
    "selection": "銘柄選択",
    "cost": "売買コスト",
    "none": "なし（マイナス要因なし）",
}


def _to_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def attribute_period(row):
    """Decompose one equity row's gap vs benchmark. Returns None if malformed."""
    values = {}
    for field in _REQUIRED_FIELDS:
        parsed = _to_float(row.get(field))
        if parsed is None:
            return None
        values[field] = parsed

    gross = values["gross_period_return"]
    cost = values["cost_return"]
    bench_gross = values["gross_benchmark_return"]
    bench_cost = values["benchmark_cost_return"]
    exposure = values["gross_exposure"]

    exposure_effect = (exposure - 1.0) * bench_gross
    selection_effect = gross - exposure * bench_gross
    cost_effect = bench_cost - cost
    net_gap = (gross - cost) - (bench_gross - bench_cost)
    net_gap_vs_real = exposure_effect + selection_effect - cost

    return {
        "date": row.get("date"),
        "gross_exposure": exposure,
        "exposure_effect": exposure_effect,
        "selection_effect": selection_effect,
        "cost_effect": cost_effect,
        "net_gap": net_gap,
        "net_gap_vs_real_benchmark": net_gap_vs_real,
    }


def attribute_report(report):
    """Attribute every period of a portfolio_backtest.json payload."""
    if not report.get("available", False):
        return {
            "available": False,
            "reason": report.get("reason"),
            "periods": [],
            "totals": None,
            "n_skipped": 0,
        }

    periods = []
    n_skipped = 0
    for row in report.get("equity") or []:
        attributed = attribute_period(row)
        if attributed is None:
            n_skipped += 1
            continue
        periods.append(attributed)

    total_keys = (
        "exposure_effect",
        "selection_effect",
        "cost_effect",
        "net_gap",
        "net_gap_vs_real_benchmark",
    )
    totals = {key: sum(p[key] for p in periods) for key in total_keys}

    return {
        "available": True,
        "start_date": report.get("start_date"),
        "end_date": report.get("end_date"),
        "periods": periods,
        "totals": totals,
        "n_skipped": n_skipped,
    }


def main_verdict(totals):
    """Name the largest negative contributor: exposure / selection / cost / none."""
    effects = {
        "exposure": totals.get("exposure_effect", 0.0),
        "selection": totals.get("selection_effect", 0.0),
        "cost": totals.get("cost_effect", 0.0),
    }
    worst_name = min(effects, key=lambda k: effects[k])
    if effects[worst_name] >= 0.0:
        return "none"
    return worst_name


def _pct(value):
    return f"{value * 100:+7.2f}%"


def _print_table(result, last):
    periods = result["periods"]
    shown = periods[-last:] if last and last > 0 else periods
    print(
        f"負けの3分解 (期間 {result.get('start_date')} 〜 {result.get('end_date')},"
        f" 全{len(periods)}期間, 表示{len(shown)}期間)"
    )
    if result["n_skipped"]:
        print(f"注意: 欠損フィールドでスキップした行 = {result['n_skipped']}")
    print()
    header = (
        f"{'date':<12} {'gross':>6} {'expo効果':>9} {'選択効果':>9}"
        f" {'コスト効果':>10} {'差(同一basis)':>13} {'差(実TOPIX)':>12}"
    )
    print(header)
    print("-" * len(header))
    for p in shown:
        print(
            f"{p['date'] or '?':<12} {p['gross_exposure']:>6.2f} {_pct(p['exposure_effect']):>9}"
            f" {_pct(p['selection_effect']):>9} {_pct(p['cost_effect']):>10}"
            f" {_pct(p['net_gap']):>13} {_pct(p['net_gap_vs_real_benchmark']):>12}"
        )
    totals = result["totals"]
    print("-" * len(header))
    print(
        f"{'累計':<12} {'':>6} {_pct(totals['exposure_effect']):>9}"
        f" {_pct(totals['selection_effect']):>9} {_pct(totals['cost_effect']):>10}"
        f" {_pct(totals['net_gap']):>13} {_pct(totals['net_gap_vs_real_benchmark']):>12}"
    )
    print()
    verdict = main_verdict(totals)
    print(f"最大のマイナス要因: {_EFFECT_LABELS[verdict]}")
    print(
        "見方: expo効果=現金を持ちすぎた/足りなかった影響,"
        " 選択効果=同じ投資額でTOPIXを持った場合との銘柄選びの差,"
        " コスト効果=同一basisベンチとの売買コスト差。"
    )
    print(
        "「差(実TOPIX)」はコストゼロのバイ&ホールドTOPIXに対する差"
        "（expo効果+選択効果-自分のコスト）。"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Decompose the portfolio-vs-TOPIX gap into exposure / selection / cost."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to portfolio_backtest.json (default: docs/portfolio_backtest.json)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=0,
        help="Show only the last N periods in the table (totals stay full-window).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full attribution as JSON to stdout instead of a table.",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    try:
        report = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: could not read {input_path}: {exc}", file=sys.stderr)
        return 1

    result = attribute_report(report)
    if not result["available"]:
        print(f"backtest report unavailable (reason: {result.get('reason')})")
        return 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_table(result, args.last)
    return 0


if __name__ == "__main__":
    sys.exit(main())
