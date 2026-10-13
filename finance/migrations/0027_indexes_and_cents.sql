-- 1.4.2 (review 2026-10): indexes for the pages that grew slow on a long history, and every stored
-- amount in whole cents.

-- Dashboard / Transactions / the month list: one person's live rows by date.
CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date) WHERE deleted_at IS NULL;
-- Skipped possible duplicates, looked up from their statement / CSV import and the rows they point at.
CREATE INDEX IF NOT EXISTS idx_skipped_duplicates_statement ON skipped_duplicates(statement_id);
CREATE INDEX IF NOT EXISTS idx_skipped_duplicates_csv_import ON skipped_duplicates(csv_import_id);
CREATE INDEX IF NOT EXISTS idx_skipped_duplicates_matched ON skipped_duplicates(matched_transaction_id);
CREATE INDEX IF NOT EXISTS idx_skipped_duplicates_inserted ON skipped_duplicates(inserted_transaction_id);
-- Toll trips: the passes of one trip.
CREATE INDEX IF NOT EXISTS idx_toll_transactions_trip ON toll_transactions(trip_id);

-- An amount read by the AI used to be stored as it came (e.g. 12.3400000001); the duplicate check and
-- the transfer matching compare cents. (A value that rounds to 0.00 is left alone: amount != 0 is a rule.)
UPDATE transactions SET amount = ROUND(amount, 2) WHERE amount != ROUND(amount, 2) AND ROUND(amount, 2) != 0;
UPDATE skipped_duplicates SET amount = ROUND(amount, 2) WHERE amount != ROUND(amount, 2) AND ROUND(amount, 2) != 0;

INSERT INTO schema_version (version) VALUES (27);
