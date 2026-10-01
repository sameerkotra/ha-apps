-- Round 39: a directory of HA users this addon has actually seen a
-- request from, built up passively by app/auth.py's get_current_user
-- (an upsert on every authenticated request) rather than a live call to
-- HA's own user list (REQUIREMENTS.md section 3 already explains why
-- that path was deliberately dropped in favor of the static ADMIN_USERS
-- list). Powers the admin-only "switch user" dropdown (base.html) and
-- gives the "Viewing as <name>" banner a real display name instead of
-- falling back to the raw user id.
CREATE TABLE IF NOT EXISTS known_users (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO schema_version (version) VALUES (4);
