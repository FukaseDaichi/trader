# Design: AI-assisted curation pool refresh

**Date:** 2026-06-16
**Status:** Approved (design)
**Author:** brainstorming session (fukase + Claude)

## Problem

`curation_pool.yml` is the candidate母集団 (currently ~50 liquid large-cap JP
names) from which the daily curation swaps names into `tickers.yml`. It is the
**ceiling of the whole system**: a name that is not in the pool can never become
an enabled signal. Today the pool is maintained by hand and only "refreshed
roughly weekly" in practice — there is no automated process that reviews it.

The daily layer already handles short-term price action (technical screen →
`curation_merge`). What is missing is a slower, fundamentals-driven review of
the母集団 itself.

## Goal

Add a weekly AI-assisted review of `curation_pool.yml` that:

1. **Bootstrap phase:** grows the pool from ~50 toward a target of 60–80 names
   by *adding* fundamentally strong, liquid candidates (add-only).
2. **Steady-state phase:** once the pool reaches its target size, maintains a
   constant total by replacing names (drop one, add one), enabled only after
   the add-only phase has proven out.

The selection axis is **fundamentals + liquidity** — long-term "worth holding,
liquid large-cap" quality. Short-term technicals stay in the daily layer and
are explicitly out of scope here.

## Non-goals

- No change to the daily curation, KPI gate, signal generation, or
  notifications. The pool's *contents* change; downstream scoring over
  `pool ∪ enabled ∪ watchlist` picks up new members unchanged.
- No human approval gate on pool changes (consistent with the existing
  "AI proposes JSON / deterministic script decides" pattern). Changes are
  visible via audit JSON + LINE notification, not blocked.
- No drop logic in the first rollout (add-only). Drop/replace is a later,
  config-gated enablement.

## Big decisions (settled)

| Decision | Resolution |
|---|---|
| Structure | Dedicated skill + dedicated deterministic merge + weekly workflow step (mirrors `technical_screen → curation_merge`) |
| Write authority | AI emits proposal JSON only; deterministic script is the **only** writer of `curation_pool.yml` |
| Selection axis | Fundamentals + liquidity (no short-term technicals) |
| Pool size | Grow 50 → target 60–80; auto-flip to replace-only once `len(pool) >= pool_target_size` |
| Enabled protection | Names currently enabled in `tickers.yml` are never drop candidates |
| Notification | LINE message summarizing weekly pool adds/drops; no blocking human gate |
| Rollout | Add-only first; drop/replace gated behind a config flag, enabled later |

## Architecture (A案)

```
weekly-fundamental-report.yml (Saturday, JST)
────────────────────────────────────────────
[existing] curation_warmup.py        refresh pool+watchlist price history
        │
[new]  /jp-stock-pool-screen  (AI skill, Claude)
         inputs:  current pool + enabled + per-candidate liquidity/fundamental
                  metrics + (optional) global-macro/fundamental screen JSON
         output:  docs/curation/pool_candidates_latest.json  (proposal only)
        │
[new]  scripts/curation_pool_merge.py  (deterministic)
         guardrails: liquidity floor / churn cap / sector cap / pool target /
                     cooldown / enabled-protection / add-only-vs-replace mode
         output:  curation_pool.yml  (rewritten, only when changed)
                  docs/curation/pool_decision_<DATE>.json  (audit)
        │
[existing] commit-and-push.sh  →  push curation_pool.yml + docs/curation
        │
(next trading day onward) existing daily curation_warmup → curation_merge
  treat the new pool as母集団. No code change downstream.
```

### Why this fits

The repo's iron rule is "an LLM never writes the universe file; a deterministic
script does." This design applies the same pattern one level up (→ pool):

- `technical_screen → technical_latest.json → curation_merge → tickers.yml`
- `pool_screen     → pool_candidates_latest.json → curation_pool_merge → curation_pool.yml`

## Components

### 1. Skill: `.claude/skills/jp-stock-pool-screen/`
- Research fundamentally strong, liquid JP large-caps from primary sources
  (IR, filings) plus the per-candidate metrics provided as input.
- Emits `docs/curation/pool_candidates_latest.json` only. Never edits
  `curation_pool.yml`, `tickers.yml`, or any other state.
- May reuse `docs/curation/fundamental_latest.json` and
  `docs/curation/macro_latest.json` (already produced earlier in the same
  weekly workflow) as supporting context.
- Mirrors the conventions of the existing `jp-stock-fundamental-screen` skill
  (schema-stable JSON, source-backed rationale, dates).

### 2. Script: `scripts/curation_pool_merge.py`
- Pure-logic core in a testable module (mirror `src/universe.py` ↔
  `universe_select.py` split, or keep logic inline if small — implementer's
  call). No LLM.
- Reads: `pool_candidates_latest.json`, current `curation_pool.yml`, current
  enabled set from `tickers.yml`, local parquet liquidity for candidates.
- Applies guardrails (below) and rewrites `curation_pool.yml` **only when the
  pool actually changes** (idempotent; identical inputs → identical file,
  no churn of comments/order beyond the change).
- Writes `docs/curation/pool_decision_latest.json` + dated copy with the full
  ranking, each candidate's action (`add`/`drop`/`keep`/`reject`) and the
  reason (which guardrail fired).
- `--apply` / `--dry-run` flags, matching `curation_merge.py` and
  `universe_select.py`.

### 3. Guardrails (config in `tickers.yml` → `settings.curation.pool`)
Proposed knobs (concrete defaults — implementer may tune):

