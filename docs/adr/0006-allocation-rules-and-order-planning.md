# ADR 0006: Allocation rules and order planning

Status: accepted

## Context

The engine (ADR 0003) must turn three answers (goal, risk tolerance, horizon) into a target
allocation, measure the gap to the current portfolio, and plan draft orders. The rules must be
simple enough to explain, fully deterministic, and written by this project for fictional funds.

## Decision

### Units and types

- Three asset classes: equity, fixed income, gold. Weights are whole percent as `Decimal`
  and always sum to exactly 100.
- Money and prices are `Decimal`. Orders trade whole units only.
- The Phase 1 universe has exactly one instrument per asset class.

### Target allocation

Inputs: goal (preserve, balanced, growth), risk from 1 to 5, horizon in whole years (0 or more).

1. `equity = 20 + 8 * (risk - 1)`
2. Horizon adjustment: under 3 years subtract 10, 10 years or more add 10.
3. Goal adjustment: preserve subtracts 5, growth adds 5.
4. Clamp equity to the range 10 to 70.
5. `gold = 5` if equity is above 60, otherwise `gold = 10`.
6. `fixed_income = 100 - equity - gold`.

Resulting ranges: equity 10 to 67, gold 5 or 10, fixed income 28 to 80. No further
clamping is needed because these ranges always sum correctly.

### Gap and drift

- Current weights are measured on the value of holdings only (cash excluded). An empty
  portfolio has all weights equal to 0.
- Gap per class is `current - target` in percentage points. Positive means overweight.
- A class drifts when the absolute gap is at least 5 points, or the absolute gap divided by
  the target weight is at least 0.25. Both thresholds are parameters with these defaults.

### Order planning

Total value is holdings plus cash. The target value of a class is total value times its
weight divided by 100. Two modes:

- **cash flow**: buy only. Spend cash on underweight classes, largest deficit first.
- **rebalance**: first sell overweight classes, then buy underweight classes with cash plus
  the sale proceeds.

Rules for both modes:

- Units are rounded down, so a trade never overshoots the target of its class.
- Orders below the minimum order value (default 10) are dropped.
- Cash never goes negative, and nothing is sold that is not held.
- Sells come first, then buys, each sorted by symbol. The result is deterministic.

## Consequences

- The rules fit on one page and can be explained to a user in a sentence each.
- Rounding down leaves a small amount of cash unspent, which is accepted.
- Property tests can state strong invariants: weights sum to 100, cash is never negative,
  total value is conserved, and no class moves further from its target.
