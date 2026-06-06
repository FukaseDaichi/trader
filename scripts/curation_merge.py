#!/usr/bin/env python3
"""
Deterministic merge + guardrails for AI ticker curation.

Combines the technical (daily) and fundamental (weekly cache) reports into a
ranked decision, applies guardrails (churn cap, sector cap, warmup, cooldown,
fundamental freshness), and edits tickers.yml. Writes an audit log. No LLM.

This is the safety-critical core. The decision logic (compute_decision) is a
pure function over plain dicts so it can be unit-tested without the filesystem.

See specification_document/ai_ticker_curation/02_merge_guardrails.md.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import date
from pathlib import Path

from curation_common import (
    CURATION_DIR,
    DATA_DIR,
    WATCHLIST_DIR,
    enabled_codes,
    get_curation_settings,
    load_tickers_config,
    now_jst_iso,
    parse_iso_date,
    read_json,
    save_tickers_config,
    today_jst,
    today_jst_iso,
    write_json,
)

NEG_INF = float("-inf")


# ---------------------------------------------------------------------------
# Pure decision logic (unit-testable)
# ---------------------------------------------------------------------------

def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def _candidates_by_code(report) -> dict:
    if not report or not isinstance(report.get("candidates"), list):
        return {}
    out = {}
    for c in report["candidates"]:
        if isinstance(c, dict) and c.get("code"):
            out[c["code"]] = c
    return out


def _sector_increase_ok(universe: list[str], cand: str, records: dict, cap_pct: float) -> bool:
    """Allow promotion unless the candidate's own sector would exceed the cap."""
    total = len(universe)
    if total < 3:
        return True
    sector = (records.get(cand, {}).get("sector")) or "unknown"
    count = sum(1 for c in universe if (records.get(c, {}).get("sector") or "unknown") == sector)
    return (count / total) <= (cap_pct / 100.0) + 1e-9


