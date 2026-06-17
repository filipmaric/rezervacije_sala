# API Reference

The route overview in `app.py` gives a quick map of the application.

This section links to the Python modules that implement the behavior behind those routes.

- `auth.py` for login, logout, CSRF, and rate limiting
- `mobile_auth.py` for Android bearer-token auth
- `attendance.py` for QR attendance and check-in flows
- `occupancy.py` for room list and occupancy reads
- `calendar_views.py` for calendar metadata and updates
- `semester.py` for shared semester lookup helpers
- `reservations.py` for reservation writes and cancellations
- `reservations_views.py` for the My Reservations page
- `db.py` for SQLite helpers
- `config.py` for environment-backed settings
- `factory.py` for app creation
- `main.py` for the homepage blueprint

## Quick Route Examples

```bash
curl -i http://127.0.0.1:5000/
curl -i "http://127.0.0.1:5000/occupancy?date=2026-03-09"
curl -i -H "Content-Type: application/json" \
  -d '{"username":"teacher","password":"secret"}' \
  http://127.0.0.1:5000/login
curl -i -H "Content-Type: application/json" \
  -d '{"username":"student","password":"secret","device_id":"phone-1","device_name":"Android"}' \
  http://127.0.0.1:5000/auth/login
curl -i "http://127.0.0.1:5000/my_reservations_data"
curl -i "http://127.0.0.1:5000/calendar_data?month=3&year=2026"
```

For attendance, the teacher page first fetches:

- `GET /attendance/<kind>/<event_id>/<event_date>/data`

The student page uses:

- `GET /attendance/<kind>/<event_id>/<event_date>/challenge`
- `POST /attendance/<kind>/<event_id>/<event_date>/join`
