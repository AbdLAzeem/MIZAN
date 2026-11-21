import json
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from conftest import Account
from mizan.agent.knowledge import Passage
from mizan.agent.tools import ToolContext, ToolOutcome, execute_tool, tool_specs
from mizan.agent.types import ToolCall
from mizan.db import Connection

HOLDINGS = {"NGEF": 10, "DIBF": 5, "SGLD": 1}
MakeAccount = Callable[..., Account]


def ctx_for(conn: Connection, a: Account, **kw: Any) -> ToolContext:
    return ToolContext(conn, a.user_id, a.account_id, **kw)


def call(ctx: ToolContext, name: str, **args: Any) -> ToolOutcome:
    return execute_tool(ctx, ToolCall("c1", name, args))


def data(out: ToolOutcome) -> dict[str, Any]:
    assert out.ok, out.error
    return json.loads(out.content)["data"]  # type: ignore[no-any-return]


def count(conn: Connection, table: str) -> int:
    r = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()  # type: ignore[arg-type]
    assert r is not None
    return int(r["n"])


def test_exactly_six_tools_and_none_can_confirm() -> None:
    specs = tool_specs()
    assert [s.name for s in specs] == [
        "get_portfolio",
        "get_target_allocation",
        "compute_gap",
        "prepare_orders",
        "cancel_draft",
        "search_knowledge",
    ]
    assert all(s.description for s in specs)
    assert "token" not in json.dumps([s.parameters for s in specs])
    assert not any("confirm" in s.name or "execute" in s.name for s in specs)


def test_get_portfolio(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    d = data(call(ctx_for(conn, a), "get_portfolio"))
    assert d["cash"] == "1000.00" and d["total_value"] == "2957.85"
    assert d["holdings_value"] == "1957.85"
    assert [h["symbol"] for h in d["holdings"]] == ["DIBF", "NGEF", "SGLD"]
    assert d["holdings"][0] == {"symbol": "DIBF", "units": 5, "price": "98.75", "value": "493.75"}
    assert set(d["weights"]) == {"equity", "fixed_income", "gold"}
    assert all(len(v.split(".")[1]) == 2 for v in d["weights"].values())


def test_get_target_allocation(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    d = data(call(ctx_for(conn, a), "get_target_allocation"))
    assert (d["goal"], d["risk"], d["horizon_years"]) == ("balanced", 3, 5)
    assert d["weights"] == {"equity": "36.00", "fixed_income": "54.00", "gold": "10.00"}


def test_compute_gap(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    d = data(call(ctx_for(conn, a), "compute_gap"))
    assert set(d) == {"current", "target", "gap", "drift_alerts"}
    assert Decimal(d["gap"]["equity"]) > 25
    assert "equity" in d["drift_alerts"]


def test_prepare_orders_keeps_the_token_out_of_band(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    out = call(ctx_for(conn, a), "prepare_orders", mode="cash_flow")
    d = data(out)
    assert d["order_count"] == 1 and d["cash_after"] == "12.50"
    assert d["orders"] == [
        {"symbol": "DIBF", "side": "buy", "units": 10, "price": "98.75", "value": "987.50"}
    ]
    assert out.ui is not None and len(out.ui["token"]) >= 32
    assert out.ui["token"] not in out.content
    assert out.ui["draft_id"] == d["draft_id"]
    audit = conn.execute("SELECT actor FROM audit_log").fetchall()
    assert audit == [{"actor": "agent"}]
    assert count(conn, "orders") == 0


def test_draft_limits_cancel_the_draft(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    out = call(ctx_for(conn, a, max_draft_value=Decimal("100")), "prepare_orders", mode="cash_flow")
    assert not out.ok and out.error == "draft_limit_exceeded" and out.ui is None
    row = conn.execute("SELECT status FROM order_drafts").fetchone()
    assert row == {"status": "cancelled"}


def test_empty_plan_and_cancel_flow(conn: Connection, make_account: MakeAccount) -> None:
    empty = make_account("0", {})
    assert call(ctx_for(conn, empty), "prepare_orders", mode="cash_flow").error == "nothing_to_do"
    a = make_account("1000", HOLDINGS)
    ctx = ctx_for(conn, a)
    draft_id = data(call(ctx, "prepare_orders", mode="cash_flow"))["draft_id"]
    assert data(call(ctx, "cancel_draft", draft_id=draft_id)) == {
        "draft_id": draft_id,
        "status": "cancelled",
    }
    assert call(ctx, "cancel_draft", draft_id=draft_id).error == "draft_not_open"
    assert call(ctx, "cancel_draft", draft_id=str(uuid.uuid4())).error == "draft_not_found"


def test_unknown_and_malformed_calls_never_raise_and_change_nothing(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    ctx = ctx_for(conn, a)
    assert call(ctx, "confirm_order", draft_id="x", token="y").error == "unknown_tool"
    assert call(ctx, "prepare_orders", mode="yolo").error == "invalid_arguments"
    assert call(ctx, "prepare_orders", mode="cash_flow", token="x").error == "invalid_arguments"
    assert call(ctx, "cancel_draft", draft_id="not-a-uuid").error == "invalid_arguments"
    assert count(conn, "orders") == 0 and count(conn, "order_drafts") == 0


def test_foreign_account_is_not_found(conn: Connection, make_account: MakeAccount) -> None:
    a, b = make_account("1000", HOLDINGS), make_account("1000", HOLDINGS)
    ctx = ToolContext(conn, a.user_id, b.account_id)
    for name in ("get_portfolio", "compute_gap"):
        assert call(ctx, name).error == "account_not_found"
    assert call(ctx, "prepare_orders", mode="cash_flow").error == "account_not_found"


def test_search_knowledge(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    kb = [Passage("gold.md", "What gold is", "Gold is a metal held as a store of value.")]
    ctx = ctx_for(conn, a, kb=kb)
    d = data(call(ctx, "search_knowledge", query="what is gold"))
    assert d["untrusted_reference_text"] is True
    assert d["passages"] == [
        {
            "source": "gold.md",
            "heading": "What gold is",
            "text": "Gold is a metal held as a store of value.",
        }
    ]
    assert call(ctx, "search_knowledge", query="").error == "invalid_arguments"
    assert call(ctx, "search_knowledge", query="x" * 201).error == "invalid_arguments"
