-- =============================================================================
-- 0007_utility_bills_async_placeholder.sql
-- =============================================================================
-- Phase 6 (REQUIREMENTS.md section 15) -- utility bill upload/parsing.
-- utility_bills as originally migrated (0001) required utility_type,
-- provider, period_start_date, period_end_date and cost all NOT NULL --
-- fine for a row inserted only once a bill is fully parsed, but the
-- async-processing pattern this app already uses for bank statements
-- (section 4: "this row's own id IS the job id the upload response
-- returns; no separate jobs table") needs to insert a row BEFORE any of
-- that is known, at upload time, so the UI has something to poll status
-- on immediately. statements gets away with this because its only
-- NOT-NULL "content" column is account_id, which is genuinely known
-- upfront (the user picks it on the upload form) -- everything actually
-- extracted by parsing (bank_name, statement_period, ...) was already
-- nullable there. utility_bills has no equivalent upfront-known column
-- for utility_type specifically: a single Xcel PDF can yield BOTH an
-- electric row and a gas row, so utility_type genuinely isn't knowable
-- until parsing runs, not just "not yet entered like the others are".
--
-- Fix: `provider` becomes the upfront-known column (picked on the
-- upload form, same role account_id plays for statements -- exactly
-- two real values, Xcel Energy / Aurora Water, section 15), and
-- utility_type / period_start_date / period_end_date / cost all become
-- nullable, filled in once parsing completes -- the same relationship
-- statements already has with bank_name/statement_period/
-- account_number_last4.
--
-- SQLite can't ALTER a column's NOT NULL/CHECK in place, so this is a
-- full rebuild (SQLite's own documented 12-step procedure). Zero real
-- rows exist in this table as of this migration (Phase 6 was never
-- built before this round -- STATUS.md), so there's no data at risk,
-- but it's still done as a proper rebuild (temp table, copy, drop,
-- rename), not a DROP/CREATE shortcut, since electric_meter_details'
-- FK needs foreign_keys off for the swap regardless of row count, and
-- a real rebuild is what stays correct if this migration file is ever
-- copied as a template for a future one that DOES have real rows.
-- =============================================================================

PRAGMA foreign_keys = OFF;

CREATE TABLE utility_bills_new (
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
);

INSERT INTO utility_bills_new (
    id, user_id, utility_type, provider, period_start_date, period_end_date,
    usage_amount, usage_unit, cost, due_date, account_number_last4,
    status, error_message, raw_text_length, review_status, deterministic_value, llm_value,
    file_hash, pdf_path, original_filename, uploaded_at, processed_at, pdf_deleted_at,
    deleted_at, current_step, llm_raw_response
)
SELECT
    id, user_id, utility_type, provider, period_start_date, period_end_date,
    usage_amount, usage_unit, cost, due_date, account_number_last4,
    status, error_message, raw_text_length, review_status, deterministic_value, llm_value,
    file_hash, pdf_path, original_filename, uploaded_at, processed_at, pdf_deleted_at,
    deleted_at, current_step, llm_raw_response
FROM utility_bills;

DROP TABLE utility_bills;
ALTER TABLE utility_bills_new RENAME TO utility_bills;

CREATE INDEX IF NOT EXISTS idx_utility_bills_user     ON utility_bills(user_id);
CREATE INDEX IF NOT EXISTS idx_utility_bills_type     ON utility_bills(user_id, utility_type);
CREATE INDEX IF NOT EXISTS idx_utility_bills_period   ON utility_bills(period_start_date);
CREATE INDEX IF NOT EXISTS idx_utility_bills_hash     ON utility_bills(file_hash) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_utility_bills_deleted  ON utility_bills(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_utility_bills_status   ON utility_bills(status) WHERE status = 'processing';
CREATE INDEX IF NOT EXISTS idx_utility_bills_error    ON utility_bills(status) WHERE status = 'error';

PRAGMA foreign_keys = ON;

INSERT INTO schema_version (version) VALUES (7);
