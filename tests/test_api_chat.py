import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from conftest import Account
from mizan.agent.types import AgentTurn, ScriptedLLM, ToolCall
from mizan.api import create_app
from mizan.db import Connection
from mizan.reconcile import reconcile
from mizan.worker import run_until_idle

HOLDINGS = {"NGEF": 10, "DIBF": 5, "SGLD": 1}
MakeAccount = Callable[..., Account]


def parse_sse(text: str) -> list[tuple[str, Any]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        events.append(
            (lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: ")))
        )
    return events


def headers(a: Account, key: str | None = None) -> dict[str, str]:
    h = {"X-User-Id": str(a.user_id)}
    if key:
        h["Idempotency-Key"] = key
    return h


def test_chat_without_a_configured_model_is_unavailable(
    conn: Connection, db_url: str, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    client = TestClient(create_app(db_url))
    r = client.post(f"/accounts/{a.account_id}/chat", json={"message": "hi"}, headers=headers(a))
    assert r.status_code == 503 and r.json() == {"detail": "llm_not_configured"}


def test_chat_streams_events(conn: Connection, db_url: str, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    llm = ScriptedLLM([AgentTurn(text="Gold is a metal that people hold as a store of value.")])
    client = TestClient(create_app(db_url, llm))
    r = client.post(
        f"/accounts/{a.account_id}/chat", json={"message": "What is gold?"}, headers=headers(a)
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [n for n, _ in events]
    assert names[0] == "intent" and names[-1] == "done" and "message" in names
    text = "".join(d["text"] for n, d in events if n == "message")
    assert "store of value" in text and "educational simulation" in text.lower()


def test_chat_validation_and_ownership(
    conn: Connection, db_url: str, make_account: MakeAccount
) -> None:
    a, b = make_account("1000", HOLDINGS), make_account("1000", HOLDINGS)
    client = TestClient(create_app(db_url, ScriptedLLM([])))
    assert (
        client.post(
            f"/accounts/{a.account_id}/chat", json={"message": ""}, headers=headers(a)
        ).status_code
        == 422
    )
    foreign = client.post(
        f"/accounts/{b.account_id}/chat", json={"message": "hi"}, headers=headers(a)
    )
    assert foreign.status_code == 404 and foreign.json() == {"detail": "account_not_found"}


def test_end_to_end_chat_draft_confirm_execute_reconcile(
    conn: Connection, db_url: str, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    llm = ScriptedLLM(
        [
            AgentTurn(tool_calls=(ToolCall("1", "prepare_orders", {"mode": "cash_flow"}),)),
            AgentTurn(text="Drafted: buy 10 units of DIBF at 98.75, total 987.50. Review it."),
        ]
    )
    client = TestClient(create_app(db_url, llm))
    chat = client.post(
        f"/accounts/{a.account_id}/chat",
        json={"message": "Prepare orders to rebalance"},
        headers=headers(a),
    )
    events = parse_sse(chat.text)
    draft = next(d for n, d in events if n == "draft")
    assert (
        Decimal(
            client.get(f"/accounts/{a.account_id}/portfolio", headers=headers(a)).json()["cash"]
        )
        == 1000
    )
    run_until_idle(conn)  # nothing confirmed yet, so nothing executes
    assert (
        Decimal(
            client.get(f"/accounts/{a.account_id}/portfolio", headers=headers(a)).json()["cash"]
        )
        == 1000
    )

    url = f"/accounts/{a.account_id}/drafts/{draft['draft_id']}/confirm"
    ok = client.post(url, json={"token": draft["token"]}, headers=headers(a, "ui-click-1"))
    assert ok.status_code == 200
    run_until_idle(conn)
    after = client.get(f"/accounts/{a.account_id}/portfolio", headers=headers(a)).json()
    assert Decimal(after["cash"]) == Decimal("12.50")
    assert reconcile(conn) == []
