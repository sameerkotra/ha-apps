-- Finance Dashboard: the database schema as migrations 0001-0024 leave it (generated; the
-- migrations in finance/migrations are the source of truth). SQLite, WAL mode, foreign keys on.

CREATE TABLE accounts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               TEXT    NOT NULL,
    name                  TEXT    NOT NULL,
    type                  TEXT    NOT NULL CHECK (type IN ('checking', 'savings', 'credit_card')),
    bank_name             TEXT,
    account_number_last4  TEXT,                                  -- masked, never the full number
    is_archived           INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),   -- section 11, non-destructive
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at            TEXT                                   -- section 11: soft-delete, Scope B; NULL = active
, preferred_upload_method TEXT NOT NULL DEFAULT 'both'
    CHECK (preferred_upload_method IN ('both', 'pdf', 'csv')), website_url TEXT);

CREATE TABLE ai_usage_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    at             TEXT    NOT NULL DEFAULT (datetime('now')),
    purpose        TEXT    NOT NULL DEFAULT '',
    provider       TEXT    NOT NULL DEFAULT '',
    model          TEXT    NOT NULL DEFAULT '',
    input_tokens   INTEGER,                                          -- NULL: not reported (or the request failed)
    output_tokens  INTEGER,
    seconds        REAL    NOT NULL DEFAULT 0,
    error          TEXT    NOT NULL DEFAULT '',
    earlier        INTEGER NOT NULL DEFAULT 0                        -- 1: copied from a document when the log started
);

CREATE TABLE app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by  TEXT
);

CREATE TABLE category_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT    NOT NULL,
    pattern       TEXT    NOT NULL,                                -- e.g. "AMAZON", "AMAZON DIGITAL"
    match_type    TEXT    NOT NULL CHECK (match_type IN ('exact', 'prefix', 'substring')),
    category      TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE csv_imports (
    id                                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                           TEXT    NOT NULL,
    original_filename                 TEXT,
    imported_at                       TEXT    NOT NULL DEFAULT (datetime('now')),
    row_count                         INTEGER NOT NULL,

    -- Same shape as statements.categorization_llm_* below, for the
    -- categorization LLM call this import triggered, if any -- NULL
    -- when every row had an explicit category or matched a rule, since
    -- categorize_via_llm is never even called then.
    categorization_llm_prompt         TEXT,
    categorization_llm_raw_response   TEXT,
    categorization_llm_error          TEXT
, fatal_error TEXT, error_count INTEGER NOT NULL DEFAULT 0, duplicate_count INTEGER NOT NULL DEFAULT 0, row_errors TEXT, duplicates TEXT, deleted_at TEXT, skipped_status_count INTEGER NOT NULL DEFAULT 0, skipped_status TEXT, ai_usage TEXT);

CREATE TABLE custom_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, name)
);

CREATE TABLE electric_meter_details (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    utility_bill_id             INTEGER NOT NULL UNIQUE REFERENCES utility_bills(id) ON DELETE CASCADE,

    grid_import_on_peak_kwh      REAL,
    grid_import_off_peak_kwh     REAL,
    grid_import_total_kwh        REAL,

    solar_export_on_peak_kwh     REAL,
    solar_export_off_peak_kwh    REAL,
    solar_export_total_kwh       REAL,

    net_on_peak_kwh              REAL,
    net_off_peak_kwh             REAL,
    net_total_kwh                REAL,

    net_generated_on_peak_kwh    REAL,
    net_generated_off_peak_kwh   REAL,
    net_generated_total_kwh      REAL,

    on_peak_rate                REAL,   -- $/kWh
    on_peak_charge               REAL,   -- $, computed from net_on_peak_kwh
    off_peak_rate                REAL,
    off_peak_charge              REAL
);

