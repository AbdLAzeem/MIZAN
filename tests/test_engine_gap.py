from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from mizan.engine.allocation import Goal, target_allocation
from mizan.engine.gap import compute_gap, current_weights, drift_alerts
from mizan.engine.types import AssetClass, Instrument, Portfolio

E, F, G = AssetClass.EQUITY, AssetClass.FIXED_INCOME, AssetClass.GOLD
UNIVERSE = {
    "NGEF": Instrument("NGEF", E, Decimal("125.40")),
    "DIBF": Instrument("DIBF", F, Decimal("98.75")),
    "SGLD": Instrument("SGLD", G, Decimal("210.10")),
}
TOL = Decimal("1e-20")


def test_empty_portfolio_has_zero_weights() -> None:
    p = Portfolio(Decimal("500"), {}, UNIVERSE)
    assert current_weights(p) == {E: Decimal(0), F: Decimal(0), G: Decimal(0)}


def test_weights_exclude_cash() -> None:
    p = Portfolio(Decimal("9999"), {"NGEF": 1, "DIBF": 1}, UNIVERSE)
    w = current_weights(p)
    assert abs(w[E] - Decimal("125.40") / Decimal("224.15") * 100) < TOL
    assert w[G] == Decimal(0)
    assert abs(sum(w.values()) - 100) < TOL


def test_gap_is_current_minus_target() -> None:
    cur = {E: Decimal("50"), F: Decimal("40"), G: Decimal("10")}
    tgt = {E: Decimal("36"), F: Decimal("54"), G: Decimal("10")}
    assert compute_gap(cur, tgt) == {E: Decimal("14"), F: Decimal("-14"), G: Decimal("0")}


def test_drift_absolute_threshold() -> None:
    tgt = {E: Decimal("50"), F: Decimal("40"), G: Decimal("10")}
    gap = {E: Decimal("5"), F: Decimal("-4.9"), G: Decimal("-0.1")}
    assert drift_alerts(gap, tgt) == [E]


def test_drift_relative_threshold() -> None:
    tgt = {E: Decimal("50"), F: Decimal("40"), G: Decimal("10")}
    gap = {E: Decimal("0"), F: Decimal("0"), G: Decimal("2.5")}
    assert drift_alerts(gap, tgt) == [G]
    gap = {E: Decimal("0"), F: Decimal("0"), G: Decimal("2.4")}
    assert drift_alerts(gap, tgt) == []


def test_drift_custom_thresholds_and_order() -> None:
    tgt = {E: Decimal("50"), F: Decimal("40"), G: Decimal("10")}
    gap = {E: Decimal("-3"), F: Decimal("3"), G: Decimal("0")}
    assert drift_alerts(gap, tgt, abs_threshold=Decimal("3"), rel_threshold=Decimal("9")) == [E, F]


def test_zero_target_uses_only_absolute_threshold() -> None:
    tgt = {E: Decimal("0"), F: Decimal("100"), G: Decimal("0")}
    assert drift_alerts({E: Decimal("1"), F: Decimal("0"), G: Decimal("0")}, tgt) == []
    assert drift_alerts({E: Decimal("5"), F: Decimal("0"), G: Decimal("0")}, tgt) == [E]


@given(
    st.integers(0, 40),
    st.integers(0, 40),
    st.integers(0, 40),
    st.sampled_from(list(Goal)),
    st.integers(1, 5),
    st.integers(0, 30),
)
def test_gap_properties(ne: int, nf: int, ng: int, goal: Goal, risk: int, years: int) -> None:
    p = Portfolio(Decimal("0"), {"NGEF": ne, "DIBF": nf, "SGLD": ng}, UNIVERSE)
    cur = current_weights(p)
    assert set(cur) == {E, F, G}
    assert abs(sum(cur.values()) - (100 if p.holdings_value() > 0 else 0)) < TOL
    gap = compute_gap(cur, target_allocation(goal, risk, years))
    assert abs(sum(gap.values()) - (sum(cur.values()) - 100)) < TOL
