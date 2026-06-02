# Attendance

The attendance flow works like this:

1. A teacher opens the attendance page for one lecture or reservation.
2. The page shows a QR code that rotates every 8 seconds.
3. A student scans the QR code and gets a 90-second attendance session.
4. The student picks a challenge number that rotates every 10 seconds.
5. The server validates the student credentials and check-in choice.

The teacher page also shows the current attendance list for that lecture occurrence.

## Endpoint Flow

Teacher page:

```bash
GET /attendance/<kind>/<event_id>/<event_date>/data
```

Student QR entry:

```bash
GET /attendance/<kind>/<event_id>/<event_date>/join/<token>
```

Student page challenge payload:

```bash
GET /attendance/<kind>/<event_id>/<event_date>/challenge
```

Student check-in submission:

```bash
POST /attendance/<kind>/<event_id>/<event_date>/join
```

The student submission sends:

- `username`
- `password`
- `selected_code`
