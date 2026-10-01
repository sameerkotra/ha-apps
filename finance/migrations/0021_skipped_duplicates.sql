-- Transactions an import skipped as possible duplicates (REQUIREMENTS.md section 7), so the Review page
-- can offer "Insert anyway" when the charge is real (e.g. two identical coffees on the same day).
-- reason: 'repeated' = the same date/amount/description appeared again in the same statement or CSV;
--         'on_file'  = it matches a live transaction already stored (another statement or import).
CREATE TABLE IF NOT EXISTS skipped_duplicates (
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
CREATE INDEX IF NOT EXISTS idx_skipped_duplicates_user ON skipped_duplicates (user_id, status);

INSERT INTO schema_version (version) VALUES (21);
