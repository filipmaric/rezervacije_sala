# `mobile_attendance`

This module exposes the mobile attendance-history endpoint:

- `GET /mobile/attendance/history`

It returns the current-semester summary for the authenticated mobile student session.

The payload includes one row per course with:

- `course_id`
- `course_name`
- `course_code`
- `attended_lessons`
- `total_lessons_with_recorded_attendance`

::: mobile_attendance
