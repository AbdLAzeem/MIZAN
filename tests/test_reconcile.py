from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from conftest import Account
from mizan.db import Connection
from mizan.engine.planner import Mode
from mizan.reconcile import reconcile
from mizan.services.drafts import confirm_draft, prepare_draft
from mizan.worker import MAX_ATTEMPTS, process_next, run_until_idle

HOLDINGS = {"NGEF": 10, "DIBF": 5, "SGLD": 1}
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
MakeAccount = Callable[..., Account]


def executed(conn: Connection, a: Account, mode: Mode = Mode.REBALANCE) -> None:
    d = prepare_draft(conn, a.user_id, a.account_id, mode, now=T0)
    confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k", now=T0)
    run_until_idle(conn, now=T0)


def kinds(conn: Connection) -> list[str]:
    return [d.kind for d in reconcile(conn)]


def test_clean_system_has_no_discrepancies(conn: Connection, make_account: MakeAccount) -> None:
    executed(conn, make_account("1000", HOLDINGS))
    executed(conn, make_account("500", {"NGEF": 3}), Mode.CASH_FLOW)
    assert reconcile(conn) == []


def test_empty_database_is_clean(conn: Connection) -> None:
    assert reconcile(conn) == []


def test_detects_cash_mismatch(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    executed(conn, a)
    conn.execute(
        "UPDATE accounts SET cash_balance = cash_balance + 5 WHERE id = %s", (a.account_id,)
    )
    found = reconcile(conn)
    assert [d.kind for d in found] == ["cash_mismatch"]
    assert found[0].account_id == a.account_id and found[0].detail


def test_detects_units_mismatch(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    executed(conn, a)
    conn.execute(
        "UPDATE holdings SET units = units + 1 WHERE account_id = %s AND symbol = 'DIBF'",
        (a.account_id,),
    )
    assert kinds(conn) == ["units_mismatch"]


def test_detects_unbalanced_ledger_transaction(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    executed(conn, a)
    conn.execute(
        "UPDATE ledger_entries SET credit = credit + 1 "
        "WHERE id = (SELECT min(id) FROM ledger_entries WHERE credit > 0)"
    )
    assert "unbalanced_transaction" in kinds(conn)


def test_detects_executed_order_without_ledger(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k", now=T0)
    conn.execute("UPDATE orders SET status = 'executed', executed_at = %s", (T0,))
    found = kinds(conn)
    assert "executed_order_without_ledger" in found
    assert "units_mismatch" in found


def test_detects_ledger_for_unexecuted_order(conn: Connection, make_account: MakeAccount) -> None:
    a = make_account("1000", HOLDINGS)
    executed(conn, a)
    conn.execute("UPDATE orders SET status = 'confirmed'")
    assert "ledger_for_unexecuted_order" in kinds(conn)


def test_reports_dead_events_and_does_not_modify_anything(
    conn: Connection, make_account: MakeAccount
) -> None:
    a = make_account("1000", HOLDINGS)
    d = prepare_draft(conn, a.user_id, a.account_id, Mode.CASH_FLOW, now=T0)
    confirm_draft(conn, a.user_id, a.account_id, d.draft_id, d.token, "k", now=T0)
    conn.execute("UPDATE accounts SET cash_balance = 0 WHERE id = %s", (a.account_id,))
    now = T0
    for _ in range(MAX_ATTEMPTS):
        process_next(conn, now=now)
        now += timedelta(minutes=5)
    before = conn.execute("SELECT * FROM outbox_events").fetchall()
    found = reconcile(conn)
    assert [x.kind for x in found] == ["cash_mismatch", "dead_event"]
    assert conn.execute("SELECT * FROM outbox_events").fetchall() == before
