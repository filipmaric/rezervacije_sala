# Classroom Reservation System

[![docs](https://github.com/filip/rezervacije_sala/actions/workflows/docs.yml/badge.svg)](https://github.com/filip/rezervacije_sala/actions/workflows/docs.yml)
[![tests](https://github.com/filip/rezervacije_sala/actions/workflows/tests.yml/badge.svg)](https://github.com/filip/rezervacije_sala/actions/workflows/tests.yml)

This repository contains a Flask app for room reservations, weekly timetable occupancy, and calendar management.

## Documentation Site

The repository includes a MkDocs-based documentation site in `docs/` with:

- setup instructions,
- deployment notes,
- the attendance flow,
- and module-level API docs generated from Python docstrings.

The main entry points are:

- [docs/index.md](/home/filip/Dropbox/prodekan/rezervacije_sala/docs/index.md) for the docs home page
- [docs/api.md](/home/filip/Dropbox/prodekan/rezervacije_sala/docs/api.md) for the API index
- [mkdocs.yml](/home/filip/Dropbox/prodekan/rezervacije_sala/mkdocs.yml) for the docs site configuration

Build it with:

```bash
pip install mkdocs mkdocstrings[python]
mkdocs serve
```

or:

```bash
mkdocs build
```

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

In production, the app is started by `systemd` through `gunicorn` and `wsgi:application`.
The real production configuration should be kept in an environment file, for example:

- `/var/www/rezervacije/rezervacije.env`

The repository includes [`rezervacije.env.example`](/home/filip/Dropbox/prodekan/rezervacije_sala/rezervacije.env.example) as a template you can copy and fill in.

The service unit should load that file with `EnvironmentFile=...`.

Required variables:

```bash
APP_ENV=production
APPLICATION_ROOT=/rezervacije
STATIC_URL_PATH=/rezervacije/static
TEACHER_AUTH_BACKEND=radius
STUDENT_AUTH_BACKEND=radius
SECRET_KEY=your_flask_session_secret
SERVICE_API_KEY=your_service_api_key
ATTENDANCE_SECRET=your_attendance_signing_secret
ATTENDANCE_ALLOWED_LOCATIONS=[{"name":"MATF","latitude":44.8153,"longitude":20.4567,"radius_m":150}]
TEACHER_RADIUS_SERVER=your.teacher.radius.server
TEACHER_RADIUS_SECRET=your_teacher_radius_secret
TEACHER_RADIUS_DICTIONARY=/path/to/teacher/dictionary
STUDENT_RADIUS_SERVER=your.student.radius.server
STUDENT_RADIUS_SECRET=your_student_radius_secret
STUDENT_RADIUS_DICTIONARY=/path/to/student/dictionary
```

Example service file:

```ini
[Unit]
Description=Gunicorn za Flask aplikaciju rezervacije sala
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/rezervacije
Environment="PATH=/var/www/rezervacije/venv/bin"
EnvironmentFile=/var/www/rezervacije/rezervacije.env
ExecStart=/var/www/rezervacije/venv/bin/gunicorn -b 127.0.0.1:5000 wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart rezervacije
```

`scripts/run_prod.sh` is optional. It is a convenience launcher for manual runs and ad-hoc testing; it is not the canonical production startup path.

The helper script refuses to start unless the required variables are set.

## Lecture Attendance

The main occupancy table shows a QR action on lecture entries that the logged-in user can manage. That opens an attendance page with:

- a QR code for students to scan,
- a live list of registered students,
- and a student check-in page that uses a rotating short challenge code.

Operationally, the attendance flow uses:

- a QR link that rotates every 8 seconds,
- a student attendance session that remains valid for 90 seconds after scanning,
- and a separate challenge code that rotates every 10 seconds on the teacher page.

Teacher authentication uses the `TEACHER_AUTH_BACKEND`/`TEACHER_RADIUS_*` variables above.

Student authentication uses a separate RADIUS server and its own variables:

```bash
export STUDENT_AUTH_BACKEND=radius
export STUDENT_RADIUS_SERVER=your.radius.server
export STUDENT_RADIUS_SECRET=your_shared_secret
export STUDENT_RADIUS_DICTIONARY=/path/to/dictionary
```

The challenge generator uses `ATTENDANCE_SECRET` if you want to override the default signing secret.
`ATTENDANCE_ALLOWED_LOCATIONS` is a JSON list of fixed geofences where Android QR scanning is allowed. Each entry needs `name`, `latitude`, `longitude`, and `radius_m`.
Attendance records also store the best-effort client IP address and the registration source (`web` or `android`).

If you have `aktivniStudenti.csv` and want to load it into the local student directory, run:

```bash
python scripts/import_students.py app.db --csv-file aktivniStudenti.csv
```

The importer creates the `students` table automatically if it does not already exist.

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
- Local development uses mock teacher authentication unless `TEACHER_AUTH_BACKEND=radius` is set.
- Run the test suite with:

```bash
pytest -q
```
