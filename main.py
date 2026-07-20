import os
from datetime import datetime

from src.env import get_env_bool
from src.timeutil import now_jst
from src.utils import log_exc
from src.config import (
    TICKERS,
    BACKTEST_GATE_CONFIG,
    DOCS_DIR,
    LINE_CONFIG,
    get_label_config,
    get_model_runtime_config,
    get_portfolio_config,
    get_cross_section_config,
)
from src.data_loader import update_data, load_data, sync_data_files
from src.labels import effective_horizon
from src.model import build_feature_frame, phase1_feature_cols
from src.predictor import generate_signal
from src.notifier import send_notification, send_line_text
from src.dashboard import update_dashboard
from src.backtest import format_gate_summary, write_backtest_report
from src import (
    db,
    macro,
    model_store,
    phase1,
    cs_model,
    db_records,
    portfolio,
    dashboard,
    digest,
)


def _env_bool(name: str, default: bool) -> bool:
    return get_env_bool(name, default, invalid="false")


def _run_date_jst() -> str:
    override = os.environ.get("RUN_DATE_JST", "").strip()
    if override:
        try:
            datetime.strptime(override, "%Y-%m-%d")
            return override
        except ValueError:
            print(f"Invalid RUN_DATE_JST={override!r}; using current JST date.")
    return now_jst().strftime("%Y-%m-%d")


def _now_jst_str() -> str:
    """JST wall-clock timestamp string for dashboard ``generated_at`` stamps."""
    return now_jst().strftime("%Y-%m-%d %H:%M:%S")


def _empty_metrics():
    semantics = {
        "turnover_days": "sessions with non-zero entry or exit notional",
        "round_trips": "completed aggregate signed-position episodes",
        "signal_cohorts": "non-zero signal sleeves opened",
        "avg_daily_net_return": "mean net return across every simulated session",
        "expectancy_per_trade": "mean compounded net return of completed round trips",
        "avg_daily_turnover": "mean entry-plus-exit notional across sessions",
        "trades": "deprecated alias of round_trips",
        "expectancy": "deprecated alias of expectancy_per_trade",
        "turnover": "deprecated alias of avg_daily_turnover",
    }
    return {
        "metrics_schema_version": 2,
        "metrics_semantics": semantics,
        "oos_days": 0,
        "turnover_days": 0,
        "round_trips": 0,
        "signal_cohorts": 0,
        "avg_daily_net_return": 0.0,
        "expectancy_per_trade": 0.0,
        "avg_daily_turnover": 0.0,
        "trades": 0,
        "cagr": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "expectancy": 0.0,
        "turnover": 0.0,
        "net_return_total": 0.0,
    }


def _latest_close_or_none(ticker_code):
    try:
        df = load_data(ticker_code)
        if df is None or df.empty or "close" not in df.columns:
            return None
        close = df["close"].dropna()
        if close.empty:
            return None
        return float(close.iloc[-1])
    except Exception:
        return None


def _failure_signal(ticker_info, reason, error=None, close=None):
    detail = reason if error is None else f"{reason}: {error}"
    return {
        "ticker": ticker_info["code"],
        "name": ticker_info["name"],
        "date": _run_date_jst(),
        "close": close,
        "prob_up": None,
        "action": "HOLD",
        "raw_action": "HOLD",
        "gate_passed": False,
        "confidence_label": "自信なし",
        "confidence_reason": detail,
        "reason": f"処理失敗のため見送り（{detail}）",
        "limit_price": None,
        "stop_loss": None,
        "status": "failed",
        "error": error,
    }


def _failure_backtest_entry(ticker_info, reason, error=None, validation_warnings=None):
    failures = [reason]
    if error:
        failures.append(str(error))
    return {
        "ticker": ticker_info["code"],
        "name": ticker_info["name"],
        "status": "failed",
        "passed": False,
        "reason": reason,
        "failures": failures,
        "error": error,
        "metrics": _empty_metrics(),
        "metrics_tuning": _empty_metrics(),
        "metrics_holdout": _empty_metrics(),
        "thresholds": None,
        "threshold_optimization": None,
        "data_validation_warnings": validation_warnings or [],
    }


def _read_json_file(path):
    """Read and parse a JSON file. Returns parsed dict or None (never raises)."""
    try:
        import json

        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _build_macro_regime(macro_panel):
    """Build macro regime dict from macro_panel + macro_latest.json.

    Returns {"market_bias": ..., "usdjpy": ...} or partial/{} on any failure.
    Never raises.
    """
    try:
        macro_latest_path = DOCS_DIR / "curation" / "macro_latest.json"
        macro_latest = _read_json_file(macro_latest_path) or {}
        market_bias = macro_latest.get("market_bias")
        usdjpy = None
        panel = macro_panel
        try:
            # macro_panel is a pandas DataFrame in the daily run; take the latest
            # non-null usdjpy. (dict fallback kept for callers/tests.)
            if panel is not None and "usdjpy" in getattr(panel, "columns", []):
                col = panel["usdjpy"].dropna()
                if not col.empty:
                    usdjpy = float(col.iloc[-1])
            elif isinstance(panel, dict) and panel.get("usdjpy") is not None:
                usdjpy = float(panel["usdjpy"])
        except Exception:  # noqa: BLE001
            pass
        return {"market_bias": market_bias, "usdjpy": usdjpy}
    except Exception:  # noqa: BLE001
        return {}


