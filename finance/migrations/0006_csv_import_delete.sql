-- Round 41: a delete option for a CSV import and everything it inserted
-- -- same soft-delete/restore/30-day-purge lifecycle every other row in
-- this app already gets (REQUIREMENTS.md section 11), which csv_imports
-- never had a deleted_at column to participate in until now.
ALTER TABLE csv_imports ADD COLUMN deleted_at TEXT;

INSERT INTO schema_version (version) VALUES (6);
