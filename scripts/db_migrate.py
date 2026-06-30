#!/usr/bin/env python3
"""
Idempotent migration runner for the Phase 0 measurement layer.

Applies any .sql file in migrations/ not yet recorded in schema_migrations,
then seeds tickers (from tickers.yml) and the legacy model_registry row.

Usage:
  uv run python scripts/db_migrate.py
  uv run python scripts/db_migrate.py --dry-run

Exits 0 (no-op) when DB is disabled or DATABASE_URL is unset.
Exits non-zero on a real connection / SQL error so bootstrap failures surface.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from scripts.curation_common import load_tickers_config, now_jst_iso  # noqa: E402

MIGRATIONS_DIR = ROOT / "migrations"


def _ensure_migrations_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    conn.commit()


def _applied_versions(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {r[0] for r in cur.fetchall()}


def _apply_pending(conn, dry_run: bool) -> list[str]:
    applied = _applied_versions(conn)
    pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.stem not in applied)
    done = []
    for path in pending:
        sql = path.read_text(encoding="utf-8")
        print(f"Applying migration {path.stem}{' (dry-run)' if dry_run else ''}...")
        if dry_run:
            continue
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
            )
        conn.commit()
        done.append(path.stem)
    return done


def _seed_tickers(conn) -> int:
    cfg = load_tickers_config()
    rows = []
    for t in cfg.get("tickers", []):
        if not isinstance(t, dict) or not t.get("code"):
            continue
        rows.append(
            (
                t["code"],
                t.get("name") or t["code"],
                t.get("sector"),
                bool(t.get("enabled", True)),
                t.get("source"),
                t.get("added_on"),
                t.get("disabled_on"),
            )
        )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO tickers (code, name, sector, enabled, source, added_on, disabled_on)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (code) DO UPDATE SET"
            "  name=EXCLUDED.name, sector=EXCLUDED.sector, enabled=EXCLUDED.enabled,"
            "  source=EXCLUDED.source, added_on=EXCLUDED.added_on, disabled_on=EXCLUDED.disabled_on",
            rows,
        )
    conn.commit()
    return len(rows)


def _seed_legacy_model(conn) -> None:
    from psycopg.types.json import Jsonb

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_registry"
            " (version, trained_at, kind, universe, feature_set, params, cv_metrics, artifact_uri, active)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (version) DO NOTHING",
            (
                db.LEGACY_MODEL_VERSION,
                now_jst_iso(),
                "per_ticker_legacy_daily",
                Jsonb([]),
                Jsonb([]),
                Jsonb({}),
                Jsonb({}),
                None,
                True,
            ),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not db.db_enabled():
        print("DB disabled or DATABASE_URL unset; nothing to migrate.")
        return 0

    conn = db.connect()
    try:
        _ensure_migrations_table(conn)
        done = _apply_pending(conn, dry_run=args.dry_run)
        if args.dry_run:
            print("Dry-run complete.")
            return 0
        n_tickers = _seed_tickers(conn)
        _seed_legacy_model(conn)
        print(f"Migrations applied: {done or '(none pending)'}")
        print(
            f"Seeded {n_tickers} tickers and legacy model '{db.LEGACY_MODEL_VERSION}'."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
