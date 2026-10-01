-- REQUIREMENTS.md sections 22–23. The AI address/model keys become provider-neutral, and an
-- install that already has utility bills or toll statements keeps those features switched on
-- (a new install starts with them off).
UPDATE app_settings SET key = 'ai_url' WHERE key = 'ollama_url'
    AND NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'ai_url');
UPDATE app_settings SET key = 'ai_model' WHERE key = 'ollama_model'
    AND NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'ai_model');
INSERT OR IGNORE INTO app_settings (key, value, updated_by)
    SELECT 'feature_utilities', '1', 'upgrade (had utility bills)' WHERE EXISTS (SELECT 1 FROM utility_bills);
INSERT OR IGNORE INTO app_settings (key, value, updated_by)
    SELECT 'feature_tolls', '1', 'upgrade (had toll statements)' WHERE EXISTS (SELECT 1 FROM toll_statements);
-- Re-extract with notes for utility bills (section 23), as statements have (0019).
ALTER TABLE utility_bills ADD COLUMN extraction_notes TEXT;
INSERT INTO schema_version (version) VALUES (23);
