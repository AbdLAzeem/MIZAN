"""Outbox event worker and order execution."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from psycopg.types.json import Jsonb

from mizan.db import Connection

MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 1


@dataclass(frozen=True)
class ProcessOutcome:
    """Outcome of processing an outbox event."""

    event_id: int
    status: Literal["done", "retry", "dead"]
    order_id: uuid.UUID | None


def _finish_event(conn: Connection, event_id: int) -> None:
    """Mark an event as done inside the caller's transaction."""
    conn.execute(
        "UPDATE outbox_events SET status = 'done' WHERE id = %s",
        (event_id,),
    )


def execute_order(conn: Connection, order_id: uuid.UUID, now: datetime) -> None:
    """Execute a confirmed order inside the caller's transaction."""
    order = conn.execute(
        "SELECT id, draft_id, account_id, status FROM orders WHERE id = %s FOR UPDATE",
        (order_id,),
    ).fetchone()
    if order is None:
        raise ValueError(f"Order {order_id} not found")

    if order["status"] in ("executed", "failed"):
        return

    account_id = uuid.UUID(str(order["account_id"]))
    draft_id = uuid.UUID(str(order["draft_id"]))

    draft = conn.execute(
        "SELECT orders FROM order_drafts WHERE id = %s",
        (draft_id,),
    ).fetchone()
    if draft is None:
        raise ValueError(f"Draft {draft_id} not found")

    raw_orders = draft["orders"]
    if isinstance(raw_orders, str):
        raw_orders = json.loads(raw_orders)

    acc = conn.execute(
        "SELECT cash_balance FROM accounts WHERE id = %s FOR UPDATE",
        (account_id,),
    ).fetchone()
    if acc is None:
        raise ValueError(f"Account {account_id} not found")

    cash_balance = Decimal(str(acc["cash_balance"]))

    holdings_rows = conn.execute(
        "SELECT symbol, units FROM holdings WHERE account_id = %s FOR UPDATE",
        (account_id,),
    ).fetchall()
    holdings: dict[str, int] = {row["symbol"]: int(row["units"]) for row in holdings_rows}

    transaction_id = uuid.uuid4()
    ledger_records: list[tuple[uuid.UUID, uuid.UUID, str, Decimal, Decimal, datetime]] = []

    for item in raw_orders:
        symbol = str(item["symbol"])
        side = str(item["side"])
        units = int(item["units"])
        price = Decimal(str(item["price"]))

        value = units * price
        if value != value.quantize(Decimal("0.01")):
            raise ValueError(f"Order value {value} has more than 2 decimal places")

        current_units = holdings.get(symbol, 0)
        pos_account = f"position:{account_id}:{symbol}"
        cash_account = f"cash:{account_id}"

        if side == "buy":
            if cash_balance < value:
                raise ValueError("insufficient funds")
            cash_balance -= value
            holdings[symbol] = current_units + units
            ledger_records.append(
                (transaction_id, order_id, pos_account, value, Decimal("0.00"), now)
            )
            ledger_records.append(
                (transaction_id, order_id, cash_account, Decimal("0.00"), value, now)
            )
        elif side == "sell":
            if current_units < units:
                raise ValueError("insufficient units")
            cash_balance += value
            holdings[symbol] = current_units - units
            ledger_records.append(
                (transaction_id, order_id, cash_account, value, Decimal("0.00"), now)
            )
            ledger_records.append(
                (transaction_id, order_id, pos_account, Decimal("0.00"), value, now)
            )
        else:
            raise ValueError(f"Unsupported order side: {side}")

    conn.execute(
        "UPDATE accounts SET cash_balance = %s WHERE id = %s",
        (cash_balance, account_id),
    )

    for symbol, new_units in holdings.items():
        conn.execute(
            "INSERT INTO holdings (account_id, symbol, units) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (account_id, symbol) DO UPDATE SET units = EXCLUDED.units",
            (account_id, symbol, new_units),
        )

    for rec in ledger_records:
        conn.execute(
            "INSERT INTO ledger_entries "
            "(transaction_id, order_id, ledger_account, debit, credit, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            rec,
        )

    conn.execute(
        "UPDATE orders SET status = 'executed', executed_at = %s WHERE id = %s",
        (now, order_id),
    )

    conn.execute(
        "INSERT INTO audit_log (actor, action, account_id, payload, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            "system",
            "order.executed",
            account_id,
            Jsonb({"order_id": str(order_id)}),
            now,
        ),
    )


def process_next(
    conn: Connection,
    *,
    now: datetime | None = None,
) -> ProcessOutcome | None:
    """Process the next pending outbox event."""
    effective_now = now if now is not None else datetime.now(UTC)

    event_id: int | None = None
    event_order_id: uuid.UUID | None = None

    try:
        with conn.transaction():
            row = conn.execute(
                "SELECT id, event_type, payload "
                "FROM outbox_events "
                "WHERE status = 'pending' AND next_attempt_at <= %s "
                "ORDER BY id "
                "LIMIT 1 FOR UPDATE SKIP LOCKED",
                (effective_now,),
            ).fetchone()

            if row is None:
                return None

            event_id = int(row["id"])
            event_type = str(row["event_type"])
            payload: Any = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)

            if "order_id" in payload and payload["order_id"] is not None:
                event_order_id = uuid.UUID(str(payload["order_id"]))

            if event_type == "order.confirmed":
                if event_order_id is None:
                    raise ValueError("Missing order_id in order.confirmed payload")
                execute_order(conn, event_order_id, effective_now)
                _finish_event(conn, event_id)
            else:
                raise ValueError(f"Unknown event type: {event_type}")

        return ProcessOutcome(
            event_id=event_id,
            status="done",
            order_id=event_order_id,
        )

    except Exception as err:
        if event_id is None:
            raise

        with conn.transaction():
            ev = conn.execute(
                "SELECT attempts, payload FROM outbox_events WHERE id = %s FOR UPDATE",
                (event_id,),
            ).fetchone()
            if ev is None:
                raise

            n = int(ev["attempts"]) + 1
            payload = ev["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)

            extracted_order_id: uuid.UUID | None = None
            if "order_id" in payload and payload["order_id"] is not None:
                extracted_order_id = uuid.UUID(str(payload["order_id"]))

            if n >= MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE outbox_events "
                    "SET attempts = %s, last_error = %s, status = 'dead' "
                    "WHERE id = %s",
                    (n, str(err), event_id),
                )
                if extracted_order_id is not None:
                    conn.execute(
                        "UPDATE orders SET status = 'failed' "
                        "WHERE id = %s AND status = 'confirmed'",
                        (extracted_order_id,),
                    )
                return ProcessOutcome(
                    event_id=event_id,
                    status="dead",
                    order_id=extracted_order_id,
                )
            else:
                next_attempt = effective_now + timedelta(seconds=BASE_DELAY_SECONDS * (2**n))
                conn.execute(
                    "UPDATE outbox_events "
                    "SET attempts = %s, last_error = %s, next_attempt_at = %s "
                    "WHERE id = %s",
                    (n, str(err), next_attempt, event_id),
                )
                return ProcessOutcome(
                    event_id=event_id,
                    status="retry",
                    order_id=extracted_order_id,
                )


def run_until_idle(
    conn: Connection,
    *,
    now: datetime | None = None,
    max_events: int = 1000,
) -> list[ProcessOutcome]:
    """Process pending events until idle or max_events reached."""
    outcomes: list[ProcessOutcome] = []
    while len(outcomes) < max_events:
        outcome = process_next(conn, now=now)
        if outcome is None:
            break
        outcomes.append(outcome)
    return outcomes
