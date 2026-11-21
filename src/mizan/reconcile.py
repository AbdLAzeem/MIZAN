"""Reconciliation job for ledger, holdings, accounts, and outbox consistency."""

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mizan.db import Connection


@dataclass(frozen=True)
class Discrepancy:
    """Represents a single consistency discrepancy found during reconciliation."""

    kind: str
    account_id: uuid.UUID | None
    order_id: uuid.UUID | None
    detail: str


def reconcile(conn: Connection) -> list[Discrepancy]:
    """Perform read-only reconciliation checks across the system."""
    discrepancies: list[Discrepancy] = []

    # 1. unbalanced_transaction: ledger transaction_id where debit != credit
    unbalanced_rows = conn.execute(
        """
        SELECT
            transaction_id,
            (array_agg(order_id))[1] AS order_id,
            sum(debit) AS total_debit,
            sum(credit) AS total_credit
        FROM ledger_entries
        GROUP BY transaction_id
        HAVING sum(debit) <> sum(credit)
        """
    ).fetchall()
    for row in unbalanced_rows:
        order_id_val = uuid.UUID(str(row["order_id"])) if row["order_id"] is not None else None
        tot_debit = Decimal(str(row["total_debit"]))
        tot_credit = Decimal(str(row["total_credit"]))
        discrepancies.append(
            Discrepancy(
                kind="unbalanced_transaction",
                account_id=None,
                order_id=order_id_val,
                detail=(
                    f"Transaction {row['transaction_id']} is unbalanced: "
                    f"total debit {tot_debit} differs from total credit {tot_credit}."
                ),
            )
        )

    # 2. executed_order_without_ledger: order executed without ledger entries
    executed_missing_ledger = conn.execute(
        """
        SELECT o.id, o.account_id
        FROM orders o
        LEFT JOIN ledger_entries le ON le.order_id = o.id
        WHERE o.status = 'executed' AND le.id IS NULL
        """
    ).fetchall()
    for row in executed_missing_ledger:
        o_id = uuid.UUID(str(row["id"]))
        a_id = uuid.UUID(str(row["account_id"]))
        discrepancies.append(
            Discrepancy(
                kind="executed_order_without_ledger",
                account_id=a_id,
                order_id=o_id,
                detail=f"Executed order {o_id} has no ledger entries.",
            )
        )

    # 3. ledger_for_unexecuted_order: ledger entries belonging to non-executed order
    ledger_unexecuted = conn.execute(
        """
        SELECT DISTINCT o.id, o.account_id, o.status
        FROM ledger_entries le
        JOIN orders o ON o.id = le.order_id
        WHERE o.status <> 'executed'
        """
    ).fetchall()
    for row in ledger_unexecuted:
        o_id = uuid.UUID(str(row["id"]))
        a_id = uuid.UUID(str(row["account_id"]))
        discrepancies.append(
            Discrepancy(
                kind="ledger_for_unexecuted_order",
                account_id=a_id,
                order_id=o_id,
                detail=f"Order {o_id} has status '{row['status']}' but contains ledger entries.",
            )
        )

    # 4. cash_mismatch: cash_balance != opening_cash + sum(debit - credit) on cash:<account>
    cash_rows = conn.execute(
        """
        SELECT
            a.id AS account_id,
            a.cash_balance,
            a.opening_cash,
            COALESCE(sum(le.debit - le.credit), 0) AS net_cash_flow
        FROM accounts a
        LEFT JOIN ledger_entries le ON le.ledger_account = 'cash:' || a.id
        GROUP BY a.id, a.cash_balance, a.opening_cash
        """
    ).fetchall()
    for row in cash_rows:
        a_id = uuid.UUID(str(row["account_id"]))
        actual_cash = Decimal(str(row["cash_balance"]))
        opening_cash = Decimal(str(row["opening_cash"]))
        net_cash = Decimal(str(row["net_cash_flow"]))
        expected_cash = opening_cash + net_cash
        if actual_cash != expected_cash:
            discrepancies.append(
                Discrepancy(
                    kind="cash_mismatch",
                    account_id=a_id,
                    order_id=None,
                    detail=(
                        f"Account {a_id} cash mismatch: expected {expected_cash} "
                        f"(opening {opening_cash} + net flow {net_cash}), got actual {actual_cash}."
                    ),
                )
            )

    # 5. units_mismatch: holdings.units must equal opening_units + net executed units from drafts
    # Calculate executed delta units per (account_id, symbol)
    account_symbol_executed_deltas: dict[tuple[uuid.UUID, str], int] = {}
    executed_draft_rows = conn.execute(
        """
        SELECT o.account_id, od.orders
        FROM orders o
        JOIN order_drafts od ON od.id = o.draft_id
        WHERE o.status = 'executed'
        """
    ).fetchall()
    for row in executed_draft_rows:
        act_id = uuid.UUID(str(row["account_id"]))
        raw_orders: Any = row["orders"]
        if isinstance(raw_orders, str):
            raw_orders = json.loads(raw_orders)
        if isinstance(raw_orders, list):
            for item in raw_orders:
                sym = str(item["symbol"])
                side = str(item["side"])
                units = int(item["units"])
                key = (act_id, sym)
                delta = units if side == "buy" else (-units if side == "sell" else 0)
                account_symbol_executed_deltas[key] = (
                    account_symbol_executed_deltas.get(key, 0) + delta
                )

    # Fetch all holdings rows
    holdings_rows = conn.execute(
        "SELECT account_id, symbol, units, opening_units FROM holdings"
    ).fetchall()
    known_holdings: dict[tuple[uuid.UUID, str], tuple[int, int]] = {}
    for h in holdings_rows:
        act_id = uuid.UUID(str(h["account_id"]))
        sym = str(h["symbol"])
        actual_units = int(h["units"])
        opening_units = int(h["opening_units"])
        known_holdings[(act_id, sym)] = (actual_units, opening_units)

    all_keys = set(known_holdings.keys()) | set(account_symbol_executed_deltas.keys())
    for act_id, sym in all_keys:
        actual_units, opening_units = known_holdings.get((act_id, sym), (0, 0))
        net_delta = account_symbol_executed_deltas.get((act_id, sym), 0)
        expected_units = opening_units + net_delta
        if actual_units != expected_units:
            discrepancies.append(
                Discrepancy(
                    kind="units_mismatch",
                    account_id=act_id,
                    order_id=None,
                    detail=(
                        f"Units mismatch for account {act_id} symbol {sym}: "
                        f"expected {expected_units} (opening {opening_units} + net {net_delta}), "
                        f"got actual {actual_units}."
                    ),
                )
            )

    # 6. dead_event: every outbox_events row with status dead
    dead_rows = conn.execute(
        """
        SELECT id, payload
        FROM outbox_events
        WHERE status = 'dead'
        """
    ).fetchall()
    for row in dead_rows:
        payload: Any = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        ev_order_id: uuid.UUID | None = None
        if isinstance(payload, dict) and payload.get("order_id") is not None:
            try:
                ev_order_id = uuid.UUID(str(payload["order_id"]))
            except (ValueError, TypeError):
                ev_order_id = None
        discrepancies.append(
            Discrepancy(
                kind="dead_event",
                account_id=None,
                order_id=ev_order_id,
                detail=f"Outbox event {row['id']} is in dead status.",
            )
        )

    # Results are sorted by (kind, str(account_id), str(order_id))
    discrepancies.sort(key=lambda d: (d.kind, str(d.account_id), str(d.order_id)))
    return discrepancies