CREATE TABLE known_users (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE recurring_overrides (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT    NOT NULL,
    merchant_pattern  TEXT    NOT NULL,
    is_recurring      INTEGER NOT NULL CHECK (is_recurring IN (0, 1)),  -- dismiss a false positive, or force-flag early
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE saved_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    description     TEXT    NOT NULL DEFAULT '',
    sql             TEXT    NOT NULL,
    variables_json  TEXT    NOT NULL DEFAULT '[]',
    totals_json     TEXT    NOT NULL DEFAULT '{"on": true, "skip": []}',
    chart_json      TEXT    NOT NULL DEFAULT '{"type": "none"}',
    no_time_limit   INTEGER NOT NULL DEFAULT 0 CHECK (no_time_limit IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    last_run_at     TEXT
);

CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')));

CREATE TABLE skipped_duplicates (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 TEXT    NOT NULL,
    account_id              INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    statement_id            INTEGER REFERENCES statements(id) ON DELETE CASCADE,
    csv_import_id           INTEGER REFERENCES csv_imports(id) ON DELETE CASCADE,
    date                    TEXT    NOT NULL,
    amount                  REAL    NOT NULL,
    description             TEXT    NOT NULL,
    category                TEXT,
    reason                  TEXT    NOT NULL CHECK (reason IN ('repeated', 'on_file')),
    matched_transaction_id  INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    status                  TEXT    NOT NULL DEFAULT 'skipped' CHECK (status IN ('skipped', 'inserted', 'dismissed')),
    inserted_transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at             TEXT
);

CREATE TABLE statements (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               TEXT    NOT NULL,
    account_id            INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,

    bank_name             TEXT,
    statement_period      TEXT,                                  -- e.g. "2026-03"; bank cycles align to calendar
                                                                   -- months closely enough that explicit start/end
                                                                   -- bounds (unlike utility_bills) weren't needed
    account_number_last4  TEXT,
    original_filename     TEXT,

    -- Async processing (section 4) — this row's own id IS the job id the
    -- upload response returns; no separate jobs table. htmx polls this row.
    status                TEXT    NOT NULL DEFAULT 'processing'
                                  CHECK (status IN ('processing', 'pending_review', 'complete', 'error')),
    error_message         TEXT,                                  -- set when status = 'error' (section 4)
    raw_text_length       INTEGER,                                -- section 4: near-zero => no text layer => rejected

    -- Duplicate detection (section 6)
    file_hash             TEXT,                                   -- SHA-256; outlives pdf_deleted_at

    -- PDF lifecycle (section 6)
    pdf_path              TEXT,                                   -- /data/pending/<user_id>/<id>.pdf while present
    uploaded_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    processed_at            TEXT,
    pdf_deleted_at           TEXT,

    deleted_at            TEXT,                                   -- section 11: soft-delete, Scope A or B

    -- Processing visibility (admin debug view, section 13)
    current_step          TEXT,                                   -- what's happening right now during status='processing'
    llm_raw_response       TEXT,                                   -- the raw Ollama response text, for debugging

    -- Deterministic-path validation (sections 4-5, 7) — the deterministic
    -- path no longer reconstructs whole transactions to compare against
    -- the LLM's (see app/parser/deterministic.py + pipeline.py); instead:
    deterministic_amounts  TEXT,                                   -- JSON list of every money-shaped figure found in
                                                                     -- the statement's pdftotext text (with duplicates),
                                                                     -- used to validate each LLM-extracted amount
    previous_balance       REAL,                                    -- statement's own printed "Previous Balance",
    new_balance            REAL,                                    -- and "New Balance" (found by label) — NULL when
                                                                     -- neither label was found (reconciliation skipped,
                                                                     -- not treated as a mismatch)
    balance_mismatch       TEXT                                     -- NULL when the extracted transactions' sum
                                                                     -- accounts for that movement (or reconciliation
                                                                     -- was skipped); otherwise a human-readable note
                                                                     -- of the discrepancy. Whole-statement flag, not
                                                                     -- tied to any one transaction row — see
                                                                     -- app/parser/pipeline.py's _check_reconciliation.
, categorization_llm_prompt TEXT, categorization_llm_raw_response TEXT, categorization_llm_error TEXT, needs_confirmation INTEGER NOT NULL DEFAULT 0, confirmed_at TEXT, duplicate_transactions TEXT, extraction_notes TEXT, ai_usage TEXT);

CREATE TABLE toll_cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at TEXT
);

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

CREATE TABLE toll_pattern_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE toll_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT,
    signature TEXT NOT NULL,
    window_start_min INTEGER NOT NULL,
    window_end_min INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    ignored INTEGER NOT NULL DEFAULT 0
, group_id INTEGER REFERENCES toll_pattern_groups(id) ON DELETE SET NULL);

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
, ai_usage TEXT);

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

CREATE TABLE toll_trip_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    trip_key TEXT NOT NULL,
    first_dedupe_key TEXT NOT NULL,
    pattern_id INTEGER REFERENCES toll_patterns(id) ON DELETE CASCADE,
    UNIQUE (user_id, trip_key)
);

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

