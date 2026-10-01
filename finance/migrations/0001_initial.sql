-- =============================================================================
-- Finance dashboard — full schema (REQUIREMENTS.md draft v18, Phase 3 state)
-- =============================================================================
-- No ORM (section 12) — raw sqlite3, applied via the migration system
-- described in section 12: this file IS 0001_initial.sql.
--
-- Consolidated from what were originally nine migrations
-- (0001_initial through 0009_review_flag_reason) into this single file.
-- Safe to do only because no real data existed anywhere yet — the app
-- hadn't been used — so there was no deployed `schema_version` history to
-- preserve and no upgrade path to protect. From here on, the normal rule
-- resumes: this file is never edited again once something real depends on
-- it — the next schema change gets its own 0002_*.sql, same discipline as
-- before, just with the counter reset to match this single starting point.
--
-- No `users` table anywhere: identity is HA's X-Remote-User-Id per request
-- (section 2), not something this app owns. Every user_id column below is
-- that plain string, unvalidated against any local table by design.
--
-- Every connection MUST set the pragmas from section 12 (WAL, busy_timeout,
-- foreign_keys, synchronous=NORMAL) before running anything here — omitted
-- from this file since pragmas are connection-scoped, not schema.

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- Migration tracking (section 12)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- -----------------------------------------------------------------------------
-- Accounts (section 8) — checking/savings/credit_card. Solar/utilities are
-- explicitly NOT accounts (section 15) — see utility_bills below instead.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               TEXT    NOT NULL,
    name                  TEXT    NOT NULL,
    type                  TEXT    NOT NULL CHECK (type IN ('checking', 'savings', 'credit_card')),
    bank_name             TEXT,
    account_number_last4  TEXT,                                  -- masked, never the full number
    is_archived           INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),   -- section 11, non-destructive
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at            TEXT                                   -- section 11: soft-delete, Scope B; NULL = active
);

CREATE INDEX IF NOT EXISTS idx_accounts_user       ON accounts(user_id);
-- Partial: the purge sweep / "Recently deleted" view (section 11) only ever
-- wants soft-deleted rows, a small minority of the table at any time.
CREATE INDEX IF NOT EXISTS idx_accounts_deleted    ON accounts(deleted_at) WHERE deleted_at IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Bank/card statements — one row per uploaded PDF (sections 4-7, 11)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS statements (
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
);

CREATE INDEX IF NOT EXISTS idx_statements_user     ON statements(user_id);
CREATE INDEX IF NOT EXISTS idx_statements_account  ON statements(account_id);
-- file_hash lookups (section 6 dedup) only ever want LIVE statements — a
-- soft-deleted statement's hash must never block a legitimate re-upload.
CREATE INDEX IF NOT EXISTS idx_statements_hash     ON statements(file_hash) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_statements_deleted  ON statements(deleted_at) WHERE deleted_at IS NOT NULL;
-- The stuck-processing cleanup sweep (section 4) only ever looks for
-- status='processing' rows — normally a tiny fraction of the table.
CREATE INDEX IF NOT EXISTS idx_statements_status   ON statements(status) WHERE status = 'processing';
CREATE INDEX IF NOT EXISTS idx_statements_error    ON statements(status) WHERE status = 'error';   -- uploads-list filtering

-- -----------------------------------------------------------------------------
-- Transactions (sections 4-9, 11, 16)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
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
);

CREATE INDEX IF NOT EXISTS idx_transactions_account    ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user       ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_statement  ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date       ON transactions(date);          -- search (section 13)
-- The review queue (section 7) only ever lists pending_review items —
-- normally a small minority of all transactions.
CREATE INDEX IF NOT EXISTS idx_transactions_review     ON transactions(review_status) WHERE review_status = 'pending_review';
CREATE INDEX IF NOT EXISTS idx_transactions_deleted    ON transactions(deleted_at) WHERE deleted_at IS NOT NULL;
-- Transaction-level dedup (section 6) matches against LIVE transactions only.
CREATE INDEX IF NOT EXISTS idx_transactions_dedup      ON transactions(account_id, date, amount, description) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- Categorization rules (section 9) — user's own corrections, matched by
-- specificity (exact > prefix > substring) so a broad rule saved later
-- can't silently override a precise one saved earlier. Implemented in
-- app/categorize.py.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT    NOT NULL,
    pattern       TEXT    NOT NULL,                                -- e.g. "AMAZON", "AMAZON DIGITAL"
    match_type    TEXT    NOT NULL CHECK (match_type IN ('exact', 'prefix', 'substring')),
    category      TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_category_rules_user ON category_rules(user_id);

