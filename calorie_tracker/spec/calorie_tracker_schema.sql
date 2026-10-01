-- Calorie Tracker — SQLite schema (/data/calorie.db)
-- Copied from app/db.py SCHEMA. init_db() runs it at startup and after every
-- restore; every statement is idempotent.
--
-- Columns added later are already in the CREATE statements below, so a fresh DB
-- and a migrated DB end up identical. db._migrate() adds them to older files:
--   ALTER TABLE users       ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
--   ALTER TABLE saved_foods ADD COLUMN notes TEXT;
--
-- Not part of this script: `PRAGMA foreign_keys = ON` is set on every connection
-- in db._connect(), because SQLite applies it per connection, not per file.
-- Admin status is not stored here. It comes from the admin_users option at runtime.
-- Dates are ISO 'YYYY-MM-DD' strings in Home Assistant's time zone.

PRAGMA journal_mode = WAL;

-- One row per HA user who has opened the app. Upserted on every request (name refreshed).
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,           -- Home Assistant user id (from X-Remote-User-Id)
    name TEXT NOT NULL,            -- Home Assistant display name
    enabled INTEGER NOT NULL DEFAULT 1,  -- 0 = hidden from the in-app switcher
    created_at TEXT DEFAULT (datetime('now'))
);

-- 1:1 with users. Created with these defaults (INSERT OR IGNORE) when a user is first seen.
CREATE TABLE IF NOT EXISTS goals (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    calorie_goal REAL NOT NULL DEFAULT 2000,
    protein_goal REAL NOT NULL DEFAULT 150,
    carb_goal REAL NOT NULL DEFAULT 200,
    fat_goal REAL NOT NULL DEFAULT 65,
    starting_weight_kg REAL,
    target_weight_kg REAL
);

-- Any number of entries per user per day; the "latest" entry is the one with the highest date.
CREATE TABLE IF NOT EXISTS weight_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    note TEXT
);

-- Per-user templates ("repeated foods"). notes = free-text details (NULL when empty).
CREATE TABLE IF NOT EXISTS saved_foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    serving_size REAL NOT NULL DEFAULT 1,
    serving_unit TEXT NOT NULL DEFAULT 'serving',
    calories REAL NOT NULL,
    protein REAL NOT NULL DEFAULT 0,
    carbs REAL NOT NULL DEFAULT 0,
    fat REAL NOT NULL DEFAULT 0,
    notes TEXT
);

-- Each row is a standalone copy of the numbers at log time (never recomputed from saved_foods).
-- meal_type: breakfast | lunch | dinner | snack (the UI's values; not enforced by the DB).
-- Deleting the saved food sets saved_food_id to NULL and leaves the logged values untouched.
CREATE TABLE IF NOT EXISTS food_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    meal_type TEXT NOT NULL DEFAULT 'snack',
    food_name TEXT NOT NULL,
    servings REAL NOT NULL DEFAULT 1,
    calories REAL NOT NULL,
    protein REAL NOT NULL DEFAULT 0,
    carbs REAL NOT NULL DEFAULT 0,
    fat REAL NOT NULL DEFAULT 0,
    saved_food_id INTEGER REFERENCES saved_foods(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_food_logs_user_date ON food_logs(user_id, date);
CREATE INDEX IF NOT EXISTS idx_weight_logs_user_date ON weight_logs(user_id, date);

-- Admin → App settings (app/settings.py). One row per key an admin changed in
-- the app; a key without a row uses settings.DEFAULTS (ai_provider "" = AI not set
-- up, ai_url "", ai_model "", ai_api_key "", ai_max_tokens 4096,
-- expose_daily_calories_sensor true). value is JSON. updated_by = the admin's HA
-- user id. The ai_api_key row is blanked ("") in downloaded backups.
-- db._migrate() turns older ollama_url / ollama_model rows into
-- ai_provider "ollama", ai_url, ai_model (existing ai_* rows win) and deletes them.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,          -- JSON
    updated_at TEXT NOT NULL,     -- ISO-8601 UTC
    updated_by TEXT               -- HA user id / name of the admin
);
