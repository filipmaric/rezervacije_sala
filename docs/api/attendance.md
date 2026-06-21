# `attendance`

This module owns the attendance subsystem for both teachers and students.

It covers:

- QR entry tokens that rotate quickly
- the student attendance attempt cookie
- the logged-in Android bearer-token flow
- the rotating challenge number shown on the teacher page
- the student check-in POST flow
- the teacher current attendance list JSON

Main routes:

- `GET /attendance/<kind>/<id>/<date>`
- `GET /attendance/<kind>/<id>/<date>/join/<token>`
- `GET /attendance/<kind>/<id>/<date>/join`
- `GET /attendance/<kind>/<id>/<date>/challenge`
- `GET /attendance/<kind>/<id>/<date>/data`
- `POST /attendance/<kind>/<id>/<date>/join`

## Access Notes

- The teacher page and teacher data endpoint require the logged-in teacher who owns the class, or an administrator.
- The student QR join URL is a short-lived tokenized link.
- The student challenge and submission endpoints require the attendance attempt cookie created by scanning the QR code.
- The Android client can call the same challenge and submission endpoints with `Authorization: Bearer <token>`
  plus the scanned `join_token`.

## Examples

Teacher attendance data:

```bash
curl -i -b cookies.txt \
  http://127.0.0.1:5000/attendance/weekly/51/2026-06-01/data
```

Student challenge payload:

```bash
curl -i -b cookies.txt \
  http://127.0.0.1:5000/attendance/weekly/51/2026-06-01/challenge
```

Student check-in submission:

```bash
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"username":"student1","password":"secret","selected_code":1234}' \
  http://127.0.0.1:5000/attendance/weekly/51/2026-06-01/join
```

Android attendance submission:

```bash
curl -i \
  -H "Authorization: Bearer <android-token>" \
  -H "Content-Type: application/json" \
  -d '{"join_token":"<scanned-qr-token>","attendance_attempt_token":"<attempt-token>","selected_code":1234}' \
  http://127.0.0.1:5000/attendance/weekly/51/2026-06-01/join
```

Typical responses:

- `{"event": {...}, "challenge": {...}}`
- `{"students": [...], "join_token": "..."}`
- `{"success": true, "username": "student1"}`

::: attendance
