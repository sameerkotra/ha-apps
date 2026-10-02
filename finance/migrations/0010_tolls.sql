-- Toll statements (REQUIREMENTS.md section 18): a feature area separate from finance and utilities.
-- Nothing here references accounts, transactions or utility_bills.

-- A car is a (device, plate, state) combination seen on a statement; created automatically.
CREATE TABLE toll_cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    plate TEXT NOT NULL DEFAULT '',
    plate_state TEXT NOT NULL DEFAULT '',
    nickname TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, device_id, plate, plate_state)
);

CREATE TABLE toll_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'pending_review', 'complete', 'error')),
    current_step TEXT,
    error_message TEXT,
    original_filename TEXT,
    file_hash TEXT,
    pdf_path TEXT,
    pdf_deleted_at TEXT,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT,
    deleted_at TEXT,
    period_start_date TEXT,
    period_end_date TEXT,
    raw_text_length INTEGER,
    review_status TEXT CHECK (review_status IS NULL OR review_status IN ('clean', 'pending_review')),
    deterministic_value TEXT,      -- JSON of what the deterministic extraction read
    llm_value TEXT,                -- JSON of what the AI extraction read
    llm_raw_response TEXT,
    total_tolls_printed REAL,      -- the statement's printed Total Tolls, stored positive; NULL = none printed
    checks_json TEXT,              -- the verification table (18.4)
    unparsed_json TEXT,            -- lines that started like a pass but did not fit the row pattern
    skipped_duplicates INTEGER NOT NULL DEFAULT 0,
    notes TEXT,                    -- e.g. "New car added: ..."
    review_note TEXT,              -- a mismatch the person acknowledged when confirming a review
    timings_json TEXT
);
CREATE INDEX idx_toll_statements_user ON toll_statements (user_id, deleted_at);
CREATE INDEX idx_toll_statements_hash ON toll_statements (user_id, file_hash);

-- A tag: a regular trip's route (signature) and start-time window. Carries no car.
CREATE TABLE toll_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT,
    signature TEXT NOT NULL,
    window_start_min INTEGER NOT NULL,
    window_end_min INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    ignored INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_toll_patterns_user ON toll_patterns (user_id);

-- Derived cache, rebuilt from the passes; never hand-edited.
CREATE TABLE toll_trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    car_id INTEGER NOT NULL REFERENCES toll_cars(id) ON DELETE CASCADE,
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
    car_id INTEGER NOT NULL REFERENCES toll_cars(id),
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
CREATE INDEX idx_toll_transactions_car ON toll_transactions (car_id);

-- A person's manual choice for one trip: force a tag (pattern_id) or force "not tagged" (NULL).
-- Keyed by trip_key so it survives the rebuilds; first_dedupe_key lets the purge tell when the trip is gone for good.
CREATE TABLE toll_trip_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    trip_key TEXT NOT NULL,
    first_dedupe_key TEXT NOT NULL,
    pattern_id INTEGER REFERENCES toll_patterns(id) ON DELETE CASCADE,
    UNIQUE (user_id, trip_key)
);

INSERT INTO schema_version (version) VALUES (10);
