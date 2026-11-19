from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mizan.engine.allocation import Goal, target_allocation
from mizan.engine.planner import Mode, Order, OrderPlan, Side, apply_orders, plan_orders
from mizan.engine.types import AssetClass, Instrument, Portfolio

E, F, G = AssetClass.EQUITY, AssetClass.FIXED_INCOME, AssetClass.GOLD
PRICES = {"NGEF": Decimal("125.40"), "DIBF": Decimal("98.75"), "SGLD": Decimal("210.10")}
CLASSES = {"NGEF": E, "DIBF": F, "SGLD": G}
UNIVERSE = {s: Instrument(s, CLASSES[s], PRICES[s]) for s in PRICES}
TARGET = target_allocation(Goal.BALANCED, 3, 5)  # 36 / 54 / 10


def portfolio(cash: str, ne: int, nf: int, ng: int) -> Portfolio:
    return Portfolio(Decimal(cash), {"NGEF": ne, "DIBF": nf, "SGLD": ng}, UNIVERSE)


def test_order_value() -> None:
    assert Order("DIBF", Side.BUY, 10, Decimal("98.75")).value == Decimal("987.50")


def test_cash_flow_known_case() -> None:
    plan = plan_orders(portfolio("1000", 10, 5, 1), TARGET, Mode.CASH_FLOW)
    assert plan == OrderPlan(
        Mode.CASH_FLOW,
        (Order("DIBF", Side.BUY, 10, Decimal("98.75")),),
        Decimal("12.50"),
    )


def test_rebalance_known_case() -> None:
    plan = plan_orders(portfolio("1000", 10, 5, 1), TARGET, Mode.REBALANCE)
    assert plan.orders == (
        Order("NGEF", Side.SELL, 1, Decimal("125.40")),
        Order("DIBF", Side.BUY, 11, Decimal("98.75")),
    )
    assert plan.cash_after == Decimal("39.15")


def test_no_cash_and_balanced_portfolio_gives_empty_plan() -> None:
    plan = plan_orders(portfolio("0", 0, 0, 0), TARGET, Mode.CASH_FLOW)
    assert plan.orders == () and plan.cash_after == Decimal("0")


def test_min_order_value_drops_small_orders() -> None:
    plan = plan_orders(
        portfolio("1000", 10, 5, 1), TARGET, Mode.REBALANCE, min_order_value=Decimal("200")
    )
    assert plan.orders == (Order("DIBF", Side.BUY, 10, Decimal("98.75")),)


def test_apply_orders_updates_cash_and_holdings() -> None:
    p = portfolio("1000", 10, 5, 1)
    plan = plan_orders(p, TARGET, Mode.REBALANCE)
    after = apply_orders(p, plan.orders)
    assert after.cash == plan.cash_after
    assert dict(after.holdings) == {"NGEF": 9, "DIBF": 16, "SGLD": 1}


def test_apply_orders_rejects_overspend_and_oversell() -> None:
    p = portfolio("100", 1, 0, 0)
    with pytest.raises(ValueError):
        apply_orders(p, (Order("SGLD", Side.BUY, 1, Decimal("210.10")),))
    with pytest.raises(ValueError):
        apply_orders(p, (Order("NGEF", Side.SELL, 2, Decimal("125.40")),))
    with pytest.raises(ValueError):
        apply_orders(p, (Order("NOPE", Side.BUY, 1, Decimal("1")),))


@given(
    st.integers(0, 60),
    st.integers(0, 60),
    st.integers(0, 60),
    st.integers(0, 500_000).map(lambda c: Decimal(c) / 100),
    st.sampled_from(list(Goal)),
    st.integers(1, 5),
    st.integers(0, 30),
    st.sampled_from(list(Mode)),
    st.sampled_from([Decimal("0"), Decimal("10"), Decimal("100")]),
)
def test_planner_invariants(
    ne: int,
    nf: int,
    ng: int,
    cash: Decimal,
    goal: Goal,
    risk: int,
    years: int,
    mode: Mode,
    min_value: Decimal,
) -> None:
    p = Portfolio(cash, {"NGEF": ne, "DIBF": nf, "SGLD": ng}, UNIVERSE)
    target = target_allocation(goal, risk, years)
    plan = plan_orders(p, target, mode, min_order_value=min_value)
    after = apply_orders(p, plan.orders)

    assert plan == plan_orders(p, target, mode, min_order_value=min_value)  # deterministic
    assert after.cash == plan.cash_after >= 0
    assert after.total_value() == p.total_value()  # value is conserved
    assert all(o.units > 0 and o.value >= min_value for o in plan.orders)
    sides = [o.side for o in plan.orders]
    assert sides == sorted(sides, key=lambda s: s != Side.SELL)  # sells before buys
    if mode is Mode.CASH_FLOW:
        assert all(o.side is Side.BUY for o in plan.orders)

    total = p.total_value()
    for cls in AssetClass:
        goal_value = total * target[cls] / 100
        before_gap = abs(p.class_values()[cls] - goal_value)
        after_gap = abs(after.class_values()[cls] - goal_value)
        assert after_gap <= before_gap  # no class moves further from its target
