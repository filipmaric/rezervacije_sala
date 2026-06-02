# `calendar_views`

This module owns the calendar metadata view and the working-day update endpoint.

It exposes:

- `GET /calendar`
- `GET /calendar_data`
- `POST /update_calendar`

## Access Notes

- `GET /calendar` and `GET /calendar_data` are public read routes.
- `POST /update_calendar` requires an administrator session or the service bearer key.

## Examples

Fetch month metadata:

```bash
curl -i "http://127.0.0.1:5000/calendar_data?month=3&year=2026"
```

Update working days:

```bash
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '[{"date":"2026-03-09","is_working":true,"week_day":0}]' \
  http://127.0.0.1:5000/update_calendar
```

Open the calendar page:

```bash
  curl -i http://127.0.0.1:5000/calendar
```

Typical response:

- `{"calendar": {...}, "holidays": [...]}` for `GET /calendar_data`

::: calendar_views
