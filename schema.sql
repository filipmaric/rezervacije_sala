CREATE TABLE groups (
id INTEGER PRIMARY KEY,
name TEXT NOT NULL UNIQUE,
description TEXT
);
CREATE TABLE days (
date TEXT PRIMARY KEY, -- ISO YYYY-MM-DD
is_working INTEGER NOT NULL CHECK(is_working IN (0,1)),
week_day INTEGER NOT NULL DEFAULT -1 -- podrazumevano je da se gleda iz kalendara, ali može i posebno da se zada (npr. radna subota)
);
CREATE TABLE reservations (
id INTEGER PRIMARY KEY,
room_id INTEGER NOT NULL,
username INTEGER,
date TEXT NOT NULL, -- YYYY-MM-DD
start_slot INTEGER NOT NULL CHECK(start_slot >= 0),
end_slot INTEGER NOT NULL CHECK(end_slot > start_slot),
description TEXT,
created_at TEXT NOT NULL DEFAULT (datetime('now')),
FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE RESTRICT
);
CREATE TABLE administrators (
    username TEXT PRIMARY KEY
);
CREATE INDEX idx_reservations_room_date ON reservations(room_id, date, start_slot);
CREATE INDEX idx_reservations_date ON reservations(date);
CREATE TABLE semesters (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,          -- e.g. "Winter 2025/26"
    start_date TEXT NOT NULL,           -- ISO YYYY-MM-DD
    end_date TEXT NOT NULL,
    CHECK (end_date > start_date)
);
CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT UNIQUE);
CREATE TABLE courses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT,
    UNIQUE(name, code)
);
CREATE TABLE course_sessions (
    id INTEGER PRIMARY KEY,

    course_id INTEGER NOT NULL,
    teacher_id INTEGER NOT NULL,
    semester_id INTEGER NOT NULL,

    type TEXT NOT NULL,   -- p / v / l

    FOREIGN KEY(course_id) REFERENCES courses(id),
    FOREIGN KEY(teacher_id) REFERENCES teachers(id),
    FOREIGN KEY(semester_id) REFERENCES semesters(id)
);
CREATE TABLE session_groups (
    session_id INTEGER,
    group_id INTEGER,

    PRIMARY KEY(session_id, group_id),

    FOREIGN KEY(session_id) REFERENCES course_sessions(id),
    FOREIGN KEY(group_id) REFERENCES groups(id)
);
CREATE TABLE weekly_sessions (
    id INTEGER PRIMARY KEY,

    session_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,

    day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),

    start_slot INTEGER NOT NULL,
    end_slot INTEGER NOT NULL,

    FOREIGN KEY(session_id) REFERENCES course_sessions(id),
    FOREIGN KEY(room_id) REFERENCES rooms(id)
);
CREATE TABLE rooms (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, capacity INTEGER DEFAULT 0, type TEXT, location TEXT NOT NULL, code TEXT UNIQUE, priority INTEGER DEFAULT (100));
CREATE TABLE weekly_cancellations ( id INTEGER PRIMARY KEY AUTOINCREMENT, weekly_session_id INTEGER NOT NULL, date TEXT NOT NULL, username TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(weekly_session_id, date), FOREIGN KEY (weekly_session_id) REFERENCES weekly_sessions(id) ON DELETE CASCADE );
