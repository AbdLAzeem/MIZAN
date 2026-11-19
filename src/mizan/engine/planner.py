"""Order planner for rebalancing and cash flow allocation."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

from mizan.engine.types import AssetClass, Instrument, Portfolio


class Mode(StrEnum):
    """Order planning mode."""

    CASH_FLOW = "cash_flow"
    REBALANCE = "rebalance"


class Side(StrEnum):
    """Order side: buy or sell."""

    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Order:
    """An order to buy or sell whole units of an instrument."""

    symbol: str
    side: Side
    units: int
    price: Decimal

    @property
    def value(self) -> Decimal:
        """Total value of the order."""
        return Decimal(self.units) * self.price


@dataclass(frozen=True)
class OrderPlan:
    """A generated plan of orders and the remaining cash balance."""

    mode: Mode
    orders: tuple[Order, ...]
    cash_after: Decimal


def plan_orders(
    portfolio: Portfolio,
    target: Mapping[AssetClass, Decimal],
    mode: Mode,
    *,
    min_order_value: Decimal = Decimal("10"),
) -> OrderPlan:
    """Plan orders to move portfolio toward target allocation."""
    total = portfolio.total_value()
    class_vals = portfolio.class_values()

    inst_by_class: dict[AssetClass, Instrument] = {
        inst.asset_class: inst for inst in portfolio.instruments.values()
    }

    diffs: dict[AssetClass, Decimal] = {}
    for c in AssetClass:
        goal_value = total * target[c] / Decimal("100")
        diffs[c] = goal_value - class_vals[c]

    available = portfolio.cash
    sell_orders: list[Order] = []
    buy_orders: list[Order] = []

    if mode is Mode.REBALANCE:
        for c in AssetClass:
            diff = diffs[c]
            if diff < Decimal("0"):
                inst = inst_by_class[c]
                price = inst.price
                units_held = portfolio.holdings.get(inst.symbol, 0)
                raw_units = (-diff / price).to_integral_value(rounding=ROUND_FLOOR)
                units = min(units_held, int(raw_units))
                if units > 0:
                    order = Order(inst.symbol, Side.SELL, units, price)
                    if order.value >= min_order_value:
                        sell_orders.append(order)
                        available += order.value

    # Underweight classes for buying: diff > 0, sorted by diff descending, ties in enum order
    # AssetClass list order provides standard tie-breaking index
    enum_order = {ac: idx for idx, ac in enumerate(AssetClass)}
    buy_classes = [c for c in AssetClass if diffs[c] > Decimal("0")]
    buy_classes.sort(key=lambda c: (-diffs[c], enum_order[c]))

    for c in buy_classes:
        diff = diffs[c]
        inst = inst_by_class[c]
        price = inst.price
        budget = min(diff, available)
        raw_units = (budget / price).to_integral_value(rounding=ROUND_FLOOR)
        units = int(raw_units)
        if units > 0:
            order = Order(inst.symbol, Side.BUY, units, price)
            if order.value >= min_order_value:
                buy_orders.append(order)
                available -= order.value

    sell_orders.sort(key=lambda o: o.symbol)
    buy_orders.sort(key=lambda o: o.symbol)

    all_orders = tuple(sell_orders + buy_orders)
    return OrderPlan(mode=mode, orders=all_orders, cash_after=available)


def apply_orders(portfolio: Portfolio, orders: Iterable[Order]) -> Portfolio:
    """Apply orders sequentially to create a new updated portfolio."""
    cash = portfolio.cash
    holdings = dict(portfolio.holdings)

    for order in orders:
        if order.symbol not in portfolio.instruments:
            raise ValueError(f"Unknown symbol '{order.symbol}' in order")

        inst = portfolio.instruments[order.symbol]
        if order.price != inst.price:
            raise ValueError(
                f"Order price {order.price} does not match instrument price {inst.price}"
            )

        order_val = order.value
        current_units = holdings.get(order.symbol, 0)

        if order.side is Side.BUY:
            if cash < order_val:
                raise ValueError(f"Insufficient cash {cash} for buy order value {order_val}")
            cash -= order_val
            holdings[order.symbol] = current_units + order.units
        elif order.side is Side.SELL:
            if current_units < order.units:
                raise ValueError(
                    f"Cannot sell {order.units} units of {order.symbol}, only {current_units} held"
                )
            cash += order_val
            new_units = current_units - order.units
            if new_units == 0:
                holdings.pop(order.symbol, None)
            else:
                holdings[order.symbol] = new_units
        else:
            raise ValueError(f"Unknown order side {order.side}")

    return Portfolio(cash=cash, holdings=holdings, instruments=portfolio.instruments)
