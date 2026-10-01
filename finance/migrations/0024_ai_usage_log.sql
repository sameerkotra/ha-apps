-- The running total on Admin → AI usage (SPEC.md section 21.3): one row per AI request, kept when the
-- document it was for is re-run or deleted. An older database (including an imported backup) starts the
-- log from what it already holds: the requests stored on each document, i.e. its last processing run.
-- Anything in those stored lists that isn't a well-formed request is skipped, never an error.
CREATE TABLE IF NOT EXISTS ai_usage_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    at             TEXT    NOT NULL DEFAULT (datetime('now')),
    purpose        TEXT    NOT NULL DEFAULT '',
    provider       TEXT    NOT NULL DEFAULT '',
    model          TEXT    NOT NULL DEFAULT '',
    input_tokens   INTEGER,                                          -- NULL: not reported (or the request failed)
    output_tokens  INTEGER,
    seconds        REAL    NOT NULL DEFAULT 0,
    error          TEXT    NOT NULL DEFAULT '',
    earlier        INTEGER NOT NULL DEFAULT 0                        -- 1: copied from a document when the log started
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_log_at ON ai_usage_log(at);

INSERT INTO ai_usage_log (at, purpose, model, input_tokens, output_tokens, seconds, error, earlier)
SELECT COALESCE(d.at, datetime('now')),
       COALESCE(CAST(json_extract(j.value, '$.purpose') AS TEXT), ''),
       COALESCE(CAST(json_extract(j.value, '$.model') AS TEXT), ''),
       CASE WHEN json_type(j.value, '$.input_tokens') IN ('integer', 'real')
            THEN CAST(json_extract(j.value, '$.input_tokens') AS INTEGER) END,
       CASE WHEN json_type(j.value, '$.output_tokens') IN ('integer', 'real')
            THEN CAST(json_extract(j.value, '$.output_tokens') AS INTEGER) END,
       CASE WHEN json_type(j.value, '$.seconds') IN ('integer', 'real')
            THEN json_extract(j.value, '$.seconds') ELSE 0 END,
       COALESCE(CAST(json_extract(j.value, '$.error') AS TEXT), ''),
       1
FROM (
    SELECT COALESCE(processed_at, uploaded_at) AS at, ai_usage FROM statements
    UNION ALL SELECT COALESCE(processed_at, uploaded_at), ai_usage FROM utility_bills
    UNION ALL SELECT COALESCE(processed_at, uploaded_at), ai_usage FROM toll_statements
    UNION ALL SELECT imported_at, ai_usage FROM csv_imports
) AS d, json_each(CASE WHEN json_valid(d.ai_usage) AND json_type(d.ai_usage) = 'array' THEN d.ai_usage ELSE '[]' END) AS j
WHERE j.type = 'object'
  AND NOT EXISTS (SELECT 1 FROM ai_usage_log WHERE earlier = 1)
ORDER BY 1;

INSERT INTO schema_version (version) VALUES (24);
