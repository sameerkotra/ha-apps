-- Round 97: a per-account hint for which upload method is expected — a PDF
-- statement parse, a CSV import, or either (the default). Purely advisory:
-- nothing is blocked by it, it's only used client-side to warn — "this
-- account's preferred method is X, you're uploading Y, proceed anyway?" —
-- in case the wrong account got picked, with a plain option to dismiss the
-- warning and continue. Existing accounts default to 'both', same as a
-- newly created one that never sets this — they never asked to be warned.
ALTER TABLE accounts ADD COLUMN preferred_upload_method TEXT NOT NULL DEFAULT 'both'
    CHECK (preferred_upload_method IN ('both', 'pdf', 'csv'));

INSERT INTO schema_version (version) VALUES (14);
