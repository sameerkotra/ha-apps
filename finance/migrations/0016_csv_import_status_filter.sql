-- Round 99: a CSV export that includes a Status column (posted/pending,
-- e.g. a card export with Date/Time/Cardholder/Amount/Points/Balance/
-- Status/Type/Merchant/Description) should only import posted rows --
-- a pending row can still change amount or disappear before it settles,
-- so importing it now would create a transaction that later needs manual
-- cleanup. _import_csv_file (routes/transactions.py) now recognizes a
-- "status" column when present and skips any row whose value isn't
-- (case-insensitively) "posted" -- a file with no Status column at all
-- behaves exactly as before, nothing new required of existing formats.
--
-- skipped_status mirrors duplicates/row_errors: a JSON list of
-- human-readable notes, NULL when nothing was skipped, shown on the CSV
-- debug page and the immediate upload-result summary. skipped_status_count
-- is the same list's length, stored alongside for the same reason
-- duplicate_count/error_count are -- so the Uploads-page history badge
-- doesn't need to deserialize JSON just to show a number.
ALTER TABLE csv_imports ADD COLUMN skipped_status_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE csv_imports ADD COLUMN skipped_status TEXT;

INSERT INTO schema_version (version) VALUES (16);
