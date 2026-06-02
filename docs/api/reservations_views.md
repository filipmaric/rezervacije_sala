# `reservations_views`

This module serves the "My Reservations" page and the semester-scoped JSON model behind it.

It is read-only and focuses on:

- the semester list
- the selected semester
- the current user's personal reservations
- the current user's weekly course sessions
- the held instances of those weekly sessions inside the semester

It depends on the shared semester lookup helpers in `semester.py`.

## Access Notes

- `GET /my_reservations` shows the HTML page to a logged-in user.
- `GET /my_reservations_data` requires a logged-in user session.

Main routes:

- `GET /my_reservations`
- `GET /my_reservations_data`

## Examples

Fetch the current semester-scoped reservation model:

```bash
curl -i -b cookies.txt \
  "http://127.0.0.1:5000/my_reservations_data?semester_id=3"
```

Fetch the page HTML:

```bash
  curl -i -b cookies.txt http://127.0.0.1:5000/my_reservations
```

Typical response:

- a JSON object with `semesters`, `current_semester_id`, `selected_semester`, `personal_reservations`, and `courses`

::: reservations_views
