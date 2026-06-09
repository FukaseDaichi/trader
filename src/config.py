import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"
TICKERS_FILE = BASE_DIR / "tickers.yml"
STATE_FILE = DOCS_DIR / "state.json"

# Load .env file (no-op if the file doesn't exist)
load_dotenv(BASE_DIR / ".env")

# Create directories if they don't exist
DATA_DIR.mkdir(exist_ok=True)
DOCS_DIR.mkdir(exist_ok=True)

def load_tickers():
    if not TICKERS_FILE.exists():
        raise FileNotFoundError(f"Tickers configuration file not found at {TICKERS_FILE}")

    with open(TICKERS_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if not isinstance(config, dict):
        raise ValueError("tickers.yml must contain a YAML mapping")

    raw_tickers = config.get("tickers", [])
    if raw_tickers is None:
        raw_tickers = []
    if not isinstance(raw_tickers, list):
        raise ValueError("tickers.yml field 'tickers' must be a list")

    enabled_tickers = []
    seen_codes = set()
    for idx, ticker in enumerate(raw_tickers):
        label = f"tickers[{idx}]"
        if not isinstance(ticker, dict):
            raise ValueError(f"{label} must be a mapping")

        code = ticker.get("code")
        name = ticker.get("name")
        enabled = ticker.get("enabled", True)

        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{label}.code must be a non-empty string")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label}.name must be a non-empty string")
        if not isinstance(enabled, bool):
            raise ValueError(f"{label}.enabled must be a boolean when specified")

        normalized = {
            **ticker,
            "code": code.strip(),
            "name": name.strip(),
            "enabled": enabled,
        }

        if normalized["code"] in seen_codes:
            raise ValueError(f"Duplicate ticker code in tickers.yml: {normalized['code']}")
        seen_codes.add(normalized["code"])

        if enabled:
            enabled_tickers.append(normalized)

    settings = config.get("settings", {}) or {}
    if not isinstance(settings, dict):
        raise ValueError("tickers.yml field 'settings' must be a mapping when specified")

    max_tickers = settings.get("max_tickers")

    # max_tickers: null or omitted means "no upper limit".
    if max_tickers is None:
        return enabled_tickers

    try:
        max_tickers = int(max_tickers)
    except (TypeError, ValueError) as e:
        raise ValueError("settings.max_tickers must be an integer or null") from e

    if max_tickers < 1:
        raise ValueError("settings.max_tickers must be >= 1 or null")

    return enabled_tickers[:max_tickers]

def get_line_config():
    return {
        "channel_access_token": os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"),
        "user_id": os.environ.get("LINE_USER_ID"),
        "dashboard_url": os.environ.get("TRADER_DASHBOARD_URL", "https://fukasedaichi.github.io/trader/").strip()
    }


