#!/usr/bin/env python3
"""
Weekly model retrain (Phase 1, W3).

Trains a persisted per-ticker horizon-aware LightGBM ensemble (technical + macro
features), calibrates it on out-of-sample folds, saves the artifacts under
data/models/<version>/<ticker>/, and registers the version in model_registry
(when the DB is enabled), flipping the active pointer to the new version.

Robustness:
  - Every run writes to an isolated staging directory and a new immutable
    version. Existing versions are never overwritten.
  - Any ticker failure rejects the whole candidate and preserves the previous
    active pointer; the per-ticker failures remain visible in the report.
  - The candidate is promoted and the pointer is atomically replaced only
    after manifest/checksum/coverage validation succeeds.
  - When the DB is unreachable, a locally activated candidate remains usable;
    registry registration is best-effort and its status is reported.

Usage:
  uv run python scripts/weekly_model_retrain.py --output docs/weekly_retrain_report.json
  uv run python scripts/weekly_model_retrain.py --version per-ticker-v1-20260613
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import db, model_store  # noqa: E402
from src.config import (  # noqa: E402
    BACKTEST_GATE_CONFIG,
    TICKERS,
    get_label_config,
    get_model_runtime_config,
)
from src.data_loader import load_data, update_data  # noqa: E402
from src.macro import load_macro_panel  # noqa: E402
from src.model import build_feature_frame, phase1_feature_cols  # noqa: E402
from src.phase1 import train_ticker_bundle  # noqa: E402
from scripts.curation_common import now_jst_iso, today_jst_iso  # noqa: E402

MODEL_KIND = "per_ticker_horizon_v1"
JST = ZoneInfo("Asia/Tokyo")


def _default_version() -> str:
    timestamp = datetime.now(JST).strftime("%Y%m%dT%H%M%S")
    return f"per-ticker-v1-{timestamp}-{_git_commit()}-{uuid.uuid4().hex[:8]}"


def _git_commit() -> str:
    """Best-effort immutable source identifier for model provenance."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
        commit = result.stdout.strip().lower()
        if commit and all(c in "0123456789abcdef" for c in commit):
            return commit
    except (OSError, subprocess.SubprocessError):
        pass
    return "nogit"


