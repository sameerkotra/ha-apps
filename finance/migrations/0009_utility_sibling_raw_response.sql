-- A dual-fuel PDF (Xcel's electric + gas statement) is ONE upload and ONE AI call that produces
-- two bills. Only the first bill's row kept the raw Ollama response; the second row's debug view
-- said "No response captured yet". New uploads copy it (parser/utility_pipeline.py); this fills in
-- the rows already stored, from any other bill of the same upload (same user + file hash).
UPDATE utility_bills
SET llm_raw_response = (
    SELECT b2.llm_raw_response FROM utility_bills b2
    WHERE b2.user_id = utility_bills.user_id AND b2.file_hash = utility_bills.file_hash
      AND b2.id != utility_bills.id AND b2.llm_raw_response IS NOT NULL AND b2.llm_raw_response != ''
    ORDER BY b2.id LIMIT 1
)
WHERE (llm_raw_response IS NULL OR llm_raw_response = '') AND file_hash IS NOT NULL;

INSERT INTO schema_version (version) VALUES (9);
