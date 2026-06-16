#!/usr/bin/env python3
"""
Deterministic merge + guardrails for AI-assisted curation pool refresh.

The pool-screen agent writes proposal JSON only. This script is the sole writer
of curation_pool.yml, applies deterministic guardrails, writes an audit log, and
cleans stale warmup parquets from data/watchlist/.

See specification_document/ai_ticker_curation/07_pool_refresh.md.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from curation_common import (  # noqa: E402
    CURATION_DIR,
    POOL_FILE,
    WATCHLIST_DIR,
    enabled_codes,
    load_pool,
    load_tickers_config,
    now_jst_iso,
    parse_iso_date,
    read_json,
    today_jst,
    today_jst_iso,
    write_json,
)
from src.config import DATA_DIR  # noqa: E402


DEFAULT_POOL_SETTINGS = {
    "enabled": True,
    "pool_target_size": 60,
    "pool_max_size": 80,
    "cadence_days": 14,
    "max_adds_per_run": 3,
    "max_drops_per_run": 0,
    "min_fund_score_to_add": 70.0,
    # Conservative large-cap floor. Can be tuned from tickers.yml
    # settings.curation.pool.liquidity_floor_jpy.
    "liquidity_floor_jpy": 1_000_000_000.0,
    "pool_sector_cap_pct": 40.0,
    "pool_cooldown_days": 30,
}


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict | None) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def get_pool_settings(cfg: dict) -> dict:
    raw = ((cfg.get("settings") or {}).get("curation") or {}).get("pool") or {}
    return _deep_merge(DEFAULT_POOL_SETTINGS, raw)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _proposal_candidates(proposal: dict | None) -> dict[str, dict]:
    if not proposal or not isinstance(proposal.get("candidates"), list):
        return {}
    out = {}
    for c in proposal["candidates"]:
        if not isinstance(c, dict) or not c.get("code"):
            continue
        out[str(c["code"])] = c
    return out


def _valid_proposal(proposal: dict | None) -> bool:
    return bool(proposal) and isinstance(proposal.get("candidates"), list)


def _cfg_watchlist_codes(cfg: dict) -> set[str]:
    return {
        w["code"]
        for w in (cfg.get("watchlist") or [])
        if isinstance(w, dict) and w.get("code")
    }


def _sector_cap_ok(pool_after: list[dict], candidate: dict, cap_pct: float) -> bool:
    total = len(pool_after)
    if total < 3:
        return True
    sector = candidate.get("sector") or "unknown"
    count = sum(1 for p in pool_after if (p.get("sector") or "unknown") == sector)
    return (count / total) <= (cap_pct / 100.0) + 1e-9


def _candidate_identity(code: str, proposal_item: dict | None, pool_item: dict | None) -> dict:
    proposal_item = proposal_item or {}
    pool_item = pool_item or {}
    return {
        "code": code,
        "name": proposal_item.get("name") or pool_item.get("name") or code,
        "sector": proposal_item.get("sector") or pool_item.get("sector"),
    }


def _rank_add(record: dict) -> tuple:
    return (
        -(record.get("fund_score") or -1.0),
        -(record.get("liquidity_jpy") or -1.0),
        record["code"],
    )


def _rank_drop(record: dict) -> tuple:
    hint_rank = 0 if record.get("action_hint") == "drop" else 1
    score = record.get("fund_score")
    liquidity = record.get("liquidity_jpy")
    return (
        hint_rank,
        score if score is not None else 101.0,
        liquidity if liquidity is not None else float("inf"),
        record["code"],
    )


# ---------------------------------------------------------------------------
# Pure decision logic (unit-testable)
# ---------------------------------------------------------------------------

def compute_pool_decision(
    proposal: dict | None,
    current_pool: list[dict],
    cfg: dict,
    settings: dict,
    today: date,
    liquidity_lookup: dict[str, float | None] | None = None,
    cooldown_codes: set[str] | None = None,
) -> dict:
    """Return the deterministic pool decision without filesystem side effects."""
    liquidity_lookup = liquidity_lookup or {}
    cooldown_codes = cooldown_codes or set()
    pmap = _proposal_candidates(proposal)
    pool_by_code = {
        p["code"]: p
        for p in current_pool
        if isinstance(p, dict) and p.get("code")
    }
    pool_codes = list(pool_by_code)
    enabled_set = set(enabled_codes(cfg))

    target_size = int(settings["pool_target_size"])
    max_size = int(settings["pool_max_size"])
    max_adds = int(settings["max_adds_per_run"])
    max_drops = int(settings["max_drops_per_run"])
    min_score = float(settings["min_fund_score_to_add"])
    liquidity_floor = float(settings["liquidity_floor_jpy"])
    sector_cap = float(settings["pool_sector_cap_pct"])

    mode = "grow" if len(pool_codes) < target_size else "replace"

    all_codes = set(pool_codes) | set(pmap)
    records: dict[str, dict] = {}
    for code in sorted(all_codes):
        proposal_item = pmap.get(code)
        pool_item = pool_by_code.get(code)
        identity = _candidate_identity(code, proposal_item, pool_item)
        liquidity = liquidity_lookup.get(code)
        records[code] = {
            **identity,
            "fund_score": _num((proposal_item or {}).get("fund_score")),
            "liquidity_jpy": liquidity,
            "proposal_liquidity_jpy": _num((proposal_item or {}).get("liquidity_jpy")),
            "action_hint": (proposal_item or {}).get("action_hint"),
            "in_pool_before": code in pool_by_code,
            "enabled": code in enabled_set,
            "in_cooldown": code in cooldown_codes,
            "action": "keep" if code in pool_by_code else "reject",
            "reason": "already_in_pool" if code in pool_by_code else "not_selected",
        }

    add_rejections: dict[str, str] = {}
    add_candidates: list[dict] = []
    for code, r in records.items():
        if r["in_pool_before"]:
            continue
        hint = r.get("action_hint")
        if hint not in {"add", "keep", None}:
            add_rejections[code] = "not_an_add_candidate"
            continue
        if r["fund_score"] is None:
            add_rejections[code] = "missing_fund_score"
            continue
        if r["fund_score"] < min_score:
            add_rejections[code] = "fund_score_below_floor"
            continue
        if r["liquidity_jpy"] is None:
            add_rejections[code] = "missing_local_liquidity"
            continue
        if r["liquidity_jpy"] < liquidity_floor:
            add_rejections[code] = "liquidity_below_floor"
            continue
        if r["in_cooldown"]:
            add_rejections[code] = "pool_cooldown"
            continue
        add_candidates.append(r)

    add_candidates.sort(key=_rank_add)

    drop_candidates: list[dict] = []
    for code in pool_codes:
        r = records[code]
        if r["enabled"]:
            r["reason"] = "enabled_protected"
            continue
        if r.get("action_hint") == "drop":
            drop_candidates.append(r)
    drop_candidates.sort(key=_rank_drop)

    pool_after = [dict(p) for p in current_pool if isinstance(p, dict) and p.get("code")]
    dropped: list[str] = []
    added: list[str] = []
    rejected_after_guard: dict[str, str] = {}

    def current_codes() -> set[str]:
        return {p["code"] for p in pool_after}

    if mode == "grow":
        add_limit = min(max_adds, max(0, target_size - len(pool_after)), max(0, max_size - len(pool_after)))
        for r in add_candidates:
            if len(added) >= add_limit:
                rejected_after_guard[r["code"]] = "add_limit_reached"
                continue
            entry = {"code": r["code"], "name": r["name"], "sector": r.get("sector")}
            tentative = pool_after + [entry]
            if not _sector_cap_ok(tentative, entry, sector_cap):
                rejected_after_guard[r["code"]] = "sector_cap"
                continue
            pool_after = tentative
            added.append(r["code"])
    elif max_drops > 0:
        replace_limit = min(max_adds, max_drops)
        drop_queue = list(drop_candidates)
        for r in add_candidates:
            if len(added) >= replace_limit:
                rejected_after_guard[r["code"]] = "replace_limit_reached"
                continue
            if not drop_queue:
                rejected_after_guard[r["code"]] = "no_drop_candidate"
                continue
            drop = drop_queue.pop(0)
            without_drop = [p for p in pool_after if p["code"] != drop["code"]]
            entry = {"code": r["code"], "name": r["name"], "sector": r.get("sector")}
            tentative = without_drop + [entry]
            if not _sector_cap_ok(tentative, entry, sector_cap):
                rejected_after_guard[r["code"]] = "sector_cap"
                continue
            pool_after = tentative
            dropped.append(drop["code"])
            added.append(r["code"])
    else:
        for r in add_candidates:
            rejected_after_guard[r["code"]] = "replace_mode_drops_disabled"

    final_codes = current_codes()
    added_set = set(added)
    dropped_set = set(dropped)

    for code, reason in add_rejections.items():
        records[code]["action"] = "reject"
        records[code]["reason"] = reason
    for code, reason in rejected_after_guard.items():
        records[code]["action"] = "reject"
        records[code]["reason"] = reason
    for code in added_set:
        records[code]["action"] = "add"
        records[code]["reason"] = "accepted"
    for code in dropped_set:
        records[code]["action"] = "drop"
        records[code]["reason"] = "paired_replacement" if mode == "replace" else "dropped"
    for code in final_codes:
        if code not in added_set and code not in dropped_set:
            records[code]["action"] = "keep"
            if records[code].get("enabled"):
                records[code]["reason"] = "enabled_protected"
            else:
                records[code]["reason"] = "kept"

    ranking = []
    for code in sorted(records, key=lambda c: (records[c]["action"] != "add", _rank_add(records[c]))):
        r = records[code]
        ranking.append(
            {
                "code": code,
                "name": r.get("name"),
                "sector": r.get("sector"),
                "fund_score": r.get("fund_score"),
                "liquidity_jpy": r.get("liquidity_jpy"),
                "proposal_liquidity_jpy": r.get("proposal_liquidity_jpy"),
                "action_hint": r.get("action_hint"),
                "in_pool_before": r.get("in_pool_before"),
                "enabled": r.get("enabled"),
                "action": r.get("action"),
                "reason": r.get("reason"),
            }
        )

    return {
        "mode": mode,
        "pool_before": [p["code"] for p in current_pool if isinstance(p, dict) and p.get("code")],
        "pool_after": [p["code"] for p in pool_after],
        "new_pool": pool_after,
        "ranking": ranking,
        "changes": {"added": added, "dropped": dropped},
        "guardrails": {
            "pool_target_size": target_size,
            "pool_max_size": max_size,
            "max_adds_per_run": max_adds,
            "max_drops_per_run": max_drops,
            "min_fund_score_to_add": min_score,
            "liquidity_floor_jpy": liquidity_floor,
            "pool_sector_cap_pct": sector_cap,
            "pool_cooldown_days": int(settings["pool_cooldown_days"]),
            "cadence_days": int(settings["cadence_days"]),
        },
    }


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _read_parquet_for_code(code: str) -> pd.DataFrame | None:
    for path in (WATCHLIST_DIR / f"{code}.parquet", DATA_DIR / f"{code}.parquet"):
        if not path.exists():
            continue
        try:
            return pd.read_parquet(path)
        except Exception:
            continue
    return None


def compute_median_turnover_jpy(df: pd.DataFrame | None, window: int = 60) -> float | None:
    if df is None or df.empty or not {"close", "volume"}.issubset(df.columns):
        return None
    trading_value = (df.tail(window)["close"] * df.tail(window)["volume"]).dropna()
    if trading_value.empty:
        return None
    return float(trading_value.median())


def _local_liquidity_lookup(codes: list[str]) -> dict[str, float | None]:
    return {code: compute_median_turnover_jpy(_read_parquet_for_code(code)) for code in codes}


def _fetch_missing_parquets(codes: list[str]) -> list[dict]:
    from src.data_loader import update_data

    fetched = []
    WATCHLIST_DIR.mkdir(parents=True, exist_ok=True)
    for code in codes:
        if (WATCHLIST_DIR / f"{code}.parquet").exists() or (DATA_DIR / f"{code}.parquet").exists():
            continue
        try:
            df = update_data(code, dest_dir=WATCHLIST_DIR)
        except Exception as exc:  # network/parse errors must not break merge
            fetched.append({"code": code, "status": "error", "error": str(exc)})
            continue
        rows = int(len(df)) if df is not None else 0
        fetched.append({"code": code, "status": "ok" if rows else "empty", "rows": rows})
    return fetched


def save_pool(pool: list[dict], path: Path | None = None) -> None:
    path = Path(path or POOL_FILE)
    clean_pool = []
    for p in pool:
        item = {"code": p["code"], "name": p.get("name", p["code"])}
        if p.get("sector") is not None:
            item["sector"] = p["sector"]
        clean_pool.append(item)
    payload = {"pool": clean_pool}
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _cleanup_warmup_files(keep_codes: set[str], apply: bool) -> dict:
    WATCHLIST_DIR.mkdir(parents=True, exist_ok=True)
    stale = []
    errors = []
    bytes_removed = 0
    for path in sorted(WATCHLIST_DIR.glob("*.parquet")):
        code = path.stem
        if code in keep_codes:
            continue
        size = path.stat().st_size if path.exists() else 0
        stale.append({"code": code, "path": str(path), "bytes": size})
        if apply:
            try:
                path.unlink()
                bytes_removed += size
            except OSError as exc:
                errors.append({"code": code, "path": str(path), "error": str(exc)})
    return {
        "warmup_files_removed": stale if apply else [],
        "warmup_files_would_remove": [] if apply else stale,
        "warmup_bytes_removed": bytes_removed,
        "errors": errors,
    }


def _load_pool_cooldowns(today: date, cooldown_days: int) -> set[str]:
    cooldown = set()
    for path in sorted(CURATION_DIR.glob("pool_decision_*.json")):
        if path.name == "pool_decision_latest.json":
            continue
        payload = read_json(path)
        d = parse_iso_date((payload or {}).get("date"))
        if not d or (today - d).days >= cooldown_days:
            continue
        for item in (payload or {}).get("ranking", []):
            if isinstance(item, dict) and item.get("action") == "drop" and item.get("code"):
                cooldown.add(item["code"])
    latest = read_json(CURATION_DIR / "pool_decision_latest.json")
    d = parse_iso_date((latest or {}).get("date"))
    if d and (today - d).days < cooldown_days:
        for item in (latest or {}).get("ranking", []):
            if isinstance(item, dict) and item.get("action") == "drop" and item.get("code"):
                cooldown.add(item["code"])
    return cooldown


def should_run_pool_refresh(settings: dict, today: date, force: bool = False) -> dict:
    if force:
        return {"due": True, "reason": "forced", "last_date": None, "days_since": None}
    cadence = int(settings["cadence_days"])
    latest = read_json(CURATION_DIR / "pool_decision_latest.json")
    if latest and latest.get("proposal_valid") is False:
        return {"due": True, "reason": "previous_proposal_invalid", "last_date": None, "days_since": None}
    last_date = parse_iso_date((latest or {}).get("date"))
    if not last_date:
        return {"due": True, "reason": "no_previous_pool_decision", "last_date": None, "days_since": None}
    days_since = (today - last_date).days
    due = days_since >= cadence
    return {
        "due": due,
        "reason": "cadence_elapsed" if due else "cadence_not_elapsed",
        "last_date": last_date.isoformat(),
        "days_since": days_since,
    }


def _write_github_output(values: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for key, value in values.items():
            if isinstance(value, bool):
                value = "true" if value else "false"
            elif value is None:
                value = ""
            f.write(f"{key}={value}\n")


# ---------------------------------------------------------------------------
# Main side-effect runner
# ---------------------------------------------------------------------------

def run(
    proposal_path: Path,
    date_str: str,
    apply: bool,
    fetch_missing: bool,
) -> int:
    cfg = load_tickers_config()
    settings = get_pool_settings(cfg)
    if not settings.get("enabled", True):
        print("Pool refresh disabled in settings.curation.pool.enabled; no changes.")
        return 0

    today = parse_iso_date(date_str) or today_jst()
    proposal = read_json(proposal_path)
    current_pool = load_pool()
    if not _valid_proposal(proposal):
        audit = {
            "schema_version": 1,
            "date": date_str,
            "as_of": date_str,
            "generated_at": now_jst_iso(),
            "applied": bool(apply),
            "proposal_valid": False,
            "pool_written": False,
            "inputs": {"proposal": str(proposal_path)},
            "mode": "noop",
            "ranking": [],
            "changes": {"added": [], "dropped": []},
            "guardrails": {
                "cadence_days": int(settings["cadence_days"]),
                "pool_target_size": int(settings["pool_target_size"]),
                "pool_max_size": int(settings["pool_max_size"]),
            },
            "pool_before": [p["code"] for p in current_pool if isinstance(p, dict) and p.get("code")],
            "pool_after": [p["code"] for p in current_pool if isinstance(p, dict) and p.get("code")],
            "skipped_reason": "missing_or_invalid_proposal",
        }
        write_json(CURATION_DIR / "pool_decision_latest.json", audit)
        write_json(CURATION_DIR / f"pool_decision_{date_str}.json", audit)
        print(f"Pool proposal missing or invalid at {proposal_path}; no changes.")
        return 0

    proposal_codes = sorted(_proposal_candidates(proposal))

    fetched = []
    if fetch_missing and proposal_codes:
        fetched = _fetch_missing_parquets(proposal_codes)

    liquidity = _local_liquidity_lookup(sorted(set(proposal_codes) | {p["code"] for p in current_pool}))
    cooldown_codes = _load_pool_cooldowns(today, int(settings["pool_cooldown_days"]))
    decision = compute_pool_decision(
        proposal,
        current_pool,
        cfg,
        settings,
        today,
        liquidity_lookup=liquidity,
        cooldown_codes=cooldown_codes,
    )

    pool_changed = decision["pool_before"] != decision["pool_after"]
    pool_written = False
    if apply and pool_changed:
        save_pool(decision["new_pool"])
        pool_written = True

    keep_codes = set(decision["pool_after"]) | set(enabled_codes(cfg)) | _cfg_watchlist_codes(cfg)
    cleanup = _cleanup_warmup_files(keep_codes, apply=apply)

    audit = {
        "schema_version": 1,
        "date": date_str,
        "as_of": date_str,
        "generated_at": now_jst_iso(),
        "applied": bool(apply),
        "proposal_valid": True,
        "pool_written": pool_written,
        "inputs": {"proposal": str(proposal_path)},
        "mode": decision["mode"],
        "ranking": decision["ranking"],
        "changes": decision["changes"],
        "guardrails": decision["guardrails"],
        "pool_before": decision["pool_before"],
        "pool_after": decision["pool_after"],
        "fetched_missing_parquets": fetched,
        "cleanup": cleanup,
    }
    write_json(CURATION_DIR / "pool_decision_latest.json", audit)
    write_json(CURATION_DIR / f"pool_decision_{date_str}.json", audit)

    mode = "APPLIED" if apply else "DRY-RUN"
    print(
        f"[{mode}] pool_refresh date={date_str} mode={decision['mode']} "
        f"added={decision['changes']['added']} dropped={decision['changes']['dropped']} "
        f"pool_written={pool_written}"
    )
    if cleanup["errors"]:
        print(f"Warmup cleanup errors: {cleanup['errors']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deterministic merge for curation_pool.yml")
    p.add_argument("--proposal", default=None, help="path to pool_candidates_latest.json")
    p.add_argument("--date", default=None, help="YYYY-MM-DD JST (default: today JST)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="apply changes to curation_pool.yml")
    g.add_argument("--dry-run", action="store_true", help="compute decision only (default)")
    p.add_argument("--no-fetch-missing", action="store_true", help="do not fetch missing candidate parquets")
    p.add_argument("--check-due", action="store_true", help="only check the biweekly cadence guard")
    p.add_argument("--force", action="store_true", help="force cadence guard to due=true")
    p.add_argument("--github-output", action="store_true", help="write due outputs to $GITHUB_OUTPUT")
    return p


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_tickers_config()
    settings = get_pool_settings(cfg)
    date_str = args.date or today_jst_iso()
    today = parse_iso_date(date_str) or today_jst()

    if args.check_due:
        result = should_run_pool_refresh(settings, today, force=args.force)
        print(
            f"pool_refresh_due={str(result['due']).lower()} "
            f"reason={result['reason']} last_date={result['last_date']} days_since={result['days_since']}"
        )
        if args.github_output:
            _write_github_output(
                {
                    "due": result["due"],
                    "reason": result["reason"],
                    "last_date": result["last_date"],
                    "days_since": result["days_since"],
                }
            )
        return 0

    proposal_path = Path(args.proposal) if args.proposal else (CURATION_DIR / "pool_candidates_latest.json")
    apply = bool(args.apply) and not args.dry_run
    return run(proposal_path, date_str, apply, fetch_missing=not args.no_fetch_missing)


if __name__ == "__main__":
    raise SystemExit(main())
