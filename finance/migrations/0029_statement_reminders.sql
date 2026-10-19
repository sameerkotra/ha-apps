-- Expected statement dates and overdue reminders (SPEC.md section 27).

-- Per account: when its statement is ready. expected_every_months NULL = Off; 1, 2, 3, 6 or 12 otherwise.
-- expected_day: 1–31 (29–31 fall on a shorter month's last day), 0 = always the last day of the month.
-- expected_from: 'YYYY-MM', the month an every-N-months series counts from (not used for monthly).
-- reminders_on: the account's own "Remind me" (phone reminders only). expected_set_at: when the setting was
-- last set or changed, or the account restored / unarchived (UTC); cycles before that day are never overdue.
ALTER TABLE accounts ADD COLUMN expected_every_months INTEGER CHECK (expected_every_months IN (1, 2, 3, 6, 12));
ALTER TABLE accounts ADD COLUMN expected_day INTEGER CHECK (expected_day BETWEEN 0 AND 31);
ALTER TABLE accounts ADD COLUMN expected_from TEXT;
ALTER TABLE accounts ADD COLUMN reminders_on INTEGER NOT NULL DEFAULT 1 CHECK (reminders_on IN (0, 1));
ALTER TABLE accounts ADD COLUMN expected_set_at TEXT;

-- Each person's own reminder schedule (Setup → Settings → My reminders). No row = No notifications: rows are
-- only ever written when the person saves the card. offsets: JSON list of day counts after the expected date.
CREATE TABLE reminder_prefs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL UNIQUE,
    mode        TEXT    NOT NULL DEFAULT 'off' CHECK (mode IN ('off', 'remind')),
    offsets     TEXT    NOT NULL DEFAULT '[]',
    repeat_days INTEGER NOT NULL DEFAULT 0 CHECK (repeat_days IN (0, 7, 14)),
    at_time     TEXT    NOT NULL DEFAULT '09:00',
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- "Dismiss" on an overdue notice: that cycle (its expected date) gets no more reminders or notices.
CREATE TABLE statement_dismissals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    cycle_date   TEXT    NOT NULL,
    dismissed_by TEXT,
    dismissed_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (account_id, cycle_date)
);

-- The send log: each (person, account, cycle, reminder) once, so a restart never repeats one. sent_at is the
-- first try (UTC); a failed one is retried for six hours from then. Purged after a year by the daily sweep.
CREATE TABLE statement_reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    cycle_date  TEXT    NOT NULL,
    offset_days INTEGER NOT NULL,
    sent_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    result      TEXT    NOT NULL CHECK (result IN ('sent', 'no_phone', 'failed')),
    UNIQUE (user_id, account_id, cycle_date, offset_days)
);
CREATE INDEX IF NOT EXISTS idx_statement_reminders_sent ON statement_reminders(sent_at);

-- Extra notify services an admin assigns on Admin → Users (the same shape as the other household apps).
CREATE TABLE user_notify (
    user_id    TEXT NOT NULL,
    service    TEXT NOT NULL,              -- "notify.<name>", name matching ^[a-z0-9_]+$
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by TEXT,
    PRIMARY KEY (user_id, service)
);

INSERT INTO schema_version (version) VALUES (29);
