"""Current weights, gap calculation, and drift alerts."""

from collections.abc import Mapping
from decimal import Decimal

from mizan.engine.types import AssetClass, Portfolio


def current_weights(portfolio: Portfolio) -> dict[AssetClass, Decimal]:
    """Calculate current percentage weight for each asset class in holdings."""
    total = portfolio.holdings_value()
    if total == Decimal("0"):
        return {ac: Decimal("0") for ac in AssetClass}

    class_vals = portfolio.class_values()
    return {ac: (class_vals[ac] / total) * Decimal("100") for ac in AssetClass}


def compute_gap(
    current: Mapping[AssetClass, Decimal],
    target: Mapping[AssetClass, Decimal],
) -> dict[AssetClass, Decimal]:
    """Calculate gap between current and target weights per asset class."""
    return {ac: current[ac] - target[ac] for ac in AssetClass}


def drift_alerts(
    gap: Mapping[AssetClass, Decimal],
    target: Mapping[AssetClass, Decimal],
    abs_threshold: Decimal = Decimal("5"),
    rel_threshold: Decimal = Decimal("0.25"),
) -> list[AssetClass]:
    """Identify asset classes that exceed drift thresholds."""
    alerts: list[AssetClass] = []
    for ac in AssetClass:
        g = gap[ac]
        t = target[ac]
        abs_gap = abs(g)
        if abs_gap >= abs_threshold or (t > Decimal("0") and abs_gap / t >= rel_threshold):
            alerts.append(ac)
    return alerts
