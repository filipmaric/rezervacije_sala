#!/usr/bin/env python3

"""Migrate a legacy production database to the current schema."""

import argparse
from datetime import datetime
import shutil
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db_migrations


def backup_database(db_path):
    """Create a timestamped backup before mutating the database in place."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak.{timestamp}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def main(argv=None):
    """Upgrade a production SQLite database to the current schema."""
    parser = argparse.ArgumentParser(
        description="Migrate a legacy production database to the current schema."
    )
    parser.add_argument(
        "database",
        nargs="?",
        default="production.db",
        help="SQLite database path (defaults to production.db)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a timestamped backup before migrating",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.database)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    if not args.no_backup:
        backup_path = backup_database(db_path)
        print(f"Backup created at {backup_path}")

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        result = db_migrations.migrate_database(conn)
        conn.commit()
        print(
            "Migration completed for "
            f"{db_path} (rooms_changed={result['rooms_changed']}, "
            f"attendance_changed={result['attendance_changed']})"
        )
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