```yaml
settings:
  curation:
    pool:
      enabled: true
      pool_target_size: 60        # grow toward this, then maintain
      pool_max_size: 80           # hard cap, never exceed
      max_weekly_adds: 3
      max_weekly_drops: 0         # 0 = add-only (rollout phase 1)
      min_fund_score_to_add: 70   # AI score floor for new pool entries
      liquidity_floor_jpy: ...    # min median daily turnover (large-cap gate)
      pool_sector_cap_pct: 40     # reuse the diversification idea
      pool_cooldown_days: 30      # a dropped name can't re-enter for N days
```

- **Add-only ↔ replace auto-flip:** while `len(pool) < pool_target_size`, net
  adds are allowed up to `max_weekly_adds`. Once `len(pool) >= pool_target_size`,
  every add must be paired with a drop (replace-only) and total stays constant.
  Phase-1 rollout keeps `max_weekly_drops: 0`, so the pool simply grows to
  target and then holds until drops are enabled.
- **Enabled protection:** any code currently enabled in `tickers.yml` is removed
  from the drop-candidate set before ranking.
- **Liquidity floor:** computed from local parquet (median daily turnover);
  candidates below the floor are rejected regardless of AI score.
- **Sector cap / cooldown / size cap:** deterministic, same spirit as the
  existing daily guardrails.

### 4. Warmup
No new mechanism needed. The existing `scripts/curation_warmup.py` already
downloads/refreshes parquet for all `pool ∪ watchlist` names not currently
enabled, and `src/data_loader.update_data` backfills full multi-year history
from Stooq on first fetch. So a name added to the pool on Saturday has its full
history available at the next daily warmup → promotable by the normal
`min_warmup_rows: 200` gate without a cold start. The weekly workflow runs
warmup *before* the pool screen so the AI sees fresh metrics; newly-added names
are warmed on the following daily run.

### 5. Workflow integration
Add two steps to `.github/workflows/weekly-fundamental-report.yml` (Saturday),
after the existing fundamental/macro agents:
1. Run `/jp-stock-pool-screen`.
2. Run `scripts/curation_pool_merge.py --apply` (guarded by JPX-open /
   idempotency consistent with the other curation steps).
Then extend the existing commit step to include `curation_pool.yml`.

### 6. Notification
After a successful pool change, send a LINE message via the existing
`src/notifier.py` summarizing the week's pool adds/drops (names + one-line
reason). Best-effort, retry-bounded, never blocks the run. Reuse the weekly
notify step pattern already in the workflow.

## Data contracts

### `docs/curation/pool_candidates_latest.json` (AI output)
```jsonc
{
  "schema_version": 1,
  "agent": "pool",
  "model": "claude-sonnet-4-6",
  "generated_at": "<ISO+09:00>",
  "as_of": "<DATE>",
  "candidates": [
    {
      "code": "NNNN.JP",
      "name": "...",
      "sector": "...",
      "action_hint": "add" | "drop" | "keep",
      "fund_score": 0-100,
      "liquidity_jpy": <number>,        // median daily turnover
      "rationale": "...",
      "sources": ["https://..."]
    }
  ]
}
```

### `docs/curation/pool_decision_<DATE>.json` (deterministic audit)
Mirrors `decision_latest.json`: `schema_version`, `date`, `applied`,
`pool_written` (bool), inputs, the resolved mode (`grow` | `replace`), and a
`ranking` array where each entry carries `code`, `name`, `sector`, `fund_score`,
`liquidity_jpy`, `in_pool_before`, `action`, and `reason` (the guardrail that
decided it).

Both files live under `docs/curation/`, which is **already** in the
`daily-publish-dashboard.yml` `--exclude` list (`--exclude 'curation'`), so no
publish-workflow change is required.

## Error handling / safety
- Missing/invalid `pool_candidates_latest.json` → merge is a no-op, logs, exits
  0 (don't break the weekly workflow).
- Daily signal run is **never** touched by this feature; pool changes only
  affect the *contents* of the candidate set the daily run already reads.
- All writes go through the deterministic merge; the AI step has no write access
  to `curation_pool.yml` or `tickers.yml`.
- Idempotent: re-running with the same inputs produces an identical
  `curation_pool.yml`.

## Testing
- `tests/test_curation_pool_merge.py` (plain script, no pytest — matches repo
  convention), covering:
  - grow mode adds up to `max_weekly_adds`, never exceeds `pool_max_size`;
  - replace mode keeps total constant (drops gated by `max_weekly_drops`);
  - add-only phase (`max_weekly_drops: 0`) never drops;
  - enabled names are never dropped;
  - liquidity floor / sector cap / cooldown rejections;
  - idempotency (same input → byte-identical pool file);
  - malformed/empty proposal → no-op.
- Reuse/extend `tests/test_curation_merge.py` patterns.

## Rollout
1. Land skill + merge script + tests with `max_weekly_drops: 0` (add-only),
   `pool_target_size: 60`.
2. Run a few weekly cycles in `--dry-run` (or observe `pool_decision` audits)
   to confirm the AI's adds and the liquidity floor behave.
3. Let the pool grow to target over several Saturdays.
4. Once add-only is trusted and drop logic is reviewed, enable
   `max_weekly_drops` (replace-only kicks in automatically at target size).

## Open implementation details (delegated)
- Exact `liquidity_floor_jpy` value and how median turnover is computed.
- Whether to split pure logic into `src/curation_pool.py` vs inline.
- Exact LINE message wording.
- Final churn numbers (`max_weekly_adds`, cooldown days).
These are tuning/structure choices left to the implementation plan; none change
the architecture above.
