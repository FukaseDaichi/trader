#!/usr/bin/env python3
"""
Deterministic universe selection for Phase 2 cross-sectional portfolio.

Usage (report only — default, safe):
    uv run python scripts/universe_select.py --target-size 40

Usage (apply to tickers.yml):
    uv run python scripts/universe_select.py --target-size 40 --apply

This is the ONLY script allowed to write tickers.yml for universe selection.
Pure logic lives in src/universe.py.

Selection flow:
  1. Load enabled tickers + watchlist + pool via load_universe_candidates()
  2. Enrich each candidate with rows + liquidity from local parquets
  3. Call select_target_universe()
  4. Always write docs/curation/universe_selection_latest.json
  5. When --apply AND status=="ok": deterministically update tickers.yml via
     save_tickers_config() and set settings.curation.max_universe = target_size
  6. When status != "ok": print reason, DO NOT touch tickers.yml

Idempotency: re-running --apply with the same inputs produces an identical
tickers.yml (disabled_on / added_on are only set when the state actually
changes, and timestamps are not written to tickers.yml).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# scripts/ helpers (import via package name when running from project root,
# or via direct module path when running as a script)
try:
    from curation_common import (  # type: ignore[import]
        CURATION_DIR,
        WATCHLIST_DIR,
        enabled_codes,
        get_curation_settings,
        load_pool,
        load_tickers_config,
        now_jst_iso,
        save_tickers_config,
        today_jst_iso,
        write_json,
    )
except ModuleNotFoundError:
    from scripts.curation_common import (  # type: ignore[import]
        CURATION_DIR,
        WATCHLIST_DIR,
        enabled_codes,
        get_curation_settings,
        load_pool,
        load_tickers_config,
        now_jst_iso,
        save_tickers_config,
        today_jst_iso,
        write_json,
    )

from src.config import DATA_DIR, get_cross_section_config
from src.universe import compute_liquidity, load_universe_candidates, select_target_universe

# ---------------------------------------------------------------------------
# Parquet loader (no network; returns None on missing file)
# ---------------------------------------------------------------------------

def _load_parquet(code: str, source: str) -> pd.DataFrame | None:
    """
    Try to load a parquet for *code*.

    Search order:
    - enabled: data/<code>.parquet only
    - watchlist / pool: data/watchlist/<code>.parquet first, then data/<code>.parquet
    """
    if source == "enabled":
        path = DATA_DIR / f"{code}.parquet"
        if path.exists():
            try:
                return pd.read_parquet(path)
            except Exception:
                return None
        return None
    else:
        watchlist_path = WATCHLIST_DIR / f"{code}.parquet"
        if watchlist_path.exists():
            try:
                return pd.read_parquet(watchlist_path)
            except Exception:
                pass
        data_path = DATA_DIR / f"{code}.parquet"
        if data_path.exists():
            try:
                return pd.read_parquet(data_path)
            except Exception:
                pass
        return None


def _enrich_candidates(candidates: list[dict]) -> list[dict]:
    """Add rows and liquidity to each candidate dict (in-place mutation of copies)."""
    enriched = []
    for c in candidates:
        c = dict(c)  # shallow copy so we don't mutate the original
        df = _load_parquet(c["code"], c["source"])
        if df is not None and not df.empty:
            c["rows"] = len(df)
        else:
            c["rows"] = 0
        c["liquidity"] = compute_liquidity(df)
        enriched.append(c)
    return enriched


# ---------------------------------------------------------------------------
# tickers.yml update
# ---------------------------------------------------------------------------

def _apply_universe(
    cfg: dict,
    selected: list[dict],
    target_size: int,
) -> dict:
    """
    Return a new cfg dict with tickers.yml updated deterministically.

    Rules:
    - Selected codes: set enabled=True; if the code is new (pool/watchlist),
      add it to tickers with {code, name, sector, combined}; set added_on only
      when the code was not previously enabled.
    - Currently-enabled codes NOT in selected: set enabled=False; set
      disabled_on only if not already disabled (avoids timestamp churn).
    - settings.curation.max_universe = target_size.
    - Does NOT change source; does NOT touch watchlist entries here.
    """
    today = today_jst_iso()
    selected_codes = {c["code"]: c for c in selected}
    currently_enabled = set(enabled_codes(cfg))

    # Build a lookup of existing ticker entries by code
    existing: dict[str, dict] = {
        t["code"]: t
        for t in cfg.get("tickers", [])
        if isinstance(t, dict) and t.get("code")
    }

    new_tickers: list[dict] = []
    seen_codes: set[str] = set()

    # Pass 1: update existing ticker entries
    for t in cfg.get("tickers", []):
        if not isinstance(t, dict) or not t.get("code"):
            new_tickers.append(t)
            continue
        code = t["code"]
        seen_codes.add(code)
        entry = dict(t)

        if code in selected_codes:
            was_enabled = t.get("enabled", True)
            entry["enabled"] = True
            if not was_enabled:
                # Newly re-enabled from a disabled state
                entry["added_on"] = today
                entry.pop("disabled_on", None)
                # Refresh metadata from the candidate
                cand = selected_codes[code]
                if cand.get("sector") is not None:
                    entry.setdefault("sector", cand["sector"])
                if cand.get("combined") is not None:
                    entry["combined"] = cand["combined"]
        else:
            # Not selected: disable
            if t.get("enabled", True):
                # Only stamp disabled_on when actually transitioning to disabled
                entry["enabled"] = False
                entry["disabled_on"] = today
            else:
                entry["enabled"] = False  # already disabled; keep disabled_on

        new_tickers.append(entry)

    # Pass 2: add new codes from pool/watchlist that had no prior ticker entry
    for code, cand in selected_codes.items():
        if code in seen_codes:
            continue
        # Check if the code exists in watchlist (to carry over metadata)
        watchlist_entry = next(
            (w for w in cfg.get("watchlist", []) if isinstance(w, dict) and w.get("code") == code),
            None,
        )
        entry: dict = {
            "code": code,
            "name": cand.get("name", code),
            "enabled": True,
            "source": "curation",
            "added_on": today,
        }
        sector = cand.get("sector") or (watchlist_entry or {}).get("sector")
        if sector is not None:
            entry["sector"] = sector
        combined = cand.get("combined")
        if combined is not None:
            entry["combined"] = combined
        new_tickers.append(entry)

    # Update settings.curation.max_universe
    new_cfg = dict(cfg)
    new_cfg["tickers"] = new_tickers
    settings = dict(new_cfg.get("settings") or {})
    curation = dict(settings.get("curation") or {})
    curation["max_universe"] = target_size
    settings["curation"] = curation
    new_cfg["settings"] = settings

    return new_cfg


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _build_json_report(
    result: dict,
    target_size: int,
    candidates: list[dict],
) -> dict:
    """Build the JSON report dict (always written, regardless of --apply)."""
    total_candidates = len(candidates)
    passing_warmup = len(result["selected"]) + len(result["dropped_for_cap"])
    if result["status"] == "insufficient_universe":
        # When insufficient, selected is [], so we need to count differently
        passing_warmup = (
            total_candidates
            - len(result["filtered_low_warmup"])
        )

    return {
        "generated_at": now_jst_iso(),
        "status": result["status"],
        "target_size": target_size,
        "selected_size": result["selected_size"],
        "total_candidates": total_candidates,
        "passing_warmup": passing_warmup,
        "selected": [
            {
                "code": c["code"],
                "name": c.get("name", c["code"]),
                "sector": c.get("sector"),
                "combined": c.get("combined"),
                "rows": c.get("rows", 0),
                "liquidity": c.get("liquidity"),
                "source": c.get("source"),
            }
            for c in result["selected"]
        ],
        "sector_exposure": result["sector_exposure"],
        "dropped_for_cap": [
            {"code": c["code"], "sector": c.get("sector"), "combined": c.get("combined")}
            for c in result["dropped_for_cap"]
        ],
        "filtered_low_warmup": [
            {"code": c["code"], "rows": c.get("rows", 0), "source": c.get("source")}
            for c in result["filtered_low_warmup"]
        ],
        "warnings": result["warnings"],
    }


def _print_summary(result: dict, apply: bool, output_path: Path) -> None:
    status = result["status"]
    mode = "APPLIED" if apply and status == "ok" else "DRY-RUN"
    print(f"[{mode}] universe_select status={status}")
    print(f"  target_size={result['target_size']}  selected={result['selected_size']}")
    if result["sector_exposure"]:
        exposure_str = ", ".join(
            f"{s}:{n}" for s, n in sorted(result["sector_exposure"].items())
        )
        print(f"  sector_exposure: {exposure_str}")
    if result["warnings"]:
        for w in result["warnings"]:
            print(f"  WARNING: {w}")
    if result["filtered_low_warmup"]:
        print(f"  filtered_low_warmup ({len(result['filtered_low_warmup'])}): "
              + ", ".join(c["code"] for c in result["filtered_low_warmup"][:10])
              + ("..." if len(result["filtered_low_warmup"]) > 10 else ""))
    if result["dropped_for_cap"]:
        print(f"  dropped_for_cap ({len(result['dropped_for_cap'])}): "
              + ", ".join(c["code"] for c in result["dropped_for_cap"][:10])
              + ("..." if len(result["dropped_for_cap"]) > 10 else ""))
    print(f"  report: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    cs_cfg = get_cross_section_config()
    p = argparse.ArgumentParser(
        description="Deterministic universe selection for Phase 2 cross-sectional portfolio"
    )
    p.add_argument(
        "--target-size",
        type=int,
        default=cs_cfg["universe_target_size"],
        help=f"Target universe size (default: {cs_cfg['universe_target_size']})",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply the selected universe to tickers.yml (default: report only)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=CURATION_DIR / "universe_selection_latest.json",
        help="Path for the JSON report (default: docs/curation/universe_selection_latest.json)",
    )
    p.add_argument(
        "--min-warmup-rows",
        type=int,
        default=None,
        help="Minimum warmup rows required (default: from tickers.yml settings.curation.min_warmup_rows)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    # --- Load config ---
    cfg = load_tickers_config()
    curation_settings = get_curation_settings(cfg)
    cs_cfg = get_cross_section_config()

    target_size: int = args.target_size
    min_warmup_rows: int = (
        args.min_warmup_rows
        if args.min_warmup_rows is not None
        else int(curation_settings["min_warmup_rows"])
    )
    sector_cap_pct: float = float(curation_settings["sector_cap_pct"])
    min_universe: int = cs_cfg["min_universe"]
    output_path: Path = Path(args.output)

    # --- Build and enrich candidates ---
    pool = load_pool()
    raw_candidates = load_universe_candidates(cfg, pool)
    candidates = _enrich_candidates(raw_candidates)

    # --- Select universe ---
    result = select_target_universe(
        candidates,
        target_size=target_size,
        min_warmup_rows=min_warmup_rows,
        sector_cap_pct=sector_cap_pct,
        min_universe=min_universe,
    )

    # --- Always write JSON report ---
    report = _build_json_report(result, target_size, candidates)
    write_json(output_path, report)

    # --- Print summary ---
    _print_summary(result, args.apply, output_path)

    # --- Apply to tickers.yml only when --apply AND status == "ok" ---
    if args.apply:
        if result["status"] == "ok":
            new_cfg = _apply_universe(cfg, result["selected"], target_size)
            save_tickers_config(new_cfg)
            newly_enabled = [
                c["code"] for c in result["selected"]
                if c["code"] not in set(enabled_codes(cfg))
            ]
            newly_disabled = [
                code for code in enabled_codes(cfg)
                if code not in {c["code"] for c in result["selected"]}
            ]
            print(f"  tickers.yml updated: +{len(newly_enabled)} enabled, "
                  f"-{len(newly_disabled)} disabled")
            if newly_enabled:
                print(f"  newly enabled:  {newly_enabled}")
            if newly_disabled:
                print(f"  newly disabled: {newly_disabled}")
        else:
            print(
                f"  tickers.yml NOT modified: status={result['status']}\n"
                f"  Reason: {result['warnings'][0] if result['warnings'] else 'see warnings'}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
