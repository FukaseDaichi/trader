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
- Source quality: Prefer primary sources (company IR, exchange filings, official disclosures) and include concrete dates in output.
- Output contract: After updates, report changed file paths, selected tickers, concise rationale, and source links.
