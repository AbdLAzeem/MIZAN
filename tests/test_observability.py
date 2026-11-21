from collections.abc import Callable

import pytest

from conftest import Account
from mizan.agent.tools import ToolContext
from mizan.agent.types import AgentTurn, ChatMessage, ScriptedLLM, ToolCall, ToolSpec
from mizan.db import Connection
from mizan.observability import classify_outcome, run_traced

HOLDINGS = {"NGEF": 10, "DIBF": 5, "SGLD": 1}
MakeAccount = Callable[..., Account]


def test_a_run_is_stored_with_its_steps_and_tokens(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    ctx = ToolContext(conn, a.user_id, a.account_id)
    llm = ScriptedLLM(
        [
            AgentTurn(
                tool_calls=(ToolCall("1", "get_portfolio", {}),), input_tokens=100, output_tokens=10
            ),
            AgentTurn(text="You hold 1000.00 in cash.", input_tokens=150, output_tokens=12),
        ]
    )
    t = run_traced(llm, ctx, "Show my portfolio")
    assert (t.input_tokens, t.output_tokens) == (250, 22)
    run = conn.execute("SELECT * FROM agent_runs WHERE id = %s", (t.run_id,)).fetchone()
    assert run is not None
    assert run["outcome"] == "ok" and run["intent"] == "portfolio" and run["steps"] == 2
    assert run["input_tokens"] == 250 and run["user_message"] == "Show my portfolio"
    steps = conn.execute(
        "SELECT idx, kind, name, ok FROM agent_steps WHERE run_id = %s ORDER BY idx", (t.run_id,)
    ).fetchall()
    assert [(s["kind"], s["name"], s["ok"]) for s in steps] == [
        ("model", "llm", True),
        ("tool", "get_portfolio", True),
        ("model", "llm", True),
    ]


def test_refusals_and_fallbacks_are_classified(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    ctx = ToolContext(conn, a.user_id, a.account_id)
    t = run_traced(ScriptedLLM([]), ctx, "Should I buy gold?")
    row = conn.execute("SELECT outcome FROM agent_runs WHERE id = %s", (t.run_id,)).fetchone()
    assert row == {"outcome": "refused"}
    assert classify_outcome("blah") == "ok"


def test_errors_are_stored_and_reraised(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    ctx = ToolContext(conn, a.user_id, a.account_id)

    class Broken:
        def turn(self, messages: list[ChatMessage], tools: list[ToolSpec]) -> AgentTurn:
            raise TimeoutError("provider timed out")

    with pytest.raises(TimeoutError):
        run_traced(Broken(), ctx, "Show my portfolio")  # type: ignore[arg-type]
    run = conn.execute("SELECT outcome, error FROM agent_runs").fetchone()
    assert run == {"outcome": "error", "error": "provider timed out"}
    step = conn.execute("SELECT kind, ok FROM agent_steps").fetchone()
    assert step == {"kind": "model", "ok": False}
