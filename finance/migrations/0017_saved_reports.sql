-- Saved reports (REQUIREMENTS.md section 19.7): app-wide, run on whoever runs them.
-- Only the admin creates or edits them; nothing here is per user, so user wipes leave it alone.
CREATE TABLE IF NOT EXISTS saved_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    description     TEXT    NOT NULL DEFAULT '',
    sql             TEXT    NOT NULL,
    variables_json  TEXT    NOT NULL DEFAULT '[]',
    totals_json     TEXT    NOT NULL DEFAULT '{"on": true, "skip": []}',
    chart_json      TEXT    NOT NULL DEFAULT '{"type": "none"}',
    no_time_limit   INTEGER NOT NULL DEFAULT 0 CHECK (no_time_limit IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    last_run_at     TEXT
);

-- Starter reports: examples to edit or delete.
INSERT INTO saved_reports (name, description, sql, variables_json, totals_json, chart_json) VALUES (
    'Spending by category',
    'Money out per category, largest first. Transfers and excluded rows are left out.',
    'SELECT category, COUNT(*) AS transactions, ROUND(-SUM(amount), 2) AS spent
FROM transactions
WHERE amount < 0
  AND txn_type != ''transfer'' AND is_excluded = 0
  AND (:account IS NULL OR account_id = :account)
  AND (:period_start IS NULL OR date >= :period_start)
  AND (:period_end IS NULL OR date <= :period_end)
GROUP BY category
ORDER BY spent DESC',
    '[{"name": "account", "type": "account", "label": "Account", "default": "all", "allow_all": true}, {"name": "period", "type": "period", "label": "Period", "default": "month", "allow_all": true}]',
    '{"on": true, "skip": []}',
    '{"type": "bar", "x": "category", "y": ["spent"], "keep_signs": false}');
INSERT INTO saved_reports (name, description, sql, variables_json, totals_json, chart_json) VALUES (
    'Money in / out by month',
    'Money in, money out and net per month. Transfers and excluded rows are left out.',
    'SELECT strftime(''%Y-%m'', date) AS month,
       ROUND(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 2) AS money_in,
       ROUND(-SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END), 2) AS money_out,
       ROUND(SUM(amount), 2) AS net
FROM transactions
WHERE 1 = 1
  AND txn_type != ''transfer'' AND is_excluded = 0
  AND (:account IS NULL OR account_id = :account)
  AND (:period_start IS NULL OR date >= :period_start)
  AND (:period_end IS NULL OR date <= :period_end)
GROUP BY month
ORDER BY month',
    '[{"name": "account", "type": "account", "label": "Account", "default": "all", "allow_all": true}, {"name": "period", "type": "period", "label": "Period", "default": "last_12", "allow_all": true}]',
    '{"on": true, "skip": []}',
    '{"type": "bar", "x": "month", "y": ["money_in", "money_out"], "keep_signs": false}');
INSERT INTO saved_reports (name, description, sql, variables_json, totals_json, chart_json) VALUES (
    'Top 20 merchants',
    'Where the most money went, by description.',
    'SELECT description AS merchant, COUNT(*) AS times, ROUND(-SUM(amount), 2) AS spent
FROM transactions
WHERE amount < 0
  AND txn_type != ''transfer'' AND is_excluded = 0
  AND (:account IS NULL OR account_id = :account)
  AND (:period_start IS NULL OR date >= :period_start)
  AND (:period_end IS NULL OR date <= :period_end)
  AND (:category IS NULL OR category = :category)
GROUP BY description
ORDER BY spent DESC
LIMIT 20',
    '[{"name": "account", "type": "account", "label": "Account", "default": "all", "allow_all": true}, {"name": "period", "type": "period", "label": "Period", "default": "last_12", "allow_all": true}, {"name": "category", "type": "category", "label": "Category", "default": "all", "allow_all": true}]',
    '{"on": true, "skip": []}',
    '{"type": "bar", "x": "merchant", "y": ["spent"], "keep_signs": false}');
INSERT INTO saved_reports (name, description, sql, variables_json, totals_json, chart_json) VALUES (
    'Utility cost by month',
    'Cost and usage of each utility bill, by the month its billing period ends.',
    'SELECT strftime(''%Y-%m'', period_end_date) AS month, utility_type, provider,
       ROUND(SUM(cost), 2) AS cost, ROUND(SUM(usage_amount), 2) AS usage, usage_unit
FROM utility_bills
WHERE status IN (''complete'', ''pending_review'')
  AND (:period_start IS NULL OR period_end_date >= :period_start)
  AND (:period_end IS NULL OR period_end_date <= :period_end)
GROUP BY month, utility_type, provider, usage_unit
ORDER BY month, utility_type',
    '[{"name": "period", "type": "period", "label": "Period", "default": "last_12", "allow_all": true}]',
    '{"on": true, "skip": ["usage"]}',
    '{"type": "line", "x": "month", "y": ["cost"], "keep_signs": false}');

INSERT INTO schema_version (version) VALUES (17);