def _load_portfolio_regime(macro_regime=None):
    """Qualitative regime label for the Phase 2 risk brake (issue #3).

    Reads market_bias from docs/curation/macro_latest.json (weekly macro
    screen) via _build_macro_regime. Only the documented labels pass through;
    a missing file / unknown label degrades to "neutral" (brake off). Never
    raises. build_portfolio_snapshot applies risk_off_gross_mult when this
    returns "risk_off".
    """
    try:
        src = macro_regime if macro_regime is not None else _build_macro_regime(None)
        bias = str((src or {}).get("market_bias") or "").strip().lower()
        return bias if bias in ("risk_on", "neutral", "risk_off") else "neutral"
    except Exception:  # noqa: BLE001
        return "neutral"


def _label_config_for_mode(model_cfg):
    """`legacy` is the operational rollback path: old next-day binary labels."""
    label_cfg = get_label_config()
    if (
        model_cfg["model_mode"] == "legacy"
        and label_cfg.get("label_mode") != "binary_1d"
    ):
        print("Model mode legacy: forcing label_mode=binary_1d for rollback.")
        label_cfg = {
            **label_cfg,
            "label_mode": "binary_1d",
            "horizon_days": 1,
            "tb_max_days": 1,
        }
    return label_cfg


def _runtime_artifact_contract(model_cfg, label_cfg):
    feature_cols = phase1_feature_cols(model_cfg.get("macro_features_enabled", True))
    return model_store.build_phase1_artifact_contract(
        label_config=label_cfg,
        feature_columns=feature_cols,
        macro_features_enabled=model_cfg.get("macro_features_enabled", True),
        calibration_mode=model_cfg.get("calibration_mode", "isotonic"),
    )


def _runtime_gate_contract(model_cfg, label_cfg):
    artifact_contract = _runtime_artifact_contract(model_cfg, label_cfg)
    return model_store.build_phase1_gate_contract(
        BACKTEST_GATE_CONFIG, artifact_contract
    )


def _active_model_compatibility(active, model_cfg, label_cfg):
    """Validate pointer, manifest and metadata against the runtime contract."""
    result = model_store.validate_runtime_active_phase1(
        active,
        model_config=model_cfg,
        label_config=label_cfg,
        gate_config=BACKTEST_GATE_CONFIG,
    )
    return {**result, "expected_contract": result["artifact_contract"]}


def _active_model_compatible(active, model_cfg, label_cfg=None):
    """Boolean compatibility wrapper retained for callers/tests."""
    return _active_model_compatibility(
        active, model_cfg, label_cfg or get_label_config()
    )["compatible"]


def _compatibility_reason_text(reasons):
    parts = []
    seen = set()
    for reason in reasons or []:
        field = reason.get("field")
        source = reason.get("source")
        label = reason.get("code", "unknown")
        if field:
            label += f":{field}"
        if source:
            label += f"@{source}"
        if label not in seen:
            seen.add(label)
            parts.append(label)
    visible = parts[:8]
    if len(parts) > len(visible):
        visible.append(f"and_{len(parts) - len(visible)}_more")
    return ", ".join(visible) or "unknown"


