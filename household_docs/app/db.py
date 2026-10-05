"""SQLite at /data/docs.db (SPEC §5.4): schema, migrations and small helpers.

The database never holds the only copy of a document: every document is a file under /share (§5).
It keeps who may see what, the file index, search, checklist tick details, favourites and settings.

Later steps add their tables to SCHEMA (CREATE … IF NOT EXISTS, so existing databases get them at the
next start-up) and the columns older databases lack to MIGRATIONS; anything else that must run once per
start-up and after a restore goes in POST_MIGRATE (functions taking the connection).
"""
import json
import os
import sqlite3
import threading
import uuid

from . import config
from .common import db_core

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,                       -- Home Assistant user id
  name TEXT NOT NULL, username TEXT, ha_person TEXT,
  folder TEXT UNIQUE,                        -- folder name under people/ (NULL until first visit)
  disabled INTEGER NOT NULL DEFAULT 0,
  prefs TEXT,                                -- JSON: the person's own settings
  last_seen TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roots (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('person','shared')),
  user_id TEXT REFERENCES users(id),         -- kind = person
  path TEXT NOT NULL UNIQUE,                 -- relative to /share
  label TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL,
  last_scan_at TEXT, last_scan_ms INTEGER, missing INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_roots_person ON roots(user_id) WHERE kind = 'person';
