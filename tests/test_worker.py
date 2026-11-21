import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

import mizan.worker as worker
from conftest import Account
from mizan.db import Connection
from mizan.engine.planner import Mode
from mizan.services.drafts import confirm_draft, prepare_draft
from mizan.worker import MAX_ATTEMPTS, process_next, run_until_idle

HOLDINGS = {"NGEF": 10, "DIBF": 5, "SGLD": 1}
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
MakeAccount = Callable[..., Account]


def confirmed_order(conn: Connection, a: Account, mode: Mode = Mode.CASH_FLOW) -> UUID:
    d = prepare_draft(conn, a.user_id, a.account_id, mode, now=T0)
    return confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k", now=T0).order_id


def cash(conn: Connection, a: Account) -> Decimal:
    row = conn.execute("SELECT cash_balance FROM accounts WHERE id = %s", (a.account_id,))
    r = row.fetchone()
    assert r is not None
    return Decimal(r["cash_balance"])


def units(conn: Connection, a: Account) -> dict[str, int]:
    rows = conn.execute(
        "SELECT symbol, units FROM holdings WHERE account_id = %s", (a.account_id,)
    ).fetchall()
    return {r["symbol"]: r["units"] for r in rows}


def n(conn: Connection, table: str) -> int:
    r = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()  # type: ignore[arg-type]
    assert r is not None
    return int(r["n"])


def balanced_ledger(conn: Connection) -> bool:
    rows = conn.execute(
        "SELECT transaction_id, sum(debit) AS d, sum(credit) AS c FROM ledger_entries "
        "GROUP BY transaction_id"
    ).fetchall()
    return all(r["d"] == r["c"] for r in rows)


def test_no_pending_event_returns_none(conn: Connection) -> None:
    assert process_next(conn, now=T0) is None


def test_executes_a_buy_and_writes_a_balanced_ledger(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    order_id = confirmed_order(conn, a)
    out = process_next(conn, now=T0)
    assert out is not None and out.status == "done" and out.order_id == order_id
    assert cash(conn, a) == Decimal("12.50")
    assert units(conn, a)["DIBF"] == 15
    o = conn.execute("SELECT status, executed_at FROM orders WHERE id = %s", (order_id,))
    row = o.fetchone()
    assert row is not None and row["status"] == "executed" and row["executed_at"] == T0
    assert n(conn, "ledger_entries") == 2 and balanced_ledger(conn)
    ev = conn.execute("SELECT status FROM outbox_events").fetchone()
    assert ev is not None and ev["status"] == "done"
    actions = [x["action"] for x in conn.execute("SELECT action FROM audit_log").fetchall()]
    assert "order.executed" in actions


def test_rebalance_sells_then_buys(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    confirmed_order(conn, a, Mode.REBALANCE)
    out = process_next(conn, now=T0)
    assert out is not None and out.status == "done"
    assert units(conn, a) == {"NGEF": 9, "DIBF": 16, "SGLD": 1}
    assert cash(conn, a) == Decimal("39.15")
    assert n(conn, "ledger_entries") == 4 and balanced_ledger(conn)


def test_duplicate_event_is_harmless(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    order_id = confirmed_order(conn, a)
    process_next(conn, now=T0)
    conn.execute(
        "INSERT INTO outbox_events (event_type, payload, next_attempt_at) VALUES "
        "('order.confirmed', "
        "jsonb_build_object('order_id', %s::text, 'account_id', %s::text), %s)",
        (order_id, a.account_id, T0),
    )
    out = process_next(conn, now=T0)
    assert out is not None and out.status == "done"
    assert cash(conn, a) == Decimal("12.50") and n(conn, "ledger_entries") == 2


def test_failure_rolls_back_and_schedules_exponential_retries_then_dead(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    order_id = confirmed_order(conn, a)
    conn.execute("UPDATE accounts SET cash_balance = 0 WHERE id = %s", (a.account_id,))
    now = T0
    for attempt in range(1, MAX_ATTEMPTS):
        out = process_next(conn, now=now)
        assert out is not None and out.status == "retry"
        ev = conn.execute("SELECT attempts, last_error, next_attempt_at FROM outbox_events")
        row = ev.fetchone()
        assert row is not None and row["attempts"] == attempt and row["last_error"]
        assert row["next_attempt_at"] == now + timedelta(seconds=2**attempt)
        assert process_next(conn, now=now) is None  # not due yet
        now = row["next_attempt_at"]
    out = process_next(conn, now=now)
    assert out is not None and out.status == "dead"
    ev2 = conn.execute("SELECT status FROM outbox_events").fetchone()
    assert ev2 is not None and ev2["status"] == "dead"
    o = conn.execute("SELECT status FROM orders WHERE id = %s", (order_id,)).fetchone()
    assert o is not None and o["status"] == "failed"
    assert cash(conn, a) == 0 and n(conn, "ledger_entries") == 0


def test_crash_after_execution_leaves_no_partial_state_and_runs_once_later(
    conn: Connection, make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make_account("1000", HOLDINGS)
    confirmed_order(conn, a)

    def crash(_conn: Connection, _event_id: int) -> None:
        raise RuntimeError("worker died")

    monkeypatch.setattr(worker, "_finish_event", crash)
    out = process_next(conn, now=T0)
    assert out is not None and out.status == "retry"
    assert cash(conn, a) == Decimal("1000.00") and n(conn, "ledger_entries") == 0
    monkeypatch.undo()
    later = T0 + timedelta(seconds=10)
    out2 = process_next(conn, now=later)
    assert out2 is not None and out2.status == "done"
    assert cash(conn, a) == Decimal("12.50") and n(conn, "ledger_entries") == 2


def test_two_workers_never_execute_the_same_order_twice(
    conn: Connection, make_account: MakeAccount, new_conn: Callable[[], Connection]
) -> None:
    accounts = [make_account("1000", HOLDINGS) for _ in range(6)]
    for a in accounts:
        confirmed_order(conn, a)
    outcomes: list[list[object]] = []

    def work() -> None:
        outcomes.append(list(run_until_idle(new_conn(), now=T0)))

    threads = [threading.Thread(target=work) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(len(o) for o in outcomes) == 6
    for a in accounts:
        assert cash(conn, a) == Decimal("12.50") and units(conn, a)["DIBF"] == 15
    assert n(conn, "ledger_entries") == 12 and balanced_ledger(conn)
