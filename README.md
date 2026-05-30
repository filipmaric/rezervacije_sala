# Classroom Reservation System

This repository contains a Flask app for room reservations, weekly timetable occupancy, and calendar management.

## Setup After Clone

1. Create and activate a virtual environment.

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies.

```bash
pip install flask flask-login pyrad pytest
```

3. Initialize the database schema.

```bash
python app.py init-db
```

This creates `app.db` from `schema.sql` if it does not already exist.

4. Run the app locally.

```bash
python app.py
```

The default local mount point is `/`.

## Production Deployment

If you run the app on the server under `/rezervacije`, set these environment variables:

```bash
export APPLICATION_ROOT=/rezervacije
export STATIC_URL_PATH=/rezervacije/static
export AUTH_BACKEND=radius
```

Then start the app under your process manager or service supervisor.

If you keep the helper script in this repo, you can also run:

```bash
bash scripts/run_prod.sh
```

## Timetable Import Tools

The repository also includes two utility scripts for timetable data in the underscore-separated format:

- `scripts/import_timetable.py` imports a full timetable into a SQLite database from `stdin`
- `scripts/update_timetable.py` updates existing weekly sessions from a text file and asks for confirmation if the change creates future conflicts

Expected line format:

```text
teacher_groups_coursecode_day_start_end_room
```

Example:

```text
profuser_3A.3B_MAT1.p_pon_8_10_406
```

The import script also expects tab-separated `teachers.csv` and `subjects.csv` metadata files by default. Use `--teachers` and `--subjects` to point to different files if needed.
Field values should not contain `_` because `_` is the separator used by the format.

## Conflict Report

To scan the database for future conflicts between reservations and weekly timetable entries, run:

```bash
python scripts/report_conflicts.py app.db
```

You can also fix the cutoff time for reproducible checks:

```bash
python scripts/report_conflicts.py app.db --now 2026-03-10T08:00:00
```

The script exits with status `1` if conflicts are found and `0` otherwise.

## Notes

- The app creates `app.db` automatically if it is missing, but `python app.py init-db` is the explicit setup path.
- Local development uses mock authentication unless `AUTH_BACKEND=radius` is set.
- Run the test suite with:

```bash
pytest -q
```
