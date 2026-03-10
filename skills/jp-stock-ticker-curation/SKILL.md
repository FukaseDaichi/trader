---
name: jp-stock-ticker-curation
description: Research and curate fundamentally strong Japanese stock candidates from up-to-date internet sources, then update `tickers.yml` in trader repositories. Use when a user asks to add, replace, shortlist, or refresh JP tickers based on earnings momentum, guidance revisions, valuation rerating room, balance sheet quality, shareholder returns, or other fundamental upside drivers, and expects source-backed rationale plus a direct YAML edit.
---

# JP Stock Ticker Curation

Use this skill to refresh `tickers.yml` with a fundamentally driven JP stock basket.
Prioritize primary sources, date-stamped facts, and explicit tradeoffs over generic stock picks or chart-only stories.

## Workflow

1. Confirm local schema and constraints.
- Read `tickers.yml`, `README.md`, and `src/config.py`.
- Keep existing YAML structure and `settings` unless user asks to change them.
- Detect naming convention (`name` in Japanese or English) and keep it consistent.
- Treat repository paths as repo-relative when documenting changes.

2. Gather latest market evidence from the web.
- Use internet browsing for every run; do not rely on memory for "latest" financial information.
- Prefer primary sources: company IR, TDnet/EDINET disclosures, JPX pages, financial statements, and official guidance documents.
- Use secondary sources only to complement coverage gaps.
- Record concrete dates and numbers for each key claim, and note whether the claim is based on reported actuals, company guidance, or an inference.

3. Build a fundamental longlist.
- Start from liquid JP equities where the latest disclosure suggests upside from fundamentals, not just price momentum.
- Favor earnings acceleration, upward guidance revisions, margin expansion, ROE/ROIC improvement, balance-sheet strength, buybacks or dividend growth, and rerating catalysts tied to business performance.
- Exclude names with weak liquidity or stale disclosure.
- Exclude names whose recent strength is mostly one-off, technically driven, or contradicted by balance-sheet stress or deteriorating core earnings.
- Keep a temporary longlist, then narrow with the scoring framework.

4. Score with the framework.
- Read `references/selection-framework.md`.
- Score each candidate on earnings quality, guidance, valuation, cash generation, shareholder return, and fundamental catalysts.
- Drop low-conviction names and keep a balanced sector mix.

5. Select final tickers for `tickers.yml`.
- Choose the final set size from user intent. If unspecified, use 5-8 names.
- Keep portfolio concentration reasonable; avoid one-sector dominance.
- Use ticker codes in `NNNN.JP` format and set `enabled: true` for selected names.

6. Edit `tickers.yml`.
- Update only the `tickers` entries unless the user requests `settings` changes.
- Preserve valid YAML and ordering that matches the chosen basket logic.
- Prefer `apply_patch` for single-file edits.
- Preserve UTF-8. If PowerShell output shows mojibake for Japanese names, do not rewrite names based on terminal rendering alone.

7. Validate and report.
- Re-open the file and verify syntax/structure.
- Run a lightweight parser check via the project config loader:
  `uv run python -c "from src.config import load_tickers; print(len(load_tickers()))"`
- Mention that the next `main.py` / watchdog run will treat the updated enabled ticker universe as the source of truth.
- Report final picks, concise rationale, and source links.
- State limitations (no guarantee of returns; data can change quickly).

## Source Quality Rules

- Treat recency as mandatory for financial claims.
- Use absolute dates (for example `2026-02-04`) when summarizing earnings updates.
- Prioritize direct evidence over narrative commentary.
- Prefer facts tied to fundamentals: revenue, operating profit, EPS, guidance, margins, ROE, net cash/debt, buybacks, dividends, backlog/order trends, and capital allocation.
- Avoid unverifiable claims and avoid copying long excerpts.
- Avoid recommending a ticker on "theme" alone unless the thesis is anchored by current disclosed fundamentals.

## Output Contract

When finishing, provide:
- The updated file path.
- Final ticker list in code + name format.
- 1-2 line rationale per sector/theme bucket.
- A link list of sources used.
- Any verification steps that could not be run locally.

## References

- Use `references/selection-framework.md` for scoring weights, thresholds, and diversification rules.
