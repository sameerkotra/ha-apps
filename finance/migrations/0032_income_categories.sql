-- Income categories and the redesigned Categories page (SPEC.md section 9).
--
-- Every category has a kind: spending or income. The built-ins' kinds live in code (categorize.BUILTIN_KIND); a
-- person's own category gets its kind here, Spending for every one made before. The old built-in "Income" is now
-- "Other income": every column that stores a category name follows (transactions, category_rules,
-- skipped_duplicates). recurring_overrides stores a merchant, never a category, so it has nothing to rename.
-- A person's own category named like one of the new built-ins (any case) becomes that built-in: its rows keep
-- the name.

ALTER TABLE custom_categories ADD COLUMN kind TEXT NOT NULL DEFAULT 'spending' CHECK (kind IN ('spending', 'income'));
ALTER TABLE custom_categories ADD COLUMN colour TEXT;          -- a palette key (categorize.PALETTE); NULL: its default

-- When one of the person's rules last categorized a row (an import, re-categorizing, Sort my income).
ALTER TABLE category_rules ADD COLUMN last_matched_at TEXT;

-- The person's own choices for the built-in categories: hidden from the menus, and their colour.
CREATE TABLE IF NOT EXISTS category_prefs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    name        TEXT    NOT NULL,                                   -- a built-in category's name
    hidden      INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    colour      TEXT,                                               -- a palette key; NULL: its default
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, name)
);

-- Rule suggestions the person dismissed ("You set AMAZON MKTPL to Shopping 3 times"), for good: by merchant and
-- category. The suggestions themselves are worked out from category_source = 'manual' rows, never stored.
CREATE TABLE IF NOT EXISTS category_suggestion_dismissals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT    NOT NULL,
    merchant      TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    dismissed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, merchant, category)
);

UPDATE transactions SET category = 'Other income' WHERE category = 'Income';
UPDATE category_rules SET category = 'Other income' WHERE category = 'Income';
UPDATE skipped_duplicates SET category = 'Other income' WHERE category = 'Income';

DELETE FROM custom_categories
WHERE lower(name) IN ('salary', 'interest', 'dividends', 'refunds', 'reimbursements', 'gifts received', 'other income');

INSERT INTO schema_version (version) VALUES (32);
