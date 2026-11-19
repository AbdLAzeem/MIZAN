"""Shared fixtures. Database tests need DATABASE_URL and are skipped without it."""

import json
import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from mizan.db import Connection, connect, migrate

SEED = Path(__file__).resolve().parents[1] / "data" / "seed" / "instruments.json"


@dataclass(frozen=True)
class Account:
    user_id: uuid.UUID
    account_id: uuid.UUID


@pytest.fixture
def db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    return url


@pytest.fixture
def conn(db_url: str) -> Iterator[Connection]:
    """A connection to a freshly migrated and seeded database."""
    c = connect(db_url)
    c.execute("DROP SCHEMA public CASCADE")
    c.execute("CREATE SCHEMA public")
    migrate(c)
    for item in json.loads(SEED.read_text(encoding="utf-8")):
        c.execute(
            "INSERT INTO instruments (symbol, name, asset_class, price) VALUES (%s, %s, %s, %s)",
            (item["symbol"], item["name"], item["asset_class"], Decimal(item["price"])),
        )
    yield c
    c.close()


@pytest.fixture
def new_conn(db_url: str) -> Iterator[Callable[[], Connection]]:
    """Factory for extra connections to the same database (for concurrency tests)."""
    opened: list[Connection] = []

    def make() -> Connection:
        c = connect(db_url)
        opened.append(c)
        return c

    yield make
    for c in opened:
        c.close()


@pytest.fixture
def make_account(conn: Connection) -> Callable[..., Account]:
    """Create a user, an account with a target profile, cash and holdings."""

    def make(
        cash: str = "1000",
        holdings: dict[str, int] | None = None,
        profile: tuple[str, int, int] = ("balanced", 3, 5),
    ) -> Account:
        from mizan.engine.allocation import Goal, target_allocation

        user = conn.execute("INSERT INTO users (name) VALUES ('tester') RETURNING id").fetchone()
        assert user is not None
        acc = conn.execute(
            "INSERT INTO accounts (user_id, cash_balance, opening_cash) VALUES (%s, %s, %s) "
            "RETURNING id",
            (user["id"], Decimal(cash), Decimal(cash)),
        ).fetchone()
        assert acc is not None
        for symbol, units in (holdings or {}).items():
            conn.execute(
                "INSERT INTO holdings (account_id, symbol, units, opening_units) VALUES (%s, %s, %s, %s)",
                (acc["id"], symbol, units, units),
            )
        goal, risk, years = profile
        weights = {k.value: str(v) for k, v in target_allocation(Goal(goal), risk, years).items()}
        conn.execute(
            "INSERT INTO target_profiles (account_id, goal, risk, horizon_years, weights) "
            "VALUES (%s, %s, %s, %s, %s)",
            (acc["id"], goal, risk, years, json.dumps(weights)),
        )
        return Account(user["id"], acc["id"])

    return make