def _write_report(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _failure_entry(
    ticker: dict,
    reason: str,
    error: str | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "ticker": ticker["code"],
        "name": ticker["name"],
        "status": "failed",
        "model_ready": False,
        "reason": reason,
        "error": error,
        "data_validation_warnings": warnings or [],
    }


def _median(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    return float(statistics.median(nums)) if nums else None


def run_retrain(output_path: Path, version: str) -> int:
    label_cfg = get_label_config()
    model_cfg = get_model_runtime_config()
    feature_cols = phase1_feature_cols(model_cfg.get("macro_features_enabled", True))
    artifact_contract = model_store.build_phase1_artifact_contract(
        label_config=label_cfg,
        feature_columns=feature_cols,
        macro_features_enabled=model_cfg.get("macro_features_enabled", True),
        calibration_mode=model_cfg.get("calibration_mode", "isotonic"),
    )
    gate_contract = model_store.build_phase1_gate_contract(
        BACKTEST_GATE_CONFIG, artifact_contract
    )
    config_payload = {
        "label_config": artifact_contract["label_config"],
        "model_training_config": {
            "calibration_mode": model_cfg.get("calibration_mode"),
            "macro_features_enabled": model_cfg.get("macro_features_enabled"),
            "min_calibration_rows": model_cfg.get("min_calibration_rows"),
        },
        "backtest_gate_config": gate_contract["gate_config"],
        "gate_contract": gate_contract,
        "feature_set": feature_cols,
    }
    config_sha256 = model_store.payload_sha256(config_payload)
    git_commit = _git_commit()
    generated_at = now_jst_iso()
    target_tickers = [t["code"] for t in TICKERS]
    previous_active = model_store.read_active_model() or {}
    previous_version = previous_active.get("version")
    previous_metadata = (
        model_store.read_version_metadata(previous_version)
        if previous_version
        else None
    )

    macro_panel = load_macro_panel()
    if macro_panel is None:
        print(
            "weekly: no macro panel found; training on technical features only "
            "(macro columns will be NaN)."
        )

    entries = []
    calibration_map: dict[str, dict | None] = {}
    cv_by_ticker: dict[str, dict] = {}
    quality_rows: list[dict] = []
    run_date = today_jst_iso()
    active_set = False
    manifest_sha256 = None
    candidate_validation: dict = {
        "passed": False,
        "failures": [{"code": "candidate_not_built"}],
    }
    artifact_error = None
    promoted_path = None
    staging_model_dir = None
    staging_cleanup = {
        "policy": "always_delete_managed_staging_after_report_evidence_is_captured",
        "status": "not_created",
    }

    try:
        staging_model_dir = model_store.create_staging_model_dir(version)
        staging_cleanup["status"] = "pending"
        print(f"weekly: building immutable candidate {version} in staging.")
    except Exception as exc:  # noqa: BLE001
        artifact_error = f"{type(exc).__name__}: {exc}"
        candidate_validation = {
            "passed": False,
            "failures": [{"code": "staging_creation_failed", "error": artifact_error}],
        }

    version_metadata = {
        "version": version,
        "kind": MODEL_KIND,
        "artifact_schema_version": model_store.PHASE1_MANIFEST_SCHEMA_VERSION,
        "artifact_contract": artifact_contract,
        "gate_contract": gate_contract,
        **model_store.phase1_gate_contract_metadata_fields(gate_contract),
        "generated_at": generated_at,
        "git_commit": git_commit,
        "config_sha256": config_sha256,
        "horizon_days": artifact_contract["effective_horizon_days"],
        "effective_horizon_days": artifact_contract["effective_horizon_days"],
        "label_mode": artifact_contract["label_config"]["label_mode"],
        "label_config": artifact_contract["label_config"],
        "macro_features_enabled": artifact_contract["macro_features_enabled"],
        "feature_set": feature_cols,
        "feature_columns": artifact_contract["feature_columns"],
        "feature_schema_hash": artifact_contract["feature_schema_hash"],
        "calibration_mode": artifact_contract["calibration_mode"],
        "calibration_id": artifact_contract["calibration_id"],
        "execution_contract_version": artifact_contract["execution_contract_version"],
        "universe": target_tickers,
        "trained_tickers": [],
        "cv_metrics": {"by_ticker": {}, "aggregate": {}},
    }

    if staging_model_dir is not None:
        try:
            for ticker in TICKERS:
                code = ticker["code"]
                warnings: list[str] = []
                try:
                    updated = update_data(code)
                    if updated is not None:
                        warnings = updated.attrs.get("validation_warnings", []) or []

                    df = load_data(code)
                    if df is not None:
                        warnings = list(
                            dict.fromkeys(
                                warnings
                                + (df.attrs.get("validation_warnings", []) or [])
                            )
                        )
                    if df is None or len(df) < 60:
                        entries.append(
                            _failure_entry(
                                ticker, "insufficient_data", warnings=warnings
                            )
                        )
                        continue

                    featured = build_feature_frame(
                        df,
                        macro_panel=macro_panel,
                        ticker_info=ticker,
                        macro_enabled=model_cfg.get("macro_features_enabled", True),
                    )
                    if featured.empty:
                        entries.append(
                            _failure_entry(ticker, "empty_features", warnings=warnings)
                        )
                        continue

                    result, info = train_ticker_bundle(
                        featured,
                        BACKTEST_GATE_CONFIG,
                        label_cfg,
                        model_cfg,
                        model_version=version,
                    )
                    if result is None:
                        entries.append(
                            _failure_entry(
                                ticker,
                                info.get("reason", "training_failed"),
                                warnings=warnings,
                            )
                        )
                        continue

                    gate = result["gate_result"]
                    bundle_metadata = dict(result["metadata"])
                    bundle_metadata.update(
                        model_store.phase1_contract_metadata_fields(artifact_contract)
                    )
                    bundle_metadata["model_version"] = version
                    gate_evidence = bundle_metadata.get("gate_evidence")
                    if not isinstance(gate_evidence, dict):
                        entries.append(
                            _failure_entry(
                                ticker, "gate_evidence_missing", warnings=warnings
                            )
                        )
                        continue
                    model_store.save_model_bundle(
                        version,
                        code,
                        result["boosters"],
                        bundle_metadata,
                        model_dir=staging_model_dir,
                    )

                    cv = result["cv_metrics"]
                    calibration_map[code] = bundle_metadata.get("calibration")
                    cv_by_ticker[code] = cv
                    quality_rows.append(
                        {
                            "run_date": run_date,
                            "model_version": version,
                            "ticker": code,
                            "horizon_days": gate.get("horizon_days"),
                            "brier": cv.get("brier"),
                            "brier_raw": cv.get("brier_raw"),
                            "ic": cv.get("ic"),
                            "auc": cv.get("auc"),
                            "hit_rate": cv.get("hit_rate"),
                            "calibration_rows": (cv.get("calibration") or {}).get(
                                "rows"
                            ),
                            "psi_max": None,
                            "warning": False,
                        }
                    )

                    entries.append(
                        {
                            "ticker": code,
                            "name": ticker["name"],
                            "status": "ok",
                            "model_ready": True,
                            "model_version": version,
                            "horizon_days": gate.get("horizon_days"),
                            "label_mode": gate.get("label_mode"),
                            "cv_metrics": cv,
                            "calibration_applied": (cv.get("calibration") or {}).get(
                                "applied"
                            ),
                            "gate_passed": bool(gate.get("passed", False)),
                            "gate_reason": gate.get("reason"),
                            "failures": gate.get("failures", []),
                            "metrics": gate.get("metrics", {}),
                            "data_validation_warnings": warnings,
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    entries.append(
                        _failure_entry(
                            ticker,
                            "ticker_processing_failed",
                            error=f"{type(e).__name__}: {e}",
                            warnings=warnings,
                        )
                    )

            ok_entries = [e for e in entries if e.get("status") == "ok"]
            aggregate = {
                "median_ic": _median([cv_by_ticker[c].get("ic") for c in cv_by_ticker]),
                "median_brier": _median(
                    [cv_by_ticker[c].get("brier") for c in cv_by_ticker]
                ),
                "median_auc": _median(
                    [cv_by_ticker[c].get("auc") for c in cv_by_ticker]
                ),
            }
            version_metadata["trained_tickers"] = [e["ticker"] for e in ok_entries]
            version_metadata["cv_metrics"] = {
                "by_ticker": cv_by_ticker,
                "aggregate": aggregate,
            }
            model_store.save_version_metadata(
                version, version_metadata, model_dir=staging_model_dir
            )

            failed_entries = [
                {
                    "ticker": e.get("ticker"),
                    "reason": e.get("reason"),
                    "error": e.get("error"),
                }
                for e in entries
                if e.get("status") != "ok"
            ]
            manifest = model_store.create_phase1_manifest(
                version,
                target_tickers=target_tickers,
                trained_tickers=version_metadata["trained_tickers"],
                failures=failed_entries,
                config_payload=config_payload,
                artifact_contract=artifact_contract,
                gate_contract=gate_contract,
                generated_at=generated_at,
                git_commit=git_commit,
                model_dir=staging_model_dir,
            )
            model_store.save_phase1_manifest(
                version, manifest, model_dir=staging_model_dir
            )
            candidate_validation = model_store.evaluate_phase1_candidate(
                version,
                previous_metadata=previous_metadata,
                model_dir=staging_model_dir,
            )
            model_store.record_phase1_candidate_validation(
                version,
                candidate_validation,
                staging_model_dir=staging_model_dir,
            )
            manifest_sha256 = model_store.phase1_manifest_sha256(
                version, staging_model_dir
            )

            if candidate_validation.get("passed"):
                activated_at = now_jst_iso()
                promoted_path = model_store.activate_staged_phase1_version(
                    version,
                    staging_model_dir,
                    pointer_metadata={
                        "kind": MODEL_KIND,
                        **model_store.phase1_contract_metadata_fields(
                            artifact_contract
                        ),
                        "artifact_contract": artifact_contract,
                        **model_store.phase1_gate_contract_metadata_fields(
                            gate_contract
                        ),
                        "gate_contract": gate_contract,
                        "generated_at": generated_at,
                        "activated_at": activated_at,
                        "previous_version": previous_version,
                        "config_sha256": config_sha256,
                        "horizon_days": version_metadata["horizon_days"],
                        "label_mode": version_metadata["label_mode"],
                        "n_models": len(ok_entries),
                        "git_commit": git_commit,
                    },
                )
                active_set = True
                print(
                    f"weekly: promoted {len(ok_entries)} verified bundles as "
                    f"{version}; active pointer updated atomically."
                )
            else:
                print(
                    "weekly: candidate rejected; active pointer left unchanged: "
                    f"{candidate_validation.get('failures', [])}"
                )
        except Exception as exc:  # noqa: BLE001
            artifact_error = f"{type(exc).__name__}: {exc}"
            candidate_validation = {
                "passed": False,
                "failures": [
                    {
                        "code": "candidate_build_or_activation_failed",
                        "error": artifact_error,
                    }
                ],
            }
            print(
                "weekly: candidate deployment failed; active pointer preserved: "
                f"{artifact_error}"
            )
        finally:
            try:
                model_store.discard_staging_model_dir(staging_model_dir, version)
                staging_cleanup["status"] = "removed"
            except Exception as cleanup_exc:  # noqa: BLE001
                staging_cleanup["status"] = "cleanup_failed"
                staging_cleanup["error"] = (
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )
                print(
                    "weekly: staging cleanup failed (directory is gitignored): "
                    f"{staging_cleanup['error']}"
                )

    ok_entries = [e for e in entries if e.get("status") == "ok"]
    aggregate = {
        "median_ic": _median([cv_by_ticker[c].get("ic") for c in cv_by_ticker]),
        "median_brier": _median([cv_by_ticker[c].get("brier") for c in cv_by_ticker]),
        "median_auc": _median([cv_by_ticker[c].get("auc") for c in cv_by_ticker]),
    }

    db_registered = False
    db_registry_error = None
    db_registry_queued = 0
    db_registry_queue_error = None
    db_registry_event_id = None
    db_registry_status = "not_activated"
    db_quality_error = None

    active_pointer_for_registry = model_store.read_active_model() or {}
    pointer_provenance = {
        "source": "active_model.json",
        "version": active_pointer_for_registry.get("version"),
        "manifest_sha256": active_pointer_for_registry.get("manifest_sha256"),
        "config_sha256": active_pointer_for_registry.get("config_sha256"),
        "activated_at": active_pointer_for_registry.get("activated_at"),
        "git_commit": active_pointer_for_registry.get("git_commit"),
    }
    registry_args = {
        "kind": MODEL_KIND,
        "universe": version_metadata["universe"],
        "feature_set": feature_cols,
        "params": {
            "lgb": "see src.model._LGB_PARAMS",
            "label_config": artifact_contract["label_config"],
            "artifact_contract": artifact_contract,
            "file_active_pointer": pointer_provenance,
        },
        "cv_metrics": {"by_ticker": cv_by_ticker, "aggregate": aggregate},
        "calibration": calibration_map,
        "artifact_uri": model_store.artifact_uri(version),
        "make_active": True,
    }

    pointer_matches_candidate = (
        pointer_provenance["version"] == version
        and pointer_provenance["manifest_sha256"] == manifest_sha256
        and pointer_provenance["config_sha256"] == config_sha256
    )
    if active_set and not pointer_matches_candidate:
        db_registry_status = "pointer_mismatch"
        db_registry_error = (
            "active_pointer_provenance_mismatch: "
            f"expected_version={version}, actual_version={pointer_provenance['version']}"
        )
        print(f"weekly: DB registry sync blocked: {db_registry_error}")
    elif active_set and db.db_enabled():
        conn = None
        try:
            conn = db.connect()
            db.register_model_version(conn, version, **registry_args)
            db_registered = True
            db_registry_status = "registered"
            print(f"weekly: registered {version} in model_registry (active).")
            try:
                for row in quality_rows:
                    db.upsert_model_quality(conn, row)
            except Exception as e:  # noqa: BLE001
                db_quality_error = f"{type(e).__name__}: {e}"
                print(f"weekly: DB quality sync incomplete: {db_quality_error}")
        except Exception as e:  # noqa: BLE001
            db_registry_error = f"{type(e).__name__}: {e}"
            print(f"weekly: DB registry sync incomplete: {db_registry_error}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:  # noqa: BLE001
                    close_error = f"{type(e).__name__}: {e}"
                    if db_registered:
                        db_quality_error = db_quality_error or (
                            f"connection_close_failed: {close_error}"
                        )
                    else:
                        db_registry_error = db_registry_error or (
                            f"connection_close_failed: {close_error}"
                        )
    elif active_set:
        db_registry_error = "db_disabled"
        print(
            "weekly: DB disabled; queueing model_registry registration "
            "(artifacts + active pointer remain locally active)."
        )

    if active_set and pointer_matches_candidate and not db_registered:
        queued_result = db.queue_model_registry_event(version, **registry_args)
        db_registry_queued = int(queued_result.get("queued") or 0)
        db_registry_event_id = queued_result.get("event_id")
        if queued_result.get("ok") and db_registry_queued == 1:
            db_registry_status = "queued"
            print(
                f"weekly: queued model_registry event {db_registry_event_id} "
                "for the next outbox flush."
            )
        else:
            db_registry_status = "queue_failed"
            db_registry_queue_error = queued_result.get("reason") or "queue_failed"
            print(
                f"weekly: model_registry event queue failed: {db_registry_queue_error}"
            )

    # Refresh docs/model_quality.json so the dashboard reflects the new model.
    if active_set:
        try:
            from src.dashboard import export_model_quality

            export_model_quality()
        except Exception as e:  # noqa: BLE001
            print(
                f"weekly: model_quality export skipped (ignored): {type(e).__name__}: {e}"
            )

    active_after = model_store.read_active_model() or {}
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model_version": version,
        "candidate_version": version,
        "active_model_version": active_after.get("version"),
        "active_set": active_set,
        "db_registered": db_registered,
        "db_registry_error": db_registry_error,
        "db_registry_queued": db_registry_queued,
        "db_registry_queue_error": db_registry_queue_error,
        "db_registry_status": db_registry_status,
        "db_quality_error": db_quality_error,
        "label_mode": label_cfg.get("label_mode"),
        "horizon_days": version_metadata["horizon_days"],
        "macro_features_enabled": version_metadata["macro_features_enabled"],
        "feature_count": len(feature_cols),
        "aggregate_cv": aggregate,
        "summary": {
            "total_tickers": len(entries),
            "ok_tickers": len(ok_entries),
            "failed_tickers": len(entries) - len(ok_entries),
            "model_ready_tickers": len(ok_entries),
            "gate_passed_tickers": sum(1 for e in ok_entries if e.get("gate_passed")),
        },
        "deployment": {
            "status": (
                "activated" if active_set else "error" if artifact_error else "rejected"
            ),
            "previous_active_version": previous_version,
            "active_model_version": active_after.get("version"),
            "candidate_validation": candidate_validation,
            "manifest_sha256": manifest_sha256,
            "promoted_path": promoted_path,
            "artifact_error": artifact_error,
            "staging_cleanup": staging_cleanup,
            "registry_consistent": bool(db_registered) if db.db_enabled() else None,
            "registry_sync": {
                "status": db_registry_status,
                "registered": db_registered,
                "queued": db_registry_queued,
                "event_id": db_registry_event_id,
                "error": db_registry_error,
                "queue_error": db_registry_queue_error,
                "quality_error": db_quality_error,
                "file_active_pointer": pointer_provenance,
            },
        },
        "entries": entries,
    }

    _write_report(output_path, payload)
    print(f"Weekly model retrain report exported to {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly Phase 1 model retrain")
    parser.add_argument("--output", default="docs/weekly_retrain_report.json")
    parser.add_argument(
        "--version",
        default=None,
        help=(
            "Unique model version label "
            "(default per-ticker-v1-<JST timestamp>-<git>-<run id>)"
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    version = args.version or _default_version()
    return run_retrain(Path(args.output), version)


if __name__ == "__main__":
    raise SystemExit(main())
