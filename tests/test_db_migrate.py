from mizan.db import Connection, migrate

TABLES = {
    "users", "accounts", "instruments", "holdings", "target_profiles", "order_drafts",
    "orders", "ledger_entries", "audit_log", "outbox_events", "idempotency_keys",
}  # fmt: skip


def test_all_tables_exist(conn: Connection) -> None:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {r["table_name"] for r in rows} >= TABLES


def test_migrate_is_idempotent(conn: Connection) -> None:
    assert migrate(conn) == []


def test_negative_cash_is_rejected(conn: Connection) -> None:
    import psycopg
    import pytest

    user = conn.execute("INSERT INTO users (name) VALUES ('x') RETURNING id").fetchone()
    assert user is not None
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO accounts (user_id, cash_balance, opening_cash) VALUES (%s, -1, 0)",
            (user["id"],),
        )
