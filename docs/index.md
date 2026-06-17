# Classroom Reservation System

This site documents the classroom reservation app, its deployment setup, and the Python modules that implement the API.

## What to read

- [Setup](setup.md) for a fresh clone
- [Production](deployment.md) for `systemd` deployment
- [Attendance](attendance.md) for QR attendance flow
- [API reference](api.md) for the module-level Python API docs

## Module Map

- `auth.py`: login, logout, CSRF, and rate limiting
- `mobile_auth.py`: Android bearer-token auth backed by student RADIUS
- `attendance.py`: QR attendance and check-in flows
- `occupancy.py`: room list and occupancy reads
- `calendar_views.py`: calendar metadata and updates
- `semester.py`: shared semester lookup helpers
- `reservations.py`: reservation writes and cancellations
- `reservations_views.py`: the My Reservations page
- `db.py`: SQLite helpers
- `config.py`: environment-backed settings
- `factory.py`: app creation
- `main.py`: homepage blueprint
