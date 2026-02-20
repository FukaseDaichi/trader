# Walkthrough - Stock Management and Localization

## Changes

### 1. `tickers.yml`

- Added Nintendo (7974.JP) and Japan Tobacco (2914.JP).

### 2. Web App (`web/src/app`)

- **Routing**: Introduced dynamic routing `web/src/app/stocks/[ticker]/page.tsx`.
- **Home Page**: `web/src/app/page.tsx` now lists available stocks with summary.
- **Localization**: Translated UI elements to Japanese.

### How to use

1. Run `python main.py` to fetch data for new stocks and generate `docs/history_data.json`.
2. Copy `docs/history_data.json` to `web/public/history_data.json`.
3. Run `npm run dev` in `web` directory.
4. Visit `http://localhost:3000` to see the stock list.
5. Click on a stock to view details.

### Maintenance Note

- When adding new stocks to `tickers.yml`, you must also update the list in `web/src/app/stocks/[ticker]/page.tsx` (`generateStaticParams` function) if you are strictly using `output: export`. For development mode, this is not strictly required but recommended.
