-- Round 98: per-transaction duplicate detection during PDF statement
-- processing (process_statement, parser/pipeline.py) -- mirrors the exact
-- (account, date, amount, description) check CSV import already runs, so
-- a statement re-derived from the same underlying data (a re-downloaded
-- copy of the same PDF with different bytes/hash, or a period that was
-- already imported via CSV) no longer creates real duplicate transaction
-- rows. It also catches a transaction the AI extraction reads twice
-- within its own single pass (e.g. also reading a totals/summary line as
-- if it were its own transaction) -- excluded from both the insert and
-- the whole-statement reconciliation sum, since it was never really
-- there twice.
--
-- duplicate_transactions holds the human-readable notes for whichever of
-- the above happened on this statement (JSON list of strings, same shape
-- as csv_imports.duplicates), shown on the statement debug page. NULL
-- when nothing was skipped.
ALTER TABLE statements ADD COLUMN duplicate_transactions TEXT;

INSERT INTO schema_version (version) VALUES (15);
