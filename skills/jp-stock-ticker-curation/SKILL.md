---
name: jp-stock-ticker-curation
description: Research and curate Japanese stock candidates from up-to-date internet sources, then update tickers.yml in trader repositories. Use when a user asks to add, replace, or shortlist JP tickers based on likely upside, recent earnings, guidance changes, valuation, or sector themes, and expects source-backed rationale plus a direct YAML edit.
---

# JP Stock Ticker Curation

## Overview

Use this skill to run a repeatable workflow for internet-based JP stock selection and safe `tickers.yml` updates.
Prioritize primary sources, date-stamped facts, and explicit tradeoffs over generic stock picks.

## Workflow

1. Confirm local schema and constraints.
- Read `tickers.yml` and repository docs (`README.md` or config loaders).
- Keep existing YAML structure and `settings` unless user asks to change them.
- Detect naming convention (`name` in Japanese or English) and keep it consistent.

2. Gather latest market evidence from the web.
- Use internet browsing for every run; do not rely on memory for "latest" financial information.
- Prefer primary sources: company IR, exchange filings, financial statements, official guidance documents.
- Use secondary sources only to complement coverage gaps.
- Record concrete dates and numbers for each key claim.

3. Build candidate universe and filter quickly.
- Start from liquid JP equities relevant to current themes (for example semiconductors, financials, industrials).
- Exclude names with weak liquidity or stale disclosure.
- Keep a temporary longlist, then narrow with the scoring framework.

4. Score with the framework.
- Read `references/selection-framework.md`.
- Score each candidate on earnings momentum, guidance, valuation, balance sheet quality, shareholder return, catalysts, and risk.
- Drop low-conviction names and keep a balanced sector mix.

5. Select final tickers for `tickers.yml`.
- Choose the final set size from user intent. If unspecified, use 5-8 names.
- Keep portfolio concentration reasonable; avoid one-sector dominance.
- Use ticker codes in `NNNN.JP` format and set `enabled: true` for selected names.

6. Edit `tickers.yml`.
- Update only the `tickers` entries unless the user requests `settings` changes.
- Preserve valid YAML and ordering that matches the chosen basket logic.
- Prefer `apply_patch` for single-file edits.

7. Validate and report.
- Re-open the file and verify syntax/structure.
- If available, run a lightweight parser check via project config loader.
- Report final picks, concise rationale, and source links.
- State limitations (no guarantee of returns; data can change quickly).

## Source Quality Rules

- Treat recency as mandatory for financial claims.
- Use absolute dates (for example `2026-02-04`) when summarizing earnings updates.
- Prioritize direct evidence over narrative commentary.
- Avoid unverifiable claims and avoid copying long excerpts.

## Output Contract

When finishing, provide:
- The updated file path.
- Final ticker list in code + name format.
- 1-2 line rationale per sector/theme bucket.
- A link list of sources used.
- Any verification steps that could not be run locally.

## References

- Use `references/selection-framework.md` for scoring weights, thresholds, and diversification rules.
