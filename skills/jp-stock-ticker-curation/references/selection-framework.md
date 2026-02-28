# Selection Framework

Use this framework to score JP stock candidates before editing `tickers.yml`.

## Scoring Model (100 points)

1. Earnings momentum (25 points)
- Check latest quarterly YoY revenue and operating profit trends.
- Reward acceleration and strong progress versus full-year plan.

2. Guidance and revision trend (20 points)
- Check current company guidance and any recent upward revisions.
- Reward firms with improving guidance credibility.

3. Valuation attractiveness (15 points)
- Compare forward multiples against own history or close peers.
- Reward growth not fully priced in; penalize crowded valuations.

4. Financial quality (15 points)
- Check balance sheet resilience, cash flow quality, and leverage risk.
- Reward sustainable capital structure.

5. Shareholder return policy (10 points)
- Check buybacks, dividend policy, and payout visibility.
- Reward stable or improving return policy.

6. Near-term catalyst strength (10 points)
- Check concrete upcoming catalysts: launches, capex cycles, regulation, contracts.
- Reward catalysts with clear timelines.

7. Risk and liquidity penalty (5 points)
- Penalize regulatory overhang, one-off dependence, weak liquidity, or event binary risk.

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
