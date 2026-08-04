#!/usr/bin/env python3
"""
Weekly cross-sectional model retrain (Phase 2, Task 4C).

Trains a single cross-sectional LightGBM model over the full enabled universe
(one row per date x ticker), calibrates it on walk-forward OOS folds, saves the
artifact bundle under data/models/<version>/, registers the version in
model_registry (when the DB is enabled), flips the active CS-model pointer, and
writes a quality-report JSON to docs/cs_model_quality.json.

Robustness:
  - A ticker that fails to update/load is recorded and skipped; others continue.
  - When the panel is too thin to train (fewer than min_daily_names tickers on
    most dates, or fewer than train_min_rows labelled rows), the script writes an
    ``available: false`` quality report, leaves existing artifacts untouched, and
    exits 0.
  - When the DB is unreachable, artifacts + quality report + active pointer are
    still written locally; registry registration is the only step skipped.

Usage:
  uv run python scripts/weekly_cross_section_retrain.py --dry-run
  uv run python scripts/weekly_cross_section_retrain.py --output docs/cs_model_quality.json
  uv run python scripts/weekly_cross_section_retrain.py --version cs-v1-20260613
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import db, model_store  # noqa: E402
from src.backtest import evaluate_portfolio_kpi_gate, format_portfolio_gate_summary  # noqa: E402
from src.config import (  # noqa: E402
    BACKTEST_GATE_CONFIG,
    TICKERS,
    get_cross_section_config,
    get_model_runtime_config,
    get_portfolio_config,
)
from src.cross_section import build_cs_panel  # noqa: E402
from src.cs_model import train_cs_model  # noqa: E402
from src.data_loader import load_data, update_data  # noqa: E402
from src.execution import (  # noqa: E402
    ENTRY_PRICE_BASIS,
    EXECUTION_CONTRACT_VERSION,
    EXIT_PRICE_BASIS,
    execution_contract_metadata,
)
from src.macro import load_macro_panel  # noqa: E402
from src.portfolio_backtest import (
    run_portfolio_backtest,
    write_portfolio_backtest_report,
)  # noqa: E402
from scripts.curation_common import now_jst_iso, today_jst_iso  # noqa: E402


def _default_version() -> str:
    return f"cs-v1-{today_jst_iso().replace('-', '')}"


_OOS_EXECUTION_PROVENANCE = (
    "market_row_number",
    "market_as_of_date",
    "entry_date",
    "execution_exit_date",
    "entry_price",
    "execution_exit_price",
    "execution_contract_version",
    "entry_price_basis",
    "exit_price_basis",
)


def _attach_oos_execution_provenance(bundle: dict, panel) -> None:
    """Merge auditable v2 execution columns into the persisted OOS frame.

    ``train_cs_model`` deliberately emits a compact prediction frame. The
    weekly orchestrator owns artifact metadata, so it reattaches the execution
    window from the labelled panel before saving or portfolio evaluation.
    """
    oos = bundle.get("oos_predictions")
    if oos is None or getattr(oos, "empty", True) or getattr(panel, "empty", True):
        return

    keys = ["date", "ticker"]
    if not set(keys).issubset(oos.columns) or not set(keys).issubset(panel.columns):
        return
    provenance_cols = [c for c in _OOS_EXECUTION_PROVENANCE if c in panel.columns]
    if not provenance_cols:
        return

    provenance = panel[keys + provenance_cols].drop_duplicates(
        subset=keys,
        keep="last",
    )
    compact_oos = oos.drop(
        columns=[c for c in provenance_cols if c in oos.columns],
        errors="ignore",
    )
    bundle["oos_predictions"] = compact_oos.merge(
        provenance,
        on=keys,
        how="left",
        validate="many_to_one",
    )


def _oos_benchmark_coverage(oos, macro_panel) -> dict:
    """Measure exact-basis TOPIX coverage across OOS decision dates."""
    required_basis = f"{ENTRY_PRICE_BASIS}_to_{EXIT_PRICE_BASIS}"
    total = 0
    if oos is not None and not getattr(oos, "empty", True) and "date" in oos.columns:
        total = int(pd.to_datetime(oos["date"], errors="coerce").nunique())

    base = {
        "available": False,
        "scope": "all_oos_decision_dates",
        "required_basis": required_basis,
        "required_open_column": "topix_open",
        "total_periods": total,
        "available_periods": 0,
        "coverage_ratio": float(0.0) if total else None,
    }
    if macro_panel is None or not isinstance(macro_panel, pd.DataFrame):
        return {**base, "reason": "topix_open_unavailable_same_basis"}
    if not {"date", "topix_open", "topix"}.issubset(macro_panel.columns):
        return {**base, "reason": "topix_open_unavailable_same_basis"}
    required_oos = {
        "date",
        "market_as_of_date",
        "entry_date",
        "execution_exit_date",
        "execution_contract_version",
    }
    if oos is None or not required_oos.issubset(oos.columns):
        return {**base, "reason": "execution_dates_unavailable"}

    topix = macro_panel[["date", "topix_open", "topix"]].copy()
    topix["date"] = pd.to_datetime(topix["date"], errors="coerce")
    topix["topix_open"] = pd.to_numeric(topix["topix_open"], errors="coerce")
    topix["topix"] = pd.to_numeric(topix["topix"], errors="coerce")
    topix = topix.dropna(subset=["date"])
    valid_open_dates = set(topix.loc[topix["topix_open"] > 0, "date"].dropna())
    valid_close_dates = set(topix.loc[topix["topix"] > 0, "date"].dropna())

    available = 0
    for decision_date, cross in oos.groupby("date"):
        if (
            not cross["execution_contract_version"]
            .astype(str)
            .eq(EXECUTION_CONTRACT_VERSION)
            .all()
        ):
            continue
        market_as_of = pd.to_datetime(cross["market_as_of_date"], errors="coerce")
        if market_as_of.isna().any() or len(market_as_of.unique()) != 1:
            continue
        if pd.Timestamp(market_as_of.iloc[0]) != pd.Timestamp(decision_date):
            continue
        entry = pd.to_datetime(cross["entry_date"], errors="coerce")
        exit_ = pd.to_datetime(cross["execution_exit_date"], errors="coerce")
        if entry.isna().any() or exit_.isna().any():
            continue
        entry_values = entry.unique()
        exit_values = exit_.unique()
        if len(entry_values) != 1 or len(exit_values) != 1:
            continue
        if (
            pd.Timestamp(entry_values[0]) in valid_open_dates
            and pd.Timestamp(exit_values[0]) in valid_close_dates
        ):
            available += 1

    complete = total > 0 and available == total
    return {
        **base,
        "available": complete,
        "available_periods": available,
        "coverage_ratio": float(available / total) if total else None,
        "reason": None if complete else "incomplete_same_basis_coverage",
    }


def run_retrain(output_path: Path, version: str, *, dry_run: bool) -> int:
    cs_cfg = get_cross_section_config()
    model_cfg = get_model_runtime_config()
    macro_enabled: bool = bool(model_cfg.get("macro_features_enabled", True))
    label_horizon_days: int = int(cs_cfg["label_horizon_days"])
    execution_contract = execution_contract_metadata()

    # --- Load macro panel (best-effort; None is tolerated by build_cs_panel) ---
    macro_panel = load_macro_panel()
    if macro_panel is None:
        print(
            "cs-retrain: no macro panel found; building panel on technical features only "
            "(macro columns will be NaN)."
        )

    # --- Build tickers_data: update + load each enabled ticker ---
    tickers_data = []
    skipped = 0
    for ticker in TICKERS:
        code = ticker["code"]
        try:
            update_data(code)
        except Exception as e:  # noqa: BLE001 — fetch failure must not abort
            print(
                f"cs-retrain: update_data({code}) failed (ignored): {type(e).__name__}: {e}"
            )

        try:
            df = load_data(code)
        except Exception as e:  # noqa: BLE001
            print(
                f"cs-retrain: load_data({code}) failed (ignored): {type(e).__name__}: {e}"
            )
            skipped += 1
            continue

        if df is None or len(df) < 60:
            skipped += 1
            continue

        tickers_data.append((ticker, df))

    print(
        f"cs-retrain: {len(tickers_data)} tickers with usable data "
        f"({skipped} skipped / insufficient)."
    )

    # --- Build labelled cross-sectional panel ---
    panel = build_cs_panel(
        tickers_data,
        macro_panel=macro_panel,
        macro_enabled=macro_enabled,
        with_labels=True,
        label_config={"label_horizon_days": label_horizon_days},
    )

    # --- Leakage hardening: purge_gap >= label horizon ---
    train_config = {
        "purge_gap": max(
            int(BACKTEST_GATE_CONFIG.get("purge_gap", 5)),
            label_horizon_days,
        ),
    }

    # --- Train ---
    bundle, info = train_cs_model(
        panel,
        config=train_config,
        macro_enabled=macro_enabled,
    )

    # --- Insufficient panel path: write available:false report, exit 0 ---
    if bundle is None:
        reason = info.get("reason", "training_failed")
        print(
            f"cs-retrain: training returned no bundle — reason={reason} "
            f"(rows={info.get('rows')}, dates={info.get('dates')}). "
            f"Leaving existing artifacts untouched."
        )
        payload = {
            "available": False,
            "generated_at": now_jst_iso(),
            "model_version": None,
            "reason": reason,
            "info": info,
            "dry_run": dry_run,
            "execution_contract": execution_contract,
            "benchmark_coverage": {
                "available": False,
                "reason": "portfolio_backtest_not_run",
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"cs-retrain: quality report (available=false) written to {output_path}")
        return 0

    _attach_oos_execution_provenance(bundle, panel)
    bundle["execution_contract"] = execution_contract
    artifact_benchmark_coverage = _oos_benchmark_coverage(
        bundle.get("oos_predictions"),
        macro_panel,
    )
    bundle["benchmark_coverage"] = artifact_benchmark_coverage

    # --- Resolve kind and build metadata ---
    objective: str = bundle["objective"]
    kind = (
        "cross_sectional_ranker_v1"
        if objective == "ranker"
        else "cross_sectional_regression_v1"
    )

    feature_cols: list[str] = bundle["feature_cols"]
    universe: list[str] = bundle["universe"]
    universe_size: int = len(universe)
    fallback_reason = info.get("fallback_reason")

    feature_schema: dict = {
        "feature_cols": feature_cols,
        "objective": objective,
        "macro_enabled": bundle["macro_enabled"],
        "label_horizon_days": label_horizon_days,
        "execution_contract": execution_contract,
        "benchmark_coverage": artifact_benchmark_coverage,
    }

    generated_at = now_jst_iso()

    version_metadata: dict = {
        "version": version,
        "kind": kind,
        "generated_at": generated_at,
        "objective": objective,
        "horizon_days": label_horizon_days,
        "macro_features_enabled": macro_enabled,
        "universe": universe,
        "universe_size": universe_size,
        "feature_set": feature_cols,
        "metrics": bundle["metrics"],
        "fallback_reason": fallback_reason,
        "execution_contract": execution_contract,
        "benchmark_coverage": artifact_benchmark_coverage,
    }

    # --- Persist (skipped in dry-run) ---
    db_registered = False

    if dry_run:
        print("[dry-run] would save artifact + flip active pointer")
    else:
        model_store.save_cs_bundle(
            version,
            bundle["booster"],
            feature_schema=feature_schema,
            calibration=bundle["calibration"],
            feature_reference=bundle["feature_reference"],
            sector_encoder=bundle["sector_encoder"],
            universe=bundle["universe"],
            oos_predictions=bundle["oos_predictions"],
            version_metadata=version_metadata,
        )
        print(f"cs-retrain: saved CS bundle under version {version}.")

        model_store.write_active_cs_model(
            version,
            {
                "kind": kind,
                "horizon_days": label_horizon_days,
                "objective": objective,
                "macro_features_enabled": macro_enabled,
                "universe_size": universe_size,
                "portfolio_enabled": True,
                "generated_at": generated_at,
                "execution_contract": execution_contract,
                "benchmark_coverage": artifact_benchmark_coverage,
            },
        )
        print(f"cs-retrain: active CS model pointer updated to {version}.")

        # --- DB registration (best-effort, never abort) ---
        if db.db_enabled():
            try:
                conn = db.connect()
                try:
                    db.register_model_version(
                        conn,
                        version,
                        kind=kind,
                        universe=universe,
                        feature_set=feature_cols,
                        params={
                            "lgb": "see src.cs_model._lgb_params",
                            "cs_config": cs_cfg,
                            "train_config": train_config,
                            "execution_contract": execution_contract,
                            "benchmark_coverage": artifact_benchmark_coverage,
                        },
                        cv_metrics=bundle["metrics"],
                        calibration=bundle["calibration"],
                        artifact_uri=model_store.artifact_uri(version),
                        make_active=True,
                    )
                    db_registered = True
                    print(
                        f"cs-retrain: registered {version} in model_registry (active)."
                    )
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001 — never abort on DB error
                print(
                    f"cs-retrain: DB registration skipped (ignored): "
                    f"{type(e).__name__}: {e}"
                )
        else:
            print(
                "cs-retrain: DB disabled; model_registry registration skipped "
                "(artifacts + active pointer written locally)."
            )

    # --- Portfolio backtest (best-effort; never aborts the retrain) ---
    # Build price_frames keyed by ticker code (= ticker_info["code"]) to match
    # the ticker column in bundle["oos_predictions"].
    portfolio_summary: dict | None = None
    benchmark_coverage: dict = {
        "available": False,
        "reason": "portfolio_backtest_not_run",
    }
    try:
        portfolio_cfg = get_portfolio_config()
        portfolio_cfg["top_n"] = cs_cfg.get("top_n", 8)

        price_frames = {
            ticker_info["code"]: df[["date", "close"]].copy()
            for ticker_info, df in tickers_data
            if "date" in df.columns and "close" in df.columns
        }

        # sectors: available when ticker_info carries a "sector" field.
        sectors = {
            ticker_info["code"]: ticker_info["sector"]
            for ticker_info, _ in tickers_data
            if ticker_info.get("sector")
        }

        bt_result = run_portfolio_backtest(
            bundle["oos_predictions"],
            price_frames,
            macro_panel,
            portfolio_cfg,
            sectors=sectors,
            label_horizon_days=label_horizon_days,
            cost_bps=float(portfolio_cfg.get("cost_bps", 10.0)),
            slippage_bps=float(portfolio_cfg.get("slippage_bps", 5.0)),
        )
        gate = evaluate_portfolio_kpi_gate(bt_result, portfolio_cfg)
        benchmark_coverage = dict(bt_result.get("benchmark_coverage") or {})
        gate_summary = format_portfolio_gate_summary(gate)
        print(f"cs-retrain: portfolio backtest {gate_summary}")

        # Write docs/portfolio_backtest.json (always, even in dry-run). The
        # evaluated KPI gate rides along so read_portfolio_gate() checks the
        # real pass/fail (active-mode safety, issue #2).
        bt_report_path = Path(ROOT_DIR) / "docs" / "portfolio_backtest.json"
        write_portfolio_backtest_report(
            bt_result,
            output_path=str(bt_report_path),
            model_version=version,
            run_date=today_jst_iso(),
            generated_at=generated_at,
            gate=gate,
        )
        print(f"cs-retrain: portfolio backtest report written to {bt_report_path}")

        # Best-effort DB persist (skipped in dry-run).
        if not dry_run and db.db_enabled():
            db_res = db.record_backtest_run(
                bt_result,
                today_jst_iso(),
                model_version=version,
                scope="portfolio",
            )
            if db_res.get("ok"):
                print(
                    f"cs-retrain: backtest_runs row inserted (id={db_res.get('run_id')})."
                )
            else:
                print(
                    f"cs-retrain: backtest DB persist skipped (ignored): "
                    f"{db_res.get('reason')}"
                )

        # Compact summary to include in cs_model_quality.json.
        m = bt_result.get("metrics") or {}
        portfolio_summary = {
            "status": bt_result.get("status"),
            "gate_passed": gate.get("passed"),
            "gate_failures": gate.get("failures"),
            "n_periods": bt_result.get("n_periods"),
            "sharpe": m.get("sharpe"),
            "cagr": m.get("cagr"),
            "max_drawdown": m.get("max_drawdown"),
            "information_ratio": m.get("information_ratio"),
            "turnover": m.get("turnover"),
            "execution_contract": bt_result.get("execution_contract"),
            "benchmark_coverage": benchmark_coverage,
        }

    except Exception as e:  # noqa: BLE001 — backtest failure must never abort retrain
        benchmark_coverage = {
            "available": False,
            "reason": "portfolio_backtest_failed",
        }
        print(
            f"cs-retrain: portfolio backtest failed (ignored): {type(e).__name__}: {e}"
        )

    # --- Write quality report ---
    payload = {
        "available": True,
        "generated_at": generated_at,
        "model_version": version,
        "kind": kind,
        "objective": objective,
        "universe_size": universe_size,
        "dry_run": dry_run,
        "db_registered": db_registered,
        "metrics": bundle["metrics"],
        "fallback_reason": fallback_reason,
        "execution_contract": execution_contract,
        "benchmark_coverage": benchmark_coverage,
        "portfolio_backtest": portfolio_summary,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"cs-retrain: quality report written to {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Weekly cross-sectional Phase 2 model retrain"
    )
    parser.add_argument(
        "--output",
        default="docs/cs_model_quality.json",
        help="Path to write the quality report JSON (default: docs/cs_model_quality.json)",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Model version label (default: cs-v1-YYYYMMDD)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build panel + train but skip all persistence (artifact, active pointer, DB)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    version = args.version or _default_version()
    return run_retrain(Path(args.output), version, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
