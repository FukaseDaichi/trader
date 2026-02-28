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

    enabled_tickers = [t for t in config.get("tickers", []) if t.get("enabled", True)]

    settings = config.get("settings", {}) or {}
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
    }

TICKERS = load_tickers()
LINE_CONFIG = get_line_config()
BACKTEST_GATE_CONFIG = get_backtest_gate_config()