CREATE TABLE IF NOT EXISTS nodes (           -- the index: one row per file and folder in every root
  id TEXT PRIMARY KEY,
  root_id TEXT NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
  rel TEXT NOT NULL,                         -- path inside the root, '/'-separated
  parent_id TEXT REFERENCES nodes(id),       -- NULL: at the top of the root
  name TEXT NOT NULL, name_folded TEXT NOT NULL, ext TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('folder','note','markdown','checklist','sheet','file')),
  size INTEGER, mtime TEXT, mtime_ns INTEGER, ctime TEXT, inode INTEGER, dev INTEGER, sha256 TEXT,
  created_by TEXT, updated_by TEXT,          -- users.id when done through the app; NULL = outside the app
  color TEXT, content_indexed INTEGER NOT NULL DEFAULT 0,
  gone_at TEXT,                              -- not found on disk (kept 30 days so a rename can be matched)
  trash_id TEXT,                             -- in Trash (trash.id): hidden everywhere, kept until emptied
  UNIQUE (root_id, rel)
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name_folded);
CREATE INDEX IF NOT EXISTS idx_nodes_mtime ON nodes(mtime);
CREATE INDEX IF NOT EXISTS idx_nodes_ext ON nodes(ext);
CREATE INDEX IF NOT EXISTS idx_nodes_inode ON nodes(dev, inode);
CREATE TABLE IF NOT EXISTS shares (
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,                     -- users.id or '*' (Everyone)
  role TEXT NOT NULL CHECK (role IN ('manager','editor','viewer')),
  viewers_tick INTEGER NOT NULL DEFAULT 1,
  added_by TEXT, added_at TEXT NOT NULL,
  PRIMARY KEY (node_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_shares_user ON shares(user_id);
CREATE TABLE IF NOT EXISTS root_access (     -- admin shared folders (step 3)
  root_id TEXT NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('ro','rw')),
  PRIMARY KEY (root_id, user_id)
);
CREATE TABLE IF NOT EXISTS versions (
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  n INTEGER NOT NULL, file TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  made_by TEXT,                              -- who wrote this content; NULL = changed outside the app
  saved_by TEXT,                             -- whose save kept this copy (the 5-minute rule)
  created_at TEXT NOT NULL,
  PRIMARY KEY (node_id, n)
);
CREATE TABLE IF NOT EXISTS trash (
  id TEXT PRIMARY KEY, node_id TEXT, root_id TEXT NOT NULL, original_rel TEXT NOT NULL,
  trash_rel TEXT NOT NULL,                   -- relative to the docs folder's .trash
  owner_id TEXT, deleted_by TEXT, deleted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklist_ticks (
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  item_key TEXT NOT NULL,                    -- hash of the item text + occurrence number
  done_by TEXT, done_at TEXT,
  PRIMARY KEY (node_id, item_key)
);
CREATE TABLE IF NOT EXISTS user_state (
  user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  favourite INTEGER NOT NULL DEFAULT 0, pinned_order INTEGER, opened_at TEXT,
  hidden INTEGER NOT NULL DEFAULT 0,         -- Hide (§6.3): left out of Shared with me / Everyone
  PRIMARY KEY (user_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_user_state_opened ON user_state(user_id, opened_at);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS user_notify (
  user_id TEXT NOT NULL, service TEXT NOT NULL, created_at TEXT, created_by TEXT,
  PRIMARY KEY (user_id, service)
);
CREATE TABLE IF NOT EXISTS audit_log (       -- no content, ever
  id TEXT PRIMARY KEY, actor_id TEXT, node_id TEXT, root_id TEXT, action TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_sha ON nodes(sha256);
CREATE INDEX IF NOT EXISTS idx_root_access_user ON root_access(user_id);
CREATE TABLE IF NOT EXISTS saved_searches (  -- the Search page (§10.6): a person's saved searches
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, query TEXT NOT NULL,
  filters TEXT NOT NULL,                     -- JSON: the Search page's options and filters
  pinned INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_saved_user ON saved_searches(user_id);
CREATE TABLE IF NOT EXISTS recent_searches (
  user_id TEXT NOT NULL, query TEXT NOT NULL, filters TEXT NOT NULL, used_at TEXT NOT NULL,
  PRIMARY KEY (user_id, query, filters)
);
CREATE TABLE IF NOT EXISTS node_tags (       -- tags (§17.1), by node id: they follow renames and moves
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,                         -- as typed (the first spelling wins)
  tag_folded TEXT NOT NULL,                  -- lower case: one tag whatever its case
  added_by TEXT, added_at TEXT NOT NULL,
  PRIMARY KEY (node_id, tag_folded)
);
CREATE INDEX IF NOT EXISTS idx_node_tags_tag ON node_tags(tag_folded);
CREATE TABLE IF NOT EXISTS node_links (      -- links between documents (§17.3): [[Title]] in a note's text
  from_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  text TEXT NOT NULL,                        -- what is between [[ ]] in the file
  to_id TEXT,                                -- the item it points at (kept through renames); NULL = not found
  PRIMARY KEY (from_id, text)
);
CREATE INDEX IF NOT EXISTS idx_node_links_to ON node_links(to_id);
CREATE TABLE IF NOT EXISTS activity (        -- what changed (§17.4): names and who, never any content
  id TEXT PRIMARY KEY,
  actor_id TEXT,                             -- users.id; NULL = outside the app (found by the index)
  node_id TEXT NOT NULL, root_id TEXT NOT NULL,
  parent_id TEXT,                            -- the folder it was in then (folder follows; deleted items)
  action TEXT NOT NULL,                      -- created | edited | renamed | moved | deleted | restored | shared
  detail TEXT,                               -- JSON: {"from", "to"} for renames and moves (names only)
  target_user TEXT,                          -- shared: with whom ('*' = Everyone)
  count INTEGER NOT NULL DEFAULT 1,          -- edits by the same person grouped into this row
  created_at TEXT NOT NULL, last_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_last ON activity(last_at);
CREATE INDEX IF NOT EXISTS idx_activity_node ON activity(node_id, last_at);
CREATE INDEX IF NOT EXISTS idx_activity_parent ON activity(parent_id, last_at);
CREATE TABLE IF NOT EXISTS follows (         -- phone notifications for a followed item (§17.4)
  user_id TEXT NOT NULL,
  target TEXT NOT NULL,                      -- a node id, or "root:<id>" (an admin shared folder's top)
  created_at TEXT NOT NULL,
  cursor TEXT NOT NULL,                      -- activity up to this time has been told (or was before the follow)
  last_sent_at TEXT,                         -- at most one notification per item per 15 minutes
  PRIMARY KEY (user_id, target)
);
CREATE TABLE IF NOT EXISTS ha_sensors (      -- items shown in Home Assistant (§17.9)
  entity_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('checklist','sheet','folder')),
  node_id TEXT NOT NULL,                     -- a node id, or "root:<id>" for an admin shared folder's top
  cell_ref TEXT,                             -- sheets: "Tab!B4" or "Tab!B2:B9"
  options TEXT,                              -- JSON: {name, unit, hideItems}
  created_by TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ha_sensors_node ON ha_sensors(node_id);
CREATE TABLE IF NOT EXISTS storage_daily (   -- one snapshot a day per root: the storage report's growth (§17.8)
  day TEXT NOT NULL, root_id TEXT NOT NULL, files INTEGER NOT NULL, bytes INTEGER NOT NULL,
  PRIMARY KEY (day, root_id)
);
CREATE TABLE IF NOT EXISTS pdf_text (        -- text found in PDFs (§17.5), searchable through fts
  node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
  sha256 TEXT,                               -- of the file the text came from
  status TEXT NOT NULL,                      -- ok | encrypted | scanned | too_big | failed
  pages INTEGER, text TEXT,
  page_lines TEXT,                           -- JSON: the line where each page starts in `text`
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ocr_text (        -- text read by AI from an image or a scanned PDF (§17.6)
  node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
  sha256 TEXT, text TEXT NOT NULL, model TEXT,
  edited_by TEXT,                            -- a person corrected it (users.id)
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_folders (      -- "Read text from scans in this folder" (§17.6)
  target TEXT PRIMARY KEY,                   -- a folder node id or "root:<id>"
  added_by TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_calls (        -- every request to the AI model (Admin → AI usage); nobody's content
  id TEXT PRIMARY KEY, at TEXT NOT NULL,
  purpose TEXT NOT NULL,                     -- ocr | summarise | checklist | sheet | ask | test
  provider TEXT NOT NULL, model TEXT NOT NULL, ok INTEGER NOT NULL, error TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0, ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ai_calls_at ON ai_calls(at);
CREATE TABLE IF NOT EXISTS bus_requests (    -- what a person asked another app (§17.15–§17.16): the page polls it
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL, node_id TEXT,
  kind TEXT NOT NULL,                        -- chats | card | lists | todo
  ref TEXT NOT NULL,                         -- the bus message's ref ("chats:<id>" …): answers find their request
  state TEXT NOT NULL,                       -- pending | done | failed
  data TEXT,                                 -- JSON: what to do with the answer (a card's share role …)
  result TEXT,                               -- JSON: the answer as the page shows it (no document content)
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bus_requests_user ON bus_requests(user_id, created_at);
CREATE TABLE IF NOT EXISTS todo_sends (      -- checklist → Todo (§17.16): one row per send; the note on the checklist
  id TEXT PRIMARY KEY, node_id TEXT NOT NULL, user_id TEXT NOT NULL, request_id TEXT,
  target TEXT NOT NULL,                      -- JSON: {"listId"} or {"new": {"name", "shared"}}
  list_id TEXT, list_name TEXT, list_kind TEXT, created_list INTEGER NOT NULL DEFAULT 0,
  move INTEGER NOT NULL DEFAULT 0,
  parts TEXT NOT NULL,                       -- JSON: [{"n": items, "keys": [...], "state"}], item texts only until sent
  items_total INTEGER NOT NULL, items_sent INTEGER NOT NULL DEFAULT 0, items_removed INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL,                       -- sending | done | failed
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_todo_sends_node ON todo_sends(node_id, created_at);
CREATE TABLE IF NOT EXISTS filing_rules (    -- §17.18: what happens to a file arriving in a folder
  id TEXT PRIMARY KEY,
  target TEXT NOT NULL,                      -- the folder: a node id or "root:<id>" (an admin shared folder's top)
  position INTEGER NOT NULL,                 -- first match wins
  pattern TEXT NOT NULL,                     -- the name, wildcards as in search (§10.2)
  type TEXT,                                 -- note | checklist | sheet | file | pdf | image (NULL: any)
  min_kb INTEGER, max_kb INTEGER,
  rename TEXT,                               -- {date} {yyyy} {mm} {name} {n}; NULL: keep the name
  move_to TEXT,                              -- a folder in the same space; NULL: stay
  created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filing_rules_target ON filing_rules(target, position);
CREATE TABLE IF NOT EXISTS filing_queue (    -- files that arrived and wait for the folder's rules
  node_id TEXT PRIMARY KEY, how TEXT NOT NULL, queued_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS filing_log (      -- what the rules did (Undo for 7 days, from 🕑 Activity)
  id TEXT PRIMARY KEY, node_id TEXT NOT NULL, rule_id TEXT, actor_id TEXT,
  from_parent TEXT, from_name TEXT NOT NULL, to_parent TEXT, to_name TEXT NOT NULL,
  created_at TEXT NOT NULL, undone_at TEXT, undone_by TEXT
);
CREATE TABLE IF NOT EXISTS cleanup_rules (   -- §17.19: one per folder
  target TEXT PRIMARY KEY,                   -- a folder node id or "root:<id>"
  action TEXT NOT NULL CHECK (action IN ('trash','archive')),
  days INTEGER NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS kid_parents (     -- §17.20: who may view a child's My docs (set by an admin)
  child_id TEXT NOT NULL, parent_id TEXT NOT NULL, added_by TEXT, added_at TEXT NOT NULL,
  PRIMARY KEY (child_id, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_kid_parents_parent ON kid_parents(parent_id);
CREATE TABLE IF NOT EXISTS keep_imports (    -- notes brought in from Google Keep (§17.11): re-imports skip them
  user_id TEXT NOT NULL, keep_id TEXT NOT NULL, node_id TEXT, imported_at TEXT NOT NULL,
  PRIMARY KEY (user_id, keep_id)
);
"""

FTS_SQL = ("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(node_id UNINDEXED, name, body, "
           "tokenize='unicode61 remove_diacritics 2')")
# Without FTS5 (a Python built against an SQLite without it) search falls back to LIKE on this table.
FTS_FALLBACK_SQL = "CREATE TABLE IF NOT EXISTS fts (node_id TEXT PRIMARY KEY, name TEXT, body TEXT)"

# Columns older databases lack: (table, column, definition). db_core.add_missing_columns adds them.
MIGRATIONS: list = [
    # checklists: open and ticked items (search: "Checklists with open items" / "all done", §10.3)
    ("nodes", "check_open", "INTEGER"),
    ("nodes", "check_done", "INTEGER"),
    # a PNG / JPEG / GIF / WebP by its first bytes (the image type): previews and thumbnails (§3.3)
    ("nodes", "preview", "TEXT"),
    # sheets: for each line of the searchable text, the cell it came from ("Tab\tC14", JSON) — §10.2
    ("nodes", "sheet_cells", "TEXT"),
    # a pinned sheet's card shows one cell (§17.13): {"tab", "ref"} as JSON
    ("user_state", "pin_cell", "TEXT"),
    # PDFs (§17.5): the line where each page starts in the indexed text (snippets say "page 3")
    ("nodes", "pdf_pages", "TEXT"),
    # Kids' space (§17.20): a child's account; an admin shared folder made for children
    ("users", "is_child", "INTEGER NOT NULL DEFAULT 0"),
    ("roots", "kids", "INTEGER NOT NULL DEFAULT 0"),
    # when the person was marked as a child: a parent's view covers only what was made since (§17.20)
    ("users", "child_since", "TEXT"),
    # What changed since you last looked (§17.21): what a person last saw of an item, and since when they count
    ("user_state", "seen_at", "TEXT"),
    ("user_state", "seen_sha", "TEXT"),
    ("users", "seen_from", "TEXT"),
]


def _bus_tables(conn) -> None:
    """Messages between the household apps (bus_outbox, bus_seen, bus_apps — APP_MESSAGES_SPEC §4)."""
    from .common import app_bus
    app_bus.migrate(conn)


def _seen_from(conn) -> None:
    """§17.21: items count as changed from the moment the feature first runs for a person (not their whole past)."""
    conn.execute("UPDATE users SET seen_from = ? WHERE seen_from IS NULL", (config.now_iso(),))


# Functions run after the schema and MIGRATIONS, at every start-up and after a restore: f(conn).
POST_MIGRATE: list = [_bus_tables, _seen_from]

# Set while an admin restore swaps the database file: requests get 503 and background jobs wait.
RESTORING = threading.Event()
_fts_state = {"available": None}


def new_id() -> str:
    return uuid.uuid4().hex


def _connect() -> sqlite3.Connection:
    return db_core.connect(config.DB_PATH, timeout=30, check_same_thread=False,
                           pragmas=("foreign_keys = ON", "busy_timeout = 30000"))


def get_conn():
    """A connection that commits when the block ends (and rolls back on an error) — app/common/db_core.py."""
    return db_core.transaction(_connect)


def has_fts(conn=None) -> bool:
    """Is `fts` a real FTS5 table (else search uses LIKE)?"""
    if _fts_state["available"] is None:
        def check(c):
            r = c.execute("SELECT sql FROM sqlite_master WHERE name = 'fts'").fetchone()
            return bool(r and "fts5" in (r[0] or "").lower())
        if conn is not None:
            _fts_state["available"] = check(conn)
        else:
            with get_conn() as c:
                _fts_state["available"] = check(c)
    return _fts_state["available"]


def _create_fts(conn) -> None:
    try:
        conn.execute(FTS_SQL)
    except sqlite3.OperationalError:          # no FTS5 in this SQLite
        conn.execute(FTS_FALLBACK_SQL)


def migrate(conn) -> None:
    conn.executescript(SCHEMA)
    _create_fts(conn)
    db_core.add_missing_columns(conn, MIGRATIONS)
    for fn in POST_MIGRATE:
        fn(conn)


def init_db() -> None:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    _fts_state["available"] = None


REQUIRED_TABLES = ("users", "roots", "nodes", "shares", "app_settings")


# ---------- small key/value state (rows in app_settings that aren't App settings) ----------
def state_get(conn, key: str, default=None):
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if r is None:
        return default
    try:
        return json.loads(r["value"])
    except ValueError:
        return default


def state_set(conn, key: str, value, by: str | None = None) -> None:
    conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                 "updated_by = excluded.updated_by", (key, json.dumps(value), config.now_iso(), by))


def audit(conn, action: str, actor_id: str | None = None, node_id: str | None = None,
          root_id: str | None = None) -> None:
    """An action, who did it, on what — never any content or names."""
    conn.execute("INSERT INTO audit_log (id, actor_id, node_id, root_id, action, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (new_id(), actor_id, node_id, root_id, action, config.now_iso()))
