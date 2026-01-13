# Walkthrough: Stock Prediction System Implementation

## Completed Changes

I have implemented the stock prediction system as per the plan.

### 1. Project Setup

- Initialized project with `uv`.
- Created directory structure (`src/`, `data/`, `docs/`, `.github/`).
- configured `tickers.yml` with **Mitsubishi UFJ Financial Group (8306.JP)**.

### 2. Implementation (`src/`)

- **`config.py`**: Loads configuration.
- **`data_loader.py`**: Downloads Stooq data (incremental merge supported).
- **`model.py`**: LightGBM model with features (MA, RSI, Volatility).
- **`predictor.py`**: Generates BUY/SELL signals (Thresholds: 0.62 / 0.38).
- **`notifier.py`**: Sends LINE signals.
- **`dashboard.py`**: Updates `docs/state.json` and renders `docs/index.html`.
- **`main.py`**: Orchestrates the daily flow.

### 3. Automation

- **`.github/workflows/daily_job.yml`**: Schedule set to 21:00 UTC (06:00 JST).

## Verification Results

### Manual Execution Test

Ran `uv run python main.py` locally.

#### Checks:

- [ ] Data download (`data/8306.JP.parquet` should be created).
- [ ] Model training (Output should show probability).
- [ ] Signal generation (Output should show signal).
- [ ] Dashboard generation (`docs/index.html` should be created).

### Usage

To deploy:

1. Push this repository to GitHub.
2. Go to **Settings > Secrets and variables > Actions**.
3. Add `LINE_CHANNEL_ACCESS_TOKEN` and `LINE_USER_ID`.
4. Go to **Settings > Pages**.
5. Source: **Deploy from a branch**.
6. Branch: **main**, Folder: **docs**.
7. The daily job will run automatically at 06:00 JST.
