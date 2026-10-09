-- Utility bills wait for the person's confirmation unless "Skip confirmation" is ticked at upload (as statements
-- do, 0019); bills already here count as confirmed. `corrections`: meter figures the app took from the bill's own
-- printed meter table instead of the AI's reading (shown on the review page).
ALTER TABLE utility_bills ADD COLUMN needs_confirmation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE utility_bills ADD COLUMN corrections TEXT;
INSERT INTO schema_version (version) VALUES (26);
