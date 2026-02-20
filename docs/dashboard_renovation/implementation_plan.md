# Dashboard Renovation Plan

## Goal Description

Revamp the current stock trading dashboard to be more interactive and informative.
Current static HTML is limited in visualization. We will move to a React-based SPA hosted on GitHub Pages.
The new dashboard will allow users to:

- Switch between different time ranges (Week, Year, All).
- Visualize technical indicators (MA, RSI, etc.) used by the model.
- Clearly see "Buy" signals and prediction confidence.

## User Review Required

> [!IMPORTANT] > **New Dependency**: Node.js and npm will be required to build the frontend.
> The runtime (GitHub Actions) will NOT need Node.js if we commit the built assets to `docs/`.
> Daily updates will only touch `docs/history_data.json`, so the `main.py` flow remains purely Python.
> **Design**: The frontend will use a modern dark theme (TailwindCSS) with Recharts for graphing.

## Proposed Changes

### Backend (Python)

Refactor python scripts to support data export.

#### [MODIFY] [model.py](file:///c:/Users/119003/git/trader/src/model.py)

- Refactor `add_features` to ensure it can be called easily for data export without running predictions.

#### [MODIFY] [dashboard.py](file:///c:/Users/119003/git/trader/src/dashboard.py)

- Add function `export_history_data()` that:
  - Loads all historical data for enabled tickers.
  - Calculates features (MA, RSI, etc.).
  - Merges with Signal history.
  - Saves to `docs/history_data.json`.
- Remove/Update `generate_html` to purely serve as a placeholder or remove if fully replacing (initially we might keep it or replace `index.html` with the React entry point).
- **Decision**: The React build process will overwrite `docs/index.html`. `dashboard.py` should STOP generating HTML and ONLY update `history_data.json`.

### Frontend (New)

Create `web/` directory for the Next.js application.

#### [NEW] `web/package.json`

- Next.js, React, Recharts, TailwindCSS, Lucide-React.
- **Config**: `output: 'export'` in `next.config.js` for GitHub Pages compatibility.

#### [NEW] `web/app/page.tsx`

- Main dashboard container (App Router).
- Fetches `../history_data.json` (relative path works in GH Pages).

#### [NEW] `web/components/StockChart.tsx`

- Interactive chart component.
- Candlestick series for price.
- Line series for MA5, MA20, MA60.
- Separate synchronized charts for RSI and Volume.

#### [NEW] `web/components/SignalCard.tsx`

- Displays the latest prediction: "BUY" / "SELL" / "WAIT".
- Shows probability and reasons.

## Verification Plan

### Automated Tests

- Run `uv run python main.py` and verify `docs/history_data.json` is generated and valid.
- Run `npm run build` in `web/` to verify static export succeeds.

### Manual Verification

- **Local Preview**:
  1. Run `uv run python main.py` to generate data.
  2. Go to `web/` and run `npm run dev`.
  3. Verify charts render correctly and data matches `history_data.json`.
  4. Test time range selectors (Week/Year).
  5. Test indicator toggles.
- **Production Build**:
  1. Run `npm run build` (outputs to `docs/` or `out/` then moved to `docs/`).
  2. Open `docs/index.html` in browser (might need a local static server like `python -m http.server -d docs`).
