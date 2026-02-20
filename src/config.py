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
        config = yaml.safe_load(f)

    return [t for t in config.get("tickers", []) if t.get("enabled", True)][:config.get("settings", {}).get("max_tickers", 3)]

def get_line_config():
    return {
        "channel_access_token": os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"),
        "user_id": os.environ.get("LINE_USER_ID")
    }

TICKERS = load_tickers()
LINE_CONFIG = get_line_config()
