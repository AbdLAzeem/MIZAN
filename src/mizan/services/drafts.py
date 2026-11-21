"""Draft and confirmation service for rebalancing and cash flow orders."""

import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from mizan.db import Connection
from mizan.engine.planner import Mode, Order, Side, plan_orders
from mizan.engine.types import AssetClass, Instrument, Portfolio


class ServiceError(Exception):
    """Base exception for service layer errors."""


class AccountNotFound(ServiceError):
    """Account unknown or not owned by user."""


class DraftNotFound(ServiceError):
    """Draft unknown or belongs to another account."""


class DraftNotOpen(ServiceError):
    """Draft is not in open status."""


class DraftExpired(ServiceError):
    """Draft has expired."""


class InvalidToken(ServiceError):
    """Confirmation token is invalid."""


class OrderSetTampered(ServiceError):
    """Draft orders have been tampered with."""


class IdempotencyConflict(ServiceError):
    """Idempotency key reused with a different request."""


class EmptyPlan(ServiceError):
    """Generated plan contains no orders."""


@dataclass(frozen=True)
class PreparedDraft:
    """A prepared order draft awaiting confirmation."""

    draft_id: uuid.UUID
    token: str
    expires_at: datetime
    orders: tuple[Order, ...]
    order_set_hash: str
    cash_after: Decimal


