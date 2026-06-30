#!/usr/bin/env python3
"""
Phase 2 shadow-validation report generator (roadmap Task 10).

Assembles normalized per-(date, ticker) records over a shadow window from the
DB, then calls the PURE comparison logic in ``src.portfolio_shadow`` to write
``docs/portfolio_shadow_report.json`` — a Phase 1 (per-ticker) vs Phase 2
(cross-sectional + portfolio) comparison on daily IC, top-N realized return,
turnover, drawdown, hit rate, and expected-return calibration. This is the
data-driven input for the eventual one-env-var flip of Phase 2 from shadow to
active; it does NOT flip anything.

Robustness (this runs as a ``continue-on-error`` weekly step):
  - DB disabled              -> {"available": false, "reason": "db_disabled"}, exit 0.
  - DB error / no rows       -> {"available": false, "reason": "db_error: <Type>"
                                 | "insufficient_shadow_history"}, exit 0.
  - Never raises catastrophically; the JSON is always written.

DB join (see migrations/0001 + 0003):
  ``signal_outcomes.realized_ret`` for (run_date, ticker, horizon) is a MARKET
  property — it applies to any model's prediction of that name on that date. We
  anchor on the union of (run_date, ticker) that have a Phase 1 prediction
  and/or a Phase 2 cs prediction, LEFT JOIN the Phase 1 signal -> its outcome
  for the shared realized return, and LEFT JOIN both model versions' rows. The
  Phase 2 portfolio weights come from ``portfolio_snapshots.positions`` (JSONB),
  fetched separately and merged per (date, ticker).

Usage:
  TRADER_DB_ENABLED=false uv run python scripts/portfolio_shadow_report.py
  uv run python scripts/portfolio_shadow_report.py --output docs/portfolio_shadow_report.json
  uv run python scripts/portfolio_shadow_report.py --lookback-days 60 --top-n 8 --horizon-days 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import db, portfolio, portfolio_shadow  # noqa: E402
from src.config import get_cross_section_config  # noqa: E402
from scripts.curation_common import now_jst_iso, today_jst_iso  # noqa: E402


def _active_readiness(report, *, gate_passed, min_shadow_days=10):
    shadow_days = int(report.get("n_dates", 0))
    delta = (report.get("comparison") or {}).get("delta") or {}
    cs_ic_vs_phase1 = delta.get("daily_ic")
    reasons = []
    if shadow_days < min_shadow_days:
        reasons.append(f"shadow_days {shadow_days} < {min_shadow_days}")
    if not gate_passed:
        reasons.append("portfolio_gate not passed")
    if cs_ic_vs_phase1 is None:
        reasons.append("cs_ic_vs_phase1 unavailable")
    elif cs_ic_vs_phase1 < -0.005:
        reasons.append(f"cs_ic_vs_phase1 {cs_ic_vs_phase1:.4f} < -0.005")
    return {
        "active_ready": not reasons,
        "shadow_days": shadow_days,
        "min_shadow_days": min_shadow_days,
        "portfolio_gate_passed": bool(gate_passed),
        "cs_ic_vs_phase1": cs_ic_vs_phase1,
        "reasons": reasons,
    }


def _unavailable_report(
    reason: str, generated_at: str, *, window: dict | None = None
) -> dict:
    """Uniform available=false payload; carries active_readiness like the full report."""
    payload = {"available": False, "reason": reason, "generated_at": generated_at}
    if window is not None:
        payload["window"] = window
    payload["active_readiness"] = _active_readiness(
        payload, gate_passed=portfolio.read_portfolio_gate()
    )
    return payload


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Match the other scripts' write style (mkdir + indent=2 UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_pred_outcome_rows(
    conn, start_date: str, end_date: str, horizon_days: int
) -> list[dict]:
    """
    One row per (run_date, ticker) with BOTH models' predictions + the shared
    market realized return.

    Anchor = all (run_date, ticker) in the window that have a Phase 1 OR a
    Phase 2 cs prediction at this horizon (FULL OUTER JOIN of the two prediction
    slices). ``realized_ret`` comes from the Phase 1 signal's outcome for the
    same (run_date, ticker, horizon); LEFT JOINs keep a row even when the signal
    or outcome is missing (-> NULL, surfaced as None).
    """
    from psycopg.rows import dict_row

    sql = (
        "WITH p1 AS ("
        "  SELECT run_date, ticker, prob_up AS p1_prob_up"
        "  FROM predictions"
        "  WHERE horizon_days = %(horizon)s"
        "    AND run_date BETWEEN %(start)s AND %(end)s"
        "    AND model_version NOT LIKE 'cs-%%'"
        "), p2 AS ("
        "  SELECT run_date, ticker, cs_rank AS p2_cs_rank,"
        "         expected_ret AS p2_expected_ret, prob_up AS p2_prob_up"
        "  FROM predictions"
        "  WHERE horizon_days = %(horizon)s"
        "    AND run_date BETWEEN %(start)s AND %(end)s"
        "    AND model_version LIKE 'cs-%%'"
        ")"
        " SELECT"
        "   COALESCE(p1.run_date, p2.run_date) AS run_date,"
        "   COALESCE(p1.ticker, p2.ticker)     AS ticker,"
        "   p1.p1_prob_up,"
        "   sig.action AS p1_action,"
        "   o.realized_ret,"
        "   p2.p2_cs_rank, p2.p2_expected_ret, p2.p2_prob_up"
        " FROM p1"
        " FULL OUTER JOIN p2"
        "   ON p1.run_date = p2.run_date AND p1.ticker = p2.ticker"
        " LEFT JOIN signals sig"
        "   ON sig.run_date = COALESCE(p1.run_date, p2.run_date)"
        "  AND sig.ticker  = COALESCE(p1.ticker, p2.ticker)"
        " LEFT JOIN signal_outcomes o"
        "   ON o.signal_id = sig.id AND o.horizon_days = %(horizon)s"
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql, {"horizon": horizon_days, "start": start_date, "end": end_date}
        )
        return cur.fetchall()


def _fetch_snapshot_weights(conn, start_date: str, end_date: str) -> dict:
    """
    {run_date_iso -> {ticker -> {"weight": float|None, "prev_weight": float|None}}}
    parsed from ``portfolio_snapshots.positions`` (JSONB) over the window.
    """
    from psycopg.rows import dict_row

    out: dict[str, dict] = {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT run_date, positions FROM portfolio_snapshots"
            " WHERE run_date BETWEEN %(start)s AND %(end)s",
            {"start": start_date, "end": end_date},
        )
        rows = cur.fetchall()
    for r in rows:
        positions = r.get("positions") or []
        day_map: dict[str, dict] = {}
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            tk = pos.get("ticker")
            if not tk:
                continue
            day_map[tk] = {
                "weight": pos.get("target_weight"),
                "prev_weight": pos.get("prev_weight"),
            }
        out[str(r.get("run_date"))] = day_map
    return out


def _assemble_records(pred_rows: list[dict], weights_by_date: dict) -> list[dict]:
    """Normalize joined DB rows into the pure-logic record contract."""
    records = []
    for r in pred_rows:
        run_date = str(r.get("run_date"))
        ticker = r.get("ticker")
        if not ticker:
            continue
        wmap = weights_by_date.get(run_date, {})
        wt = wmap.get(ticker, {})
        records.append(
            {
                "date": run_date,
                "ticker": ticker,
                "realized_ret": r.get("realized_ret"),
                "p1_prob_up": r.get("p1_prob_up"),
                "p1_action": r.get("p1_action"),
                "p2_cs_rank": r.get("p2_cs_rank"),
                "p2_expected_ret": r.get("p2_expected_ret"),
                "p2_prob_up": r.get("p2_prob_up"),
                "p2_weight": wt.get("weight"),
                "p2_prev_weight": wt.get("prev_weight"),
            }
        )
    return records


def _resolve_active_cs_version(conn) -> str | None:
    """Best-effort: the active cross-sectional model version (informational)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM model_registry"
                " WHERE active = TRUE AND version LIKE 'cs-%'"
                " ORDER BY trained_at DESC LIMIT 1"
            )
            row = cur.fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001 — purely informational
        return None


