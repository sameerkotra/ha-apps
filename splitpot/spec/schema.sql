-- Splitpot — SQLite schema (DB file: $DATA_DIR/splitpot.db, i.e. /data/splitpot.db).
-- Matches main.py init_db(): the CREATE TABLEs plus everything its inline
-- migrations add, so a fresh DB and a migrated one end up with the same
-- columns and indexes. (Migrated DBs may have the added columns at the end of
-- the column list; the code never relies on column order.)
-- Every connection runs PRAGMA foreign_keys = ON; init_db sets journal_mode = WAL.
-- Ids are UUID4 strings; timestamps are ISO 8601 UTC (datetime.now(timezone.utc).isoformat()).

PRAGMA foreign_keys = ON;

-- Users (Admin → Users in the UI). Only ever inserted/renamed by the Home Assistant person.* sync; never deleted.
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,              -- HA friendly_name, kept in sync
    created_at   TEXT NOT NULL,
    ha_entity_id TEXT,                       -- sync key, e.g. 'person.ann' (migration-added)
    disabled     INTEGER NOT NULL DEFAULT 0, -- 1 = hidden from pickers, history kept (migration-added)
    ha_user_id   TEXT,                       -- the Person's HA user id (attributes.user_id), lower-case; identifies the signed-in person (migration-added)
    receive_notifications INTEGER NOT NULL DEFAULT 1  -- the person's own choice under My settings (migration-added)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ha_entity ON users(ha_entity_id)
    WHERE ha_entity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0   -- app opens into this group (migration-added)
);
-- At most one default group (also enforced in PUT /api/groups/{id}/default).
CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_one_default ON groups(is_default)
    WHERE is_default = 1;

CREATE TABLE IF NOT EXISTS group_members (
    group_id  TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,              -- add order, for display
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);

-- Expenses AND settle-up payments (split_type = 'payment').
CREATE TABLE IF NOT EXISTS expenses (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    description TEXT NOT NULL,               -- payments: '<from> paid <to>'
    amount      REAL NOT NULL,               -- total, rounded to 2 dp
    paid_by     TEXT NOT NULL REFERENCES users(id),  -- payments: the person paying back
    split_type  TEXT NOT NULL,               -- 'equal' | 'custom' | 'percent' | 'payment'
    date        TEXT NOT NULL                -- full UTC timestamp, or bare 'YYYY-MM-DD' = that local (HA time zone) day
);
CREATE INDEX IF NOT EXISTS idx_expenses_group ON expenses(group_id);
CREATE INDEX IF NOT EXISTS idx_expenses_date  ON expenses(date);
-- The group ledger newest first, a page at a time (keyset on date + id; SPEC §6).
CREATE INDEX IF NOT EXISTS idx_expenses_group_date ON expenses(group_id, date, id);

-- Each person's share of one expense; shares sum to expenses.amount.
-- Payments have exactly one row: the recipient, for the full amount.
CREATE TABLE IF NOT EXISTS expense_splits (
    expense_id TEXT NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL REFERENCES users(id),
    amount     REAL NOT NULL,                -- >= 0, 2 dp
    percent    REAL,                         -- split_type 'percent' only: the person's %, 2 dp (migration-added)
    PRIMARY KEY (expense_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_expense_splits_expense ON expense_splits(expense_id);

-- Append-only activity log, written in the same transaction as the change.
-- group_id is a soft reference (no FK) so rows survive group deletion.
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,               -- see SPEC.md §5 event types
    message     TEXT NOT NULL,               -- precomputed, human-readable
    group_id    TEXT,                        -- NULL for user_* and group_deleted events
    actor       TEXT,                        -- HA display name (or login name) of the requester (migration-added)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

-- Admin → Users: extra notify services per person, on top of the phones Home
-- Assistant links to them (common/ha_notify.py, people_admin.py). Keyed by
-- Splitpot's own users.id (not the HA user id).
CREATE TABLE IF NOT EXISTS user_notify (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service     TEXT NOT NULL,               -- "notify.<name>", name matching ^[a-z0-9_]+$
    created_at  TEXT NOT NULL,
    created_by  TEXT,                        -- the admin's login name (or user id)
    PRIMARY KEY (user_id, service)
);

-- Admin → App settings. One row per setting that has been saved
-- (ha_sync_enabled, sync_interval_minutes, currency, notify_charges,
-- notify_payments); a missing row means the default (sync on, 5 minutes,
-- USD, charge notifications off, settle-up notifications on).
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,               -- JSON, e.g. '10' or '"EUR"'
    updated_at  TEXT NOT NULL,
    updated_by  TEXT                         -- HA display/login name of the admin who saved it
);