CREATE TABLE transactions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               TEXT    NOT NULL,
    account_id            INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    statement_id          INTEGER REFERENCES statements(id) ON DELETE SET NULL,

    date                  TEXT    NOT NULL,                       -- section 8: transaction date, canonical/resolved
    post_date             TEXT,                                   -- kept for reference when a statement gives both
    amount                REAL    NOT NULL CHECK (amount != 0),   -- signed: negative=outflow, positive=inflow, for a
                                                                   -- checking/savings account — but the MIRROR of that
                                                                   -- on a credit_card account, where a purchase is
                                                                   -- positive (increases what's owed) and a payment is
                                                                   -- negative (reduces it). See REQUIREMENTS.md section 5.
                                                                   -- The sign convention ITSELF is an app-layer contract,
                                                                   -- not DB-enforceable (correct sign is contextual, not
                                                                   -- structural) — but a $0 transaction is essentially
                                                                   -- always a parsing bug, and that much IS checkable.
    description           TEXT    NOT NULL,
    bank_reference        TEXT,                                   -- section 6: dedup key when the bank provides one

    category              TEXT,                                   -- section 9: NULL until categorized; 'Uncategorized'
                                                                    -- if nothing matched, never silently blank
    txn_type              TEXT    NOT NULL DEFAULT 'expense'
                                  CHECK (txn_type IN ('expense', 'income', 'transfer')),
    is_excluded            INTEGER NOT NULL DEFAULT 0 CHECK (is_excluded IN (0, 1)),   -- section 8 / section 11 cascade

    transfer_pair_id       INTEGER REFERENCES transactions(id) ON DELETE SET NULL,     -- section 8, section 11 cascade

    -- Amount-validation review (sections 4, 5, 7): 'pending_review' means
    -- this transaction's amount wasn't found anywhere in the statement's
    -- own extracted text (app/parser/deterministic.py + pipeline.py) --
    -- no separate JSON snapshot columns needed, since the row's own
    -- date/amount/description ARE the (sole) LLM extraction already.
    review_status          TEXT    NOT NULL DEFAULT 'clean'
                                  CHECK (review_status IN ('clean', 'pending_review')),

    created_at             TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at              TEXT,                                 -- section 11: soft-delete

    -- Phase 3 — REQUIREMENTS.md sections 7/8/13
    note                   TEXT CHECK (note IS NULL OR review_status = 'clean'),
                                                                    -- free-text note, searchable (section 13).
                                                                    -- Restricted to fully-imported rows structurally
                                                                    -- (not just app-level) — a still-flagged row's
                                                                    -- date/amount/description may yet be corrected
                                                                    -- during review, and a note against a value
                                                                    -- about to change would more likely confuse.
    flag_reason             TEXT                                   -- human-readable reason this row landed in
                                                                    -- review_status='pending_review' (currently
                                                                    -- always the amount-validation check above) —
                                                                    -- lets the review queue explain itself instead
                                                                    -- of just showing a bare flag. A statement
                                                                    -- flagged purely by balance reconciliation
                                                                    -- doesn't set this on any row — see
                                                                    -- app/routes/transactions.py's review_queue.
, category_source TEXT, category_matched_pattern TEXT, csv_import_id INTEGER REFERENCES csv_imports(id) ON DELETE SET NULL);

CREATE TABLE user_access (
    viewer_id   TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    granted_by  TEXT NOT NULL,
    granted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (viewer_id, owner_id),
    CHECK (viewer_id != owner_id)
);

CREATE TABLE "utility_bills" (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                TEXT    NOT NULL,

    utility_type            TEXT    CHECK (utility_type IN ('electric', 'gas', 'water')),
    provider               TEXT    NOT NULL,                       -- known upfront (upload form) -- "Xcel Energy" / "Aurora Water"

    period_start_date        TEXT,                                  -- ISO-8601, filled in once parsed
    period_end_date          TEXT,

    usage_amount            REAL,
    usage_unit              TEXT,                                  -- kWh / therms / CCF / gallons
    cost                    REAL,                                  -- filled in once parsed
    due_date                TEXT,

    account_number_last4     TEXT,

    status                  TEXT    NOT NULL DEFAULT 'processing'
                                   CHECK (status IN ('processing', 'pending_review', 'complete', 'error')),
    error_message            TEXT,
    raw_text_length          INTEGER,
    review_status            TEXT    NOT NULL DEFAULT 'clean'
                                   CHECK (review_status IN ('clean', 'pending_review')),
    deterministic_value      TEXT,
    llm_value                TEXT,

    file_hash               TEXT,
    pdf_path                 TEXT,
    original_filename        TEXT,
    uploaded_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    processed_at             TEXT,
    pdf_deleted_at            TEXT,

    deleted_at               TEXT,

    current_step             TEXT,
    llm_raw_response          TEXT
, ai_usage TEXT, extraction_notes TEXT, needs_confirmation INTEGER NOT NULL DEFAULT 0, corrections TEXT);

