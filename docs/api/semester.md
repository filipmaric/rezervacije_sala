# `semester`

This module owns the shared semester lookup helpers.

It provides:

- the ordered semester list
- the current active semester id
- lookup by semester id
- selecting the semester that the UI should default to

## Examples

The helpers are used by the semester-scoped reservation page.

For example, the My Reservations page calls them indirectly through:

```bash
curl -i -b cookies.txt \
  "http://127.0.0.1:5000/my_reservations_data?semester_id=3"
```

Typical values returned by the helper layer:

- a list of semesters ordered newest first
- the selected semester id
- the selected semester object, or `null` if it cannot be found

::: semester