def compute_decision(
    technical: dict | None,
    fundamental: dict | None,
    cfg: dict,
    settings: dict,
    today: date,
    rows_lookup: dict | None = None,
) -> dict:
    """
    Returns a decision dict:
      { conservative, fundamental_age_days, records, ranking, changes,
        guardrails, universe_before, universe_after, new_tickers, new_watchlist }
    """
    rows_lookup = rows_lookup or {}
    tech_map = _candidates_by_code(technical)
    fund_map = _candidates_by_code(fundamental)

    tw = float(settings["tech_weight"])
    fw = float(settings["fund_weight"])
    max_universe = int(settings["max_universe"])
    max_swaps = int(settings["max_daily_swaps"])
    max_adds = int(settings["max_daily_adds"])
    min_combined = float(settings["min_combined_to_promote"])
    min_gap = float(settings["min_gap"])
    keep_floor = float(settings["keep_floor"])
    min_rows = int(settings["min_warmup_rows"])
    sector_cap = float(settings["sector_cap_pct"])
    cooldown_days = int(settings["cooldown_days"])
    max_fund_age = int(settings["max_fundamental_age_days"])

    # --- fundamental freshness ---
    fund_as_of = parse_iso_date((fundamental or {}).get("as_of")) if fundamental else None
    fund_age_days = (today - fund_as_of).days if fund_as_of else None
    has_technical = bool(tech_map)
    conservative = (not has_technical) or (fund_age_days is None) or (fund_age_days > max_fund_age)

    enabled = enabled_codes(cfg)
    enabled_set = set(enabled)

    # name/sector resolution helpers
    cfg_meta = {t["code"]: t for t in cfg.get("tickers", []) if isinstance(t, dict) and t.get("code")}

    def resolve_name(code):
        for src in (fund_map.get(code), tech_map.get(code), cfg_meta.get(code)):
            if src and src.get("name"):
                return src["name"]
        return code

    def resolve_sector(code):
        for src in (fund_map.get(code), tech_map.get(code), cfg_meta.get(code)):
            if src and src.get("sector"):
                return src["sector"]
        return None

    # cooldown: codes disabled recently
    cooldown = set()
    for t in cfg.get("tickers", []):
        if isinstance(t, dict) and t.get("code") and not t.get("enabled", True):
            d = parse_iso_date(t.get("disabled_on"))
            if d and (today - d).days < cooldown_days:
                cooldown.add(t["code"])

    codes = set(tech_map) | set(fund_map) | enabled_set
    records: dict[str, dict] = {}
    for code in codes:
        tc = tech_map.get(code)
        fc = fund_map.get(code)
        t = _num(tc.get("score")) if tc else None
        f = _num(fc.get("score")) if fc else None
        both = t is not None and f is not None
        if both:
            combined = round(tw * t + fw * f, 2)
        elif t is not None:
            combined = round(t, 2)
        elif f is not None:
            combined = round(f, 2)
        else:
            combined = None
        rows = None
        if tc is not None and tc.get("rows_available") is not None:
            rows = int(tc["rows_available"])
        elif code in rows_lookup:
            rows = int(rows_lookup[code])
        else:
            rows = 0
        records[code] = {
            "code": code,
            "name": resolve_name(code),
            "sector": resolve_sector(code),
            "tech_score": t,
            "fund_score": f,
            "combined": combined,
            "both": both,
            "rows_available": rows,
            "warmup_ok": rows >= min_rows,
            "in_cooldown": code in cooldown,
        }

    def rank_score(code):
        r = records[code]
        if r["combined"] is not None:
            return r["combined"]
        return NEG_INF

    # --- eligible promotion candidates ---
    eligible = [
        code
        for code, r in records.items()
        if code not in enabled_set
        and r["both"]
        and r["combined"] is not None
        and r["combined"] >= min_combined
        and r["warmup_ok"]
        and not r["in_cooldown"]
    ]
    eligible.sort(key=lambda c: (-records[c]["combined"], c))

    universe = list(enabled)
    promoted_add: list[str] = []
    promoted_swap: list[str] = []
    demoted: list[str] = []

    if not conservative:
        # ADD phase: fill empty slots
        for code in list(eligible):
            if len(universe) >= max_universe or len(promoted_add) >= max_adds:
                break
            if _sector_increase_ok(universe + [code], code, records, sector_cap):
                universe.append(code)
                promoted_add.append(code)
                eligible.remove(code)

        # SWAP phase: replace clearly-worse enabled
        while len(promoted_swap) < max_swaps and eligible:
            cand = eligible[0]
            swap_candidates = [c for c in universe if c not in promoted_add and c not in promoted_swap]
            if not swap_candidates:
                break
            worst = min(swap_candidates, key=rank_score)
            worst_score = rank_score(worst)
            cand_combined = records[cand]["combined"]
            if cand_combined >= worst_score + min_gap and worst_score < keep_floor:
                new_universe = [c for c in universe if c != worst] + [cand]
                if _sector_increase_ok(new_universe, cand, records, sector_cap):
                    universe = new_universe
                    demoted.append(worst)
                    promoted_swap.append(cand)
                eligible.pop(0)
            else:
                break

    final_enabled = set(universe)

    # --- watchlist: strong-ish codes not enabled ---
    watchlist_floor = keep_floor
    watch_entries = []
    prev_watch = {w.get("code"): w for w in (cfg.get("watchlist") or []) if isinstance(w, dict) and w.get("code")}
    for code, r in records.items():
        if code in final_enabled:
            continue
        if r["combined"] is None or r["combined"] < watchlist_floor:
            continue
        ready = r["both"] and r["warmup_ok"] and r["combined"] >= min_combined and not r["in_cooldown"]
        prev = prev_watch.get(code, {})
        watch_entries.append(
            {
                "code": code,
                "name": r["name"],
                "sector": r["sector"],
                "fund_score": r["fund_score"],
                "tech_score": r["tech_score"],
                "combined": r["combined"],
                "status": "ready" if ready else "warming",
                "rows_available": r["rows_available"],
                "added_on": prev.get("added_on") or today.isoformat(),
            }
        )
    watch_entries.sort(key=lambda w: (-(w["combined"] or 0), w["code"]))
    watch_entries = watch_entries[:20]

    # --- ranking + actions for the audit log ---
    promoted = set(promoted_add) | set(promoted_swap)
    watch_codes = {w["code"] for w in watch_entries}

    def action_for(code):
        was = code in enabled_set
        now = code in final_enabled
        if was and now:
            return "keep"
        if not was and now:
            return "promote"
        if was and not now:
            return "demote"
        if code in watch_codes:
            return "watch"
        return "reject"

    ranking = []
    for code in sorted(records, key=lambda c: (-(records[c]["combined"] or NEG_INF), c)):
        r = records[code]
        ranking.append(
            {
                "code": code,
                "name": r["name"],
                "sector": r["sector"],
                "tech_score": r["tech_score"],
                "fund_score": r["fund_score"],
                "combined": r["combined"],
                "warmup_ok": r["warmup_ok"],
                "in_universe_before": code in enabled_set,
                "action": action_for(code),
            }
        )

    # --- build new tickers list ---
    new_tickers = []
    seen = set()
    for t in cfg.get("tickers", []):
        if not isinstance(t, dict) or not t.get("code"):
            continue
        code = t["code"]
        seen.add(code)
        entry = dict(t)
        r = records.get(code, {})
        if code in final_enabled:
            entry["enabled"] = True
            if code in promoted:
                # Snapshot scores only at promotion time; do not churn kept
                # entries with daily-changing scores (those live in the log).
                entry["source"] = "curation"
                entry["added_on"] = today.isoformat()
                entry.pop("disabled_on", None)
                if r.get("combined") is not None:
                    entry["combined"] = r["combined"]
                    entry["tech_score"] = r["tech_score"]
                    entry["fund_score"] = r["fund_score"]
        else:
            if t.get("enabled", True):  # newly demoted
                entry["enabled"] = False
                entry["disabled_on"] = today.isoformat()
            else:
                entry["enabled"] = False
        new_tickers.append(entry)

    # append promoted codes that had no prior entry
    for code in universe:
        if code in seen:
            continue
        r = records.get(code, {})
        new_tickers.append(
            {
                "code": code,
                "name": r.get("name", code),
                "enabled": True,
                "source": "curation",
                "added_on": today.isoformat(),
                "sector": r.get("sector"),
                "combined": r.get("combined"),
                "tech_score": r.get("tech_score"),
                "fund_score": r.get("fund_score"),
            }
        )

    changes = {
        "promoted": sorted(promoted),
        "promoted_add": sorted(promoted_add),
        "promoted_swap": sorted(promoted_swap),
        "demoted": sorted(demoted),
        "watchlist": sorted(watch_codes),
    }
    guardrails = {
        "max_universe": max_universe,
        "max_daily_swaps": max_swaps,
        "max_daily_adds": max_adds,
        "applied_swaps": len(promoted_swap),
        "applied_adds": len(promoted_add),
        "conservative_mode": conservative,
        "fundamental_age_days": fund_age_days,
        "has_technical": has_technical,
        "sector_cap_pct": sector_cap,
    }

    return {
        "conservative": conservative,
        "fundamental_age_days": fund_age_days,
        "records": records,
        "ranking": ranking,
        "changes": changes,
        "guardrails": guardrails,
        "universe_before": list(enabled),
        "universe_after": list(universe),
        "new_tickers": new_tickers,
        "new_watchlist": watch_entries,
    }


