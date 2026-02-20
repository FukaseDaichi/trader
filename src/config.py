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

TICKERS = load_tickers()
LINE_CONFIG = get_line_config()
