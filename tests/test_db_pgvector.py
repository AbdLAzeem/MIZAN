"""Runs only when DATABASE_URL points at PostgreSQL with pgvector (always true in CI)."""

import os

import pytest

pytestmark = pytest.mark.db

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")
def test_postgres_has_pgvector() -> None:
    import psycopg

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        available = conn.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
        ).fetchone()
        if available is None:
            pytest.skip("pgvector is not installed on this server")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        row = conn.execute("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector").fetchone()
    assert row is not None
    assert row[0] == 1.0
