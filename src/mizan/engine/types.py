"""Engine domain types for instruments, asset classes, and portfolios."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class AssetClass(StrEnum):
    """Asset class categories."""

    EQUITY = "equity"
    FIXED_INCOME = "fixed_income"
    GOLD = "gold"


@dataclass(frozen=True)
class Instrument:
    """Financial instrument with symbol, asset class, and price."""

    symbol: str
    asset_class: AssetClass
    price: Decimal

    def __post_init__(self) -> None:
        if self.price <= Decimal("0"):
            raise ValueError(f"Price must be greater than zero, got {self.price}")


@dataclass(frozen=True)
class Portfolio:
    """Portfolio containing cash, holdings, and universe instruments."""

    cash: Decimal
    holdings: Mapping[str, int]
    instruments: Mapping[str, Instrument]

    def __post_init__(self) -> None:
        if self.cash < Decimal("0"):
            raise ValueError(f"Cash cannot be negative, got {self.cash}")
        if any(units < 0 for units in self.holdings.values()):
            raise ValueError("Holdings units cannot be negative")
        for symbol in self.holdings:
            if symbol not in self.instruments:
                raise ValueError(f"Holdings symbol '{symbol}' not found in instruments")
        counts: dict[AssetClass, int] = {ac: 0 for ac in AssetClass}
        for inst in self.instruments.values():
            if inst.asset_class in counts:
                counts[inst.asset_class] += 1
            else:
                raise ValueError(f"Unknown asset class: {inst.asset_class}")
        if len(self.instruments) != len(AssetClass) or any(count != 1 for count in counts.values()):
            raise ValueError("Instruments must contain exactly one instrument for each asset class")

    def holdings_value(self) -> Decimal:
        """Return the sum of units times price across all holdings."""
        return sum(
            (
                Decimal(units) * self.instruments[symbol].price
                for symbol, units in self.holdings.items()
            ),
            Decimal("0"),
        )

    def total_value(self) -> Decimal:
        """Return total portfolio value including cash."""
        return self.holdings_value() + self.cash

    def class_values(self) -> dict[AssetClass, Decimal]:
        """Return the total value of holdings for each asset class."""
        values: dict[AssetClass, Decimal] = {ac: Decimal("0") for ac in AssetClass}
        for symbol, units in self.holdings.items():
            inst = self.instruments[symbol]
            values[inst.asset_class] += Decimal(units) * inst.price
        return values
