-- =============================================================================
-- 0003_categorization_debug.sql
-- =============================================================================
-- Admin categorization-debug view (REQUIREMENTS.md sections 9/13, user
-- request round 22): explains WHY a transaction ended up with the
-- category it has -- which step of categorize.py's cascade decided it
-- (a user rule, a built-in rule, the LLM fallback, or the explicit
-- "Uncategorized" default), and for the LLM step specifically, the raw
-- prompt/response of the batched call that produced it. Symmetric
-- across both import paths that call categorize_transactions -- a PDF
-- statement (app/parser/pipeline.py) and a CSV import
-- (app/routes/transactions.py) -- since both funnel through the exact
-- same cascade function, this schema serves both from one design.

-- One row per CSV import that actually inserted at least one
-- transaction (a whole-file-rejected CSV never reaches this point --
-- nothing was categorized, nothing to debug). The CSV file itself is
-- still deleted right after import (REQUIREMENTS.md section 10,
-- unchanged) -- this is a lightweight audit/debug record, not a
-- reintroduction of persistent CSV storage.
CREATE TABLE IF NOT EXISTS csv_imports (
    id                                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                           TEXT    NOT NULL,
    original_filename                 TEXT,
    imported_at                       TEXT    NOT NULL DEFAULT (datetime('now')),
    row_count                         INTEGER NOT NULL,

    -- Same shape as statements.categorization_llm_* below, for the
    -- categorization LLM call this import triggered, if any -- NULL
    -- when every row had an explicit category or matched a rule, since
    -- categorize_via_llm is never even called then.
    categorization_llm_prompt         TEXT,
    categorization_llm_raw_response   TEXT,
    categorization_llm_error          TEXT
);

CREATE INDEX IF NOT EXISTS idx_csv_imports_user ON csv_imports(user_id);

-- statements gets the same three categorization-debug columns -- a
-- SEPARATE LLM call from the existing llm_raw_response column (0001)
-- -- that one is the vision EXTRACTION call; this is the
-- categorization call that runs afterward, the exact same
-- categorize_transactions function a CSV import also calls.
ALTER TABLE statements ADD COLUMN categorization_llm_prompt TEXT;
ALTER TABLE statements ADD COLUMN categorization_llm_raw_response TEXT;
ALTER TABLE statements ADD COLUMN categorization_llm_error TEXT;

-- transactions: which cascade step decided this row's category, and
-- (for a rule match) which specific pattern, plus a CSV-import
-- provenance link mirroring statement_id.
ALTER TABLE transactions ADD COLUMN category_source TEXT;
-- One of: 'user_rule', 'builtin_rule', 'llm', 'uncategorized_fallback',
-- 'manual' (a human set it directly -- the manual-entry form, or a
-- later edit/bulk-categorize), 'csv_explicit' (the CSV file's own
-- Category column had a value for this row -- never entered the
-- cascade at all), 'card_payment' (pipeline.py's own pre-cascade
-- "Payments" shortcut for a credit-card payment row -- also never
-- reaches the cascade). NULL on any row inserted before this
-- migration -- genuinely unknown, not guessed at retroactively.
ALTER TABLE transactions ADD COLUMN category_matched_pattern TEXT;
-- The winning category_rules/builtin pattern, only when category_source
-- is 'user_rule' or 'builtin_rule'. NULL otherwise.
ALTER TABLE transactions ADD COLUMN csv_import_id INTEGER REFERENCES csv_imports(id) ON DELETE SET NULL;
-- Mirrors statement_id -- for a CSV-imported row, lets the debug view
-- list every transaction from one CSV import.

CREATE INDEX IF NOT EXISTS idx_transactions_csv_import ON transactions(csv_import_id);

INSERT INTO schema_version (version) VALUES (3);
