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
  Postgres (`DATABASE_URL`, schema in `migrations/`) and settle executable
  1/5/10-session outcomes (next-session open to horizon-session close). DB
  failures queue to `data/outbox/` JSONL and replay.
- **Phase 1 — signal quality**: 5-day triple-barrier labels, isotonic
  calibration, macro/regime features, exact-candidate holdout gates,
  schema-v3 persisted models with atomic activation, IC/Brier/PSI drift checks.
- **Phase 2 — cross-sectional portfolio (shadow)**: weekly cross-sectional
  LightGBM ranker over the whole universe, daily long-only target portfolio
  with risk caps → `docs/portfolio_latest.json`. Shadow mode never alters
  Phase 1 signals or notifications.
- **Phase 3 — manual-trading UX + hardening**: execution-contract-versioned
  settlement and benchmark coverage (`benchmark_ret`/`excess_ret` stay NULL
  when no same-basis TOPIX open exists), a settle-day performance
  export (`docs/performance_detail.json` + `docs/signal_outcomes_recent.json`),
  a daily LINE digest and weekly performance summary (with bounded push retry),
  a `/performance` dashboard page (TOPIX shown only with complete same-basis
  coverage, plus drawdown, calibration, recent outcomes), and active-mode wiring so `TRADER_PORTFOLIO_MODE=active`
  reflects `target_weight` into signals with no further code change (the flip
  itself stays a deliberate manual step gated on the shadow report).

Full as-built specs, known issues, and backlog: `specification_document/`
(start at its `README.md`; completed phase plans normally live in git history,
while user-requested decision records or plans with unfinished operational
rollout remain in `plans/` with explicit status).

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
3. **Predict + exact gate** (`src/phase1.py`, `src/model_store.py`,
   `src/backtest.py`): `TRADER_MODEL_MODE=auto` uses a runtime/manifest-verified
   weekly bundle and its own purged-OOS gate evidence; otherwise it trains an
   ephemeral candidate whose own tuning/embargo/holdout evidence supplies the
   thresholds and gate. Its stable version includes the artifact and gate
   contract hashes; the exact booster is bound separately by its bundle hash.
   No separately trained surrogate gate is reused.
   Evidence mismatch or gate failure forces `HOLD`; `legacy` is the binary-1d
   rollback path but still uses the v2 execution contract.
4. **Signal** (`src/predictor.py`): `prob_up` → 5-level action with a
   volatility guard. (Notification moved post-loop — see step 7.)

Run-level steps after the ticker loop (Phase 3 reordered these so notifications
fire once, after the portfolio snapshot, and target weights persist):

5. **Phase 2 inference** (`src/cross_section.py`, `src/cs_model.py`,
   `src/portfolio.py`): cross-sectional prediction + portfolio snapshot →
   `docs/portfolio_latest.json` + DB (only when `TRADER_PORTFOLIO_ENABLED`).
6. **Active-mode merge** (`portfolio.merge_target_weights`): reflect
   `target_weight` into signals — no-op in shadow / gate-fail / no-snapshot.
7. **Notify** (`src/notifier.py` `send_line_text`, retry-bounded): the daily
   digest (`src/digest.py`, `TRADER_NOTIFY_DIGEST_ENABLED`) is the primary
   channel and lists gate-passed buy/sell ticker names per action; per-ticker
   pushes are OFF by default (`TRADER_NOTIFY_PER_TICKER_ENABLED=false` since
   2026-06-11, LINE free-tier quota) and remain available as an opt-in.
8. **Phase 0 write-through** (`src/db.py`, `src/db_records.py`) — after the
   merge so `signals.target_weight` lands.
9. **Dashboard export** (`src/dashboard.py`): `docs/state.json`,
   `docs/dashboard_index.json`, `docs/tickers/*.json`, plus best-effort
   `performance_summary.json` / `performance_detail.json` /
   `signal_outcomes_recent.json` (Phase 0/3) and `model_quality.json` (Phase 1).

Weekly/auxiliary: `scripts/weekly_model_retrain.py` (unique staged Phase 1
schema-v3 candidate → full-coverage/checksum/evidence gate → atomic pointer +
`model_registry`, with registry outbox fallback),
`scripts/weekly_cross_section_retrain.py` (CS model →
`docs/cs_model_quality.json`), `scripts/portfolio_shadow_report.py` (Phase 1
vs Phase 2 + `active_readiness`), `scripts/settle_outcomes.py` (next-open
realized returns, legacy-only TOPIX refill, settle-day performance export;
`--restate-execution-contract`),
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
  (net non-overlapping equity, TOPIX only when same-basis coverage is complete,
  drawdown / calibration / recent outcomes via
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
  fundamental & report Sat 07:00 (also runs the **biweekly** pool refresh,
  every 14 days), universe refresh Sun 07:00.
- **Nightly**: rotating refresh 19:30.
- **Monthly/Quarterly**: calendar sync, full audit, stress test.

## Key Conventions

- Python 3.13 managed with `uv`; tests are plain Python scripts under `tests/`.
- **The daily signal run must never break**: DB, macro, saved-model, and
  Phase 2 failures all degrade gracefully (fallback or skip + log). Preserve
  this property in any change to `main.py` or its dependencies.
- The KPI gate must pass before any actionable signal; failures → `HOLD`.
- Saved-model, drift, and model-quality readers must share the same runtime
  artifact/gate/manifest compatibility validation. Old or corrupt artifacts
  fail closed; they are never presented as current quality evidence.
- If Phase 1 feature semantics change without changing column names, bump the
  Phase 1 artifact schema version and retrain; the ordered feature hash alone
  cannot identify a same-name semantic change.
- Phase 2 is shadow: in shadow mode portfolio code must not modify Phase 1
  signals or notifications. Active wiring exists (Phase 3
  `portfolio.merge_target_weights`) so `TRADER_PORTFOLIO_MODE=active` reflects
  `target_weight` into signals **only** when the portfolio KPI gate passes; the
  flip to active stays a deliberate manual env change gated on the shadow report
  (`active_readiness` in `docs/portfolio_shadow_report.json`). Shadow behavior
  must remain byte-for-byte unchanged. Active also requires current v2,
  net-vs-net accounting, complete same-basis benchmark coverage, and an exact
  CS model-version match between the backtest and today's snapshot. Current
  macro data has no TOPIX open, so active intentionally remains fail-closed.
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

`tickers.yml` and `curation_pool.yml` are curated automatically by Claude
running in GitHub Actions (`claude-code-action@v1`). Cadence: technical screen
**daily**, fundamental + global-macro + weekly report **weekly** (Sat), pool
refresh **biweekly** (inside the Saturday workflow).

**Critical invariant**: curation agents emit JSON/Markdown only and never edit
those two files directly. The deterministic `scripts/curation_merge.py`
(→ `tickers.yml`) and `scripts/curation_pool_merge.py` (→ `curation_pool.yml`)
are the **sole writers**, under guardrails (churn/sector cap, warmup, cooldown,
freshness, pool liquidity floor + add-only/replace). A PreToolUse hook
(`.claude/hooks/protect-deterministic-files.sh`) enforces the no-direct-edit
rule. CI skills live in `.claude/skills/`; tuning knobs in `tickers.yml`
`settings.curation` (pool knobs under `.pool`).

Full design, data contracts, cadence, guardrails, scripts, and rollout:
`specification_document/ai_ticker_curation/` (start at `00_overview.md`;
`07_pool_refresh.md` covers the candidate pool / 母集団).
