# `mobile_auth`

This module exposes the Android login backend:

- `POST /mobile/login`
- `GET /mobile/me`
- `POST /mobile/logout`
- `GET /mobile/sessions`
- `GET /mobile/healthz`

It reuses the student RADIUS settings from `config.py`, stores opaque bearer tokens in SQLite,
and blocks a different username from logging in on the same device during the same UTC day.
The attendance challenge response includes the geofence for the room where the scanned class
is held.

## Typical Flow

1. The app posts `username`, `password`, `device_id`, and `device_name` to `/mobile/login`.
2. The backend returns an opaque bearer token.
3. The app sends `Authorization: Bearer <token>` to `/mobile/me` or `/mobile/logout`.

::: mobile_auth
