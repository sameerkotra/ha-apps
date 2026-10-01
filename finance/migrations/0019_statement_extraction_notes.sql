-- Re-extract with notes (REQUIREMENTS.md section 4): what the person said was wrong with the previous
-- reading, sent to the model with the PDF on the next extraction and shown on the debug page.
ALTER TABLE statements ADD COLUMN extraction_notes TEXT;

INSERT INTO schema_version (version) VALUES (19);