def _get_env_float(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be a float value") from e


def _get_env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return int(default)
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer value") from e


def _get_env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return bool(default)
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value (true/false)")


def _get_env_choice(name, default, choices):
    raw = os.environ.get(name)
    value = (raw if raw not in (None, "") else default).strip().lower()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ValueError(f"{name} must be one of: {allowed}")
    return value


def get_backtest_gate_config():
    """
    Runtime config for KPI gate. Defaults are conservative and can be tuned by env vars.
    """
    return {
        "enabled": _get_env_bool("TRADER_KPI_GATE_ENABLED", True),
        "validation_years": _get_env_int("TRADER_BT_VALIDATION_YEARS", 4),
        "val_size": _get_env_int("TRADER_BT_VAL_SIZE", 60),
        "purge_gap": _get_env_int("TRADER_BT_PURGE_GAP", 5),
        "n_folds": _get_env_int("TRADER_BT_FOLDS", 3),
        "train_min_rows": _get_env_int("TRADER_BT_MIN_TRAIN_ROWS", 200),
        "cost_bps": _get_env_float("TRADER_BT_COST_BPS", 10.0),
        "slippage_bps": _get_env_float("TRADER_BT_SLIPPAGE_BPS", 5.0),
        "allow_short": _get_env_bool("TRADER_BT_ALLOW_SHORT", False),
        "min_cagr": _get_env_float("TRADER_KPI_MIN_CAGR", 0.03),
        "min_expectancy": _get_env_float("TRADER_KPI_MIN_EXPECTANCY", 0.0001),
        "max_drawdown": _get_env_float("TRADER_KPI_MAX_DRAWDOWN", 0.25),
        "min_sharpe": _get_env_float("TRADER_KPI_MIN_SHARPE", 0.20),
        "min_trades": _get_env_int("TRADER_KPI_MIN_TRADES", 10),
        "auto_threshold_enabled": _get_env_bool("TRADER_AUTO_THRESHOLD_ENABLED", True),
        "auto_threshold_min_trades": _get_env_int("TRADER_AUTO_THRESHOLD_MIN_TRADES", 8),
        "auto_threshold_objective": _get_env_choice(
            "TRADER_AUTO_THRESHOLD_OBJECTIVE",
            "expectancy",
            {"expectancy", "cagr", "sharpe", "net_return"},
        ),
        "auto_threshold_min_gap": _get_env_float("TRADER_AUTO_THRESHOLD_MIN_GAP", 0.05),
    }


def _get_env_str(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return str(default)
    return str(raw)


def get_label_config():
    """
    Phase 1 label / target configuration (roadmap §6.1).

    `binary_1d` reproduces the legacy next-day binary target for rollback and
    A/B comparison; `triple_barrier` (default) and `vol_norm` are the Phase 1
    horizon-aware targets.
    """
    return {
        "label_mode": _get_env_choice(
            "TRADER_LABEL_MODE",
            "triple_barrier",
            {"triple_barrier", "vol_norm", "binary_1d"},
        ),
        "horizon_days": max(1, _get_env_int("TRADER_TARGET_HORIZON_DAYS", 5)),
        "tb_tp_atr": max(0.0, _get_env_float("TRADER_TB_TP_ATR", 1.5)),
        "tb_sl_atr": max(0.0, _get_env_float("TRADER_TB_SL_ATR", 1.0)),
        "tb_max_days": max(1, _get_env_int("TRADER_TB_MAX_DAYS", 5)),
        "vol_col": "volatility",
    }


def get_model_runtime_config():
    """Phase 1 model-mode / calibration / artifact configuration."""
    default_model_dir = str(DATA_DIR / "models")
    return {
        "model_mode": _get_env_choice(
            "TRADER_MODEL_MODE", "auto", {"auto", "legacy", "phase1"}
        ),
        "calibration_mode": _get_env_choice(
            "TRADER_CALIBRATION_MODE", "isotonic", {"isotonic", "none"}
        ),
        "macro_features_enabled": _get_env_bool("TRADER_MACRO_FEATURES_ENABLED", True),
        "model_dir": _get_env_str("TRADER_MODEL_DIR", default_model_dir),
        "active_model_file": _get_env_str(
            "TRADER_MODEL_ACTIVE_FILE", str(DATA_DIR / "models" / "active_model.json")
        ),
        "min_calibration_rows": max(10, _get_env_int("TRADER_MIN_CALIBRATION_ROWS", 60)),
    }


def get_cross_section_config():
    """Phase 2 cross-sectional model / universe configuration."""
    return {
        "objective": _get_env_choice("TRADER_CS_OBJECTIVE", "ranker", {"ranker", "regression"}),
        "active_model_file": _get_env_str("TRADER_CS_MODEL_ACTIVE_FILE", str(DATA_DIR / "models" / "active_cs_model.json")),
        "min_universe": _get_env_int("TRADER_CS_MIN_UNIVERSE", 30),
        "top_n": _get_env_int("TRADER_CS_TOP_N", 8),
        "label_horizon_days": max(1, _get_env_int("TRADER_CS_LABEL_HORIZON_DAYS", 5)),
        "min_daily_names": _get_env_int("TRADER_CS_MIN_DAILY_NAMES", 20),
        "panel_lookback_years": max(1, _get_env_int("TRADER_CS_PANEL_LOOKBACK_YEARS", 5)),
        "universe_target_size": _get_env_int("TRADER_UNIVERSE_TARGET_SIZE", 40),
    }


def get_portfolio_config():
    """Phase 2 long-only portfolio construction / KPI-gate configuration."""
    return {
        "enabled": _get_env_bool("TRADER_PORTFOLIO_ENABLED", False),
        "mode": _get_env_choice("TRADER_PORTFOLIO_MODE", "shadow", {"shadow", "active"}),
        "target_vol": _get_env_float("TRADER_PORTFOLIO_TARGET_VOL", 0.12),
        "max_name_weight": _get_env_float("TRADER_PORTFOLIO_MAX_NAME_WEIGHT", 0.20),
        "sector_cap": _get_env_float("TRADER_PORTFOLIO_SECTOR_CAP", 0.40),
        "max_gross": _get_env_float("TRADER_PORTFOLIO_MAX_GROSS", 1.00),
        "min_weight": _get_env_float("TRADER_PORTFOLIO_MIN_WEIGHT", 0.03),
        "notrade_band": _get_env_float("TRADER_PORTFOLIO_NOTRADE_BAND", 0.02),
        "min_expected_ret": _get_env_float("TRADER_PORTFOLIO_MIN_EXPECTED_RET", 0.0),
        "risk_off_gross_mult": _get_env_float("TRADER_PORTFOLIO_RISK_OFF_GROSS_MULT", 0.50),
        "cov_lookback_days": _get_env_int("TRADER_PORTFOLIO_COV_LOOKBACK_DAYS", 60),
        "backtest_min_sharpe": _get_env_float("TRADER_PORTFOLIO_BACKTEST_MIN_SHARPE", 0.30),
        "backtest_max_dd": _get_env_float("TRADER_PORTFOLIO_BACKTEST_MAX_DD", 0.25),
        "backtest_min_ir": _get_env_float("TRADER_PORTFOLIO_BACKTEST_MIN_IR", 0.00),
        "backtest_max_turnover": _get_env_float("TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER", 0.40),
    }


TICKERS = load_tickers()
LINE_CONFIG = get_line_config()
BACKTEST_GATE_CONFIG = get_backtest_gate_config()
LABEL_CONFIG = get_label_config()
MODEL_RUNTIME_CONFIG = get_model_runtime_config()
