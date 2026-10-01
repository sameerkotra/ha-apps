-- App settings edited in Setup → Settings (REQUIREMENTS.md section 21): take effect at once, no restart.
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by  TEXT
);

-- AI calls made for each document (tokens, time), shown on its debug page. JSON list.
ALTER TABLE statements ADD COLUMN ai_usage TEXT;
ALTER TABLE utility_bills ADD COLUMN ai_usage TEXT;
ALTER TABLE toll_statements ADD COLUMN ai_usage TEXT;
ALTER TABLE csv_imports ADD COLUMN ai_usage TEXT;

INSERT INTO schema_version (version) VALUES (22);
