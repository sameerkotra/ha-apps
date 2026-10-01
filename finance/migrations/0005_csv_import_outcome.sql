-- Round 40: CSV import stops navigating to a separate full-page results
-- screen (import_results.html) after every import -- it now behaves like
-- PDF upload (inline "Importing..." feedback, a row in the existing "CSV
-- import history" table on the Uploads page with a status badge, and a
-- "debug" link to open the full detail on demand). That means every
-- import attempt needs its own csv_imports row from now on, including
-- ones that imported 0 rows (previously no row was created at all when
-- nothing was inserted -- there was nothing to show it on, since the
-- only place results were ever shown was the page being removed here).
-- These columns hold exactly the detail import_results.html used to
-- render inline, now shown instead via /csv-import-debug on demand.
ALTER TABLE csv_imports ADD COLUMN fatal_error TEXT;
ALTER TABLE csv_imports ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE csv_imports ADD COLUMN duplicate_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE csv_imports ADD COLUMN row_errors TEXT;   -- JSON array of strings, NULL when none
ALTER TABLE csv_imports ADD COLUMN duplicates TEXT;   -- JSON array of strings, NULL when none

INSERT INTO schema_version (version) VALUES (5);