def run_report(
    output_path: Path,
    *,
    top_n: int,
    horizon_days: int,
    lookback_days: int,
    as_of: str | None,
) -> int:
    generated_at = now_jst_iso()

    if not db.db_enabled():
        _atomic_write_json(
            output_path, _unavailable_report("db_disabled", generated_at)
        )
        print(f"shadow-report: DB disabled; wrote available=false to {output_path}")
        return 0

    end_iso = as_of or today_jst_iso()
    try:
        end_date = date.fromisoformat(end_iso)
    except ValueError:
        end_date = date.fromisoformat(today_jst_iso())
        end_iso = end_date.isoformat()
    start_iso = (end_date - timedelta(days=max(1, int(lookback_days)))).isoformat()
    window = {
        "start": start_iso,
        "end": end_iso,
        "lookback_days": int(lookback_days),
        "horizon_days": int(horizon_days),
    }

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001 — never fail the weekly job
        _atomic_write_json(
            output_path,
            _unavailable_report(
                f"db_error: {type(exc).__name__}", generated_at, window=window
            ),
        )
        print(
            f"shadow-report: connect failed ({type(exc).__name__}); wrote available=false"
        )
        return 0

    try:
        pred_rows = _fetch_pred_outcome_rows(conn, start_iso, end_iso, horizon_days)
        weights_by_date = _fetch_snapshot_weights(conn, start_iso, end_iso)
        model_version = _resolve_active_cs_version(conn)
    except Exception as exc:  # noqa: BLE001 — DB read failure must not abort
        _atomic_write_json(
            output_path,
            _unavailable_report(
                f"db_error: {type(exc).__name__}", generated_at, window=window
            ),
        )
        print(
            f"shadow-report: DB read failed ({type(exc).__name__}); wrote available=false"
        )
        return 0
    finally:
        conn.close()

    records = _assemble_records(pred_rows, weights_by_date)
    report = portfolio_shadow.build_shadow_report(
        records,
        top_n=top_n,
        window=window,
        generated_at=generated_at,
        model_version=model_version,
    )
    report["active_readiness"] = _active_readiness(
        report, gate_passed=portfolio.read_portfolio_gate()
    )
    _atomic_write_json(output_path, report)
    print(
        f"shadow-report: available={report['available']} "
        f"reason={report.get('reason')} n_dates={report.get('n_dates')} "
        f"n_records={report.get('n_records')} -> {output_path}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    cs_cfg = get_cross_section_config()
    parser = argparse.ArgumentParser(
        description="Phase 2 shadow-validation report (Phase 1 vs Phase 2 comparison)"
    )
    parser.add_argument(
        "--output",
        default="docs/portfolio_shadow_report.json",
        help="Path to write the report JSON (default: docs/portfolio_shadow_report.json)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=int(cs_cfg.get("top_n", 8)),
        help="Top-N book size for top-N / hit-rate / calibration (default: CS config top_n)",
    )
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=5,
        help="Outcome horizon to compare (default: 5)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        help="Shadow window length in calendar days back from --as-of (default: 60)",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Window end date YYYY-MM-DD (default: today JST)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run_report(
            Path(args.output),
            top_n=args.top_n,
            horizon_days=args.horizon_days,
            lookback_days=args.lookback_days,
            as_of=args.as_of,
        )
    except Exception as exc:  # noqa: BLE001 — last-resort guard, never fail weekly job
        try:
            _atomic_write_json(
                Path(args.output),
                {
                    "available": False,
                    "reason": f"unexpected_error: {type(exc).__name__}",
                    "generated_at": now_jst_iso(),
                },
            )
        except Exception:  # noqa: BLE001
            pass
        print(
            f"shadow-report: unexpected error ({type(exc).__name__}); wrote available=false"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
