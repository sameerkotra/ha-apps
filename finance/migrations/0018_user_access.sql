-- Shared access (REQUIREMENTS.md section 20): the admin lets a regular user (viewer) use another
-- user's data (owner) with full rights, e.g. a household member sees the statements already uploaded
-- instead of uploading them twice. The viewer opens straight into the first owner's data.
CREATE TABLE IF NOT EXISTS user_access (
    viewer_id   TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    granted_by  TEXT NOT NULL,
    granted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (viewer_id, owner_id),
    CHECK (viewer_id != owner_id)
);

INSERT INTO schema_version (version) VALUES (18);
