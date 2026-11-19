"""Database access: connections and plain SQL migrations."""

from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

Connection = psycopg.Connection[dict[str, Any]]


def connect(url: str) -> Connection:
    """Open an autocommit connection. Callers open explicit transactions with
    `with conn.transaction():` so every transaction boundary is visible in the code."""
    return psycopg.connect(url, autocommit=True, row_factory=dict_row)


def _vector_available(conn: Connection) -> bool:
    row = conn.execute(
        "SELECT 1 AS ok FROM pg_available_extensions WHERE name = 'vector'"
    ).fetchone()
    return row is not None


def migrate(conn: Connection) -> list[str]:
    """Apply pending migrations in file name order and return the names applied.

    Migrations whose file name ends with `_vector.sql` are skipped when the server does not
    provide the pgvector extension, so a plain PostgreSQL still works for development."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    vector = _vector_available(conn)
    applied: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done or (path.name.endswith("_vector.sql") and not vector):
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        applied.append(path.name)
    return applied
