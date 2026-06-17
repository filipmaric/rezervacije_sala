# `mobile_auth`

This module exposes the Android login backend:

- `POST /auth/login`
- `GET /auth/me`
- `POST /auth/logout`
- `GET /auth/sessions`
- `GET /attendance/locations`
- `GET /healthz`

It reuses the student RADIUS settings from `config.py`, stores opaque bearer tokens in SQLite,
and blocks a different username from logging in on the same device during the same UTC day.

## Typical Flow

1. The app posts `username`, `password`, `device_id`, and `device_name` to `/auth/login`.
2. The backend returns an opaque bearer token.
3. The app sends `Authorization: Bearer <token>` to `/auth/me`, `/attendance/locations`, or `/auth/logout`.

::: mobile_auth
