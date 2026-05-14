# AGENTS.md instructions for this repository

This file is the canonical guidance for agents working in this repository.
`CLAUDE.md` intentionally delegates here with `@AGENTS.md`.

## Project Overview

Automated stock prediction and trading signal system for Japanese equities.
It fetches daily price data from Stooq with automatic yfinance fallback when
data is stale, trains LightGBM ensembles, generates 5-level signals
(`BUY`/`MILD_BUY`/`HOLD`/`MILD_SELL`/`SELL`), notifies via LINE, and displays
results on a Next.js dashboard hosted on GitHub Pages.

The system runs autonomously via GitHub Actions at 06:00 JST on JPX trading
days.

## Commands

### Python backend and ML

```bash
uv sync
uv run python main.py
```

- `uv sync`: Install or update Python dependencies.
- `uv run python main.py`: Run the full daily pipeline. Requires `.env` for LINE notifications.

### Frontend dashboard

```bash
cd web && npm install
cd web && npm run dev
cd web && npm run build:prod
cd web && npm run lint
```

- `npm run dev`: Start the dev server at `http://localhost:3000`.
- `npm run build:prod`: Production build with the `/trader` base path.
- `npm run lint`: Run ESLint.

### Deploy dashboard to GitHub Pages

```bash
cd web && npm run build:prod && cp -r out/* ../docs/
```

## Architecture

### Daily Pipeline (`main.py`)

For each enabled ticker in `tickers.yml`:

1. **Data sync** (`src/data_loader.py`): Download CSV data from Stooq, fall back to yfinance if data is stale, and merge into per-ticker parquet files in `data/`.
2. **Feature engineering** (`src/model.py`): Generate 35 technical indicators, including returns, MA, RSI, MACD, Bollinger Bands, ATR, volume, candlestick, and calendar features.
3. **KPI gate** (`src/backtest.py`): Run a walk-forward 3-fold out-of-sample backtest with cost and slippage. Block actionable signals if CAGR, MaxDD, Sharpe, or expectancy fail thresholds. Auto-optimize signal thresholds per ticker.
4. **Train and predict** (`src/model.py`): Train a 4-model LightGBM ensemble using 3 folds plus full data, then output `prob_up`.
5. **Signal generation** (`src/predictor.py`): Map `prob_up` to a 5-level action with a volatility guard.
6. **LINE notification** (`src/notifier.py`): Push the signal via the LINE Messaging API.
7. **Dashboard export** (`src/dashboard.py`): Write `docs/history_data.json`, `docs/state.json`, and `docs/backtest_report.json`.

### Configuration

- `tickers.yml`: Enabled stock tickers with code, name, and enabled flag.
- `.env`: LINE tokens, KPI thresholds, dashboard URL, and related settings. See `.env.example`.
- KPI and threshold parameters are environment-driven with safe code defaults in `src/config.py`.

### Frontend (`web/`)

Next.js static export served from `docs/` via GitHub Pages.

- `src/app/page.tsx`: Home dashboard with ticker grid and signal history.
- `src/app/stocks/[ticker]/page.tsx`: Ticker detail with candlestick chart, MA, RSI, and volume.
- `src/components/StockChart.tsx`: Recharts composite chart using Japanese candlesticks.
- `src/components/SignalCard.tsx`: Signal display card.
- `src/types/index.ts`: TypeScript interfaces.
- Dark theme with TailwindCSS 4 and Japanese text throughout.

### CI/CD (`.github/workflows/`)

- **Daily**: `daily-preopen-core.yml` at 06:00 JST, retries at 06:20 and 06:40, dashboard publish, and watchdog at 12:30.
- **Weekly**: Model retrain on Saturday and universe refresh on Sunday.
- **Monthly**: Calendar sync and full audit.
- JPX calendar guard (`scripts/jpx_calendar.py`) skips non-trading days.
- Idempotent guard (`scripts/run_guard.py`) prevents duplicate runs.

### Data Files

- `data/*.parquet`: Per-ticker OHLCV price history.
- `docs/*.json`: Dashboard data, backtest reports, and audit reports.
- `data/jpx_holidays.json`: Cached JPX holiday calendar.

## Key Conventions

- Use Python 3.13 managed with `uv`.
- Signal thresholds can be auto-optimized per ticker with an expectancy, CAGR, or Sharpe objective.
- The KPI gate must pass before any actionable signal fires; failed tickers are forced to `HOLD`.
- The frontend reads from `docs/history_data.json`; no API server is needed.
- Japanese UI convention: red (`赤`) means up and blue (`青`) means down.

## Skills

A skill is a set of local instructions stored in a `SKILL.md` file.

### Available skills

- `jp-stock-ticker-curation`: Research fundamentally strong Japanese stocks from up-to-date internet sources and update `tickers.yml` with source-backed selections. (file: `skills/jp-stock-ticker-curation/SKILL.md`)

### How to use skills

- Discovery: Use the skill list above as the source of truth for this repository.
- Trigger rule: If the user mentions `jp-stock-ticker-curation` (with `$SkillName` or plain text), or asks to research JP stocks and update `tickers.yml`, especially for fundamental upside, load and follow that skill.
- Scope: Apply the skill only for the current turn unless re-requested.
- Missing/blocked: If the skill file cannot be read, report the issue briefly and continue with the best fallback workflow.
- Progressive loading: Read `SKILL.md` first, then load only the needed files from `references/`.
- Source quality: Prefer primary sources such as company IR, exchange filings, and official disclosures; include concrete dates in output.
- Output contract: After updates, report changed file paths, selected tickers, concise rationale, and source links.
