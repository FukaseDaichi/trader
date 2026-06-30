#!/usr/bin/env python3
"""
Notify the weekly report's GitHub URL via LINE, in the casual girl-navigator
persona ("〜だね！"). Reuses the LINE Messaging API like src/notifier.py.

See specification_document/ai_ticker_curation/05_weekly_report.md (§4).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from curation_common import (
    REPORTS_DIR,
    get_curation_settings,
    load_tickers_config,
    today_jst_iso,
)

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import get_line_config  # noqa: E402
from src import notifier  # noqa: E402


def resolve_repo_slug() -> str:
    slug = os.environ.get("TRADER_REPO_SLUG", "").strip()
    if slug:
        return slug
    try:
        url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        url = ""
    # git@github.com:owner/repo.git  or  https://github.com/owner/repo.git
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    return "FukaseDaichi/trader"


def report_url(report_path: str, branch: str | None = None) -> str:
    slug = resolve_repo_slug()
    branch = branch or os.environ.get("TRADER_REPORT_BRANCH", "main")
    rel = report_path.lstrip("/")
    return f"https://github.com/{slug}/blob/{branch}/{rel}"


def extract_headline(report_file: Path) -> str | None:
    if not report_file.exists():
        return None
    try:
        text = report_file.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("###"):
            # "### 1. レーザーテック（6920）🔴" -> strip markdown + numbering
            cleaned = line.lstrip("#").strip()
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
            if cleaned:
                return cleaned
    return None


def build_message(persona: str, headline: str | None, url: str) -> str:
    lines = [f"{persona}だよ！今週の日本株レポートができたよ〜！📈✨"]
    if headline:
        lines.append(f"今週の注目は {headline} だね！")
    lines.append("続きはこちら👇")
    lines.append(url)
    lines.append("（投資は自己責任だよ！最後は自分で決めてね🙆‍♀️）")
    return "\n".join(lines)


def send_line(text: str) -> bool:
    cfg = get_line_config()
    token = cfg.get("channel_access_token")
    user_id = cfg.get("user_id")
    if not token or not user_id:
        print("LINE configuration missing. Skipping notification.")
        print("---- would send ----")
        print(text)
        return False
    return notifier.send_line_text(text)


def run(report_path: str, date_str: str) -> int:
    cfg = load_tickers_config()
    settings = get_curation_settings(cfg)
    persona = (settings.get("report") or {}).get("persona_name") or "あおい"

    report_file = Path(report_path)
    if not report_file.is_absolute():
        # allow passing either "reports/weekly_X.md" or just the filename
        report_file = Path(report_path)
        if not report_file.exists():
            report_file = REPORTS_DIR / Path(report_path).name

    headline = extract_headline(report_file)
    url = report_url(
        report_path if "/" in report_path else f"reports/{Path(report_path).name}"
    )
    text = build_message(persona, headline, url)
    send_line(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Notify weekly report URL via LINE")
    p.add_argument(
        "--report", required=True, help="report path, e.g. reports/weekly_2026-06-06.md"
    )
    p.add_argument("--date", default=None, help="YYYY-MM-DD JST")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return run(args.report, args.date or today_jst_iso())


if __name__ == "__main__":
    raise SystemExit(main())
