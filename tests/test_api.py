import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from conftest import Account
from mizan.api import create_app
from mizan.db import Connection
from mizan.worker import run_until_idle

HOLDINGS = {"NGEF": 10, "DIBF": 5, "SGLD": 1}
MakeAccount = Callable[..., Account]


@pytest.fixture
def client(conn: Connection, db_url: str) -> TestClient:
    return TestClient(create_app(db_url))


def h(a: Account, key: str | None = None) -> dict[str, str]:
    headers = {"X-User-Id": str(a.user_id)}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def base(a: Account) -> str:
    return f"/accounts/{a.account_id}"


def test_health_needs_no_auth(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_missing_or_bad_user_header_is_unauthorized(
    client: TestClient, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    for headers in ({}, {"X-User-Id": "not-a-uuid"}):
        r = client.get(f"{base(a)}/portfolio", headers=headers)
        assert r.status_code == 401 and r.json() == {"detail": "unauthorized"}


def test_portfolio(client: TestClient, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    r = client.get(f"{base(a)}/portfolio", headers=h(a))
    assert r.status_code == 200
    body = r.json()
    assert Decimal(body["cash"]) == Decimal("1000")
    assert Decimal(body["holdings_value"]) == Decimal("1957.85")
    assert Decimal(body["total_value"]) == Decimal("2957.85")
    assert [x["symbol"] for x in body["holdings"]] == ["DIBF", "NGEF", "SGLD"]
    assert abs(sum(Decimal(v) for v in body["weights"].values()) - 100) < Decimal("1e-9")


def test_foreign_and_unknown_accounts_both_give_404(
    client: TestClient, make_account: MakeAccount
) -> None:
    a, b = make_account("1000", HOLDINGS), make_account("1000", HOLDINGS)
    r1 = client.get(f"{base(b)}/portfolio", headers=h(a))
    r2 = client.get(f"/accounts/{uuid.uuid4()}/portfolio", headers=h(a))
    assert r1.status_code == r2.status_code == 404
    assert r1.json() == r2.json() == {"detail": "account_not_found"}


def test_full_flow_draft_confirm_replay_and_worker(
    client: TestClient, conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    d = client.post(f"{base(a)}/drafts", json={"mode": "cash_flow"}, headers=h(a))
    assert d.status_code == 201
    draft = d.json()
    assert len(draft["orders"]) == 1
    o = draft["orders"][0]
    assert (o["symbol"], o["side"], o["units"]) == ("DIBF", "buy", 10)
    assert Decimal(o["price"]) == Decimal("98.75")
    url = f"{base(a)}/drafts/{draft['draft_id']}/confirm"
    c1 = client.post(url, json={"token": draft["token"]}, headers=h(a, "key-1"))
    c2 = client.post(url, json={"token": draft["token"]}, headers=h(a, "key-1"))
    assert c1.status_code == c2.status_code == 200
    assert c1.json()["replayed"] is False and c2.json()["replayed"] is True
    assert c1.json()["order_id"] == c2.json()["order_id"]
    cash_before = client.get(f"{base(a)}/portfolio", headers=h(a)).json()["cash"]
    assert Decimal(cash_before) == Decimal("1000")  # confirming does not execute
    run_until_idle(conn)
    cash_after = client.get(f"{base(a)}/portfolio", headers=h(a)).json()["cash"]
    assert Decimal(cash_after) == Decimal("12.50")


def test_confirm_requires_idempotency_key(client: TestClient, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    draft = client.post(f"{base(a)}/drafts", json={"mode": "cash_flow"}, headers=h(a)).json()
    r = client.post(
        f"{base(a)}/drafts/{draft['draft_id']}/confirm",
        json={"token": draft["token"]},
        headers=h(a),
    )
    assert r.status_code == 400 and r.json() == {"detail": "idempotency_key_required"}


def test_wrong_token_is_forbidden_and_cancel_then_confirm_conflicts(
    client: TestClient, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    draft = client.post(f"{base(a)}/drafts", json={"mode": "cash_flow"}, headers=h(a)).json()
    url = f"{base(a)}/drafts/{draft['draft_id']}/confirm"
    bad = client.post(url, json={"token": "nope"}, headers=h(a, "k1"))
    assert bad.status_code == 403 and bad.json() == {"detail": "invalid_token"}
    assert (
        client.post(f"{base(a)}/drafts/{draft['draft_id']}/cancel", headers=h(a)).status_code == 204
    )
    late = client.post(url, json={"token": draft["token"]}, headers=h(a, "k2"))
    assert late.status_code == 409 and late.json() == {"detail": "draft_not_open"}


def test_expired_draft_is_gone(
    client: TestClient, conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    draft = client.post(f"{base(a)}/drafts", json={"mode": "cash_flow"}, headers=h(a)).json()
    conn.execute("UPDATE order_drafts SET expires_at = now() - interval '1 minute'")
    r = client.post(
        f"{base(a)}/drafts/{draft['draft_id']}/confirm",
        json={"token": draft["token"]},
        headers=h(a, "k1"),
    )
    assert r.status_code == 410 and r.json() == {"detail": "draft_expired"}


def test_empty_plan_and_bad_mode(client: TestClient, make_account: MakeAccount) -> None:
    a = make_account("0", {})
    r = client.post(f"{base(a)}/drafts", json={"mode": "cash_flow"}, headers=h(a))
    assert r.status_code == 422 and r.json() == {"detail": "empty_plan"}
    r2 = client.post(f"{base(a)}/drafts", json={"mode": "yolo"}, headers=h(a))
    assert r2.status_code == 422


def test_another_user_cannot_use_a_draft(client: TestClient, make_account: MakeAccount) -> None:
    a, b = make_account("1000", HOLDINGS), make_account("1000", HOLDINGS)
    draft = client.post(f"{base(a)}/drafts", json={"mode": "cash_flow"}, headers=h(a)).json()
    r = client.post(
        f"{base(a)}/drafts/{draft['draft_id']}/confirm",
        json={"token": draft["token"]},
        headers=h(b, "k1"),
    )
    assert r.status_code == 404


def test_there_is_no_endpoint_that_executes_orders(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {
        "/health",
        "/accounts/{account_id}/portfolio",
        "/accounts/{account_id}/drafts",
        "/accounts/{account_id}/drafts/{draft_id}/confirm",
        "/accounts/{account_id}/drafts/{draft_id}/cancel",
        "/accounts/{account_id}/chat",
    }
