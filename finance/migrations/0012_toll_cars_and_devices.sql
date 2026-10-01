-- E-470, round 80: the person creates cars; the tags (Device #) found on statements are added as they appear
-- and are assigned to a car afterwards. A tag can be moved to another car, and a car can have several tags over
-- time (a replaced transponder). The plate is stored on the tag and follows the newest statement.
--
-- There was no E-470 data when this was written, so the tables that held cars and passes are rebuilt empty and
-- any statement rows (never completed) are cleared rather than migrated.

DELETE FROM toll_trip_overrides;
DELETE FROM toll_statements;
DROP TABLE toll_trips;
DROP TABLE toll_transactions;
DROP TABLE toll_cars;

-- A car: only what the person types. Its tags, plates and totals are read from the tags assigned to it.
CREATE TABLE toll_cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at TEXT
);
CREATE INDEX idx_toll_cars_user ON toll_cars (user_id);

-- A tag found on a statement. device_id '' = a heading with no tag (then identified by plate and state).
CREATE TABLE toll_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    plate TEXT NOT NULL DEFAULT '',
    plate_state TEXT NOT NULL DEFAULT '',
    plate_as_of TEXT,              -- date of the latest pass that set the stored plate
    car_id INTEGER REFERENCES toll_cars(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_toll_devices_tag ON toll_devices (user_id, device_id) WHERE device_id != '';
CREATE UNIQUE INDEX idx_toll_devices_plate ON toll_devices (user_id, plate, plate_state) WHERE device_id = '';
CREATE INDEX idx_toll_devices_car ON toll_devices (car_id);

-- Derived cache, rebuilt from the passes; never hand-edited. vehicle: 'c<car id>' for a tag assigned to a car,
-- 'd<tag row id>' for a tag that is not assigned yet.
CREATE TABLE toll_trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    vehicle TEXT NOT NULL,
    trip_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    signature TEXT NOT NULL,
    pass_count INTEGER NOT NULL,
    amount REAL NOT NULL,
    pattern_id INTEGER REFERENCES toll_patterns(id) ON DELETE SET NULL
);
CREATE INDEX idx_toll_trips_user ON toll_trips (user_id, started_at);

CREATE TABLE toll_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    statement_id INTEGER NOT NULL REFERENCES toll_statements(id) ON DELETE CASCADE,
    device_ref INTEGER NOT NULL REFERENCES toll_devices(id),
    occurred_at TEXT NOT NULL,     -- ISO 'YYYY-MM-DD HH:MM:SS', local time as printed
    date TEXT NOT NULL,            -- 'YYYY-MM-DD'
    agency TEXT,
    road TEXT,
    plaza TEXT,
    lane TEXT,
    direction TEXT,
    amount REAL NOT NULL,
    raw_line TEXT,
    dedupe_key TEXT NOT NULL,
    trip_id INTEGER REFERENCES toll_trips(id) ON DELETE SET NULL,
    review_status TEXT NOT NULL DEFAULT 'clean' CHECK (review_status IN ('clean', 'pending_review')),
    deleted_at TEXT
);
CREATE INDEX idx_toll_transactions_user_date ON toll_transactions (user_id, date);
CREATE INDEX idx_toll_transactions_statement ON toll_transactions (statement_id);
CREATE INDEX idx_toll_transactions_dedupe ON toll_transactions (user_id, dedupe_key);
CREATE INDEX idx_toll_transactions_device ON toll_transactions (device_ref);

INSERT INTO schema_version (version) VALUES (12);
