import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from conftest import Account
from mizan.db import Connection
from mizan.engine.planner import Mode, Side
from mizan.services.drafts import (
    AccountNotFound,
    ConfirmResult,
    DraftExpired,
    DraftNotFound,
    DraftNotOpen,
    EmptyPlan,
    IdempotencyConflict,
    InvalidToken,
    OrderSetTampered,
    cancel_draft,
    confirm_draft,
    hash_token,
    prepare_draft,
)

HOLDINGS = {"NGEF": 10, "DIBF": 5, "SGLD": 1}
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
MakeAccount = Callable[..., Account]


def count(conn: Connection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()  # type: ignore[arg-type]
    assert row is not None
    return int(row["n"])


def test_prepare_known_plan_and_stores_only_the_token_hash(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    assert [(o.symbol, o.side, o.units) for o in d.orders] == [("DIBF", Side.BUY, 10)]
    assert len(d.token) >= 32 and d.expires_at == T0 + timedelta(minutes=10)
    row = conn.execute("SELECT * FROM order_drafts WHERE id = %s", (d.draft_id,)).fetchone()
    assert row is not None
    assert row["confirmation_token_hash"] == hash_token(d.token)
    assert d.token not in json.dumps(row, default=str)
    audit = conn.execute("SELECT actor, action FROM audit_log").fetchall()
    assert audit == [{"actor": "agent", "action": "draft.prepared"}]


def test_foreign_and_unknown_accounts_look_the_same(
    conn: Connection, make_account: MakeAccount
) -> None:
    a, b = make_account("1000", HOLDINGS), make_account("1000", HOLDINGS)
    with pytest.raises(AccountNotFound):
        prepare_draft(conn, a.user_id, b.account_id, Mode.CASH_FLOW)
    with pytest.raises(AccountNotFound):
        prepare_draft(conn, a.user_id, uuid.uuid4(), Mode.CASH_FLOW)


def test_empty_plan_is_rejected(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("0", {})
    with pytest.raises(EmptyPlan):
        prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW)


def test_confirm_writes_order_and_outbox_but_does_not_move_money(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    r = confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k1", now=T0)
    assert not r.replayed
    order = conn.execute("SELECT status, draft_id FROM orders WHERE id = %s", (r.order_id,))
    assert order.fetchone() == {"status": "confirmed", "draft_id": d.draft_id}
    draft = conn.execute("SELECT status FROM order_drafts WHERE id = %s", (d.draft_id,))
    assert draft.fetchone() == {"status": "confirmed"}
    ev = conn.execute("SELECT event_type, status, payload FROM outbox_events").fetchall()
    assert len(ev) == 1 and ev[0]["event_type"] == "order.confirmed"
    assert ev[0]["status"] == "pending" and ev[0]["payload"]["order_id"] == str(r.order_id)
    cash = conn.execute("SELECT cash_balance FROM accounts").fetchone()
    assert cash is not None and str(cash["cash_balance"]) == "1000.00"
    assert ("user", "order.confirmed") in [
        (x["actor"], x["action"]) for x in conn.execute("SELECT * FROM audit_log").fetchall()
    ]


def test_same_key_twice_is_a_replay_not_a_second_order(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    r1 = confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k1", now=T0)
    r2 = confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k1", now=T0)
    assert r2.replayed and r2.order_id == r1.order_id
    assert count(conn, "orders") == 1 and count(conn, "outbox_events") == 1


def test_new_key_after_confirmation_is_rejected(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k1", now=T0)
    with pytest.raises(DraftNotOpen):
        confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k2", now=T0)


def test_key_reuse_for_a_different_request_conflicts(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    d1 = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    d2 = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    confirm_draft(conn, a.user_id, a.account_id, d1.draft_id, d1.token, "k", now=T0)
    with pytest.raises(IdempotencyConflict):
        confirm_draft(conn, a.user_id, a.account_id, d2.draft_id, d2.token, "k", now=T0)


def test_wrong_token_writes_nothing(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    with pytest.raises(InvalidToken):
        confirm_draft(conn, a.user_id, a.account_id, d.draft_id, "nope", "k1", now=T0)
    assert count(conn, "orders") == 0 and count(conn, "outbox_events") == 0
    assert count(conn, "idempotency_keys") == 0


def test_expired_draft_is_rejected(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    late = T0 + timedelta(minutes=10)
    with pytest.raises(DraftExpired):
        confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k1", now=late)


def test_tampered_order_set_is_rejected(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    conn.execute(
        "UPDATE order_drafts SET orders = jsonb_set(orders, '{0,units}', '99') WHERE id = %s",
        (d.draft_id,),
    )
    with pytest.raises(OrderSetTampered):
        confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k1", now=T0)
    assert count(conn, "orders") == 0


def test_cannot_confirm_a_draft_of_another_account(
    conn: Connection, make_account: MakeAccount
) -> None:
    a, b = make_account("1000", HOLDINGS), make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    with pytest.raises(DraftNotFound):
        confirm_draft(conn, b.user_id, b.account_id, d.draft_id, d.token, "k1", now=T0)
    with pytest.raises(AccountNotFound):
        confirm_draft(conn, b.user_id, a.account_id, d.draft_id, d.token, "k1", now=T0)


def test_cancelled_draft_cannot_be_confirmed(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    cancel_draft(conn, a.user_id, a.account_id, d.draft_id)
    with pytest.raises(DraftNotOpen):
        confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k1", now=T0)


def test_concurrent_double_click_creates_exactly_one_order(
    conn: Connection, make_account: MakeAccount, new_conn: Callable[[], Connection]
) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    results: list[ConfirmResult] = []

    def click() -> None:
        c = new_conn()
        results.append(confirm_draft(c, a.user_id, a.account_id, d.draft_id, d.token, "k1", now=T0))

    threads = [threading.Thread(target=click) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4
    assert len({r.order_id for r in results}) == 1
    assert count(conn, "orders") == 1 and count(conn, "outbox_events") == 1
