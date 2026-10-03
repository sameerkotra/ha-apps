# Recommendation Engine

## Principle

Recommendations are deterministic facts plus optional LLM wording.

## Inputs

- approved price observations
- common items
- store locations
- purchase frequency
- observation dates
- comparable unit prices

## Candidate process

1. Find frequently purchased items.
2. Restrict to a configurable recent window, default 90 days.
3. Normalize compatible units.
4. Group observations by store location.
5. Calculate representative price.
6. Compare locations.
7. Require minimum savings threshold.
8. Calculate confidence.
9. Store evidence.
10. Generate explanation asynchronously.

## Freshness

Example:

```text
0-7 days    1.00
8-30 days   0.85
31-60 days  0.65
61-90 days  0.40
90+ days    0.15
```

## Confidence

Example:

```text
observation_factor =
    min(observation_count / 5, 1)

confidence =
    observation_factor
    * freshness_factor
    * consistency_factor
```

Keep formulas configurable.

## Explanation rules

The LLM receives calculated values and may only explain them.

It must not:
- recalculate savings
- invent a price
- claim current availability
- claim current promotion
- turn historical data into a current claim

## Evidence

Every recommendation must reference one or more:
- price observation
- receipt
- historical average
- tax observation
- purchase frequency record

Recommendations without evidence are invalid.

## What is implemented (v1)

The calculations live in `app/services/analytics.py` and are computed on request from the last
`RECOMMENDATION_WINDOW_DAYS` days of price observations. Nothing is stored and no LLM is involved,
so the "explanation rules" above are met trivially. Where this differs from the design above:
freshness is a simple linear decay rather than the stepped table, and there is no consistency factor.

| Suggestion | Rule |
| --- | --- |
| **Cheaper elsewhere** (`switch_store`) | For an item bought at two or more store locations: the *usual* location is the one with the most receipts; the *best* is the one with the lowest latest price. Suggested when the usual location costs at least `MINIMUM_SAVINGS_PERCENT` more. `est_monthly_savings` = price gap x average units per purchase x purchases per 30 days |
| **Confidence** | `min(1, observations at both stores / 6) x freshness`, where freshness falls linearly from 1 to 0.2 as the cheaper price ages across the window |
| **Price alerts** | The newest price at a store versus the average of that store's earlier prices for the item (at least two earlier, newest within 30 days). Flagged at 10% or more, up or down |
| **Restock** | Items bought on at least three distinct days. Due once days since the last purchase reach the average gap; overdue at 1.5x; dropped beyond 3x (probably no longer bought) |
| **Store scorecard** | Over items sold at two or more chains, how often each chain had the lowest latest price, and its average premium over the cheapest |
| **Price trend** | First versus last calendar month's average price (or first versus last price within one month); under 3% is "flat" |

Every result carries its evidence (store labels, prices, dates, receipt counts). These thresholds are
sensible defaults, not tuned against real shopping data. Estimates say "about", and low confidence is
shown as an early hint.

## Daily job

`app/services/scheduler.py` starts with the app. At start-up it checks any home with no result or
one older than 23 hours (so an app that was off overnight catches up), then it runs for all homes
every day at `RECOMMENDATION_RUN_HOUR` (default 3, server local time; set `TZ` for another zone).
`app/services/deals.py` does the work per home in its own transaction, so one home failing never stops
the others. It stores the result of `analytics.recommendations` plus `analytics.cheapest_by_item`
(every item's cheapest store over the window, including items bought at only one store).
The Insights "Savings" tab still calculates live; the Best prices page shows the stored daily result.
