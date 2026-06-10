# AGENTS.md instructions for this repository

This file is the canonical guidance for agents working in this repository.
`CLAUDE.md` intentionally delegates here with `@AGENTS.md`.

## Project Overview

Automated stock prediction and trading-signal system for Japanese equities.
It runs autonomously via GitHub Actions on JPX trading days and publishes a
Next.js dashboard from `docs/` to GitHub Pages. Four layers:

- **Daily signals**: fetch OHLCV from Stooq (yfinance fallback), build 34
  technical + 11 macro features, gate each ticker through a walk-forward OOS
  backtest (KPI gate), predict `prob_up` with LightGBM, emit 5-level signals
  (`BUY`/`MILD_BUY`/`HOLD`/`MILD_SELL`/`SELL`), notify gate-passed non-HOLD
  signals via LINE.
- **Phase 0 — measurement**: write predictions/signals through to Neon
  Postgres (`DATABASE_URL`, schema in `migrations/`) and settle 1/5/10-day
  realized outcomes. DB failures queue to `data/outbox/` JSONL and replay.
- **Phase 1 — signal quality**: 5-day triple-barrier labels, isotonic
  calibration, macro/regime features, weekly-trained persisted models
  (`data/models/` + `active_model.json` pointer), IC/Brier/PSI drift checks.
- **Phase 2 — cross-sectional portfolio (shadow)**: weekly cross-sectional
  LightGBM ranker over the whole universe, daily long-only target portfolio
  with risk caps → `docs/portfolio_latest.json`. Shadow mode never alters
  Phase 1 signals or notifications.
- **Phase 3 — manual-trading UX + hardening**: TOPIX-benchmark settlement
  (`signal_outcomes.benchmark_ret`/`excess_ret`), a settle-day performance
  export (`docs/performance_detail.json` + `docs/signal_outcomes_recent.json`),
  a daily LINE digest and weekly performance summary (with bounded push retry),
  a `/performance` dashboard page (equity vs TOPIX, drawdown, calibration,
  recent outcomes), and active-mode wiring so `TRADER_PORTFOLIO_MODE=active`
  reflects `target_weight` into signals with no further code change (the flip
  itself stays a deliberate manual step gated on the shadow report).

Full as-built specs, known issues, and backlog: `specification_document/`
(start at its `README.md`; completed phase plans were deleted and live in git
history — that README documents the retrieval command and the plan lifecycle).

## Commands

```bash
uv sync                                   # install Python deps (Python 3.13)
uv run python main.py                     # run the full daily pipeline
uv run python scripts/db_migrate.py       # apply DB schema (needs DATABASE_URL)
uv run python tests/test_<name>.py        # tests are plain scripts, no pytest

cd web && npm install
cd web && npm run dev                     # dev server at http://localhost:3000
cd web && npm run build:prod              # static export with /trader base path
cd web && npm run lint
```

`main.py` works without `.env`: LINE notification and DB writes are skipped
when unconfigured. `.env.example` is the authoritative, commented list of all
environment variables (data source, KPI gate, Phase 0/1/2 knobs); defaults
live in `src/config.py`.

## Architecture

### Daily pipeline (`main.py`)

Per enabled ticker in `tickers.yml`:

1. **Data sync** (`src/data_loader.py`): Stooq CSV with yfinance fallback when
   stale, OHLCV validation, merge into `data/*.parquet`. Parquet files of
   disabled tickers are archived to `data/archive/`, never deleted.
2. **Features** (`src/model.py`, `src/macro.py`): 34 technical + 11 macro
   features (USD/JPY, TOPIX, Nikkei, Nikkei VI, JGB10y from `data/macro/`).
3. **KPI gate** (`src/backtest.py`): horizon-aware walk-forward OOS backtest
   with cost/slippage. Gate failure forces `HOLD`. Auto-optimizes per-ticker
   signal thresholds.
