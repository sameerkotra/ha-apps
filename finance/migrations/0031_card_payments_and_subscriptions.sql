-- Credit card payment due dates, subscription price changes and their notifications (SPEC.md sections 29–31).

-- Per credit card: the day its payment is due each month, set by the person (never read from statements).
-- payment_due_day: 1–31 (29–31 fall on a shorter month's last day), 0 = always the last day; NULL = Off.
-- payment_due_set_at: when it was last set or changed, or the card restored / unarchived (UTC); due dates before
-- that day never count (no notice, no reminder).
ALTER TABLE accounts ADD COLUMN payment_due_day INTEGER CHECK (payment_due_day BETWEEN 0 AND 31);
ALTER TABLE accounts ADD COLUMN payment_due_set_at TEXT;

-- Each person's own notifications for card payments and subscription changes (Setup → Settings). One row per
-- person and kind; no row = No notifications (rows are only written when the person saves the card).
-- kind 'card_payments': offsets is a JSON list of days relative to the due date (-7 … 3; 0 = on the day).
-- kind 'subscriptions': offsets is '[]' (each change is sent once).
CREATE TABLE notify_prefs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('card_payments', 'subscriptions')),
    mode        TEXT    NOT NULL DEFAULT 'off' CHECK (mode IN ('off', 'on')),
    offsets     TEXT    NOT NULL DEFAULT '[]',
    at_time     TEXT    NOT NULL DEFAULT '09:00',
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, kind)
);

-- "Mark as paid" on a payment-due notice: that due date gets no more notices or reminders.
CREATE TABLE payment_dismissals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    due_date     TEXT    NOT NULL,
    dismissed_by TEXT,
    dismissed_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (account_id, due_date)
);

-- The card payment reminders' send log: each (person, card, due date, reminder) once. sent_at is the first try
-- (UTC); a failed one is retried for six hours from then. Purged after a year by the daily sweep.
CREATE TABLE payment_reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    due_date    TEXT    NOT NULL,
    offset_days INTEGER NOT NULL,
    sent_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    result      TEXT    NOT NULL CHECK (result IN ('sent', 'no_phone', 'failed')),
    UNIQUE (user_id, account_id, due_date, offset_days)
);
CREATE INDEX IF NOT EXISTS idx_payment_reminders_sent ON payment_reminders(sent_at);

-- Dismiss on a subscription change (Home, Recurring): series is the recurring series' merchant family (its
-- merchant key), change_key names the change ("price:15.49>17.99:2026-09-12", "new:MERCHANT@9.99"); a later,
-- different change of the same series has another key and shows again.
CREATE TABLE subscription_dismissals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    series       TEXT    NOT NULL,
    change_key   TEXT    NOT NULL,
    dismissed_by TEXT,
    dismissed_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (account_id, series, change_key)
);

-- The subscription changes' send log: each (person, series, change) once. found_at: the check that first saw it
-- (UTC; it is sent at the person's next chosen time after that, result 'pending' until then); sent_at: the first
-- try. Purged after a year by the daily sweep.
CREATE TABLE subscription_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    series      TEXT    NOT NULL,
    change_key  TEXT    NOT NULL,
    found_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    sent_at     TEXT,
    result      TEXT    NOT NULL DEFAULT 'pending' CHECK (result IN ('pending', 'sent', 'no_phone', 'failed')),
    UNIQUE (user_id, account_id, series, change_key)
);
CREATE INDEX IF NOT EXISTS idx_subscription_alerts_found ON subscription_alerts(found_at);

INSERT INTO schema_version (version) VALUES (31);
