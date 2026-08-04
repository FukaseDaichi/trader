#!/usr/bin/env python3
"""
Notify curation pool changes via LINE.

Reads docs/curation/pool_decision_latest.json by default and sends a concise
best-effort summary. Missing LINE config or no pool changes are non-fatal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from curation_common import CURATION_DIR, read_json, today_jst_iso

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import get_line_config  # noqa: E402
from src import notifier  # noqa: E402


def _entry_by_code(audit: dict) -> dict[str, dict]:
    return {
        item["code"]: item
        for item in audit.get("ranking", [])
        if isinstance(item, dict) and item.get("code")
    }


def build_message(audit: dict) -> str | None:
    changes = audit.get("changes") or {}
    added = changes.get("added") or []
    dropped = changes.get("dropped") or []
    if not added and not dropped:
        return None

    entries = _entry_by_code(audit)
    date_str = audit.get("date") or today_jst_iso()
    lines = [f"候補プール更新 ({date_str})"]
    if added:
        lines.append("追加:")
        for code in added:
            e = entries.get(code, {})
            name = e.get("name") or code
            reason = e.get("reason") or "accepted"
            score = e.get("fund_score")
            score_text = f" score={score:g}" if isinstance(score, (int, float)) else ""
            lines.append(f"- {name} ({code}){score_text}: {reason}")
    if dropped:
        lines.append("除外:")
        for code in dropped:
            e = entries.get(code, {})
            name = e.get("name") or code
            reason = e.get("reason") or "dropped"
            lines.append(f"- {name} ({code}): {reason}")
    lines.append("日次キュレーションは次回以降、この新しい母集団を使うよ。")
    return "\n".join(lines)


def send_line(text: str) -> bool:
    cfg = get_line_config()
    token = cfg.get("channel_access_token")
    user_id = cfg.get("user_id")
    if not token or not user_id:
        print("LINE configuration missing. Skipping pool notification.")
        print("---- would send ----")
        print(text)
        return False
    return notifier.send_line_text(text)


def run(decision_path: Path) -> int:
    audit = read_json(decision_path)
    if not audit:
        print(f"Pool decision not found or invalid: {decision_path}")
        return 0
    text = build_message(audit)
    if not text:
        print("No pool changes; notification skipped.")
        return 0
    send_line(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Notify curation pool changes via LINE")
    p.add_argument(
        "--decision",
        type=Path,
        default=CURATION_DIR / "pool_decision_latest.json",
        help="Path to pool decision JSON",
    )
    p.add_argument(
        "--date", default=None, help="YYYY-MM-DD JST (accepted for workflow symmetry)"
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    return run(args.decision)


if __name__ == "__main__":
    raise SystemExit(main())
