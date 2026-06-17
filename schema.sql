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
CREATE TABLE attendance_records (
    id INTEGER PRIMARY KEY,
    event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
    event_id INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    username TEXT NOT NULL,
    registration_source TEXT NOT NULL DEFAULT 'web' CHECK(registration_source IN ('web', 'android')),
    client_ip TEXT,
    failed_attempts_before_success INTEGER NOT NULL DEFAULT 0,
    spot_check_flagged INTEGER NOT NULL DEFAULT 0,
    spot_check_teacher_username TEXT,
    spot_check_flagged_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_kind, event_id, event_date, username)
);
CREATE INDEX idx_attendance_records_event ON attendance_records(event_kind, event_id, event_date);
CREATE TABLE attendance_session_failures (
    session_token TEXT PRIMARY KEY,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE students (
    username TEXT PRIMARY KEY,
    student_index TEXT NOT NULL,
    surname TEXT NOT NULL,
    given_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_students_student_index ON students(student_index);
CREATE TABLE mobile_auth_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radius_username TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE mobile_auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    device_name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_reason TEXT,
    FOREIGN KEY(user_id) REFERENCES mobile_auth_users(id)
);
CREATE INDEX idx_mobile_auth_sessions_user_id ON mobile_auth_sessions(user_id);
CREATE INDEX idx_mobile_auth_sessions_token_hash ON mobile_auth_sessions(token_hash);
CREATE TABLE mobile_auth_device_login_policies (
    device_id TEXT PRIMARY KEY,
    last_username TEXT NOT NULL,
    last_login_date TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_mobile_auth_device_login_policies_login_date ON mobile_auth_device_login_policies(last_login_date);
