import os
from datetime import UTC, datetime, timedelta

from src.config import (
    TICKERS,
    BACKTEST_GATE_CONFIG,
    get_label_config,
    get_model_runtime_config,
)
from src.data_loader import update_data, load_data, sync_data_files
from src.labels import effective_horizon
from src.model import build_feature_frame, train_and_predict
from src.predictor import generate_signal
from src.notifier import send_notification
from src.dashboard import update_dashboard
from src.backtest import evaluate_kpi_gate, format_gate_summary, write_backtest_report
from src import db, macro, model_store, phase1


def _run_date_jst() -> str:
    override = os.environ.get("RUN_DATE_JST", "").strip()
    if override:
        try:
            datetime.strptime(override, "%Y-%m-%d")
            return override
        except ValueError:
            print(f"Invalid RUN_DATE_JST={override!r}; using current JST date.")
    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y-%m-%d")


def _empty_metrics():
    return {
        "oos_days": 0,
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


def _label_config_for_mode(model_cfg):
    """`legacy` is the operational rollback path: old next-day binary labels."""
    label_cfg = get_label_config()
    if model_cfg["model_mode"] == "legacy" and label_cfg.get("label_mode") != "binary_1d":
        print("Model mode legacy: forcing label_mode=binary_1d for rollback.")
        label_cfg = {
            **label_cfg,
            "label_mode": "binary_1d",
            "horizon_days": 1,
            "tb_max_days": 1,
        }
    return label_cfg


def _active_model_compatible(active, model_cfg):
    """
    Avoid using a saved model trained with a different macro-feature setting.
    Older pointers lacked the flag; those are treated as macro-enabled.
    """
    if not active:
        return False
    expected = bool(model_cfg.get("macro_features_enabled", True))
    actual = bool(active.get("macro_features_enabled", True))
    return actual == expected


def _attach_confidence_fields(signal, gate_result, model_ready):
    gate_passed = bool(gate_result.get("passed", False))
    failures = gate_result.get("failures") or []
    fail_summary = ", ".join(failures) if failures else str(gate_result.get("reason", "unknown"))

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
        signal["limit_price"] = None
        signal["stop_loss"] = None
        return signal

    # Even when probability is available, block actionable output on gate failure.
    if not gate_passed:
        signal["action"] = "HOLD"
        signal["reason"] = "自信なしのため見送り（過去検証で基準未達）"
        signal["limit_price"] = None
        signal["stop_loss"] = None

    return signal


def _predict_for_ticker(featured, ticker_info, ctx):
    """
    Produce (prob_up, model_ready, phase1_fields) honoring TRADER_MODEL_MODE.

    - phase1: an active saved bundle is required; missing -> not model_ready.
    - auto:   use the saved bundle when available, else legacy daily training.
    - legacy: always train from scratch with the configured label.
    """
    code = ticker_info["code"]
    mode = ctx["model_cfg"]["model_mode"]
    label_cfg = ctx["label_cfg"]
    active = ctx["active"]
    horizon = effective_horizon(label_cfg)

    if mode in ("auto", "phase1") and active:
        version = active.get("version")
        bundle = model_store.load_model_bundle(version, code)
        if bundle is not None:
            pred = phase1.predict_ticker(featured, bundle, label_cfg)
            if pred is not None:
                print(f"Inference for {code}: saved model {version} "
                      f"(prob_up={pred['prob_up']:.2%}, exp_ret={pred['expected_ret']})")
                return pred["prob_up"], True, {
                    "model_version": version,
                    "horizon_days": pred["horizon_days"],
                    "raw_score": pred["raw_score"],
                    "expected_ret": pred["expected_ret"],
                    "features_hash": pred["features_hash"],
                }
        if mode == "phase1":
            print(f"Active model has no bundle for {code}; phase1 mode -> failed HOLD.")
            return 0.5, False, {"model_version": version, "horizon_days": horizon}
        print(f"No saved bundle for {code}; auto mode -> legacy training fallback.")

    if mode == "phase1":
        print(f"No active model for {code}; phase1 mode -> failed HOLD.")
        return 0.5, False, {
            "model_version": active.get("version") if active else None,
            "horizon_days": horizon,
        }

    # legacy / auto-fallback: train from scratch with the configured label.
    model, prob_up = train_and_predict(
        featured, runtime_config=BACKTEST_GATE_CONFIG, label_config=label_cfg
    )
    if model is None:
        return 0.5, False, {"model_version": db.LEGACY_MODEL_VERSION, "horizon_days": horizon}
    return prob_up, True, {
        "model_version": db.LEGACY_MODEL_VERSION,
        "horizon_days": horizon,
        "raw_score": prob_up,
        "expected_ret": None,
        "features_hash": None,
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
        validation_warnings = list(dict.fromkeys(
            validation_warnings + (df.attrs.get("validation_warnings", []) or [])
        ))
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
        print(f"Data empty after feature engineering for {code}. Recording failed HOLD state.")
        close = _latest_close_or_none(code)
        return (
            _failure_signal(ticker_info, "empty_features", close=close),
            _failure_backtest_entry(
                ticker_info,
                "empty_features",
                validation_warnings=validation_warnings,
            ),
        )

    # 4. KPI Gate (cost/slippage-inclusive horizon-aware OOS backtest)
    gate_result = evaluate_kpi_gate(featured, BACKTEST_GATE_CONFIG, label_config=ctx["label_cfg"])
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
        "data_validation_warnings": validation_warnings,
    }

    # 5. Predict (saved Phase 1 model or legacy fallback per TRADER_MODEL_MODE)
    prob_up, model_ready, phase1_fields = _predict_for_ticker(featured, ticker_info, ctx)
    if not model_ready:
        print(f"Model inference unavailable for {code}. Falling back to neutral probability.")
        prob_up = 0.5

    print(f"Prediction for {code}: Up Probability = {prob_up:.2%}")
    thresholds = gate_result.get("thresholds")

    # 6. Generate Signal
    signal = generate_signal(featured, prob_up, ticker_info, thresholds=thresholds)
    signal["thresholds"] = thresholds
    signal["threshold_optimization"] = gate_result.get("threshold_optimization")
    signal["status"] = "ok"
    # Phase 1 prediction provenance (flows into predictions table).
    signal["model_version"] = phase1_fields.get("model_version")
    signal["horizon_days"] = phase1_fields.get("horizon_days")
    signal["raw_score"] = phase1_fields.get("raw_score")
    signal["expected_ret"] = phase1_fields.get("expected_ret")
    signal["features_hash"] = phase1_fields.get("features_hash")
    signal = _attach_confidence_fields(signal, gate_result, model_ready=model_ready)

    if not signal["gate_passed"]:
        print(f"Actionable signal blocked for {code}: {signal.get('confidence_reason', 'gate failed')}")

    # 7. Notify
    if signal["gate_passed"]:
        send_notification(signal)

    return signal, backtest_entry


def main():
    print("Starting daily stock prediction job...")

    active_codes = [ticker_info["code"] for ticker_info in TICKERS]
    print(f"Configured tickers: {', '.join(active_codes) if active_codes else '(none)'}")

    # Keep data directory aligned with active tickers in tickers.yml.
    # Inactive parquet files are archived, not deleted; failure here should not
    # stop active ticker processing.
    try:
        sync_data_files(active_codes)
    except Exception as e:
        print(f"Failed to archive inactive data files. Continuing daily run: {type(e).__name__}: {e}")

    # Phase 1 inference context: model mode, label config, macro panel, and the
    # active saved model (read once for the whole run).
    model_cfg = get_model_runtime_config()
    label_cfg = _label_config_for_mode(model_cfg)
    macro_panel = macro.load_macro_panel()
    active = None
    if model_cfg["model_mode"] in ("auto", "phase1"):
        active = model_store.read_active_model()
        if active and not _active_model_compatible(active, model_cfg):
            print(
                "Active model macro-feature setting is incompatible with "
                f"TRADER_MACRO_FEATURES_ENABLED={model_cfg['macro_features_enabled']}; "
                "saved-model inference disabled for this run."
            )
            active = None
    mode = model_cfg["model_mode"]
    active_label = active.get("version") if active else "none"
    print(f"Model mode: {mode}; active model: {active_label}; "
          f"macro panel: {'loaded' if macro_panel is not None else 'absent'}")
    ctx = {
        "model_cfg": model_cfg,
        "label_cfg": label_cfg,
        "macro_panel": macro_panel,
        "active": active,
    }

    signals = []
    backtest_entries = []

    for ticker_info in TICKERS:
        try:
            signal, backtest_entry = _process_ticker(ticker_info, ctx)
        except Exception as e:
            code = ticker_info["code"]
            error = f"{type(e).__name__}: {e}"
            print(f"Failed to process {code}. Recording failed HOLD state. {error}")
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

    # Phase 0: write-through predictions/signals to the measurement DB.
    # Never let DB issues break the daily run (notification + dashboard).
    try:
        db_result = db.record_run(signals, _run_date_jst())
        print(f"DB record_run: {db_result}")
    except Exception as e:  # defensive: record_run itself should not raise
        print(f"DB record_run unexpected error (ignored): {type(e).__name__}: {e}")

    report_path = write_backtest_report(backtest_entries)
    print(f"Backtest KPI report exported to {report_path}")

    # 8. Update Dashboard (always run to keep frontend data in sync with tickers.yml)
    update_dashboard(signals)
    if not signals:
        print("No signals generated. Dashboard data was still refreshed.")

    print("\nDaily job completed.")


if __name__ == "__main__":
    main()
