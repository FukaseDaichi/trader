import os
from datetime import UTC, datetime, timedelta

from src.config import TICKERS, BACKTEST_GATE_CONFIG
from src.data_loader import update_data, load_data, sync_data_files
from src.model import add_features, train_and_predict
from src.predictor import generate_signal
from src.notifier import send_notification
from src.dashboard import update_dashboard
from src.backtest import evaluate_kpi_gate, format_gate_summary, write_backtest_report
from src import db


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


def _process_ticker(ticker_info):
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

    # 3. Feature Engineering
    df = add_features(df)
    if df.empty:
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

    # 4. KPI Gate (cost/slippage-inclusive OOS backtest)
    gate_result = evaluate_kpi_gate(df, BACKTEST_GATE_CONFIG)
    gate_summary = format_gate_summary(gate_result)
    gate_status = "PASS" if gate_result["passed"] else "FAIL"
    print(f"KPI gate {gate_status} for {code}: {gate_summary}")

    backtest_entry = {
        "ticker": code,
        "name": ticker_info["name"],
        "status": "ok",
        "passed": gate_result["passed"],
        "reason": gate_result["reason"],
        "failures": gate_result["failures"],
        "metrics": gate_result["metrics"],
        "metrics_tuning": gate_result.get("metrics_tuning"),
        "metrics_holdout": gate_result.get("metrics_holdout"),
        "thresholds": gate_result.get("thresholds"),
        "threshold_optimization": gate_result.get("threshold_optimization"),
        "data_validation_warnings": validation_warnings,
    }

    # 5. Train & Predict (always run so dashboard can show raw probability)
    model, prob_up = train_and_predict(df, runtime_config=BACKTEST_GATE_CONFIG)
    model_ready = model is not None
    if not model_ready:
        print(f"Model training failed for {code}. Falling back to neutral probability.")
        prob_up = 0.5

    print(f"Prediction for {code}: Up Probability = {prob_up:.2%}")
    thresholds = gate_result.get("thresholds")

    # 6. Generate Signal
    signal = generate_signal(df, prob_up, ticker_info, thresholds=thresholds)
    signal["thresholds"] = thresholds
    signal["threshold_optimization"] = gate_result.get("threshold_optimization")
    signal["status"] = "ok"
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

    signals = []
    backtest_entries = []

    for ticker_info in TICKERS:
        try:
            signal, backtest_entry = _process_ticker(ticker_info)
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
