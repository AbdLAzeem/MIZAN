from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mizan.engine.allocation import Goal, target_allocation
from mizan.engine.types import AssetClass, Instrument, Portfolio

E, F, G = AssetClass.EQUITY, AssetClass.FIXED_INCOME, AssetClass.GOLD


def universe() -> dict[str, Instrument]:
    return {
        "NGEF": Instrument("NGEF", E, Decimal("125.40")),
        "DIBF": Instrument("DIBF", F, Decimal("98.75")),
        "SGLD": Instrument("SGLD", G, Decimal("210.10")),
    }


def test_asset_class_values() -> None:
    assert [c.value for c in AssetClass] == ["equity", "fixed_income", "gold"]


def test_portfolio_values() -> None:
    p = Portfolio(Decimal("100"), {"NGEF": 2, "DIBF": 1}, universe())
    assert p.holdings_value() == Decimal("349.55")
    assert p.total_value() == Decimal("449.55")
    assert p.class_values() == {E: Decimal("250.80"), F: Decimal("98.75"), G: Decimal("0")}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cash": Decimal("-1"), "holdings": {}},
        {"cash": Decimal("0"), "holdings": {"NGEF": -1}},
        {"cash": Decimal("0"), "holdings": {"UNKNOWN": 1}},
    ],
)
def test_portfolio_rejects_invalid_input(kwargs: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValueError):
        Portfolio(kwargs["cash"], kwargs["holdings"], universe())


def test_portfolio_rejects_bad_universe() -> None:
    two_equities = universe() | {"XEQ": Instrument("XEQ", E, Decimal("1"))}
    with pytest.raises(ValueError):
        Portfolio(Decimal("0"), {}, two_equities)
    missing = {k: v for k, v in universe().items() if k != "SGLD"}
    with pytest.raises(ValueError):
        Portfolio(Decimal("0"), {}, missing)
    with pytest.raises(ValueError):
        Instrument("BAD", E, Decimal("0"))


def test_known_allocations() -> None:
    a = target_allocation(Goal.BALANCED, 3, 5)
    assert a == {E: Decimal("36"), F: Decimal("54"), G: Decimal("10")}
    a = target_allocation(Goal.GROWTH, 5, 10)
    assert a == {E: Decimal("67"), F: Decimal("28"), G: Decimal("5")}
    a = target_allocation(Goal.PRESERVE, 1, 1)
    assert a == {E: Decimal("10"), F: Decimal("80"), G: Decimal("10")}
    a = target_allocation(Goal.BALANCED, 5, 5)
    assert a == {E: Decimal("52"), F: Decimal("38"), G: Decimal("10")}
    a = target_allocation(Goal.BALANCED, 5, 10)
    assert a == {E: Decimal("62"), F: Decimal("33"), G: Decimal("5")}


@pytest.mark.parametrize("risk", [0, 6, -1])
def test_invalid_risk(risk: int) -> None:
    with pytest.raises(ValueError):
        target_allocation(Goal.BALANCED, risk, 5)


def test_invalid_horizon() -> None:
    with pytest.raises(ValueError):
        target_allocation(Goal.BALANCED, 3, -1)


@given(st.sampled_from(list(Goal)), st.integers(1, 5), st.integers(0, 60))
def test_allocation_invariants(goal: Goal, risk: int, horizon: int) -> None:
    a = target_allocation(goal, risk, horizon)
    assert set(a) == {E, F, G}
    assert sum(a.values()) == Decimal(100)
    assert Decimal(10) <= a[E] <= Decimal(67)
    assert a[G] in (Decimal(5), Decimal(10))
    assert Decimal(28) <= a[F] <= Decimal(80)
    assert all(v == v.to_integral_value() for v in a.values())


@given(st.sampled_from(list(Goal)), st.integers(1, 4), st.integers(0, 60))
def test_more_risk_never_lowers_equity(goal: Goal, risk: int, horizon: int) -> None:
    assert (
        target_allocation(goal, risk + 1, horizon)[E] >= target_allocation(goal, risk, horizon)[E]
    )