# ---------------------------------------------------------------------------
# Apply (filesystem side effects)
# ---------------------------------------------------------------------------

def _move_promoted_data(promoted: list[str]) -> list[dict]:
    moves = []
    for code in promoted:
        src = WATCHLIST_DIR / f"{code}.parquet"
        dst = DATA_DIR / f"{code}.parquet"
        if src.exists() and not dst.exists():
            try:
                shutil.move(str(src), str(dst))
                moves.append({"from": str(src), "to": str(dst)})
            except OSError as exc:
                print(f"Data move failed for {code}: {exc}")
        elif src.exists() and dst.exists():
            # already have canonical data; drop the warmup copy
            try:
                src.unlink()
            except OSError:
                pass
    return moves


def run(
    technical_path: Path,
    fundamental_path: Path | None,
    date_str: str,
    apply: bool,
) -> int:
    cfg = load_tickers_config()
    settings = get_curation_settings(cfg)

    if not settings.get("enabled", True):
        print("Curation disabled in settings.curation.enabled; no changes.")
        return 0

    technical = read_json(technical_path)
    fundamental = read_json(fundamental_path) if fundamental_path else None
    if fundamental is None:
        # fall back to the cached latest if a path was not provided / missing
        fundamental = read_json(CURATION_DIR / "fundamental_latest.json")

    today = parse_iso_date(date_str) or today_jst()

    decision = compute_decision(technical, fundamental, cfg, settings, today)

    data_moves = []
    promoted = decision["changes"]["promoted"]

    # Only rewrite tickers.yml when the universe or watchlist actually changes,
    # so conservative days keep the file (and its comments) byte-stable.
    universe_changed = set(decision["universe_before"]) != set(decision["universe_after"])
    before_watch = {
        (w.get("code"), w.get("status"))
        for w in (cfg.get("watchlist") or [])
        if isinstance(w, dict)
    }
    after_watch = {(w["code"], w["status"]) for w in decision["new_watchlist"]}
    tickers_changed = universe_changed or (before_watch != after_watch)
    tickers_written = False

    if apply and promoted:
        data_moves = _move_promoted_data(promoted)

    if apply and tickers_changed:
        new_cfg = dict(cfg)
        new_cfg["tickers"] = decision["new_tickers"]
        if decision["new_watchlist"]:
            new_cfg["watchlist"] = decision["new_watchlist"]
        else:
            new_cfg.pop("watchlist", None)
        save_tickers_config(new_cfg)
        tickers_written = True

    audit = {
        "schema_version": 1,
        "date": date_str,
        "as_of": date_str,
        "applied": bool(apply),
        "tickers_written": tickers_written,
        "inputs": {
            "technical": str(technical_path),
            "fundamental": str(fundamental_path) if fundamental_path else "fundamental_latest.json",
        },
        "weights": {"tech": settings["tech_weight"], "fund": settings["fund_weight"]},
        "fundamental_age_days": decision["fundamental_age_days"],
        "conservative_mode": decision["conservative"],
        "ranking": decision["ranking"],
        "changes": decision["changes"],
        "guardrails": decision["guardrails"],
        "data_moves": data_moves,
        "universe_before": decision["universe_before"],
        "universe_after": decision["universe_after"],
        "generated_at": now_jst_iso(),
    }
    write_json(CURATION_DIR / "decision_latest.json", audit)
    write_json(CURATION_DIR / f"decision_{date_str}.json", audit)

    mode = "APPLIED" if apply else "DRY-RUN"
    c = decision["changes"]
    print(
        f"[{mode}] {date_str} conservative={decision['conservative']} "
        f"adds={c['promoted_add']} swaps={c['promoted_swap']} demoted={c['demoted']}"
    )
    print(f"universe: {decision['universe_before']} -> {decision['universe_after']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deterministic merge for AI ticker curation")
    p.add_argument("--technical", default=None, help="path to technical_latest.json")
    p.add_argument("--fundamental", default=None, help="path to fundamental_latest.json (optional)")
    p.add_argument("--date", default=None, help="YYYY-MM-DD JST (default: today JST)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="apply changes to tickers.yml")
    g.add_argument("--dry-run", action="store_true", help="compute decision only (default)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    technical_path = Path(args.technical) if args.technical else (CURATION_DIR / "technical_latest.json")
    fundamental_path = Path(args.fundamental) if args.fundamental else None
    date_str = args.date or today_jst_iso()
    apply = bool(args.apply) and not args.dry_run
    return run(technical_path, fundamental_path, date_str, apply)


if __name__ == "__main__":
    raise SystemExit(main())