@dataclass(frozen=True)
class ConfirmResult:
    """Result of confirming an order draft."""

    order_id: uuid.UUID
    draft_id: uuid.UUID
    replayed: bool


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def order_set_hash(orders: Sequence[Order]) -> str:
    """Return the SHA-256 hex digest of serialized orders."""
    payload = [
        {
            "price": str(o.price),
            "side": o.side.value if isinstance(o.side, Side) else str(o.side),
            "symbol": o.symbol,
            "units": o.units,
        }
        for o in orders
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_portfolio(
    conn: Connection,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Portfolio:
    """Load cash, holdings, and universe instruments for an account.

    Raises AccountNotFound if the account does not exist or is not owned by user_id.
    """
    with conn.transaction():
        acc = conn.execute(
            "SELECT cash_balance FROM accounts WHERE id = %s AND user_id = %s",
            (account_id, user_id),
        ).fetchone()
        if acc is None:
            raise AccountNotFound("Account not found or not owned by user")
        cash = Decimal(str(acc["cash_balance"]))

        inst_rows = conn.execute(
            "SELECT symbol, name, asset_class, price FROM instruments"
        ).fetchall()
        instruments: dict[str, Instrument] = {
            row["symbol"]: Instrument(
                symbol=row["symbol"],
                asset_class=AssetClass(row["asset_class"]),
                price=Decimal(str(row["price"])),
            )
            for row in inst_rows
        }

        holding_rows = conn.execute(
            "SELECT symbol, units FROM holdings WHERE account_id = %s",
            (account_id,),
        ).fetchall()
        holdings: dict[str, int] = {row["symbol"]: int(row["units"]) for row in holding_rows}

    return Portfolio(cash=cash, holdings=holdings, instruments=instruments)


def prepare_draft(
    conn: Connection,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    mode: Mode,
    *,
    now: datetime | None = None,
    ttl: timedelta = timedelta(minutes=10),
) -> PreparedDraft:
    """Prepare a rebalance or cash flow draft plan and persist an open order draft."""
    effective_now = now if now is not None else datetime.now(UTC)

    portfolio = load_portfolio(conn, user_id, account_id)

    with conn.transaction():
        tp = conn.execute(
            "SELECT weights FROM target_profiles WHERE account_id = %s",
            (account_id,),
        ).fetchone()
        if tp is None:
            raise AccountNotFound("Account target profile not found")

        raw_weights = tp["weights"]
        if isinstance(raw_weights, str):
            raw_weights = json.loads(raw_weights)
        target_weights = {AssetClass(k): Decimal(str(v)) for k, v in raw_weights.items()}

        plan = plan_orders(portfolio, target_weights, mode)
        if not plan.orders:
            raise EmptyPlan("Plan produced no orders")

        orders_payload = [
            {
                "symbol": o.symbol,
                "side": o.side.value if isinstance(o.side, Side) else str(o.side),
                "units": o.units,
                "price": str(o.price),
            }
            for o in plan.orders
        ]
        orders_hash = order_set_hash(plan.orders)
        plain_token = secrets.token_urlsafe(32)
        token_digest = hash_token(plain_token)
        expires_at = effective_now + ttl

        draft_row = conn.execute(
            "INSERT INTO order_drafts "
            "(account_id, mode, orders, order_set_hash, confirmation_token_hash, "
            "expires_at, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'open', %s) "
            "RETURNING id",
            (
                account_id,
                mode.value if isinstance(mode, Mode) else str(mode),
                Jsonb(orders_payload),
                orders_hash,
                token_digest,
                expires_at,
                effective_now,
            ),
        ).fetchone()
        assert draft_row is not None
        draft_id = uuid.UUID(str(draft_row["id"]))

        conn.execute(
            "INSERT INTO audit_log (actor, action, account_id, payload, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                "agent",
                "draft.prepared",
                account_id,
                Jsonb({"draft_id": str(draft_id)}),
                effective_now,
            ),
        )

    return PreparedDraft(
        draft_id=draft_id,
        token=plain_token,
        expires_at=expires_at,
        orders=plan.orders,
        order_set_hash=orders_hash,
        cash_after=plan.cash_after,
    )


def cancel_draft(
    conn: Connection,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> None:
    """Cancel an open draft."""
    with conn.transaction():
        acc = conn.execute(
            "SELECT 1 FROM accounts WHERE id = %s AND user_id = %s",
            (account_id, user_id),
        ).fetchone()
        if acc is None:
            raise AccountNotFound("Account not found or not owned by user")

        draft = conn.execute(
            "SELECT status FROM order_drafts WHERE id = %s AND account_id = %s FOR UPDATE",
            (draft_id, account_id),
        ).fetchone()
        if draft is None:
            raise DraftNotFound("Draft not found for account")
        if draft["status"] != "open":
            raise DraftNotOpen(f"Draft status is {draft['status']}, expected open")

        conn.execute(
            "UPDATE order_drafts SET status = 'cancelled' WHERE id = %s",
            (draft_id,),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, account_id, payload) VALUES (%s, %s, %s, %s)",
            (
                "user",
                "draft.cancelled",
                account_id,
                Jsonb({"draft_id": str(draft_id)}),
            ),
        )


def confirm_draft(
    conn: Connection,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    draft_id: uuid.UUID,
    token: str,
    idempotency_key: str,
    *,
    now: datetime | None = None,
) -> ConfirmResult:
    """Confirm an open draft in a single transaction."""
    effective_now = now if now is not None else datetime.now(UTC)
    token_digest = hash_token(token)
    request_hash = hashlib.sha256(f"{draft_id}:{token_digest}".encode()).hexdigest()

    with conn.transaction():
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s || ':' || %s, 0))",
            (str(account_id), idempotency_key),
        )

        acc = conn.execute(
            "SELECT 1 FROM accounts WHERE id = %s AND user_id = %s",
            (account_id, user_id),
        ).fetchone()
        if acc is None:
            raise AccountNotFound("Account not found or not owned by user")

        key_row = conn.execute(
            "SELECT request_hash, response FROM idempotency_keys "
            "WHERE key = %s AND account_id = %s",
            (idempotency_key, account_id),
        ).fetchone()
        if key_row is not None:
            if not hmac.compare_digest(key_row["request_hash"], request_hash):
                raise IdempotencyConflict("Idempotency key reused for different request")
            resp = key_row["response"]
            if isinstance(resp, str):
                resp = json.loads(resp)
            return ConfirmResult(
                order_id=uuid.UUID(str(resp["order_id"])),
                draft_id=uuid.UUID(str(resp["draft_id"])),
                replayed=True,
            )

        draft = conn.execute(
            "SELECT mode, orders, order_set_hash, confirmation_token_hash, "
            "expires_at, status FROM order_drafts WHERE id = %s AND account_id = %s FOR UPDATE",
            (draft_id, account_id),
        ).fetchone()
        if draft is None:
            raise DraftNotFound("Draft not found for account")
        if draft["status"] != "open":
            raise DraftNotOpen(f"Draft status is {draft['status']}, expected open")
        if effective_now >= draft["expires_at"]:
            raise DraftExpired("Draft has expired")
        if not hmac.compare_digest(draft["confirmation_token_hash"], token_digest):
            raise InvalidToken("Invalid confirmation token")

        raw_orders = draft["orders"]
        if isinstance(raw_orders, str):
            raw_orders = json.loads(raw_orders)
        parsed_orders = [
            Order(
                symbol=item["symbol"],
                side=Side(item["side"]),
                units=int(item["units"]),
                price=Decimal(str(item["price"])),
            )
            for item in raw_orders
        ]
        recomputed_hash = order_set_hash(parsed_orders)
        if not hmac.compare_digest(recomputed_hash, draft["order_set_hash"]):
            raise OrderSetTampered("Order set hash mismatch")

        order_row = conn.execute(
            "INSERT INTO orders (draft_id, account_id, status, created_at) "
            "VALUES (%s, %s, 'confirmed', %s) RETURNING id",
            (draft_id, account_id, effective_now),
        ).fetchone()
        assert order_row is not None
        order_id = uuid.UUID(str(order_row["id"]))

        conn.execute(
            "UPDATE order_drafts SET status = 'confirmed' WHERE id = %s",
            (draft_id,),
        )

        outbox_payload = {
            "order_id": str(order_id),
            "account_id": str(account_id),
        }
        conn.execute(
            "INSERT INTO outbox_events (event_type, payload, status, created_at, next_attempt_at) "
            "VALUES (%s, %s, 'pending', %s, %s)",
            ("order.confirmed", Jsonb(outbox_payload), effective_now, effective_now),
        )

        conn.execute(
            "INSERT INTO audit_log (actor, action, account_id, payload, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                "user",
                "order.confirmed",
                account_id,
                Jsonb({"order_id": str(order_id), "draft_id": str(draft_id)}),
                effective_now,
            ),
        )

        idempotency_response = {
            "order_id": str(order_id),
            "draft_id": str(draft_id),
        }
        conn.execute(
            "INSERT INTO idempotency_keys (key, account_id, request_hash, response, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                idempotency_key,
                account_id,
                request_hash,
                Jsonb(idempotency_response),
                effective_now,
            ),
        )

    return ConfirmResult(
        order_id=order_id,
        draft_id=draft_id,
        replayed=False,
    )