4. **Predict** (`src/phase1.py`, `src/model_store.py`): `TRADER_MODEL_MODE=auto`
   uses the saved weekly-trained calibrated model; falls back to same-day
   legacy training when no bundle exists. `legacy` mode is the rollback path.
5. **Signal** (`src/predictor.py`): `prob_up` → 5-level action with a
   volatility guard. (Notification moved post-loop — see step 8.)

Run-level steps after the ticker loop (Phase 3 reordered these so notifications
fire once, after the portfolio snapshot, and target weights persist):

6. **Phase 2 inference** (`src/cross_section.py`, `src/cs_model.py`,
   `src/portfolio.py`): cross-sectional prediction + portfolio snapshot →
   `docs/portfolio_latest.json` + DB (only when `TRADER_PORTFOLIO_ENABLED`).
7. **Active-mode merge** (`portfolio.merge_target_weights`): reflect
   `target_weight` into signals — no-op in shadow / gate-fail / no-snapshot.
8. **Notify** (`src/notifier.py` `send_line_text`, retry-bounded): per-ticker
   LINE push for gate-passed non-HOLD (`TRADER_NOTIFY_PER_TICKER_ENABLED`),
   then the daily digest (`src/digest.py`, `TRADER_NOTIFY_DIGEST_ENABLED`).
9. **Phase 0 write-through** (`src/db.py`, `src/db_records.py`) — after the
   merge so `signals.target_weight` lands.
10. **Dashboard export** (`src/dashboard.py`): `docs/state.json`,
    `docs/dashboard_index.json`, `docs/tickers/*.json`, plus best-effort
    `performance_summary.json` / `performance_detail.json` /
    `signal_outcomes_recent.json` (Phase 0/3) and `model_quality.json` (Phase 1).

Weekly/auxiliary: `scripts/weekly_model_retrain.py` (Phase 1 artifacts +
`model_registry`), `scripts/weekly_cross_section_retrain.py` (CS model →
`docs/cs_model_quality.json`), `scripts/portfolio_shadow_report.py` (Phase 1
vs Phase 2 + `active_readiness`), `scripts/settle_outcomes.py` (realized returns
+ TOPIX benchmark + settle-day performance export; `--refill-benchmark`),
`scripts/weekly_performance_notify.py` (weekly LINE performance summary),
`scripts/drift_check.py` (→ `docs/drift_report.json`),
`scripts/universe_select.py` (deterministic universe, report-only).

### Frontend (`web/`)

Next.js 16 + React 19 + Recharts 3 + TailwindCSS 4, static export served from
`docs/` via GitHub Pages. Japanese UI, dark theme.

- Data contract: `/dashboard_index.json` and `/tickers/{code}.json` are
  required; `performance_summary.json`, `model_quality.json`,
  `portfolio_latest.json`, `performance_detail.json`,
  `signal_outcomes_recent.json` and `curation/macro_latest.json` power optional
  cards/sections that hide when absent or `available: false`. (`history_data.json`
  is a removed legacy contract — the frontend does NOT read it.) All card fetches
  go through `src/lib/fetchJson.ts` (runtime-validated; bad JSON → card hidden).
- `src/app/page.tsx` (home) + `RegimeBanner`, `src/app/performance/page.tsx`
  (equity vs TOPIX / drawdown / calibration / recent outcomes via
  `PerformanceDetail`), `src/app/stocks/[ticker]/` (detail). `src/components/`:
  `StockChart`, `SignalCard`, `PerformanceCard`, `ModelQualityCard`,
  `PortfolioCard`, `PerformanceDetail`, `RegimeBanner`. Types in
  `src/types/index.ts`.

### CI/CD (`.github/workflows/`)

All times JST. Guards: `scripts/jpx_calendar.py` (trading day),
`scripts/run_guard.py` / `scripts/curation_guard.py` (idempotency). All
commits go through `.github/scripts/commit-and-push.sh` (rebase + 3 retries).