def _bundle_artifact_compatibility(bundle, active):
    expected = (active or {}).get("artifact_contract")
    ticker_metadata = (bundle or {}).get("ticker_metadata") or {}
    result = model_store.compare_phase1_artifact_contract(
        ticker_metadata.get("artifact_contract"), expected or {}
    )
    reasons = list(result["reasons"])
    reasons.extend(
        model_store.verify_phase1_contract_metadata_fields(
            ticker_metadata, expected or {}
        )
    )
    version = (active or {}).get("version")
    if ticker_metadata.get("model_version") != version:
        reasons.append(
            {
                "code": "ticker_model_version_mismatch",
                "expected": version,
                "actual": ticker_metadata.get("model_version"),
            }
        )
    version_contract = ((bundle or {}).get("version_metadata") or {}).get(
        "artifact_contract"
    )
    version_check = model_store.compare_phase1_artifact_contract(
        version_contract, expected or {}
    )
    reasons.extend(
        {**reason, "source": "bundle_version_metadata"}
        for reason in version_check["reasons"]
    )
    reasons.extend(
        {**reason, "source": "bundle_version_metadata"}
        for reason in model_store.verify_phase1_contract_metadata_fields(
            (bundle or {}).get("version_metadata") or {}, expected or {}
        )
    )
    expected_gate_contract = (active or {}).get("gate_contract") or {}
    reasons.extend(
        model_store.compare_phase1_gate_contract(
            ticker_metadata.get("gate_contract"), expected_gate_contract
        )["reasons"]
    )
    reasons.extend(
        model_store.verify_phase1_gate_contract_metadata_fields(
            ticker_metadata, expected_gate_contract
        )
    )
    reasons.extend(
        {**reason, "source": "bundle_version_metadata"}
        for reason in model_store.compare_phase1_gate_contract(
            ((bundle or {}).get("version_metadata") or {}).get("gate_contract"),
            expected_gate_contract,
        )["reasons"]
    )
    reasons.extend(
        {**reason, "source": "bundle_version_metadata"}
        for reason in model_store.verify_phase1_gate_contract_metadata_fields(
            (bundle or {}).get("version_metadata") or {}, expected_gate_contract
        )
    )
    evidence = ticker_metadata.get("gate_evidence")
    try:
        exact_model_hash = model_store.booster_bundle_sha256(
            {
                "folds": (bundle or {}).get("folds") or [],
                "final": (bundle or {}).get("final"),
            }
        )
    except Exception as exc:  # noqa: BLE001 -- corrupt saved model fails closed
        exact_model_hash = ""
        reasons.append(
            {
                "code": "model_bundle_hash_failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    reasons.extend(
        model_store.verify_phase1_gate_evidence(
            evidence,
            model_version=version,
            artifact_contract=expected or {},
            gate_contract=expected_gate_contract,
            model_bundle_sha256=exact_model_hash,
            calibrator_sha256=model_store.payload_sha256(
                (bundle or {}).get("calibration")
            ),
            applied_calibration_id=ticker_metadata.get("applied_calibration_id"),
        )
    )
    return {"compatible": not reasons, "reasons": reasons}


def _attach_confidence_fields(signal, gate_result, model_ready):
    gate_passed = bool(gate_result.get("passed", False))
    failures = gate_result.get("failures") or []
    fail_summary = (
        ", ".join(failures) if failures else str(gate_result.get("reason", "unknown"))
    )

    signal["raw_action"] = signal.get("action", "HOLD")
    signal["gate_passed"] = gate_passed
    signal["confidence_label"] = "自信あり" if gate_passed else "自信なし"
    signal["confidence_reason"] = (
        "過去検証で基準をクリア"
        if gate_passed
        else f"過去検証で基準未達 ({fail_summary})"
    )

    # Guard rail: if model inference failed, force non-actionable output.
    if not model_ready:
        signal["gate_passed"] = False
        signal["status"] = "failed"
        signal["prob_up"] = None
        signal["raw_score"] = None
        signal["expected_ret"] = None
        signal["features_hash"] = None
        signal["confidence_label"] = "自信なし"
        signal["confidence_reason"] = "当日の予測計算に失敗"
        signal["action"] = "HOLD"
        signal["reason"] = "自信なしのため見送り（当日の予測計算に失敗）"
        _clear_entry_exit_fields(signal)
        return signal

    # Even when probability is available, block actionable output on gate failure.
    if not gate_passed:
        signal["action"] = "HOLD"
        signal["reason"] = "自信なしのため見送り（過去検証で基準未達）"
        _clear_entry_exit_fields(signal)

    return signal


def _clear_entry_exit_fields(signal):
    """Strip entry/exit price guidance from a non-actionable (HOLD-forced) signal.

    Keeps exported JSON consistent: a gate-failed or model-failed signal must not
    carry limit / take-profit / stop-loss levels that downstream consumers could
    mistake for an actionable plan.
    """
    for key in (
        "limit_price",
        "stop_loss",
        "take_profit_price",
        "stop_price",
        "take_profit_pct",
        "stop_pct",
        "time_exit_days",
        "exit_plan",
    ):
        signal[key] = None


def _predict_for_ticker(featured, ticker_info, ctx):
    """
    Produce (prob_up, model_ready, phase1_fields) honoring TRADER_MODEL_MODE.

    - phase1: an active saved bundle is required; missing -> not model_ready.
    - auto:   use the saved bundle when available, else train one ephemeral
              exact candidate with its own purged OOS gate evidence.
    - legacy: always train that ephemeral exact candidate from scratch.
    """
    code = ticker_info["code"]
    mode = ctx["model_cfg"]["model_mode"]
    label_cfg = ctx["label_cfg"]
    active = ctx["active"]
    horizon = effective_horizon(label_cfg)
    saved_model_error = ctx.get("saved_model_disabled_reason")

    if mode in ("auto", "phase1") and active:
        version = active.get("version")
        bundle = model_store.load_model_bundle(version, code)
        if bundle is not None:
            bundle_check = _bundle_artifact_compatibility(bundle, active)
            if not bundle_check["compatible"]:
                saved_model_error = _compatibility_reason_text(bundle_check["reasons"])
                print(f"Saved bundle disabled for {code}: {saved_model_error}.")
                bundle = None
        if bundle is not None:
            pred = phase1.predict_ticker(featured, bundle)
            if pred is not None:
                print(
                    f"Inference for {code}: saved model {version} "
                    f"(prob_up={pred['prob_up']:.2%}, exp_ret={pred['expected_ret']})"
                )
                return (
                    pred["prob_up"],
                    True,
                    {
                        "model_version": version,
                        "horizon_days": pred["horizon_days"],
                        "raw_score": pred["raw_score"],
                        "expected_ret": pred["expected_ret"],
                        "features_hash": pred["features_hash"],
                        "artifact_schema_version": pred["artifact_schema_version"],
                        "label_config": pred["label_config"],
                        "feature_schema_hash": pred["feature_schema_hash"],
                        "macro_features_enabled": pred["macro_features_enabled"],
                        "calibration_mode": pred["calibration_mode"],
                        "calibration_id": pred["calibration_id"],
                        "applied_calibration_id": pred["applied_calibration_id"],
                        "execution_contract_version": pred[
                            "execution_contract_version"
                        ],
                        "model_bundle_sha256": pred["model_bundle_sha256"],
                        "gate_config_hash": pred["gate_config_hash"],
                        "gate_evidence_sha256": pred["gate_evidence_sha256"],
                        "gate_result": pred["gate_result"],
                    },
                )
            saved_model_error = "saved_bundle_inference_failed"
        if mode == "phase1":
            print(f"Active model has no bundle for {code}; phase1 mode -> failed HOLD.")
            return (
                0.5,
                False,
                {
                    "model_version": version,
                    "horizon_days": active.get("effective_horizon_days", horizon),
                    "model_error": saved_model_error or "saved_bundle_missing",
                },
            )
        print(
            f"No saved bundle for {code}; auto mode -> "
            "exact-contract ephemeral candidate."
        )

    if mode == "phase1":
        print(f"No active model for {code}; phase1 mode -> failed HOLD.")
        return (
            0.5,
            False,
            {
                "model_version": active.get("version") if active else None,
                "horizon_days": horizon,
                "model_error": saved_model_error or "active_model_unavailable",
            },
        )

    # rollback / auto-fallback: train one ephemeral candidate whose own purged
    # OOS evidence supplies its calibration, thresholds and gate.  No gate from
    # a separately trained surrogate model is reused.
    fallback_model_cfg = {
        "macro_features_enabled": False,
        "calibration_mode": "none",
        "min_calibration_rows": int(ctx["model_cfg"].get("min_calibration_rows", 60)),
    }
    fallback_artifact_contract = model_store.build_phase1_artifact_contract(
        label_config=label_cfg,
        feature_columns=phase1_feature_cols(False),
        macro_features_enabled=False,
        calibration_mode="none",
    )
    fallback_gate_contract = model_store.build_phase1_gate_contract(
        BACKTEST_GATE_CONFIG, fallback_artifact_contract
    )
    fallback_version = model_store.phase1_ephemeral_model_version(
        fallback_artifact_contract, fallback_gate_contract
    )
    result, info = phase1.train_ticker_bundle(
        featured,
        BACKTEST_GATE_CONFIG,
        label_cfg,
        fallback_model_cfg,
        model_version=fallback_version,
    )
    if result is None:
        return (
            0.5,
            False,
            {
                "model_version": fallback_version,
                "horizon_days": horizon,
                "model_error": info.get("reason", "ephemeral_candidate_failed"),
            },
        )
    metadata = result["metadata"]
    in_memory_bundle = {
        "version": fallback_version,
        "folds": result["boosters"].get("folds") or [],
        "final": result["boosters"].get("final"),
        "calibration": metadata.get("calibration"),
        "feature_reference": metadata.get("feature_reference") or {},
        "ticker_metadata": metadata,
        "version_metadata": {
            "artifact_contract": metadata.get("artifact_contract"),
            "gate_contract": metadata.get("gate_contract"),
        },
    }
    pred = phase1.predict_ticker(featured, in_memory_bundle)
    if pred is None:
        return (
            0.5,
            False,
            {
                "model_version": fallback_version,
                "horizon_days": horizon,
                "model_error": "ephemeral_candidate_inference_failed",
            },
        )
    return (
        pred["prob_up"],
        True,
        {
            "model_version": fallback_version,
            "horizon_days": pred["horizon_days"],
            "raw_score": pred["raw_score"],
            "expected_ret": pred["expected_ret"],
            "features_hash": pred["features_hash"],
            "artifact_schema_version": pred["artifact_schema_version"],
            "label_config": pred["label_config"],
            "feature_schema_hash": pred["feature_schema_hash"],
            "macro_features_enabled": pred["macro_features_enabled"],
            "calibration_mode": pred["calibration_mode"],
            "calibration_id": pred["calibration_id"],
            "applied_calibration_id": pred["applied_calibration_id"],
            "execution_contract_version": pred["execution_contract_version"],
            "model_bundle_sha256": pred["model_bundle_sha256"],
            "gate_config_hash": pred["gate_config_hash"],
            "gate_evidence_sha256": pred["gate_evidence_sha256"],
            "gate_result": pred["gate_result"],
            "saved_model_fallback_reason": saved_model_error,
        },
    )


def _unavailable_model_gate(label_cfg, reason):
    """Fail-closed gate shape used when no exact model evidence is available."""
    metrics = _empty_metrics()
    return {
        "passed": False,
        "skipped": False,
        "reason": "model_gate_evidence_unavailable",
        "horizon_days": effective_horizon(label_cfg),
        "label_mode": label_cfg.get("label_mode"),
        "failures": [reason or "model_gate_evidence_unavailable"],
        "metrics": metrics,
        "metrics_tuning": dict(metrics),
        "metrics_holdout": dict(metrics),
        "thresholds": None,
        "threshold_optimization": None,
        "gate_source": "unavailable",
    }


def _process_ticker(ticker_info, ctx):
    code = ticker_info["code"]
    print(f"\nProcessing {code} ({ticker_info['name']})...")

    validation_warnings = []

    # 1. Update Data
    # In B-unyo, we run at 06:00 JST, so we should have data up to yesterday.
    # Stooq usually updates around midnight UTC or later?
    # Actually Stooq data for JP market closes at 15:00 JST, available shortly after.
    # 06:00 JST next day is safe.
    updated_df = update_data(code)
    if updated_df is not None:
        validation_warnings = updated_df.attrs.get("validation_warnings", []) or []

    # 2. Load Data
    df = load_data(code)
    if df is not None:
        validation_warnings = list(
            dict.fromkeys(
                validation_warnings + (df.attrs.get("validation_warnings", []) or [])
            )
        )
    if df is None or len(df) < 60:  # Need 60 for MA60
        print(f"Insufficient data for {code}. Recording failed HOLD state.")
        close = _latest_close_or_none(code)
        return (
            _failure_signal(ticker_info, "insufficient_data", close=close),
            _failure_backtest_entry(
                ticker_info,
                "insufficient_data",
                validation_warnings=validation_warnings,
            ),
        )

    # 3. Feature Engineering (technical + macro/regime features)
    featured = build_feature_frame(
        df,
        macro_panel=ctx["macro_panel"],
        ticker_info=ticker_info,
        macro_enabled=ctx["model_cfg"].get("macro_features_enabled", True),
    )
    if featured.empty:
        print(
            f"Data empty after feature engineering for {code}. Recording failed HOLD state."
        )
        close = _latest_close_or_none(code)
        return (
            _failure_signal(ticker_info, "empty_features", close=close),
            _failure_backtest_entry(
                ticker_info,
                "empty_features",
                validation_warnings=validation_warnings,
            ),
        )

    # 4. Predict and load the gate generated by that exact candidate model.
    # Saved and ephemeral fallback paths both return immutable/in-memory OOS
    # evidence; a separate daily surrogate gate is never trained here.
    prob_up, model_ready, phase1_fields = _predict_for_ticker(
        featured, ticker_info, ctx
    )
    if not model_ready:
        print(
            f"Model inference unavailable for {code}. Falling back to neutral probability."
        )
        prob_up = 0.5
    gate_result = phase1_fields.get("gate_result")
    if not isinstance(gate_result, dict):
        gate_result = _unavailable_model_gate(
            ctx["label_cfg"], phase1_fields.get("model_error")
        )
    gate_summary = format_gate_summary(gate_result)
    gate_status = "PASS" if gate_result["passed"] else "FAIL"
    print(f"KPI gate {gate_status} for {code}: {gate_summary}")

    backtest_entry = {
        "ticker": code,
        "name": ticker_info["name"],
        "status": "ok",
        "passed": gate_result["passed"],
        "reason": gate_result["reason"],
        "horizon_days": gate_result.get("horizon_days"),
        "label_mode": gate_result.get("label_mode"),
        "failures": gate_result["failures"],
        "metrics": gate_result["metrics"],
        "metrics_tuning": gate_result.get("metrics_tuning"),
        "metrics_holdout": gate_result.get("metrics_holdout"),
        "thresholds": gate_result.get("thresholds"),
        "threshold_optimization": gate_result.get("threshold_optimization"),
        "gate_source": gate_result.get("gate_source"),
        "gate_evidence_sha256": gate_result.get("gate_evidence_sha256"),
        "model_version": phase1_fields.get("model_version"),
        "data_validation_warnings": validation_warnings,
    }

    print(f"Prediction for {code}: Up Probability = {prob_up:.2%}")
    thresholds = gate_result.get("thresholds")

    # 6. Generate Signal
    signal = generate_signal(
        featured,
        prob_up,
        ticker_info,
        thresholds=thresholds,
        label_config=ctx["label_cfg"],
    )
    signal["thresholds"] = thresholds
    signal["threshold_optimization"] = gate_result.get("threshold_optimization")
    signal["status"] = "ok"
    # Phase 1 prediction provenance (flows into predictions table).
    signal["model_version"] = phase1_fields.get("model_version")
    signal["horizon_days"] = phase1_fields.get("horizon_days")
    signal["raw_score"] = phase1_fields.get("raw_score")
    signal["expected_ret"] = phase1_fields.get("expected_ret")
    signal["features_hash"] = phase1_fields.get("features_hash")
    for key in (
        "artifact_schema_version",
        "label_config",
        "feature_schema_hash",
        "macro_features_enabled",
        "calibration_mode",
        "calibration_id",
        "applied_calibration_id",
        "execution_contract_version",
        "model_bundle_sha256",
        "gate_config_hash",
        "gate_evidence_sha256",
        "saved_model_fallback_reason",
        "model_error",
    ):
        if key in phase1_fields:
            signal[key] = phase1_fields[key]
    signal = _attach_confidence_fields(signal, gate_result, model_ready=model_ready)

    if not signal["gate_passed"]:
        print(
            f"Actionable signal blocked for {code}: {signal.get('confidence_reason', 'gate failed')}"
        )

    return signal, backtest_entry


def run_phase2_inference(macro_panel, model_cfg, run_date):
    """
    Phase 2 cross-sectional inference. Returns a result dict for the portfolio
    layer (Task 6/8) or None when Phase 2 is disabled/unavailable.

    NEVER raises — callers still wrap in try/except as a backstop.

    Gating
    ------
    - portfolio_config["enabled"] is False -> return None (skip)
    - no active CS pointer / bundle load fails -> {"status":"fallback", ...}
    - len(TICKERS) < cross_section_config["min_universe"] -> fallback
    - too few usable tickers after load_data -> fallback

    On success: loads each enabled ticker's df, builds the cross-section panel,
    calls cs_model.infer_cross_section, persists via db.record_cs_predictions,
    and returns a success dict carrying status, mode, model_version, as_of_date,
    predictions DataFrame, tickers_data list, and bundle.

    The portfolio construction + snapshot/JSON export (Task 6/8) consumes the
    returned dict; for now those steps are left as a TODO hook.
    """
    pf_cfg = get_portfolio_config()
    cs_cfg = get_cross_section_config()

    if not pf_cfg["enabled"]:
        print("Phase 2 portfolio disabled; skipping.")
        return None

    portfolio_mode = pf_cfg["mode"]

    # --- Active CS model check ---
    active_cs = model_store.read_active_cs_model()
    if active_cs is None:
        print(
            "Phase 2: no active CS model pointer found; falling back to Phase 1 only."
        )
        return {
            "status": "fallback",
            "reason": "no_active_cs_model",
            "mode": portfolio_mode,
        }

    version = active_cs.get("version", "")
    bundle = model_store.load_cs_bundle(version)
    if bundle is None:
        print(f"Phase 2: CS bundle load failed for version={version!r}; falling back.")
        return {
            "status": "fallback",
            "reason": "bundle_load_failed",
            "mode": portfolio_mode,
            "model_version": version,
        }

    # --- Universe size check ---
    universe = TICKERS
    min_universe = int(cs_cfg.get("min_universe", 30))
    if len(universe) < min_universe:
        print(
            f"Phase 2: universe size {len(universe)} < min_universe {min_universe}; falling back."
        )
        return {
            "status": "fallback",
            "reason": "insufficient_universe",
            "mode": portfolio_mode,
            "model_version": version,
        }

    # --- Load OHLCV data for all enabled tickers (best-effort) ---
    tickers_data = []
    for ticker_info in universe:
        try:
            df = load_data(ticker_info["code"])
        except Exception as e:  # noqa: BLE001
            print(
                f"Phase 2: load_data failed for {ticker_info['code']}: {type(e).__name__}: {e}"
            )
            df = None
        if df is not None and not df.empty:
            tickers_data.append((ticker_info, df))

    if len(tickers_data) < min_universe:
        print(
            f"Phase 2: only {len(tickers_data)} tickers with usable data "
            f"(need {min_universe}); falling back."
        )
        return {
            "status": "fallback",
            "reason": "insufficient_usable_data",
            "mode": portfolio_mode,
            "model_version": version,
        }

    # --- Macro features flag (honour bundle's training setting) ---
    macro_enabled = bool(
        active_cs.get(
            "macro_features_enabled",
            (model_cfg or {}).get("macro_features_enabled", True),
        )
    )

    # --- Inference ---
    horizon_days = int(
        active_cs.get("horizon_days", cs_cfg.get("label_horizon_days", 5))
    )
    pred_df, as_of = cs_model.infer_cross_section(
        tickers_data,
        macro_panel,
        bundle,
        macro_enabled=macro_enabled,
        label_horizon_days=horizon_days,
    )

    if pred_df is None or pred_df.empty:
        print("Phase 2: infer_cross_section returned empty predictions; falling back.")
        return {
            "status": "fallback",
            "reason": "empty_predictions",
            "mode": portfolio_mode,
            "model_version": version,
        }

    # --- Build DB rows ---
    as_of_str = as_of.strftime("%Y-%m-%d") if as_of is not None else None
    cs_rows = []
    for _, row in pred_df.iterrows():
        mapped = db_records.cs_prediction_row(
            {
                "ticker": row.get("ticker"),
                "raw_score": row.get("raw_score"),
                "cs_rank": row.get("cs_rank"),
                "prob_up": row.get("prob_up"),
                "expected_ret": row.get("expected_ret"),
                "features_hash": None,  # CS panel doesn't derive per-ticker hash here
            },
            run_date,
            model_version=version,
            horizon_days=horizon_days,
            as_of_date=as_of_str,
        )
        if mapped is not None:
            cs_rows.append(mapped)

    # --- Persist (best-effort; never breaks Phase 1) ---
    db_result = db.record_cs_predictions(cs_rows, run_date)
    print(f"Phase 2 DB write: {db_result}")

    return {
        "status": "ok",
        "mode": portfolio_mode,
        "model_version": version,
        "as_of_date": as_of_str,
        "predictions": pred_df,
        "tickers_data": tickers_data,
        "bundle": bundle,
    }


def _prev_target_weights() -> dict:
    """
    Yesterday's target book as ``{ticker: target_weight}``, best-effort.

    DB first (latest portfolio_snapshots row), then docs/portfolio_latest.json,
    else ``{}``. Never raises — the portfolio build treats ``{}`` as a fresh
    book (everything is a "new" diff).
    """
    # DB-first.
    if db.db_enabled():
        try:
            conn = db.connect()
            try:
                snap = db.fetch_latest_portfolio_snapshot(conn)
            finally:
                conn.close()
            if snap and snap.get("positions"):
                return {
                    p["ticker"]: float(p.get("target_weight") or 0.0)
                    for p in snap["positions"]
                    if p.get("ticker")
                }
        except Exception as e:  # noqa: BLE001
            print(
                f"Phase 2: prev-weights DB read failed (ignored): {type(e).__name__}: {e}"
            )

    # JSON fallback (docs/portfolio_latest.json).
    try:
        from src.dashboard import PORTFOLIO_LATEST_FILE

        if PORTFOLIO_LATEST_FILE.exists():
            import json

            data = json.loads(PORTFOLIO_LATEST_FILE.read_text(encoding="utf-8"))
            if (
                isinstance(data, dict)
                and data.get("available")
                and data.get("positions")
            ):
                return {
                    p["ticker"]: float(p.get("target_weight") or 0.0)
                    for p in data["positions"]
                    if p.get("ticker")
                }
    except Exception as e:  # noqa: BLE001
        print(
            f"Phase 2: prev-weights JSON read failed (ignored): {type(e).__name__}: {e}"
        )

    return {}


def _run_portfolio_snapshot(phase2, run_date):
    """
    Build the daily portfolio snapshot from a successful Phase 2 inference
    result, write docs/portfolio_latest.json, and upsert portfolio_snapshots.

    The snapshot is returned to main() where merge_target_weights reflects
    target weights into signals when mode==active AND the gate passed
    (shadow path stays unchanged).
    """
    tickers_data = phase2.get("tickers_data") or []

    # Enrichment maps keyed by ticker code (None values are fine downstream).
    sectors: dict = {}
    names: dict = {}
    closes: dict = {}
    price_frames: dict = {}
    for ticker_info, df in tickers_data:
        code = ticker_info.get("code")
        if not code:
            continue
        sectors[code] = ticker_info.get("sector")
        names[code] = ticker_info.get("name")
        if df is not None and not df.empty and "close" in df.columns:
            closes[code] = float(df["close"].iloc[-1])
            if "date" in df.columns:
                price_frames[code] = df[["date", "close"]]

    prev_weights = _prev_target_weights()

    # Qualitative regime from the weekly macro screen: risk_off halves gross
    # via risk_off_gross_mult (issue #3 wiring; defaults to neutral on any gap).
    regime = _load_portfolio_regime()

    cfg = get_portfolio_config()
    cfg["top_n"] = get_cross_section_config().get("top_n", 8)

    snapshot = portfolio.build_portfolio_snapshot(
        phase2["predictions"],
        price_frames,
        prev_weights,
        cfg,
        sectors=sectors,
        names=names,
        closes=closes,
        regime=regime,
        run_date=run_date,
        as_of_date=phase2.get("as_of_date"),
        model_version=phase2.get("model_version"),
        mode=phase2.get("mode", "shadow"),
    )

    dashboard.export_portfolio_latest(
        snapshot,
        run_date=run_date,
        generated_at=_now_jst_str(),
    )
    db_result = db.record_portfolio_snapshot(snapshot, run_date)
    print(f"Phase 2 portfolio DB write: {db_result}")

    diff = snapshot.get("diff_summary")
    print(
        f"Phase 2 portfolio snapshot: mode={snapshot.get('mode')} "
        f"status={snapshot.get('status')} gross={snapshot.get('gross_exposure')} "
        f"positions={len(snapshot.get('positions') or [])} diff={diff}"
    )
    return snapshot


def _merge_portfolio_target_weights(signals, snapshot):
    """Apply an active snapshot only with gate evidence for that exact model.

    Shadow/fallback/missing snapshots return the original list object without
    consulting the weekly gate, preserving the shadow byte contract. Active
    snapshots fail closed when their model version is missing or does not match
    ``portfolio_backtest.json``.
    """
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("status") != "ok"
        or snapshot.get("mode") != "active"
    ):
        return signals

    gate_passed = portfolio.read_portfolio_gate(
        expected_model_version=snapshot.get("model_version")
    )
    return portfolio.merge_target_weights(
        signals,
        snapshot,
        gate_passed=gate_passed,
    )


