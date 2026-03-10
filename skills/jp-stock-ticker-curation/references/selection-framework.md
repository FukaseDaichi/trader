# Selection Framework

Use this framework to score fundamentally driven JP stock candidates before editing `tickers.yml`.

## Scoring Model (100 points)

1. Earnings momentum and quality (30 points)
- Check the latest quarterly YoY revenue, operating profit, ordinary profit, and EPS trends.
- Reward acceleration, broad-based growth, and strong progress versus the full-year plan.
- Penalize one-off gains, low-quality margin spikes, or backlog growth that is not converting into profits.

2. Guidance and revision trend (20 points)
- Check current company guidance and any recent upward revisions.
- Reward firms with improving guidance credibility, conservative targets that are repeatedly beaten, or explicit upgrades after strong execution.

3. Valuation rerating room (15 points)
- Compare forward multiples against own history or close peers.
- Reward growth not fully priced in; penalize crowded valuations.

4. Balance sheet and cash generation (15 points)
- Check balance sheet resilience, operating cash flow, free cash flow, leverage, and working-capital quality.
- Reward sustainable capital structure and improving cash conversion.

5. Shareholder return and governance (10 points)
- Check buybacks, dividend policy, payout visibility, and TSE-oriented capital efficiency actions.
- Reward stable or improving return policy and credible governance signals.

6. Fundamental catalyst strength (5 points)
- Check concrete business catalysts: price hikes, capacity additions, contract wins, launches, backlog conversion, capex cycles, or regulation with clear earnings impact.
- Reward catalysts with clear timelines and a direct path into revenue, margin, or cash flow.

7. Risk and liquidity penalty (5 points)
- Penalize customer concentration, regulatory overhang, weak liquidity, heavy cyclical sensitivity, accounting concerns, or event-binary risk.

## Hard Filters

- Require at least one primary source within the last 90 days for each selected ticker.
- Require a thesis anchored by disclosed fundamentals, not only a technical setup or macro narrative.
- Exclude names with obvious balance-sheet distress, unresolved accounting red flags, or clearly stale disclosures unless the user explicitly asks for speculative picks.

## Thresholds

- `80-100`: High conviction candidate.
- `70-79`: Selective inclusion when diversification needs it.
- `<70`: Exclude unless user explicitly requests speculative picks.

## Portfolio Construction Rules

- Keep final basket diversified across at least 3 sectors when possible.
- Avoid allocating more than 40% of final picks to one sector/theme.
- Prefer 5-8 names by default unless user asks for a smaller/larger basket.

## Evidence Rules

- Use at least one primary source per selected ticker.
- Prefer source recency within the last 90 days for core thesis points.
- Write rationale with dates and concrete metrics, not generic narratives.
- Prefer metrics that directly express fundamental improvement: sales growth, operating margin, EPS, ROE, buyback size, dividend growth, net cash/debt, and plan progress.
