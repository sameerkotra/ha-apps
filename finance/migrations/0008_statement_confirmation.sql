-- Round 61: optional "open the PDF and confirm money in/out" step before a
-- parsed statement is marked complete and its PDF deleted (REQUIREMENTS.md
-- section 7). `status` has a CHECK constraint, so instead of a new status
-- value (a full table rebuild) the step is two plain columns: a statement
-- that needs confirmation stays 'pending_review' until confirmed_at is set.
-- Existing rows default to 0 -- they never asked for the step.
ALTER TABLE statements ADD COLUMN needs_confirmation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE statements ADD COLUMN confirmed_at TEXT;

INSERT INTO schema_version (version) VALUES (8);