CREATE INDEX idx_accounts_deleted    ON accounts(deleted_at) WHERE deleted_at IS NOT NULL;

CREATE INDEX idx_accounts_user       ON accounts(user_id);

CREATE INDEX idx_ai_usage_log_at ON ai_usage_log(at);

CREATE INDEX idx_category_rules_user ON category_rules(user_id);

CREATE INDEX idx_csv_imports_user ON csv_imports(user_id);

CREATE INDEX idx_custom_categories_user ON custom_categories(user_id);

CREATE INDEX idx_recurring_overrides_user ON recurring_overrides(user_id);

CREATE INDEX idx_skipped_duplicates_user ON skipped_duplicates (user_id, status);

CREATE INDEX idx_statements_account  ON statements(account_id);

CREATE INDEX idx_statements_deleted  ON statements(deleted_at) WHERE deleted_at IS NOT NULL;

CREATE INDEX idx_statements_error    ON statements(status) WHERE status = 'error';

CREATE INDEX idx_statements_hash     ON statements(file_hash) WHERE deleted_at IS NULL;

CREATE INDEX idx_statements_status   ON statements(status) WHERE status = 'processing';

CREATE INDEX idx_statements_user     ON statements(user_id);

CREATE INDEX idx_toll_cars_user ON toll_cars (user_id);

CREATE INDEX idx_toll_devices_car ON toll_devices (car_id);

CREATE UNIQUE INDEX idx_toll_devices_plate ON toll_devices (user_id, plate, plate_state) WHERE device_id = '';

CREATE UNIQUE INDEX idx_toll_devices_tag ON toll_devices (user_id, device_id) WHERE device_id != '';

CREATE INDEX idx_toll_pattern_groups_user ON toll_pattern_groups (user_id);

CREATE INDEX idx_toll_patterns_group ON toll_patterns (group_id);

CREATE INDEX idx_toll_patterns_user ON toll_patterns (user_id);

CREATE INDEX idx_toll_statements_hash ON toll_statements (user_id, file_hash);

CREATE INDEX idx_toll_statements_user ON toll_statements (user_id, deleted_at);

CREATE INDEX idx_toll_transactions_dedupe ON toll_transactions (user_id, dedupe_key);

CREATE INDEX idx_toll_transactions_device ON toll_transactions (device_ref);

CREATE INDEX idx_toll_transactions_statement ON toll_transactions (statement_id);

CREATE INDEX idx_toll_transactions_user_date ON toll_transactions (user_id, date);

CREATE INDEX idx_toll_trips_user ON toll_trips (user_id, started_at);

CREATE INDEX idx_transactions_account    ON transactions(account_id);

CREATE INDEX idx_transactions_csv_import ON transactions(csv_import_id);

CREATE INDEX idx_transactions_date       ON transactions(date);

CREATE INDEX idx_transactions_dedup      ON transactions(account_id, date, amount, description) WHERE deleted_at IS NULL;

CREATE INDEX idx_transactions_deleted    ON transactions(deleted_at) WHERE deleted_at IS NOT NULL;

CREATE INDEX idx_transactions_review     ON transactions(review_status) WHERE review_status = 'pending_review';

CREATE INDEX idx_transactions_statement  ON transactions(statement_id);

CREATE INDEX idx_transactions_user       ON transactions(user_id);

CREATE INDEX idx_utility_bills_deleted  ON utility_bills(deleted_at) WHERE deleted_at IS NOT NULL;

CREATE INDEX idx_utility_bills_error    ON utility_bills(status) WHERE status = 'error';

CREATE INDEX idx_utility_bills_hash     ON utility_bills(file_hash) WHERE deleted_at IS NULL;

CREATE INDEX idx_utility_bills_period   ON utility_bills(period_start_date);

CREATE INDEX idx_utility_bills_status   ON utility_bills(status) WHERE status = 'processing';

CREATE INDEX idx_utility_bills_type     ON utility_bills(user_id, utility_type);

CREATE INDEX idx_utility_bills_user     ON utility_bills(user_id);
