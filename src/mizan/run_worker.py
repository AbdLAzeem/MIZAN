"""Process pending outbox events: `python -m mizan.run_worker [--loop]`."""

import argparse
import os
import sys
import time

from mizan.db import connect
from mizan.worker import run_until_idle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizan.run_worker")
    parser.add_argument("--loop", action="store_true", help="keep polling every 2 seconds")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL first", file=sys.stderr)
        return 2
    conn = connect(url)
    while True:
        outcomes = run_until_idle(conn)
        for o in outcomes:
            print(f"event {o.event_id}: {o.status}")
        if not args.loop:
            if not outcomes:
                print("no pending events")
            return 0
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
