# Selection Framework (pointer)

The canonical scoring rubric lives in
`.agents/skills/jp-stock-fundamental-screen/references/selection-framework.md`.
**Read that file and score against it.** It owns the 100-point model
(earnings 30 / guidance 20 / valuation 15 / balance sheet 15 / shareholder
return 10 / catalyst 5 / risk penalty -5), the hard filters, the thresholds, the
diversification rules, and the evidence rules. Do not restate or fork them here.

Why it is shared: this skill and `jp-stock-fundamental-screen` both write the
same `fundamental_latest.json` contract into the same `--fundamental` input of
`scripts/curation_merge.py`, and that merge ranks every candidate on one
`combined = tech_weight x tech + fund_weight x fund` scale. A rubric difference
between the two producers becomes a scoring difference inside a single ranking —
the macro tilt alone spans up to 10 fundamental points, which at the current
`fund_weight` is enough to cross `min_gap` and flip a swap decision.

## The one interactive-path difference

The weekly agent always has the macro cache in hand because the workflow runs
the macro agent immediately before it. This skill is invoked on demand, so the
cache may be absent or stale:

- `Read` `docs/curation/macro_latest.json` before scoring. If it exists and its
  `as_of` is within ~14 days, apply the macro tilt exactly as the canonical
  rubric describes — inside `catalyst` (0..5) and `risk_penalty` (-5..0) only,
  never as a new subscore, and every macro claim traced to a source in that file.
- If it is missing, empty, or older than ~14 days, score **without** the macro
  tilt and say so in the report. Do not substitute macro facts gathered from
  your own browsing for the cache, and do not invent them — the tilt is only
  auditable when it traces back to `macro_latest.json`.
