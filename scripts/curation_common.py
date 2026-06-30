#!/usr/bin/env python3
"""
Shared helpers for the AI ticker curation system.

See specification_document/ai_ticker_curation/ for the full design.
This module centralizes paths, settings loading, and tickers.yml I/O so that
the deterministic merge, warmup, technical screen, guard, and notifier scripts
stay consistent.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Reuse canonical paths from src.config (also creates data/ and docs/).
from src.config import DATA_DIR, DOCS_DIR, TICKERS_FILE  # noqa: E402

CURATION_DIR = DOCS_DIR / "curation"
REPORTS_DIR = ROOT_DIR / "reports"
WATCHLIST_DIR = DATA_DIR / "watchlist"
POOL_FILE = ROOT_DIR / "curation_pool.yml"

# Conservative, code-level defaults. tickers.yml settings.curation overrides these.
DEFAULT_CURATION_SETTINGS = {
    "enabled": True,
    "max_universe": 10,
    "max_daily_swaps": 2,
    "max_daily_adds": 2,
    "min_combined_to_promote": 70.0,
    "min_gap": 5.0,
    "keep_floor": 50.0,
    "min_warmup_rows": 200,
    "sector_cap_pct": 40.0,
    "fund_weight": 0.5,
    "tech_weight": 0.5,
    "cooldown_days": 5,
    "max_fundamental_age_days": 14,
    "report": {
        "persona_name": "あおい",
        "tone": "casual_kawaii",
    },
}


# ---------------------------------------------------------------------------
# Time helpers (JST)
# ---------------------------------------------------------------------------


def today_jst() -> date:
    return (datetime.now(UTC) + timedelta(hours=9)).date()


def today_jst_iso() -> str:
    return today_jst().strftime("%Y-%m-%d")


def now_jst_iso() -> str:
    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def parse_iso_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def read_json(path: Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_json(path: Path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# tickers.yml I/O
# ---------------------------------------------------------------------------


def load_tickers_config(path: Path | None = None) -> dict:
    path = Path(path or TICKERS_FILE)
    if not path.exists():
        return {"tickers": [], "watchlist": [], "settings": {}}
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("tickers", [])
    cfg.setdefault("watchlist", [])
    cfg.setdefault("settings", {})
    return cfg


def save_tickers_config(cfg: dict, path: Path | None = None) -> None:
    """Write tickers.yml with a stable key order, preserving UTF-8 names."""
    path = Path(path or TICKERS_FILE)
    ordered = {
        "tickers": cfg.get("tickers", []),
        "watchlist": cfg.get("watchlist", []),
        "settings": cfg.get("settings", {}),
    }
    # Drop empty watchlist to keep the file tidy when unused.
    if not ordered["watchlist"]:
        ordered.pop("watchlist")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            ordered,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def get_curation_settings(cfg: dict) -> dict:
    raw = (cfg.get("settings") or {}).get("curation") or {}
    return _deep_merge(DEFAULT_CURATION_SETTINGS, raw)


def enabled_codes(cfg: dict) -> list[str]:
    return [
        t["code"]
        for t in cfg.get("tickers", [])
        if isinstance(t, dict) and t.get("code") and t.get("enabled", True)
    ]


# ---------------------------------------------------------------------------
# Candidate pool
# ---------------------------------------------------------------------------


def load_pool(path: Path | None = None) -> list[dict]:
    path = Path(path or POOL_FILE)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    pool = data.get("pool", [])
    return [p for p in pool if isinstance(p, dict) and p.get("code")]
