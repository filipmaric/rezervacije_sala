-- Enable foreign keys (run as first statement in sqlite session)
PRAGMA foreign_keys = ON;

-- rooms
CREATE TABLE IF NOT EXISTS rooms (
id INTEGER PRIMARY KEY,
name TEXT NOT NULL UNIQUE,
capacity INTEGER DEFAULT 0,
type TEXT,
location TEXT NOT NULL
);

-- teachers
CREATE TABLE IF NOT EXISTS teachers (
id INTEGER PRIMARY KEY,
name TEXT NOT NULL UNIQUE
);


-- lectures (jedinstvena pedagoska jedinica sa jednim teacher-om)
CREATE TABLE IF NOT EXISTS lectures (
id INTEGER PRIMARY KEY,
name TEXT NOT NULL,
teacher_id INTEGER,
FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE
);


-- groups (studentske grupe)
CREATE TABLE IF NOT EXISTS groups (
id INTEGER PRIMARY KEY,
name TEXT NOT NULL UNIQUE,
description TEXT
);


-- many-to-many: lecture <-> group
CREATE TABLE IF NOT EXISTS lecture_groups (
lecture_id INTEGER NOT NULL,
group_id INTEGER NOT NULL,
PRIMARY KEY (lecture_id, group_id),
FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE,
FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
);


-- weekly_classes: ponavljajuci termini (template)
CREATE TABLE IF NOT EXISTS weekly_classes (
id INTEGER PRIMARY KEY,
lecture_id INTEGER NOT NULL,
room_id INTEGER NOT NULL,
day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6), -- 0=Monday
start_slot INTEGER NOT NULL CHECK(start_slot >= 0),
end_slot INTEGER NOT NULL CHECK(end_slot > start_slot),
FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE,
FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE RESTRICT
);

-- days: koji datumi su radni dani
CREATE TABLE IF NOT EXISTS days (
date TEXT PRIMARY KEY, -- ISO YYYY-MM-DD
is_working INTEGER NOT NULL CHECK(is_working IN (0,1)),
week_day INTEGER NOT NULL DEFAULT -1 -- podrazumevano je da se gleda iz kalendara, ali može i posebno da se zada (npr. radna subota)
);


-- reservations: ad-hoc rezervacije sala
CREATE TABLE IF NOT EXISTS reservations (
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

-- administrators
CREATE TABLE IF NOT EXISTS administrators (
    username TEXT PRIMARY KEY
);

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_weekly_classes_room_day ON weekly_classes(room_id, day_of_week, start_slot);
CREATE INDEX IF NOT EXISTS idx_reservations_room_date ON reservations(room_id, date, start_slot);
CREATE INDEX IF NOT EXISTS idx_reservations_date ON reservations(date);

-- Optional sample data (uncomment to seed a small demo dataset)
-- INSERT INTO rooms (name, capacity, type) VALUES ('706', 80, 'lecture'), ('718', 30, 'lab');
-- INSERT INTO teachers(name) VALUES ('Petar Petrović'), ('Marko Marković'), ('Jovana Jovanović');
-- INSERT INTO lectures (name, teacher_id) VALUES ('Algoritmi', 1), ('Baze podataka', 2), ('Geometrija', 3);
-- INSERT INTO groups (name) VALUES ('301'), ('302');
-- INSERT INTO lecture_groups (lecture_id, group_id) VALUES (1,1),(1,2),(2,1);
-- INSERT INTO weekly_classes (lecture_id, room_id, day_of_week, start_slot, end_slot) VALUES (1,1,0,8,10),(2,2,2,10,12),(3,2,3,11,15),(1,1,3,16,17);
-- INSERT INTO days (date, is_working) VALUES ('2025-09-01',1),('2025-09-02',1);
-- INSERT INTO days (date, is_working, week_day) VALUES ('2025-12-01',1,3);
-- Primer insert-a korisnika
-- INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description)
-- VALUES (1, 1, '2025-09-01', 12, 13, 'Ad-hoc meeting, no conflict with lectures');
INSERT INTO administrators (username) VALUES ('prodekanzanastavu'), ('admin');