-- -----------------------------------------------------------------------------
-- Recurring-charge overrides (section 16) — auto-detection is computed from
-- transactions at query time (not stored per-row); this table only holds the
-- manual exceptions layered on top of that detection.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recurring_overrides (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT    NOT NULL,
    merchant_pattern  TEXT    NOT NULL,
    is_recurring      INTEGER NOT NULL CHECK (is_recurring IN (0, 1)),  -- dismiss a false positive, or force-flag early
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_recurring_overrides_user ON recurring_overrides(user_id);

-- -----------------------------------------------------------------------------
-- Utility bills (section 15) — deliberately separate from accounts/
-- transactions entirely; no FK to either table, no cross-linking (confirmed).
-- Mirrors the statements/transactions pattern for dual-extraction, async
-- processing, and PDF lifecycle rather than inventing a second mechanism.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS utility_bills (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                TEXT    NOT NULL,

    utility_type            TEXT    NOT NULL CHECK (utility_type IN ('electric', 'gas', 'water')),
    provider               TEXT    NOT NULL,                       -- "Xcel Energy" / "Aurora Water"

    period_start_date        TEXT    NOT NULL,                     -- ISO-8601 — exact bounds, not a loose month string
    period_end_date          TEXT    NOT NULL,                     -- (section 15: Xcel cycles don't align to calendar months)

    usage_amount            REAL,
    usage_unit              TEXT,                                  -- kWh / therms / CCF / gallons
    cost                    REAL    NOT NULL,
    due_date                TEXT,

    account_number_last4     TEXT,

    -- Async processing + dual-extraction review — same shape as statements/
    -- transactions above, reused rather than reinvented (section 15)
    status                  TEXT    NOT NULL DEFAULT 'processing'
                                   CHECK (status IN ('processing', 'pending_review', 'complete', 'error')),
    error_message            TEXT,
    raw_text_length          INTEGER,
    review_status            TEXT    NOT NULL DEFAULT 'clean'
                                   CHECK (review_status IN ('clean', 'pending_review')),
    deterministic_value      TEXT,                                  -- JSON: {"usage_amount":..,"cost":..,...}
    llm_value                TEXT,

    file_hash               TEXT,
    pdf_path                 TEXT,
    original_filename        TEXT,
    uploaded_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    processed_at             TEXT,
    pdf_deleted_at            TEXT,

    deleted_at               TEXT,                                  -- section 11's soft-delete pattern, reused

    -- Processing visibility (admin debug view, section 13) — same as statements
    current_step             TEXT,
    llm_raw_response          TEXT
    -- solar_generated_kwh / solar_used_kwh deliberately NOT here — couldn't
    -- represent what real Xcel statements actually show (section 15);
    -- electric_meter_details below is the real replacement, not a rename.
);

CREATE INDEX IF NOT EXISTS idx_utility_bills_user     ON utility_bills(user_id);
CREATE INDEX IF NOT EXISTS idx_utility_bills_type     ON utility_bills(user_id, utility_type);  -- comparison view (section 15)
CREATE INDEX IF NOT EXISTS idx_utility_bills_period   ON utility_bills(period_start_date);
CREATE INDEX IF NOT EXISTS idx_utility_bills_hash     ON utility_bills(file_hash) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_utility_bills_deleted  ON utility_bills(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_utility_bills_status   ON utility_bills(status) WHERE status = 'processing';
CREATE INDEX IF NOT EXISTS idx_utility_bills_error    ON utility_bills(status) WHERE status = 'error';

-- -----------------------------------------------------------------------------
-- electric_meter_details — 1:1 companion to an electric utility_bills row,
-- holding the full on/off-peak meter-reading breakdown. Never created for
-- gas/water. Field groups mirror the bill's own four-way split exactly
-- (on-peak / off-peak / total for each of four meanings) rather than being
-- flattened into fewer numbers — REQUIREMENTS.md section 15 explains why
-- each group means something genuinely different and none can be derived
-- from another:
--   grid_import_*   — "Delivered by Xcel": grid -> you
--   solar_export_*  — "Delivered by Customer": you -> grid
--   net_*           — "Net Delivered by Xcel": import minus export, this
--                      is what the on/off-peak RATE is actually applied to
--   net_generated_* — "Net Generated by Customer": only nonzero when you
--                      exported MORE than you imported overall; reads 0
--                      most months even with real solar production
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS electric_meter_details (
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

-- -----------------------------------------------------------------------------
-- Admin config (section 3) — ADMIN_USERS is an env var per the doc, not a
-- table; nothing here manages it. No table needed for auth at all, by design.
-- -----------------------------------------------------------------------------

INSERT INTO schema_version (version) VALUES (1);
