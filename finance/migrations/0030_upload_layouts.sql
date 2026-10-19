-- The redesigned Upload page (SPEC.md sections 4 and 10): what an account remembers about its uploads.

-- csv_layout: a CSV layout that worked for this account, saved with "Remember this layout" after an import
-- (JSON: {"header_row": 3, "data_row": 4, "date_column": "Trans. Date" or null, "debit_credit": true}), applied by
-- itself to the next CSV import for the account that doesn't give its own. NULL = none saved.
-- skip_confirmation: the account's own "Skip the PDF confirmation step" (1) or not (0), used when an upload doesn't
-- say. NULL = never chosen, which is the same as 0.
ALTER TABLE accounts ADD COLUMN csv_layout TEXT;
ALTER TABLE accounts ADD COLUMN skip_confirmation INTEGER CHECK (skip_confirmation IN (0, 1));

INSERT INTO schema_version (version) VALUES (30);
