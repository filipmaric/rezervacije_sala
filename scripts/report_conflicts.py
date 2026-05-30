#!/usr/bin/env python3

import argparse
import datetime as dt
import sqlite3

from timetable_common import (
    collect_future_conflicts,
    format_conflict,
)


def parse_now(value):
    if value is None:
        return dt.datetime.now()
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "now must be an ISO datetime, e.g. 2026-03-10T08:30:00"
        ) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report future timetable and reservation conflicts from a SQLite database."
    )
    parser.add_argument("database", help="SQLite database path")
    parser.add_argument(
        "--now",
        type=parse_now,
        default=None,
        help="ISO datetime used as the cutoff, defaults to the current local time",
    )
    args = parser.parse_args(argv)

    now = args.now or dt.datetime.now()
    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row

    try:
        conflicts = collect_future_conflicts(conn, now)

        if not conflicts:
            print(f"No conflicts found after {now.isoformat(timespec='minutes')}")
            return 0

        print(f"Conflicts found after {now.isoformat(timespec='minutes')}:")
        for conflict in conflicts:
            print(format_conflict(conflict))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
