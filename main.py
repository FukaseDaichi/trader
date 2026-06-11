import os
from datetime import UTC, datetime, timedelta

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
from src.model import build_feature_frame, train_and_predict
from src.predictor import generate_signal
from src.notifier import send_notification, send_line_text
from src.dashboard import update_dashboard
from src.backtest import evaluate_kpi_gate, format_gate_summary, write_backtest_report
from src import db, macro, model_store, phase1, cs_model, db_records, portfolio, dashboard, digest


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _run_date_jst() -> str:
    override = os.environ.get("RUN_DATE_JST", "").strip()
    if override:
        try:
            datetime.strptime(override, "%Y-%m-%d")
            return override
        except ValueError:
            print(f"Invalid RUN_DATE_JST={override!r}; using current JST date.")
    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y-%m-%d")


def _now_jst_str() -> str:
    """JST wall-clock timestamp string for dashboard ``generated_at`` stamps."""
    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


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
        print("Phase 2: no active CS model pointer found; falling back to Phase 1 only.")
        return {"status": "fallback", "reason": "no_active_cs_model", "mode": portfolio_mode}

    version = active_cs.get("version", "")
    bundle = model_store.load_cs_bundle(version)
    if bundle is None:
        print(f"Phase 2: CS bundle load failed for version={version!r}; falling back.")
        return {"status": "fallback", "reason": "bundle_load_failed", "mode": portfolio_mode,
                "model_version": version}

    # --- Universe size check ---
    universe = TICKERS
    min_universe = int(cs_cfg.get("min_universe", 30))
    if len(universe) < min_universe:
        print(f"Phase 2: universe size {len(universe)} < min_universe {min_universe}; falling back.")
        return {"status": "fallback", "reason": "insufficient_universe", "mode": portfolio_mode,
                "model_version": version}

    # --- Load OHLCV data for all enabled tickers (best-effort) ---
    tickers_data = []
    for ticker_info in universe:
        try:
            df = load_data(ticker_info["code"])
        except Exception as e:  # noqa: BLE001
            print(f"Phase 2: load_data failed for {ticker_info['code']}: {type(e).__name__}: {e}")
            df = None
        if df is not None and not df.empty:
            tickers_data.append((ticker_info, df))

    if len(tickers_data) < min_universe:
        print(f"Phase 2: only {len(tickers_data)} tickers with usable data "
              f"(need {min_universe}); falling back.")
        return {"status": "fallback", "reason": "insufficient_usable_data", "mode": portfolio_mode,
                "model_version": version}

    # --- Macro features flag (honour bundle's training setting) ---
    macro_enabled = bool(
        active_cs.get(
            "macro_features_enabled",
            (model_cfg or {}).get("macro_features_enabled", True),
        )
    )

    # --- Inference ---
    horizon_days = int(active_cs.get("horizon_days", cs_cfg.get("label_horizon_days", 5)))
    pred_df, as_of = cs_model.infer_cross_section(
        tickers_data,
        macro_panel,
        bundle,
        macro_enabled=macro_enabled,
        label_horizon_days=horizon_days,
    )

    if pred_df is None or pred_df.empty:
        print("Phase 2: infer_cross_section returned empty predictions; falling back.")
        return {"status": "fallback", "reason": "empty_predictions", "mode": portfolio_mode,
                "model_version": version}

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
                    for p in snap["positions"] if p.get("ticker")
                }
        except Exception as e:  # noqa: BLE001
            print(f"Phase 2: prev-weights DB read failed (ignored): {type(e).__name__}: {e}")

    # JSON fallback (docs/portfolio_latest.json).
    try:
        from src.dashboard import PORTFOLIO_LATEST_FILE
        if PORTFOLIO_LATEST_FILE.exists():
            import json
            data = json.loads(PORTFOLIO_LATEST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("available") and data.get("positions"):
                return {
                    p["ticker"]: float(p.get("target_weight") or 0.0)
                    for p in data["positions"] if p.get("ticker")
                }
    except Exception as e:  # noqa: BLE001
        print(f"Phase 2: prev-weights JSON read failed (ignored): {type(e).__name__}: {e}")

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
        phase2["predictions"], price_frames, prev_weights, cfg,
        sectors=sectors, names=names, closes=closes, regime=regime,
        run_date=run_date, as_of_date=phase2.get("as_of_date"),
        model_version=phase2.get("model_version"), mode=phase2.get("mode", "shadow"),
    )

    dashboard.export_portfolio_latest(
        snapshot, run_date=run_date, generated_at=_now_jst_str(),
    )
    db_result = db.record_portfolio_snapshot(snapshot, run_date)
    print(f"Phase 2 portfolio DB write: {db_result}")

    diff = snapshot.get("diff_summary")
    print(f"Phase 2 portfolio snapshot: mode={snapshot.get('mode')} "
          f"status={snapshot.get('status')} gross={snapshot.get('gross_exposure')} "
          f"positions={len(snapshot.get('positions') or [])} diff={diff}")
    return snapshot


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

    run_date = _run_date_jst()

    # Phase 2: cross-sectional inference + portfolio snapshot. Never breaks Phase 1.
    snapshot = None
    try:
        phase2 = run_phase2_inference(macro_panel, model_cfg, run_date)
        if phase2 is None:
            pass  # Phase 2 disabled -> leave docs/portfolio_latest.json untouched.
        else:
            print(f"Phase 2 inference: {phase2.get('status')} "
                  f"(mode={phase2.get('mode')}, model={phase2.get('model_version')})")
            if phase2.get("status") == "ok":
                snapshot = _run_portfolio_snapshot(phase2, run_date)
            elif phase2.get("status") == "fallback":
                dashboard.export_portfolio_latest(
                    None, reason=phase2.get("reason", "fallback"),
                    run_date=run_date, generated_at=_now_jst_str(),
                )
    except Exception as e:  # noqa: BLE001
        print(f"Phase 2 inference skipped (ignored): {type(e).__name__}: {e}")

    # Phase 3: reflect active-mode target weights into signals. No-op in shadow /
    # gate-fail / no-snapshot, so shadow behavior is byte-for-byte unchanged.
    try:
        signals = portfolio.merge_target_weights(
            signals, snapshot, gate_passed=portfolio.read_portfolio_gate()
        )
    except Exception as e:  # noqa: BLE001
        print(f"merge_target_weights skipped (ignored): {type(e).__name__}: {e}")

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
                    print(f"Notification failed for {signal.get('ticker')} "
                          f"(ignored): {type(e).__name__}: {e}")
    # Task 5: daily morning digest (best-effort, never breaks the run).
    if _env_bool("TRADER_NOTIFY_DIGEST_ENABLED", True):
        try:
            from src.dashboard import PORTFOLIO_LATEST_FILE, PERFORMANCE_FILE
            portfolio_payload = snapshot if snapshot else _read_json_file(PORTFOLIO_LATEST_FILE)
            performance_payload = _read_json_file(PERFORMANCE_FILE)
            macro_regime = _build_macro_regime(macro_panel)
            text = digest.build_daily_digest(
                run_date, portfolio_payload, performance_payload,
                macro_regime, signals, LINE_CONFIG.get("dashboard_url", ""))
            send_line_text(text)
        except Exception as e:  # noqa: BLE001
            print(f"Digest notification failed (ignored): {type(e).__name__}: {e}")

    # Phase 0: write-through to the measurement DB AFTER the active-weight merge so
    # signals.target_weight lands. Never breaks the run.
    try:
        db_result = db.record_run(signals, run_date)
        print(f"DB record_run: {db_result}")
    except Exception as e:  # defensive: record_run itself should not raise
        print(f"DB record_run unexpected error (ignored): {type(e).__name__}: {e}")

    report_path = write_backtest_report(backtest_entries)
    print(f"Backtest KPI report exported to {report_path}")

    # Update Dashboard (always run to keep frontend data in sync with tickers.yml)
    update_dashboard(signals)
    if not signals:
        print("No signals generated. Dashboard data was still refreshed.")

    print("\nDaily job completed.")


if __name__ == "__main__":
    main()
