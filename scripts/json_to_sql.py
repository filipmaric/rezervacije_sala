#!/usr/bin/env python3
import json
import sys
from collections import OrderedDict


def sql_str(s):
    return "'" + s.replace("'", "''") + "'"


def main(json_path, out_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # --- 1. Skupljanje entiteta ---
    teachers = OrderedDict()     # name -> id
    rooms = OrderedDict()        # name -> id
    groups = OrderedDict()       # name -> id
    lectures = OrderedDict()     # (subject, teacher) -> id
    lecture_groups = []          # (lecture_id, group_id)
    weekly_classes = []          # raw rows, IDs resolved kasnije

    next_id = {
        "teacher": 1,
        "room": 1,
        "group": 1,
        "lecture": 1,
        "weekly": 1,
    }

    for entry in data:
        subject = entry["subject"].strip()
        teacher = entry["teacher"].strip()
        group = entry["group"].strip()
        room = entry["room"].strip()
        day = entry["day"]
        start_slot = entry["start_slot"]
        end_slot = entry["end_slot"]

        # --- teachers ---
        if teacher not in teachers:
            teachers[teacher] = next_id["teacher"]
            next_id["teacher"] += 1

        # --- rooms ---
        if room not in rooms:
            rooms[room] = next_id["room"]
            next_id["room"] += 1

        # --- groups ---
        if group not in groups:
            groups[group] = next_id["group"]
            next_id["group"] += 1

        # --- lectures (unique per subject + teacher) ---
        lecture_key = (subject, teacher)
        if lecture_key not in lectures:
            lectures[lecture_key] = next_id["lecture"]
            next_id["lecture"] += 1

        lecture_id = lectures[lecture_key]
        group_id = groups[group]
        room_id = rooms[room]

        # many-to-many: lecture -> group
        lecture_groups.append((lecture_id, group_id))

        # weekly_classes (FK will be resolved)
        weekly_classes.append({
            "id": next_id["weekly"],
            "lecture_id": lecture_id,
            "room_id": room_id,
            "day_of_week": day,
            "start_slot": start_slot,
            "end_slot": end_slot,
        })
        next_id["weekly"] += 1

    # --- Remove duplicates in lecture_groups ---
    lecture_groups = list(OrderedDict.fromkeys(lecture_groups))

    # --- 2. Generisanje SQL ---
    out = []

    # teachers
    for name, tid in teachers.items():
        out.append(
            f"INSERT INTO teachers (id, name) VALUES ({tid}, {sql_str(name)});"
        )

    # rooms
    for name, rid in rooms.items():
        out.append(
            f"INSERT INTO rooms (id, name) VALUES ({rid}, {sql_str(name)});"
        )

    # groups
    for name, gid in groups.items():
        out.append(
            f"INSERT INTO groups (id, name) VALUES ({gid}, {sql_str(name)});"
        )

    # lectures
    for (subject, teacher), lid in lectures.items():
        teacher_id = teachers[teacher]
        out.append(
            f"INSERT INTO lectures (id, name, teacher_id) "
            f"VALUES ({lid}, {sql_str(subject)}, {teacher_id});"
        )

    # lecture_groups
    for lecture_id, group_id in lecture_groups:
        out.append(
            f"INSERT INTO lecture_groups (lecture_id, group_id) "
            f"VALUES ({lecture_id}, {group_id});"
        )

    # weekly_classes
    for row in weekly_classes:
        out.append(
            f"INSERT INTO weekly_classes "
            f"(id, lecture_id, room_id, day_of_week, start_slot, end_slot) "
            f"VALUES ({row['id']}, {row['lecture_id']}, {row['room_id']}, "
            f"{row['day_of_week']}, {row['start_slot']}, {row['end_slot']});"
        )

    # upis u fajl
    with open(out_path, "w", encoding="utf-8") as f:
        for line in out:
            f.write(line + "\n")

    print(f"Generisano u {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Upotreba: python json_to_sql.py input.json output.sql")
        sys.exit(1)

    main(sys.argv[1], sys.argv[2])
