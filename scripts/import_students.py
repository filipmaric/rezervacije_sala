#!/usr/bin/env python3

"""Import aktivniStudenti.csv into the local student directory table."""

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db as mydb


def iso_now():
    """Return the current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value):
    """Trim surrounding whitespace and collapse empty values to an empty string."""
    return (value or "").strip()


def load_students(csv_path, encoding="utf-16"):
    """Read student rows from the exported CSV file."""
    with open(csv_path, "r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"Индекс", "Презиме", "Име", "Кориснички налог"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing columns in CSV: {', '.join(sorted(missing))}")

        students = []
        for row in reader:
            username = normalize_text(row.get("Кориснички налог"))
            student_index = normalize_text(row.get("Индекс"))
            surname = normalize_text(row.get("Презиме"))
            given_name = normalize_text(row.get("Име"))
            if not username:
                continue
            if not student_index or not surname or not given_name:
                continue
            students.append(
                {
                    "username": username,
                    "student_index": student_index,
                    "surname": surname,
                    "given_name": given_name,
                }
            )
    return students


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Import aktivniStudenti.csv into the student directory table."
    )
    parser.add_argument("database", help="SQLite database path")
    parser.add_argument(
        "--csv-file",
        default=str(Path(__file__).resolve().parents[1] / "aktivniStudenti.csv"),
        help="Path to aktivniStudenti.csv",
    )
    parser.add_argument(
        "--encoding",
        default="utf-16",
        help="CSV file encoding (defaults to utf-16)",
    )
    args = parser.parse_args(argv)

    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"CSV file not found: {csv_path}", file=sys.stderr)
        return 2

    try:
        students = load_students(csv_path, encoding=args.encoding)
    except Exception as exc:
        print(f"Failed to read CSV: {exc}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.database)
    try:
        mydb.init_db(conn)
        mydb.ensure_student_directory_schema(conn)
        with conn:
            cur = conn.cursor()
            now = iso_now()
            for student in students:
                cur.execute(
                    """
                    INSERT INTO students (
                        username, student_index, surname, given_name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(username) DO UPDATE SET
                        student_index = excluded.student_index,
                        surname = excluded.surname,
                        given_name = excluded.given_name,
                        updated_at = excluded.updated_at
                    """,
                    (
                        student["username"],
                        student["student_index"],
                        student["surname"],
                        student["given_name"],
                        now,
                        now,
                    ),
                )
        print(f"Imported {len(students)} students into {args.database}")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
