# Implementation Plan - Stock Management and Localization

## 1. Configuration Update

- Update `c:\Users\119003\git\trader\tickers.yml`
  - Add `7974.JP` (Nintendo)
  - Add `2914.JP` (JT)
  - Ensure `max_tickers` allows for 3 stocks.

## 2. Frontend Refactoring (Next.js)

### Current Structure

- `src/app/page.tsx`: Handles everything (Dashboard + specific stock view via state).

### New Structure

- `src/app/page.tsx`: Dashboard / Stock List.
- `src/app/stocks/[code]/page.tsx`: Individual Stock Detail page.
- Update `HistoryData` type if needed (unlikely).

### Localization

- Translate all hardcoded English strings to Japanese in:
  - `page.tsx`
  - `components/StockChart.tsx`
  - `components/SignalCard.tsx`

## 3. Data Flow

- The app reads `/history_data.json`.
- Users will need to run `python main.py` to regenerate `history_data.json` with new stocks, and copy it to `web/public`.

## Verification

- Verify routing works locally.
- Verify Japanese text is displayed.
