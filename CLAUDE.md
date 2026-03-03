# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated stock prediction and trading signal system for Japanese equities. Fetches daily price data from Stooq, trains LightGBM ensembles, generates 5-level signals (BUY/MILD_BUY/HOLD/MILD_SELL/SELL), notifies via LINE, and displays results on a Next.js dashboard hosted on GitHub Pages.

Runs autonomously via GitHub Actions at 06:00 JST on JPX trading days.

## Commands

### Python (backend/ML)

```bash
uv sync                          # Install/update Python dependencies
uv run python main.py             # Run full daily pipeline (needs .env for LINE)
```

### Frontend (Next.js dashboard)

```bash
cd web && npm install             # Install web dependencies
cd web && npm run dev             # Dev server (http://localhost:3000)
cd web && npm run build:prod      # Production build (with /trader base path)
cd web && npm run lint            # ESLint
```

### Deploy dashboard to GitHub Pages

```bash
cd web && npm run build:prod && cp -r out/* ../docs/
```

## Architecture

### Daily Pipeline (`main.py`)

For each enabled ticker in `tickers.yml`:

1. **Data sync** (`src/data_loader.py`) — Download CSV from Stooq, merge into per-ticker parquet files in `data/`
2. **Feature engineering** (`src/model.py`) — 35 technical indicators (returns, MA, RSI, MACD, BBands, ATR, volume, candlestick, calendar)
3. **KPI gate** (`src/backtest.py`) — Walk-forward 3-fold OOS backtest with cost/slippage. Blocks signals if CAGR, MaxDD, Sharpe, or Expectancy fail thresholds. Auto-optimizes signal thresholds per ticker
4. **Train & predict** (`src/model.py`) — 4-model LightGBM ensemble (3 folds + full data), outputs `prob_up`
5. **Signal generation** (`src/predictor.py`) — Maps `prob_up` to 5-level action with volatility guard
6. **LINE notification** (`src/notifier.py`) — Push signal via LINE Messaging API
7. **Dashboard export** (`src/dashboard.py`) — Writes `docs/history_data.json`, `docs/state.json`, `docs/backtest_report.json`

### Configuration

- `tickers.yml` — Enabled stock tickers (code, name, enabled flag)
- `.env` — LINE tokens, KPI thresholds, dashboard URL (see `.env.example`)
- All KPI/threshold parameters are env-driven with safe code defaults in `src/config.py`

### Frontend (`web/`)

Next.js static export served from `docs/` via GitHub Pages.

- `src/app/page.tsx` — Home dashboard (ticker grid, signal history)
- `src/app/stocks/[ticker]/page.tsx` — Ticker detail (candlestick chart, MA/RSI/volume)
- `src/components/StockChart.tsx` — Recharts composite chart (Japanese candlesticks)
- `src/components/SignalCard.tsx` — Signal display card
- `src/types/index.ts` — TypeScript interfaces
- Dark theme (slate-950), TailwindCSS 4, Japanese text throughout

### CI/CD (`.github/workflows/`)

- **Daily**: `daily-preopen-core.yml` (06:00 JST) → retries at 06:20/06:40 → dashboard publish → watchdog at 12:30
- **Weekly**: Model retrain (Sat), universe refresh (Sun)
- **Monthly**: Calendar sync, full audit
- JPX calendar guard (`scripts/jpx_calendar.py`) skips non-trading days
- Idempotent guard (`scripts/run_guard.py`) prevents duplicate runs

### Data Files

- `data/*.parquet` — Per-ticker OHLCV price history
- `docs/*.json` — Dashboard data, backtest reports, audit reports
- `data/jpx_holidays.json` — Cached JPX holiday calendar

## Key Conventions

- Python 3.13, managed with `uv`
- Signal thresholds can be auto-optimized per ticker (objective: expectancy/cagr/sharpe)
- KPI gate must pass before any actionable signal fires; failed tickers get forced HOLD
- Frontend reads from `docs/history_data.json` — no API server needed
- Japanese UI conventions: 赤 (red) = up, 青 (blue) = down

## Skills

See `AGENTS.md` for the `jp-stock-ticker-curation` skill that researches Japanese stocks and updates `tickers.yml`.
