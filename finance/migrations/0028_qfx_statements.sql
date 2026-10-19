-- 1.6.0: QFX / OFX statements (SPEC.md section 4a) and "QFX only" as a preferred upload method.

-- Where a statement came from: a PDF read by the AI (every statement before this) or a QFX/OFX download read
-- in the request. A QFX file's own date range (often "the last 90 days") is kept as period_start / period_end;
-- import_details is the QFX reading's debug record (JSON: the file's blocks, the account each went to, each row
-- with the date used, the sign rule and its FITID). Account numbers are only ever kept as their last 4.
ALTER TABLE statements ADD COLUMN source TEXT NOT NULL DEFAULT 'pdf' CHECK (source IN ('pdf', 'qfx'));
ALTER TABLE statements ADD COLUMN period_start TEXT;
ALTER TABLE statements ADD COLUMN period_end TEXT;
ALTER TABLE statements ADD COLUMN import_details TEXT;

-- The bank's own id of a transaction (OFX <FITID>), so an overlapping second download skips what's already in.
ALTER TABLE transactions ADD COLUMN fitid TEXT;
CREATE INDEX IF NOT EXISTS idx_transactions_fitid ON transactions(account_id, fitid) WHERE fitid IS NOT NULL;

-- preferred_upload_method's CHECK gains 'qfx'. SQLite can't change a CHECK in place, so accounts is rebuilt
-- (SQLite's documented procedure, as 0007 did): new table, copy, drop, rename. Every other table's foreign key
-- names "accounts" and keeps pointing at the same ids.
PRAGMA foreign_keys = OFF;

CREATE TABLE accounts_new (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               TEXT    NOT NULL,
    name                  TEXT    NOT NULL,
    type                  TEXT    NOT NULL CHECK (type IN ('checking', 'savings', 'credit_card')),
    bank_name             TEXT,
    account_number_last4  TEXT,                                  -- masked, never the full number
    is_archived           INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),   -- section 11, non-destructive
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at            TEXT,                                  -- section 11: soft-delete, Scope B; NULL = active
    preferred_upload_method TEXT NOT NULL DEFAULT 'both'
        CHECK (preferred_upload_method IN ('both', 'pdf', 'csv', 'qfx')),
    website_url           TEXT
);

INSERT INTO accounts_new (id, user_id, name, type, bank_name, account_number_last4, is_archived, created_at,
                          deleted_at, preferred_upload_method, website_url)
SELECT id, user_id, name, type, bank_name, account_number_last4, is_archived, created_at,
       deleted_at, preferred_upload_method, website_url
FROM accounts;

DROP TABLE accounts;
ALTER TABLE accounts_new RENAME TO accounts;

CREATE INDEX IF NOT EXISTS idx_accounts_user       ON accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_accounts_deleted    ON accounts(deleted_at) WHERE deleted_at IS NOT NULL;

PRAGMA foreign_keys = ON;

INSERT INTO schema_version (version) VALUES (28);