- **Daily**: ticker curation 04:30 → preopen core 06:00 (macro update →
  `main.py` → settle outcomes → drift check) → retries 06:20/06:40 →
  publish dashboard (on success) → watchdog 12:30 (freshness + drift;
  opens GitHub Issues on failure).
- **Weekly**: model retrain Sat 08:00 (Phase 1 + Phase 2 CS + shadow report),
  fundamental & report Sat 07:00, universe refresh Sun 07:00.
- **Nightly**: rotating refresh 19:30, feature precompute 20:00.
- **Monthly/Quarterly**: calendar sync, full audit, stress test.

## Key Conventions

- Python 3.13 managed with `uv`; tests are plain Python scripts under `tests/`.
- **The daily signal run must never break**: DB, macro, saved-model, and
  Phase 2 failures all degrade gracefully (fallback or skip + log). Preserve
  this property in any change to `main.py` or its dependencies.
- The KPI gate must pass before any actionable signal; failures → `HOLD`.
- Phase 2 is shadow: in shadow mode portfolio code must not modify Phase 1
  signals or notifications. Active wiring exists (Phase 3
  `portfolio.merge_target_weights`) so `TRADER_PORTFOLIO_MODE=active` reflects
  `target_weight` into signals **only** when the portfolio KPI gate passes; the
  flip to active stays a deliberate manual env change gated on the shadow report
  (`active_readiness` in `docs/portfolio_shadow_report.json`). Shadow behavior
  must remain byte-for-byte unchanged.
- `daily-publish-dashboard.yml` rsyncs `web/out/` over `docs/` with
  `--delete`. **Any new data file under `docs/` must be added to that
  workflow's `--exclude` list**, or the next publish deletes it
  (`tests/test_publish_workflow.py` checks this).
- Never let an agent edit `tickers.yml`; only the deterministic
  `scripts/curation_merge.py` may change it.
- `docs/history_data.json` is a legacy contract; `src/dashboard.py` removes it.
- Japanese UI convention: red (`赤`) means up and blue (`青`) means down.

## Skills

Local instruction sets stored in `SKILL.md` files. For this repository:

- `jp-stock-ticker-curation` (`skills/jp-stock-ticker-curation/SKILL.md`):
  interactive research of fundamentally strong Japanese stocks from primary
  sources (IR, filings), updating `tickers.yml` with source-backed picks.
  Trigger: the user names the skill or asks to research JP stocks and update
  `tickers.yml`. Read `SKILL.md` first, load `references/` only as needed.
  Prefer primary sources with concrete dates; afterwards report changed
  files, selected tickers, rationale, and source links.

## AI Ticker Curation (automated)

An automated system curates the `tickers.yml` universe via Claude running in
GitHub Actions (`claude-code-action@v1` + `CLAUDE_CODE_OAUTH_TOKEN`). Full
design and contracts: `specification_document/ai_ticker_curation/`.

- Cadence: technical screen runs **daily** (drives small universe swaps);
  a global-macro screen (rates/FX), the fundamental screen, and a casual
  girl-narrator weekly report run **weekly** (Saturday), notifying the
  report's GitHub URL via LINE.
- Agents emit JSON/Markdown only; the deterministic
  `scripts/curation_merge.py` owns `tickers.yml` edits under guardrails
  (churn cap, sector cap, warmup, cooldown, fundamental freshness).
- CI skills: `.claude/skills/{jp-stock-technical-screen,global-macro-screen,jp-stock-fundamental-screen,weekly-stock-report}/`.
- Scripts: `scripts/{technical_screen,curation_warmup,curation_merge,curation_guard,curation_notify}.py`
  (+ `scripts/curation_common.py`). Pool: `curation_pool.yml`.
  Tests: `tests/test_curation_merge.py`.
- Workflows: `.github/workflows/{daily-ticker-curation,weekly-fundamental-report}.yml`.
- Tuning knobs live in `tickers.yml` `settings.curation`. `data/watchlist/`
  is gitignored (warmup data is re-fetched each run).
