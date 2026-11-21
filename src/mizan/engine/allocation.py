"""Target allocation rules for goals, risk tolerance, and time horizons."""

from decimal import Decimal
from enum import StrEnum

from mizan.engine.types import AssetClass


class Goal(StrEnum):
    """Investment goal."""

    PRESERVE = "preserve"
    BALANCED = "balanced"
    GROWTH = "growth"


def target_allocation(goal: Goal, risk: int, horizon_years: int) -> dict[AssetClass, Decimal]:
    """Calculate target asset class weights from goal, risk, and horizon."""
    if risk < 1 or risk > 5:
        raise ValueError(f"Risk must be between 1 and 5, got {risk}")
    if horizon_years < 0:
        raise ValueError(f"Horizon years cannot be negative, got {horizon_years}")

    equity = 20 + 8 * (risk - 1)

    if horizon_years < 3:
        equity -= 10
    elif horizon_years >= 10:
        equity += 10

    if goal == Goal.PRESERVE:
        equity -= 5
    elif goal == Goal.GROWTH:
        equity += 5
    elif goal != Goal.BALANCED:
        raise ValueError(f"Unknown goal: {goal}")

    equity = max(10, min(70, equity))

    gold = 5 if equity > 60 else 10
    fixed_income = 100 - equity - gold

    return {
        AssetClass.EQUITY: Decimal(equity),
        AssetClass.FIXED_INCOME: Decimal(fixed_income),
        AssetClass.GOLD: Decimal(gold),
    }
