import pytest

from mizan import run_worker, seed
from mizan.db import Connection
from mizan.demo import run_demo


def test_demo_shows_the_agent_cannot_confirm_and_the_user_can(conn: Connection) -> None:
    lines = run_demo(conn)
    text = "\n".join(lines)
    assert "tool prepare_orders: ok" in text
    assert "tool confirm_order: refused by the tool layer" in text
    states = [x for x in lines if x.startswith("State:")]
    assert "confirmed orders 0, executed orders 0" in states[1]  # after the injection
    assert "cash 12.50" in states[-1] and "DIBF 15" in states[-1]
    assert "repeated click replayed: True" in text
    assert lines[-1] == "Reconciliation differences: 0"


def test_seed_prints_ids(
    conn: Connection,
    db_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    assert seed.main() == 0
    out = capsys.readouterr().out
    assert "USER_ID=" in out and "ACCOUNT_ID=" in out
    monkeypatch.delenv("DATABASE_URL")
    assert seed.main() == 2


def test_run_worker_once(
    conn: Connection,
    db_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    assert run_worker.main([]) == 0
    assert "no pending events" in capsys.readouterr().out
    monkeypatch.delenv("DATABASE_URL")
    assert run_worker.main([]) == 2
