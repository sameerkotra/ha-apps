-- =============================================================================
-- 0002_custom_categories.sql
-- =============================================================================
-- First real migration since the Phase 1-3 consolidation (see 0001_initial.sql's
-- own header) — the "never edit an already-applied migration" rule resumes
-- for good starting here, now that real data exists.
--
-- Until now, every category dropdown in the app (transaction category
-- edit, bulk-categorize, the "add a rule" form on the Categories page)
-- only ever offered categorize.py's fixed DISPLAY_CATEGORIES list — no
-- way for a user to add their own category name (e.g. "Pet Care") short
-- of it happening to come back from the LLM categorization fallback,
-- which _category_field.html already tolerates as an off-list value but
-- never offers as a pickable option anywhere else.
--
-- custom_categories holds exactly that: user-defined category names, to
-- be merged with DISPLAY_CATEGORIES wherever a category list is shown
-- (see app/categorize.py's list_all_categories). Deliberately NOT a
-- dumping ground for every category string that's ever been typed
-- in — only what the user explicitly added via the Categories page's
-- "Add a category" form (app/routes/categories.py), same "explicit
-- action, not incidental collection" spirit as category_rules itself.
--
-- UNIQUE(user_id, name) is case-SENSITIVE at the SQL level (SQLite's
-- default TEXT collation) — app/categorize.py does the case-insensitive
-- de-duplication against DISPLAY_CATEGORIES and existing custom
-- categories before insert, so "Groceries" can't be re-added as
-- "groceries" through normal use; this constraint is the backstop
-- against a genuine race, not the primary defense.

CREATE TABLE IF NOT EXISTS custom_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_custom_categories_user ON custom_categories(user_id);

INSERT INTO schema_version (version) VALUES (2);
