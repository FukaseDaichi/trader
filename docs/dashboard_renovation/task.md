# Task List: Dashboard Renovation

## Backend (Python) Updates

- [ ] Refactor `src/model.py` to expose feature calculation logic for external use <!-- id: 0 -->
- [ ] Update `src/dashboard.py` to generate `history_data.json` containing: <!-- id: 1 -->
  - OHLCV data (Open, High, Low, Close, Volume)
  - Calculated technical indicators (MA, RSI, etc.)
  - Prediction signals history
- [ ] Verify `history_data.json` generation locally <!-- id: 2 -->

## Frontend (React) Development

- [ ] Initialize Next.js project in `web/` directory (TS, Tailwind, App Router) <!-- id: 3 -->
- [ ] Configure `next.config.js` for Static Export (`output: 'export'`) <!-- id: 4 -->
- [ ] Install dependencies (Recharts, Lucide React, etc.) <!-- id: 5 -->
- [ ] Implement data fetching service to load `../docs/history_data.json` <!-- id: 6 -->
- [ ] Create layout component with responsive design <!-- id: 7 -->
- [ ] Develop `StockChart` component: <!-- id: 8 -->
  - Candlestick chart for price
  - Overlay for Moving Averages
  - Sub-charts for RSI and Volume
- [ ] Implement Control Panel: <!-- id: 9 -->
  - Date range selector (Week/Year/All)
  - Indicator toggles
- [ ] Display Prediction Signals & "Buy/Sell" Status prominently <!-- id: 10 -->
- [ ] Build for production and output to `docs/` <!-- id: 11 -->

## Integration & Verification

- [ ] Update `README.md` with new development instructions <!-- id: 12 -->
- [ ] Verify the full flow: `main.py` -> JSON update -> Dashboard refresh <!-- id: 13 -->
