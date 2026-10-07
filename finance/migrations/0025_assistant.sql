-- Each person's own "Let the Household Assistant answer for me" (app/tools.py; Who am I). On by default: the
-- admin's "Answer the Household Assistant" is the household's switch, this one is the person's own.
ALTER TABLE known_users ADD COLUMN assistant_ok INTEGER NOT NULL DEFAULT 1;
INSERT INTO schema_version (version) VALUES (25);
