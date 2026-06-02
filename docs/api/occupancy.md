# `occupancy`

This module owns the room list and occupancy read endpoints.

It exposes:

- `GET /rooms` for the room catalog
- `GET /occupancy?date=YYYY-MM-DD` for the merged daily timetable view

## Access Notes

- Both endpoints are public read routes.
- `GET /occupancy` requires a `date` query parameter in `YYYY-MM-DD` format.

## Examples

Fetch all rooms:

```bash
curl -i http://127.0.0.1:5000/rooms
```

Fetch only lab rooms:

```bash
curl -i "http://127.0.0.1:5000/rooms?type=lab"
```

Fetch the occupancy for one date:

```bash
  curl -i "http://127.0.0.1:5000/occupancy?date=2026-03-09"
```

Typical response:

- `{"date": "...", "is_working": true, "week_day": 0, "rooms": {...}}`

::: occupancy
