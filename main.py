from src.config import TICKERS, BACKTEST_GATE_CONFIG
from src.data_loader import update_data, load_data, sync_data_files
from src.model import add_features, train_and_predict
from src.predictor import generate_signal
from src.notifier import send_notification
from src.dashboard import update_dashboard
from src.backtest import evaluate_kpi_gate, format_gate_summary, write_backtest_report


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


def main():
    print("Starting daily stock prediction job...")

    active_codes = [ticker_info["code"] for ticker_info in TICKERS]
    print(f"Configured tickers: {', '.join(active_codes) if active_codes else '(none)'}")

    # Keep data directory aligned with active tickers in tickers.yml.
    sync_data_files(active_codes)

    signals = []
    backtest_entries = []

    for ticker_info in TICKERS:
        code = ticker_info['code']
        print(f"\nProcessing {code} ({ticker_info['name']})...")

        # 1. Update Data
        # In B-unyo, we run at 06:00 JST, so we should have data up to yesterday.
        # Stooq usually updates around midnight UTC or later?
        # Actually Stooq data for JP market closes at 15:00 JST, available shortly after.
        # 06:00 JST next day is safe.
        update_data(code)

        # 2. Load Data
        df = load_data(code)
        if df is None or len(df) < 60:  # Need 60 for MA60
            print(f"Insufficient data for {code}. Skipping.")
            continue

        # 3. Feature Engineering
        df = add_features(df)
        if df.empty:
            print(f"Data empty after feature engineering for {code}. Skipping.")
            continue

        # 4. KPI Gate (cost/slippage-inclusive OOS backtest)
        gate_result = evaluate_kpi_gate(df, BACKTEST_GATE_CONFIG)
        gate_summary = format_gate_summary(gate_result)
        gate_status = "PASS" if gate_result["passed"] else "FAIL"
        print(f"KPI gate {gate_status} for {code}: {gate_summary}")

        backtest_entries.append({
            "ticker": code,
            "name": ticker_info["name"],
            "passed": gate_result["passed"],
            "reason": gate_result["reason"],
            "failures": gate_result["failures"],
            "metrics": gate_result["metrics"],
            "metrics_tuning": gate_result.get("metrics_tuning"),
            "metrics_holdout": gate_result.get("metrics_holdout"),
            "thresholds": gate_result.get("thresholds"),
            "threshold_optimization": gate_result.get("threshold_optimization"),
        })

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
        signal = _attach_confidence_fields(signal, gate_result, model_ready=model_ready)

        if not signal["gate_passed"]:
            print(f"Actionable signal blocked for {code}: {signal.get('confidence_reason', 'gate failed')}")

        signals.append(signal)

        # 7. Notify
        if signal["gate_passed"]:
            send_notification(signal)

    report_path = write_backtest_report(backtest_entries)
    print(f"Backtest KPI report exported to {report_path}")

    # 8. Update Dashboard (always run to keep frontend data in sync with tickers.yml)
    update_dashboard(signals)
    if not signals:
        print("No signals generated. Dashboard data was still refreshed.")

    print("\nDaily job completed.")


if __name__ == "__main__":
    main()
