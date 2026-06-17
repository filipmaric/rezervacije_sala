# `reservations`

This module owns reservation writes and cancellation actions.

It handles:

- creating a single reservation
- creating multiple reservations in one transaction
- canceling a personal reservation
- toggling a weekly lecture occurrence on or off

Main routes:

- `GET /is_admin/<username>`
- `POST /reserve`
- `POST /reserve/bulk`
- `DELETE /reservation/<res_id>`
- `POST /weekly_session_cancel`

## Access Notes

- `POST /reserve` and `POST /reserve/bulk` require a logged-in user or the service bearer key.
- `DELETE /reservation/<res_id>` allows the owner, an administrator, or the service bearer key.
- `POST /weekly_session_cancel` allows the teacher who owns the weekly session, an administrator, or the service bearer key.
- Reservations and weekly class occurrences can be canceled only if they are still in the future, or if they are happening today and the current time is still before the end time.
- A current-day cancellation is also blocked once attendance has been recorded for that event.

## Examples

Create one reservation:

```bash
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"room_id":1,"date":"2026-03-09","start_slot":8,"end_slot":10,"description":"Consultation"}' \
  http://127.0.0.1:5000/reserve
```

Create several reservations at once:

```bash
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"reservations":[{"room_id":1,"date":"2026-03-09","start_slot":8,"end_slot":10,"description":"A"},{"room_id":2,"date":"2026-03-09","start_slot":10,"end_slot":12,"description":"B"}]}' \
  http://127.0.0.1:5000/reserve/bulk
```

Cancel a reservation:

```bash
curl -i -b cookies.txt \
  -X DELETE http://127.0.0.1:5000/reservation/123
```

If the reservation is happening today, the cancel request succeeds only while the current time is still before the end time and no attendance has been recorded yet.

Typical responses:

- `{"reservation_id": 123}`
- `{"created": 2, "reservation_ids": [123, 124]}`
- `{"success": true}`
- `{"error": "Reservations can be canceled only before the end time and only if attendance has not been recorded."}`
- `{"error": "Weekly classes can be canceled only before the end time and only if attendance has not been recorded."}`

::: reservations
