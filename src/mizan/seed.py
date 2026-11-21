"""Create the schema, the fictional funds, and one demo account: `python -m mizan.seed`."""

import os
import sys

from mizan.db import connect, migrate
from mizan.evals.__main__ import seed_instruments
from mizan.evals.runner import create_account


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL first", file=sys.stderr)
        return 2
    conn = connect(url)
    migrate(conn)
    seed_instruments(conn)
    user_id, account_id = create_account(
        conn, "1000", {"NGEF": 10, "DIBF": 5, "SGLD": 1}, ("balanced", 3, 5)
    )
    print(f"USER_ID={user_id}")
    print(f"ACCOUNT_ID={account_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