def main():
    print("Starting daily stock prediction job...")

    active_codes = [ticker_info["code"] for ticker_info in TICKERS]
    print(
        f"Configured tickers: {', '.join(active_codes) if active_codes else '(none)'}"
    )

    # Keep data directory aligned with active tickers in tickers.yml.
    # Inactive parquet files are archived, not deleted; failure here should not
    # stop active ticker processing.
    try:
        sync_data_files(active_codes)
    except Exception as e:
        log_exc("Failed to archive inactive data files. Continuing daily run", e)

    # Phase 1 inference context: model mode, label config, macro panel, and the
    # active saved model (read once for the whole run).
    model_cfg = get_model_runtime_config()
    label_cfg = _label_config_for_mode(model_cfg)
    macro_panel = macro.load_macro_panel()
    active = None
    saved_model_disabled_reason = None
    if model_cfg["model_mode"] in ("auto", "phase1"):
        active = model_store.read_active_model()
        compatibility = _active_model_compatibility(active, model_cfg, label_cfg)
        if active and not compatibility["compatible"]:
            saved_model_disabled_reason = _compatibility_reason_text(
                compatibility["reasons"]
            )
            print(
                "Active saved model is incompatible with the runtime artifact "
                f"contract ({saved_model_disabled_reason}); saved inference "
                "disabled for this run."
            )
            active = None
        elif not active:
            saved_model_disabled_reason = "active_model_missing"
    mode = model_cfg["model_mode"]
    active_label = active.get("version") if active else "none"
    print(
        f"Model mode: {mode}; active model: {active_label}; "
        f"macro panel: {'loaded' if macro_panel is not None else 'absent'}"
    )
    ctx = {
        "model_cfg": model_cfg,
        "label_cfg": label_cfg,
        "macro_panel": macro_panel,
        "active": active,
        "saved_model_disabled_reason": saved_model_disabled_reason,
    }

    signals = []
    backtest_entries = []

    for ticker_info in TICKERS:
        try:
            signal, backtest_entry = _process_ticker(ticker_info, ctx)
        except Exception as e:
            code = ticker_info["code"]
            error = f"{type(e).__name__}: {e}"
            log_exc(f"Failed to process {code}. Recording failed HOLD state", e)
            signal = _failure_signal(
                ticker_info,
                "ticker_processing_failed",
                error=error,
                close=_latest_close_or_none(code),
            )
            backtest_entry = _failure_backtest_entry(
                ticker_info,
                "ticker_processing_failed",
                error=error,
            )

        signals.append(signal)
        backtest_entries.append(backtest_entry)

    run_date = _run_date_jst()

    # Phase 2: cross-sectional inference + portfolio snapshot. Never breaks Phase 1.
    snapshot = None
    try:
        phase2 = run_phase2_inference(macro_panel, model_cfg, run_date)
        if phase2 is None:
            pass  # Phase 2 disabled -> leave docs/portfolio_latest.json untouched.
        else:
            print(
                f"Phase 2 inference: {phase2.get('status')} "
                f"(mode={phase2.get('mode')}, model={phase2.get('model_version')})"
            )
            if phase2.get("status") == "ok":
                snapshot = _run_portfolio_snapshot(phase2, run_date)
            elif phase2.get("status") == "fallback":
                dashboard.export_portfolio_latest(
                    None,
                    reason=phase2.get("reason", "fallback"),
                    run_date=run_date,
                    generated_at=_now_jst_str(),
                )
    except Exception as e:  # noqa: BLE001
        log_exc("Phase 2 inference skipped (ignored)", e)

    # Phase 3: reflect active-mode target weights into signals. No-op in shadow /
    # gate-fail / no-snapshot, so shadow behavior is byte-for-byte unchanged.
    try:
        signals = _merge_portfolio_target_weights(signals, snapshot)
    except Exception as e:  # noqa: BLE001
        log_exc("merge_target_weights skipped (ignored)", e)

    # Notification (post-loop): the daily digest is the primary channel (it lists
    # actionable ticker names per action). Per-ticker pushes default OFF to stay
    # inside the LINE free tier (200 push/month) with a ~50-name universe; set
    # TRADER_NOTIFY_PER_TICKER_ENABLED=true to bring them back. Each push is
    # isolated so one malformed signal can't drop the rest.
    if _env_bool("TRADER_NOTIFY_PER_TICKER_ENABLED", False):
        for signal in signals:
            if signal.get("gate_passed") and signal.get("action") != "HOLD":
                try:
                    send_notification(signal)
                except Exception as e:  # noqa: BLE001
                    print(
                        f"Notification failed for {signal.get('ticker')} "
                        f"(ignored): {type(e).__name__}: {e}"
                    )
    # Task 5: daily morning digest (best-effort, never breaks the run).
    if _env_bool("TRADER_NOTIFY_DIGEST_ENABLED", True):
        try:
            from src.dashboard import PORTFOLIO_LATEST_FILE, PERFORMANCE_FILE

            portfolio_payload = (
                snapshot if snapshot else _read_json_file(PORTFOLIO_LATEST_FILE)
            )
            performance_payload = _read_json_file(PERFORMANCE_FILE)
            macro_regime = _build_macro_regime(macro_panel)
            text = digest.build_daily_digest(
                run_date,
                portfolio_payload,
                performance_payload,
                macro_regime,
                signals,
                LINE_CONFIG.get("dashboard_url", ""),
            )
            send_line_text(text)
        except Exception as e:  # noqa: BLE001
            print(f"Digest notification failed (ignored): {type(e).__name__}: {e}")

    # Phase 0: write-through to the measurement DB AFTER the active-weight merge so
    # signals.target_weight lands. Never breaks the run.
    try:
        db_result = db.record_run(signals, run_date)
        print(f"DB record_run: {db_result}")
    except Exception as e:  # defensive: record_run itself should not raise
        log_exc("DB record_run unexpected error (ignored)", e)

    report_path = write_backtest_report(backtest_entries)
    print(f"Backtest KPI report exported to {report_path}")

    # Update Dashboard (always run to keep frontend data in sync with tickers.yml)
    update_dashboard(signals)
    if not signals:
        print("No signals generated. Dashboard data was still refreshed.")

    print("\nDaily job completed.")


if __name__ == "__main__":
    main()
