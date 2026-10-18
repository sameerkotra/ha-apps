"""One person's data: export, import and delete (PERSON_DATA_SPEC.md §2, §6) — what is a person's in this app and
how it goes. The file format, its checks and the id remapping are the shared app/common/person_data.py; the routes
are routers/people_data.py (Admin → Users → ⋯ Data).

What is theirs (exported, deleted): their `users` row (their settings), `scores`, `saved_games`, `level_progress`,
`play_sessions`, `child_limits` and `extra_time` (a child's), `user_notify` (extra notify services an admin gave them);
`notify_log` is deleted but not exported (it only stops a notification being sent twice a day).

Shared, and kept: the matches they played. The export holds a read-only record of them (`matches.json`), never
loaded on import (a match needs the other players). A delete leaves each match working for the others:

- a match still going: their seat goes to the computer, as when a player leaves (a turn-by-turn match: turns.leave;
  a race: the seat is marked as left, and the one still playing wins when they finish);
- an invite not yet started: their seat goes (with one person left it is cancelled);
- `matches.created_by` moves to the next person in it (that key cascades: deleting the creator would delete the match
  for everyone);
- a match with nobody else (no other person who is still here): deleted;
- finished seats and `matches.winner` keep their id; pages show "Former player" for an id with no person (migration
  12 took the cascade off match_players.user_id for this).

Never touched: App settings (and the AI key), levels and level builds (their "approved by" / "requested by" names are
admin history), holidays, daily challenges, AI usage, the app-bus tables.

SCHEMA is the database's migration number (db.LATEST): a file from a newer one is refused; an older one is read with
the columns it has (each later column has the default its migration gave it — the same upgrade the migrations make).

Blocking functions: call them from a thread (the routes are plain `def`s).
"""
from __future__ import annotations

import json
import logging
import re

from . import auth, config, db, games, ha_sensors, limits, saves, together, turns
from .common import ha_notify, people_admin
from .common import person_data as pd

logger = logging.getLogger("people_data")

APP = "household_arcade"
TITLE = config.APP_TITLE
SCHEMA = db.LATEST
WHAT = "their scores, saved games, play history and, for a child, play-time limits"

# The tables read from a file, in the order they are loaded (play sessions first: scores and saves point at them).
TABLES = ("users", "play_sessions", "scores", "saved_games", "level_progress", "child_limits", "extra_time",
          "user_notify")
MATCHES = "matches"                      # the read-only record (data/matches.json)

# The settings columns on `users` an import carries over (never the id, names, switched off, dates).
SETTING_COLUMNS = ("look", "sound", "handedness", "reduce_motion", "receive_notifications", "game_prefs",
                   "assistant_ok", "favourites")
# What "keep them, empty" puts back: the columns' defaults (migrations 1, 7, 9, 10). Being a child stays as it is
# (an admin's decision, not the person's data).
SETTING_DEFAULTS = {"look": None, "sound": 0, "handedness": "right", "reduce_motion": 0, "receive_notifications": 1,
                    "game_prefs": None, "assistant_ok": 1, "favourites": None}

# The person's own rows, deleted in this order (level_progress first: its key to users has no cascade).
OWN_TABLES = ("level_progress", "scores", "saved_games", "play_sessions", "child_limits", "extra_time", "user_notify",
              "notify_log")

LIMITS_OPTION = "limits"
LIMIT_UPLOAD = pd.Limits(max_upload=200 * 1024 * 1024, max_unpacked=1024 ** 3, max_members=100,
                         max_json=200 * 1024 * 1024, max_rows=2_000_000)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_TEXT = 200                          # short text columns (modes, versions, names of who did it)


class DataError(ValueError):
    """Something the admin is told (status, sentence)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Reading what a person has
# ---------------------------------------------------------------------------

def load_person(conn, user_id: str):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise DataError(404, "That person isn't known to this app.")
    return row


def _rows(conn, sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args)]


def own_rows(conn, user_id: str) -> dict[str, list[dict]]:
    """Every table of theirs, as stored (columns by name)."""
    q = lambda t, order: _rows(conn, f"SELECT * FROM {t} WHERE user_id = ? ORDER BY {order}", (user_id,))
    return {
        "users": _rows(conn, "SELECT * FROM users WHERE id = ?", (user_id,)),
        "play_sessions": q("play_sessions", "started_at, id"),
        "scores": q("scores", "id"),
        "saved_games": q("saved_games", "game"),
        "level_progress": q("level_progress", "game, mode"),
        "child_limits": q("child_limits", "user_id"),
        "extra_time": q("extra_time", "id"),
        "user_notify": q("user_notify", "service"),
    }


def _their_matches(conn, user_id: str) -> list:
    return conn.execute("SELECT m.* FROM matches m JOIN match_players p ON p.match_id = m.id WHERE p.user_id = ? "
                        "UNION SELECT * FROM matches WHERE created_by = ? ORDER BY created_at", (user_id, user_id)
                        ).fetchall()


def matches_record(conn, user_id: str) -> list[dict]:
    """The matches they played, for reading only: game, mode, when, the other players' names, their result, the
    winner."""
    out = []
    for m in _their_matches(conn, user_id):
        ps = together._players(conn, m["id"])
        me = next((p for p in ps if p["user_id"] == user_id), None)
        winner = None
        if m["winner"]:
            winner = "them" if m["winner"] == user_id else next(
                (p["name"] for p in ps if p["user_id"] == m["winner"]), together.FORMER_PLAYER)
        out.append({
            "game": m["game"], "gameName": games.name(m["game"]), "mode": m["mode"],
            "modeLabel": games.mode_label(m["game"], m["mode"]), "kind": m["kind"], "status": m["status"],
            "practice": bool(m["practice"]), "createdAt": m["created_at"], "startedAt": m["started_at"],
            "endedAt": m["ended_at"], "endReason": m["end_reason"],
            "others": [p["name"] for p in ps if p["user_id"] != user_id],
            "result": me["result"] if me else None, "score": me["score"] if me else None, "winner": winner,
        })
    return out


def _records_held(conn, user_id: str) -> int:
    """Household records (best of a game and mode) that are theirs."""
    return conn.execute(
        "SELECT COUNT(*) FROM (SELECT user_id, ROW_NUMBER() OVER (PARTITION BY s.game, s.mode "
        "ORDER BY s.score DESC, s.ended_at ASC, s.id ASC) AS rn FROM scores s JOIN users u ON u.id = s.user_id "
        "WHERE u.disabled = 0 AND s.start_level = 1) WHERE rn = 1 AND user_id = ?", (user_id,)).fetchone()[0]


def _days(sessions) -> int:
    return len({config.to_local_date(s["started_at"]) for s in sessions if s.get("started_at")} - {None})


def _counts(rows: dict, *, records: int | None = None, matches: int = 0, going: int = 0) -> list[dict]:
    """The dialogs' counts (the summary, the delete's dry run, the import preview) from a person's rows."""
    sessions = rows.get("play_sessions") or []
    progress_games = len({r.get("game") for r in rows.get("level_progress") or []})
    extra = len(rows.get("extra_time") or [])
    out = pd.count_list([
        ("scores", "scores", len(rows.get("scores") or [])),
        ("saved_games", "saved games", len(rows.get("saved_games") or [])),
        ("level_progress", "games with level progress", progress_games),
        ("matches", "matches played", matches),
        ("play_sessions", "days of play history", _days(sessions)),
        ("child_limits", "child limits", len(rows.get("child_limits") or [])),
        ("user_notify", "extra notify services", len(rows.get("user_notify") or [])),
    ])
    for c in out:
        if c["key"] == "scores" and records:
            c["detail"] = f"{records} household record{'s' if records != 1 else ''}"
        elif c["key"] == "matches" and going:
            c["detail"] = f"{going} still going"
        elif c["key"] == "play_sessions" and sessions:
            c["detail"] = f"{len(sessions)} game{'s' if len(sessions) != 1 else ''}"
        elif c["key"] == "child_limits" and extra:
            c["detail"] = f"and {extra} extra-time entr{'ies' if extra != 1 else 'y'}"
    return out


def counts(conn, user_id: str) -> list[dict]:
    rows = own_rows(conn, user_id)
    ms = _their_matches(conn, user_id)
    going = sum(1 for m in ms if m["status"] in ("invited", "playing", "paused"))
    return _counts(rows, records=_records_held(conn, user_id), matches=len(ms), going=going)


def summary(conn, user_id: str) -> dict:
    """What the dialogs show (GET …/data): also the delete's dry run."""
    row = load_person(conn, user_id)
    return {
        "person": {"id": row["id"], "name": row["name"]},
        "counts": counts(conn, user_id),
        "exportOptions": [],
        "exportNote": "The file holds their scores, play history and settings (and a child's limits). It isn't "
                      "encrypted — keep it somewhere safe.",
        "keeps": [
            "Matches they played stay for the others: a match still going goes on with the computer in their seat, "
            "and finished ones keep their result (shown as \"Former player\" once they're forgotten).",
            "Their household records pass to the next best score.",
            "Levels, App settings, holidays and AI usage aren't anyone's data and stay as they are.",
        ],
        "cannotReach": ["whole-app backups (Admin → Storage) and Home Assistant backups made before still hold their "
                        "data"],
        "deleteChoices": [],
        "forgetNote": "Their row goes too. If they still have a Home Assistant login they come back as a new, empty "
                      "person the next time they open the app — remove them in Home Assistant first if they "
                      "shouldn't come back.",
        "deleteNote": ("A child keeps the child mark but loses their play-time limits: set them again after."
                       if row["is_child"] else None),
    }


def counts_text(cs: list[dict]) -> str:
    """"12 scores, 2 saved games" — for the log (counts only, never content)."""
    return ", ".join(f"{c['count']} {c['label']}" for c in cs if c["count"]) or "nothing"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_bytes(user_id: str, admin: dict) -> tuple[bytes, str, list[dict]]:
    """The .zip (small: no files in this app), its file name, and the counts."""
    with db.get_conn() as conn:
        row = load_person(conn, user_id)
        tables = own_rows(conn, user_id)
        record = matches_record(conn, user_id)
        cs = counts(conn, user_id)
    data = pd.bytes_io_package(app=APP, app_version=config.APP_VERSION, schema=SCHEMA,
                               person={"id": row["id"], "username": row["username"], "name": row["name"]},
                               exported_by=who(admin), tables=tables, records={MATCHES: record}, title=TITLE,
                               what=WHAT)
    return data, pd.file_name(TITLE, row["name"]), cs


def who(admin: dict) -> str:
    return admin.get("username") or admin["id"]


# ---------------------------------------------------------------------------
# Checking a file's rows (the same rules as the API and the database)
# ---------------------------------------------------------------------------

class _Check:
    """Helpers that raise DataError(422, "<table>, row <n>: …")."""

    def __init__(self, table: str, n: int):
        self.where = f"{table}, row {n}"

    def fail(self, msg: str):
        raise DataError(422, f"The file can't be imported: {self.where}: {msg}")

    def int(self, row, key, lo, hi, *, default=None, nullable=False):
        v = row.get(key, default)
        if v is None:
            if nullable:
                return None
            self.fail(f"{key} is missing.")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or v != int(v):
            self.fail(f"{key} must be a whole number from {lo} to {hi}.")      # (the range first: inf, NaN)
        return int(v)

    def num(self, row, key, lo, hi, *, default=None):
        v = row.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
            self.fail(f"{key} must be a number from {lo} to {hi}.")
        return float(v)

    def flag(self, row, key, default=0):
        v = row.get(key, default)
        if v in (0, 1) or isinstance(v, bool):
            return 1 if v else 0
        self.fail(f"{key} must be 0 or 1.")

    def text(self, row, key, *, max_len=MAX_TEXT, nullable=False, default=None):
        v = row.get(key, default)
        if v is None and nullable:
            return None
        if not isinstance(v, str) or not v or len(v) > max_len:
            self.fail(f"{key} must be text of at most {max_len} characters.")
        return v

    def ts(self, row, key, *, nullable=False):
        v = row.get(key)
        if v is None and nullable:
            return None
        if not isinstance(v, str) or len(v) > 40 or config.parse_ts(v) is None:
            self.fail(f"{key} must be a date and time.")
        return v

    def json_text(self, row, key, *, max_len, nullable=True):
        v = row.get(key)
        if v is None and nullable:
            return None
        if not isinstance(v, str) or len(v) > max_len:
            self.fail(f"{key} is too long or not text.")
        try:
            json.loads(v)
        except ValueError:
            self.fail(f"{key} can't be read.")
        return v


def _mode_ok(game: str, mode) -> bool:
    if not isinstance(mode, str):
        return False
    if mode.startswith("daily-"):                     # a daily challenge's scores (daily.py)
        return bool(_DATE_RE.match(mode[6:]))
    return mode in games.mode_ids(game)


def _clean_user(c: _Check, r: dict) -> dict:
    from .routers.play import MAX_FAVOURITES
    from .routers.prefs import _merge_game_prefs
    out = {}
    look = r.get("look")
    if look is not None and look not in games.LOOKS:
        c.fail("look isn't one this app knows.")
    out["look"] = look
    for k in ("sound", "reduce_motion"):
        out[k] = c.flag(r, k, 0)
    for k in ("receive_notifications", "assistant_ok"):
        out[k] = c.flag(r, k, 1)
    out["is_child"] = c.flag(r, "is_child", 0)
    hand = r.get("handedness", "right")
    if hand not in ("right", "left"):
        c.fail("handedness must be right or left.")
    out["handedness"] = hand
    prefs = c.json_text(r, "game_prefs", max_len=4000)
    if prefs is not None:
        data = json.loads(prefs)
        if not isinstance(data, dict):
            c.fail("game_prefs must be an object of games.")
        try:                                              # the Settings page's own rules
            data = _merge_game_prefs({}, {g: v for g, v in data.items() if games.exists(g)})
        except Exception as e:                            # HTTPException with the reason
            c.fail(getattr(e, "detail", None) or str(e))
        prefs = json.dumps(data)
    out["game_prefs"] = prefs
    favs = c.json_text(r, "favourites", max_len=8000)
    if favs is not None:
        data = json.loads(favs)
        if not isinstance(data, list):
            c.fail("favourites must be a list of games.")
        keep = []
        for g in data:
            if isinstance(g, str) and games.exists(g) and g not in keep:
                keep.append(g)
        favs = json.dumps(keep[-MAX_FAVOURITES:])
    out["favourites"] = favs
    return out


def _clean_session(c: _Check, r: dict) -> dict:
    out = {"id": r.get("id"), "game": r["game"], "mode": c.text(r, "mode", max_len=60),
           "practice": c.flag(r, "practice"), "started_at": c.ts(r, "started_at")}
    out["last_beat_at"] = c.ts(r, "last_beat_at") if r.get("last_beat_at") is not None else out["started_at"]
    out["active_seconds"] = c.num(r, "active_seconds", 0, games.MAX_SECONDS * 4, default=0)
    out["ended_at"] = c.ts(r, "ended_at", nullable=True)
    out["scored"] = c.flag(r, "scored")
    out["level_count"] = c.int(r, "level_count", 0, 100000, nullable=True)
    out["base_seconds"] = c.num(r, "base_seconds", 0, games.MAX_SECONDS * 4, default=0)
    out["resumed"] = c.flag(r, "resumed")
    out["first_started_at"] = c.ts(r, "first_started_at", nullable=True)
    daily = r.get("daily")
    if daily is not None and (not isinstance(daily, str) or not _DATE_RE.match(daily)):
        c.fail("daily must be a date (YYYY-MM-DD).")
    out["daily"] = daily
    out["start_level"] = c.int(r, "start_level", 1, 100000, default=1)
    out["end_reason"] = c.text(r, "end_reason", max_len=40, nullable=True)
    out["level_seen"] = c.int(r, "level_seen", 0, 100000, nullable=True)
    if out["ended_at"] is None:          # a game that was open when exported: it ended at its last heartbeat
        out["ended_at"], out["end_reason"] = out["last_beat_at"], out["end_reason"] or "stale"
    return out


def _clean_score(c: _Check, r: dict) -> dict:
    game, mode = r["game"], r.get("mode")
    if not _mode_ok(game, mode):
        c.fail("mode isn't one of this game's.")
    score = c.int(r, "score", 0, 10 ** 12)
    level = c.int(r, "level", 0, 100000, default=1)
    seconds = c.int(r, "seconds", 0, games.MAX_SECONDS)
    if not mode.startswith("daily-"):
        # the honest-score limits a result sent to POST /api/scores is checked with (the level count isn't known
        # here, so the level is checked against the most any list allows)
        problem = games.check_score(game, mode, score, level, seconds, None)
        if problem and not problem.startswith("The level can be at most"):
            c.fail(problem)
    elif score > games.GAMES[game]["max_score"]:
        c.fail(f"That score is higher than {games.name(game)} allows.")
    return {"id": r.get("id"), "game": game, "mode": mode, "score": score, "level": level, "seconds": seconds,
            "started_at": c.ts(r, "started_at"), "ended_at": c.ts(r, "ended_at"),
            "app_version": c.text(r, "app_version", max_len=40, default="?"),
            "session_id": r.get("session_id"), "start_level": c.int(r, "start_level", 1, 100000, default=1)}


def _clean_save(c: _Check, r: dict) -> dict:
    game, mode = r["game"], r.get("mode")
    if not _mode_ok(game, mode) or mode.startswith("daily-"):
        c.fail("mode isn't one of this game's.")
    state = c.json_text(r, "state", max_len=saves.MAX_STATE_BYTES, nullable=False)
    return {"game": game, "mode": mode, "practice": c.flag(r, "practice"), "state": state,
            "state_version": c.int(r, "state_version", 0, 10 ** 6),
            "levels": c.json_text(r, "levels", max_len=saves.MAX_STATE_BYTES),
            "score": c.int(r, "score", 0, 10 ** 12), "level": c.int(r, "level", 0, 100000),
            "seconds": c.int(r, "seconds", 0, games.MAX_SECONDS), "level_count": c.int(r, "level_count", 0, 100000,
                                                                                        nullable=True),
            "started_at": c.ts(r, "started_at"), "saved_at": c.ts(r, "saved_at"), "session_id": r.get("session_id"),
            "app_version": c.text(r, "app_version", max_len=40, default="?"),
            "start_level": c.int(r, "start_level", 1, 100000, default=1)}


def _clean_progress(c: _Check, r: dict) -> dict | None:
    game, mode = r["game"], r.get("mode")
    if not games.continues(game, mode):
        return None                          # a mode that no longer carries on: nothing to load
    return {"game": game, "mode": mode, "cleared": c.int(r, "cleared", 0, 100000),
            "updated_at": c.ts(r, "updated_at")}


def _clean_limits(c: _Check, r: dict) -> dict:
    allowed = r.get("allowed_games")
    if allowed is not None:
        try:
            allowed = json.loads(allowed) if isinstance(allowed, str) else None
        except ValueError:
            allowed = None
        if not isinstance(allowed, list):
            c.fail("allowed_games can't be read.")
        allowed = [g for g in allowed if isinstance(g, str) and games.exists(g)]
    body = {"minutesSchool": r.get("minutes_school"), "minutesWeekend": r.get("minutes_weekend"),
            "quietSchoolFrom": r.get("quiet_school_from"), "quietSchoolTo": r.get("quiet_school_to"),
            "quietWeekendFrom": r.get("quiet_weekend_from"), "quietWeekendTo": r.get("quiet_weekend_to"),
            "allowedGames": allowed, "leaderboard": r.get("leaderboard", "full")}
    try:
        return limits.clean_limits(body)                 # Admin → Users' own rules
    except ValueError as e:
        c.fail(str(e))


def _clean_extra(c: _Check, r: dict) -> dict:
    d = r.get("date")
    if not isinstance(d, str) or not _DATE_RE.match(d):
        c.fail("date must be a date (YYYY-MM-DD).")
    return {"date": d, "minutes": c.int(r, "minutes", 1, limits.MAX_DAILY_MINUTES),
            "given_by": c.text(r, "given_by", nullable=True), "created_at": c.ts(r, "created_at")}


def _clean_notify(c: _Check, r: dict) -> dict:
    try:
        service = people_admin.clean_service(r.get("service"))
    except Exception as e:                               # a 422 with ha_notify's reason
        c.fail(getattr(e, "detail", None) or str(e))
    return {"service": service, "created_at": c.ts(r, "created_at"), "created_by": c.text(r, "created_by",
                                                                                        nullable=True)}


_CLEANERS = {"play_sessions": _clean_session, "scores": _clean_score, "saved_games": _clean_save,
             "level_progress": _clean_progress, "child_limits": _clean_limits, "extra_time": _clean_extra,
             "user_notify": _clean_notify}
_HAS_GAME = ("play_sessions", "scores", "saved_games", "level_progress")


def prepare(pkg: pd.Package) -> dict:
    """The file's rows checked and cleaned: {"rows": {table: [row]}, "warnings": [text], "unknown": n}. Raises
    DataError(422) naming the table and row of the first row this app wouldn't accept."""
    rows: dict[str, list] = {}
    unknown = 0
    users = pkg.tables.get("users") or []
    if len(users) > 1:
        raise DataError(422, "The file can't be imported: it holds more than one person.")
    rows["users"] = [_clean_user(_Check("users", 1), users[0])] if users else []
    for table in TABLES[1:]:
        out = []
        for i, r in enumerate(pkg.tables.get(table) or [], start=1):
            if table in _HAS_GAME:
                g = r.get("game")
                if not isinstance(g, str) or not games.exists(g):
                    unknown += 1                  # a game this version doesn't have: left out, said in the preview
                    continue
            clean = _CLEANERS[table](_Check(table, i), r)
            if clean is not None:
                out.append(clean)
        rows[table] = out
    warnings = ["Imported scores take part in the household records and the leaderboard like any other."]
    if unknown:
        warnings.append(f"{unknown} row{'s' if unknown != 1 else ''} of games this version doesn't have "
                        f"{'are' if unknown != 1 else 'is'} left out.")
    if pkg.records.get(MATCHES):
        warnings.append(f"The {len(pkg.records[MATCHES])} matches they played are in the file as a record only; "
                        "they aren't loaded (a match needs the other players).")
    if rows["child_limits"] or rows["extra_time"]:
        warnings.append("The file has play-time limits: they're loaded only with \"Also their play-time limits\".")
    return {"rows": rows, "warnings": warnings, "unknown": unknown}


def preview(conn, pkg: pd.Package, prepared: dict) -> dict:
    m = pkg.manifest
    person = pkg.person
    target = person.get("id") if person.get("id") and conn.execute(
        "SELECT 1 FROM users WHERE id = ?", (person.get("id"),)).fetchone() else None
    return {
        "app": TITLE, "appVersion": str(m.get("appVersion") or ""), "exportedAt": m.get("exportedAt"),
        "exportedBy": m.get("exportedBy"),
        "person": {k: person.get(k) for k in ("id", "name", "username") if person.get(k) is not None},
        "counts": _counts(prepared["rows"], matches=len(pkg.records.get(MATCHES) or [])),
        "warnings": prepared["warnings"],
        "options": [{"key": LIMITS_OPTION, "label": "Also their play-time limits", "default": False,
                     "help": "A child's daily minutes, quiet hours and allowed games from the file. Off: the limits "
                             "set for them in this house stay."}],
        "target": target,
    }


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def _others(conn, match_id: str, user_id: str) -> list:
    """The other people in a match who are still here (a former player's seat doesn't count), by seat."""
    return conn.execute("SELECT p.* FROM match_players p JOIN users u ON u.id = p.user_id "
                        "WHERE p.match_id = ? AND p.user_id != ? ORDER BY p.seat", (match_id, user_id)).fetchall()


def _leave_matches(conn, user_id: str) -> dict:
    """The matches rules of a delete (module docstring). Returns {"deleted", "handed_over", "left"}."""
    out = {"deleted": 0, "handed_over": 0, "left": 0}
    now = config.now_iso()
    for m in _their_matches(conn, user_id):
        others = _others(conn, m["id"], user_id)
        if not others:
            conn.execute("DELETE FROM matches WHERE id = ?", (m["id"],))
            together.LIVE.pop(m["id"], None)
            out["deleted"] += 1
            continue
        if m["created_by"] == user_id:
            conn.execute("UPDATE matches SET created_by = ? WHERE id = ?", (others[0]["user_id"], m["id"]))
            out["handed_over"] += 1
        mine = conn.execute("SELECT * FROM match_players WHERE match_id = ? AND user_id = ?",
                            (m["id"], user_id)).fetchone()
        if m["status"] == "invited":
            # not started: their seat simply goes; an invite with one person left has nobody to play
            conn.execute("DELETE FROM match_players WHERE match_id = ? AND user_id = ?", (m["id"], user_id))
            still = [p for p in others if p["invite_status"] != "declined"]
            if len(still) < 2:
                together._end(conn, m, "cancelled", None)
            together.touch(m["id"])
            out["left"] += 1
        elif m["status"] in ("playing", "paused") and mine and not mine["finished_at"] and not mine["left_at"]:
            if m["kind"] == "turns" and rules_ok(m["game"]):
                turns.leave(conn, conn.execute("SELECT * FROM matches WHERE id = ?", (m["id"],)).fetchone(),
                            [user_id], "left")
            else:
                # a race: their seat is the computer's (it plays nothing), so the one still playing wins once they
                # finish (together._gone counts a seat that left as gone)
                conn.execute("UPDATE match_players SET left_at = ?, computer = 1, result = 'lost' "
                             "WHERE match_id = ? AND user_id = ?", (now, m["id"], user_id))
                together.settle(conn, m["id"])
            together.touch(m["id"])
            out["left"] += 1
    return out


def rules_ok(game: str) -> bool:
    from . import rules
    return rules.get(game) is not None


def delete_rows(conn, user_id: str, *, forget: bool) -> dict:
    """Everything of theirs, in the caller's transaction (no commit here). Returns what went."""
    db.write_lock(conn)
    went = {t: 0 for t in OWN_TABLES}
    went.update(_leave_matches(conn, user_id))
    for t in OWN_TABLES:
        went[t] = conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,)).rowcount
    if forget:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))      # cascades whatever is left
    else:
        cols = ", ".join(f"{k} = ?" for k in SETTING_DEFAULTS)
        conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*SETTING_DEFAULTS.values(), user_id))
    return went


def delete(user_id: str, admin: dict, body: dict) -> tuple[str, list[dict], dict]:
    """The whole delete: the confirmation, one transaction, the log line. Returns (message, counts, sensor slugs
    before) — the caller removes the sensors after the commit."""
    if not isinstance(body, dict):
        raise DataError(422, "Send {confirm, forget}.")
    forget = body.get("forget", False)
    if not isinstance(forget, bool):
        raise DataError(422, "forget must be true or false.")
    with pd.one_at_a_time():
        with db.get_conn() as conn:
            row = load_person(conn, user_id)
            if not pd.names_match(body.get("confirm"), row["name"]):
                raise DataError(409, f"Type {row['name']} to confirm.")
            slugs = ha_sensors.person_slugs(conn)
            cs = counts(conn, user_id)
            delete_rows(conn, user_id, forget=forget)
    logger.info("Person data delete: %s deleted the data of %s (%s)%s", who(admin), user_id, counts_text(cs),
                " and forgot them" if forget else "")
    msg = f"Deleted {counts_text(cs)} of {row['name']}." if any(c["count"] for c in cs) \
        else f"{row['name']} had nothing here to delete."
    if forget:
        msg += " They're forgotten here."
    return msg, cs, slugs


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _apply_settings(conn, user_id: str, u: dict) -> None:
    cols = ", ".join(f"{k} = ?" for k in SETTING_COLUMNS)
    conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*(u[k] for k in SETTING_COLUMNS), user_id))


def _has_data(conn, user_id: str) -> bool:
    return any(conn.execute(f"SELECT 1 FROM {t} WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
               for t in ("scores", "play_sessions", "saved_games", "level_progress"))


def _insert(conn, table: str, row: dict) -> None:
    cols = list(row)
    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [row[c] for c in cols])


def load_rows(conn, user_id: str, rows: dict, *, replace: bool, with_limits: bool) -> dict:
    """The cleaned rows into the person, in the caller's transaction. Returns {"added": {table: n}, "skipped": n}."""
    target = load_person(conn, user_id)
    added = {t: 0 for t in TABLES}
    skipped = 0
    ids = pd.IdMap()
    is_admin = auth.is_admin_identity(target["id"], target["username"])

    # their settings: Replace takes the file's; Add takes them only for someone with nothing here yet (a person kept
    # empty, or new), so the settings they have chosen since aren't undone. Favourites are merged either way.
    if rows["users"]:
        u = dict(rows["users"][0])
        if not replace and _has_data(conn, user_id):
            mine = conn.execute("SELECT favourites FROM users WHERE id = ?", (user_id,)).fetchone()["favourites"]
            favs = json.loads(mine) if mine else []
            for g in json.loads(u["favourites"] or "[]"):
                if g not in favs:
                    favs.append(g)
            conn.execute("UPDATE users SET favourites = ? WHERE id = ?", (json.dumps(favs) if favs else None, user_id))
        else:
            _apply_settings(conn, user_id, u)
        added["users"] = 1

    # play history: a game is the same when it has the same game, mode and start
    have = {(r["game"], r["mode"], r["started_at"]): r["id"] for r in conn.execute(
        "SELECT id, game, mode, started_at FROM play_sessions WHERE user_id = ?", (user_id,))}
    for r in rows["play_sessions"]:
        key = (r["game"], r["mode"], r["started_at"])
        if key in have:
            ids.put("play_sessions", r["id"], have[key])
            skipped += 1
            continue
        new = dict(r, id=db.new_id(), user_id=user_id, match_id=None)     # matches aren't loaded
        _insert(conn, "play_sessions", new)
        ids.put("play_sessions", r["id"], new["id"])
        have[key] = new["id"]
        added["play_sessions"] += 1

    # scores: the same game, mode, score and time
    have_s = {(r["game"], r["mode"], r["score"], r["ended_at"]) for r in conn.execute(
        "SELECT game, mode, score, ended_at FROM scores WHERE user_id = ?", (user_id,))}
    for r in rows["scores"]:
        key = (r["game"], r["mode"], r["score"], r["ended_at"])
        if key in have_s:
            skipped += 1
            continue
        new = {k: v for k, v in r.items() if k != "id"}
        new.update(user_id=user_id, session_id=ids.get("play_sessions", r["session_id"]))
        _insert(conn, "scores", new)
        have_s.add(key)
        added["scores"] += 1

    # saved games: one per game — Add keeps theirs (Replace deleted theirs first)
    have_g = {r["game"] for r in conn.execute("SELECT game FROM saved_games WHERE user_id = ?", (user_id,))}
    for r in rows["saved_games"]:
        if r["game"] in have_g:
            skipped += 1
            continue
        _insert(conn, "saved_games", dict(r, user_id=user_id, session_id=ids.get("play_sessions", r["session_id"])))
        have_g.add(r["game"])
        added["saved_games"] += 1

    # level progress: the same game and mode — the further one wins
    for r in rows["level_progress"]:
        cur = conn.execute("SELECT cleared FROM level_progress WHERE user_id = ? AND game = ? AND mode = ?",
                           (user_id, r["game"], r["mode"])).fetchone()
        if cur and cur["cleared"] >= r["cleared"]:
            skipped += 1
            continue
        conn.execute("INSERT INTO level_progress (user_id, game, mode, cleared, updated_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(user_id, game, mode) DO UPDATE SET cleared = excluded.cleared, "
                     "updated_at = excluded.updated_at", (user_id, r["game"], r["mode"], r["cleared"], r["updated_at"]))
        added["level_progress"] += 1

    # a child's limits: only when asked (limits are set for a child in this house); never for an admin
    if with_limits and not is_admin and (rows["child_limits"] or rows["extra_time"]):
        conn.execute("UPDATE users SET is_child = 1 WHERE id = ?", (user_id,))
        for lim in rows["child_limits"][:1]:
            limits.save_limits(conn, user_id, lim, None)
            added["child_limits"] += 1
        have_e = {(r["date"], r["minutes"], r["created_at"]) for r in conn.execute(
            "SELECT date, minutes, created_at FROM extra_time WHERE user_id = ?", (user_id,))}
        for r in rows["extra_time"]:
            key = (r["date"], r["minutes"], r["created_at"])
            if key in have_e:
                skipped += 1
                continue
            _insert(conn, "extra_time", dict(r, user_id=user_id))
            have_e.add(key)
            added["extra_time"] += 1

    # extra notify services: the same service is the same; at most as many as Admin → Users allows
    from .routers.users import MAX_SERVICES_PER_USER
    have_n = set(ha_notify.assigned_services(conn, {"id": user_id}))
    for r in rows["user_notify"]:
        if r["service"] in have_n or len(have_n) >= MAX_SERVICES_PER_USER:
            skipped += 1
            continue
        _insert(conn, "user_notify", dict(r, user_id=user_id))
        have_n.add(r["service"])
        added["user_notify"] += 1
    return {"added": added, "skipped": skipped}


def read_upload(path: str) -> pd.Package:
    try:
        return pd.read_package(path, app=APP, schema=SCHEMA, limits=LIMIT_UPLOAD, tables=TABLES)
    except pd.PackageError as e:
        raise DataError(400, str(e))


_LABELS = {"scores": ("score", "scores"), "saved_games": ("saved game", "saved games"),
           "level_progress": ("level progress", "level progress"), "play_sessions": ("game of play history",
                                                                                     "games of play history"),
           "child_limits": ("set of limits", "sets of limits"), "extra_time": ("extra-time entry",
                                                                               "extra-time entries"),
           "user_notify": ("notify service", "notify services")}


def result_text(res: dict) -> str:
    parts = [f"{n} {_LABELS[t][0] if n == 1 else _LABELS[t][1]}" for t, n in res["added"].items()
             if t in _LABELS and n]
    text = "Imported " + (", ".join(parts) if parts else "nothing new")
    if res["added"].get("users") and not parts:
        text = "Imported their settings"
    if res["skipped"]:
        text += f"; {res['skipped']} already there {'were' if res['skipped'] != 1 else 'was'} skipped"
    return text + "."


def do_import(user_id: str, admin: dict, path: str, body: dict) -> tuple[str, dict, dict]:
    """Checks the kept file again, then (Replace: delete theirs first, keeping them) loads it — all in one
    transaction. Returns (message, result, sensor slugs before)."""
    if not isinstance(body, dict):
        raise DataError(422, "Send {mode, confirm?, options?}.")
    mode = body.get("mode", "add")
    if mode not in ("add", "replace"):
        raise DataError(422, "mode must be add or replace.")
    options = body.get("options") or {}
    if not isinstance(options, dict) or any(not isinstance(v, bool) for v in options.values()):
        raise DataError(422, "options must be an object of true / false.")
    with read_upload(path) as pkg:
        prepared = prepare(pkg)
    with pd.one_at_a_time():
        with db.get_conn() as conn:
            row = load_person(conn, user_id)
            if mode == "replace" and not pd.names_match(body.get("confirm"), row["name"]):
                raise DataError(409, f"To replace, type {row['name']} to confirm.")
            slugs = ha_sensors.person_slugs(conn)
            db.write_lock(conn)
            if mode == "replace":
                delete_rows(conn, user_id, forget=False)
            res = load_rows(conn, user_id, prepared["rows"], replace=mode == "replace",
                            with_limits=bool(options.get(LIMITS_OPTION)))
    logger.info("Person data import (%s): %s imported into %s (%s; %d skipped)", mode, who(admin), user_id,
                ", ".join(f"{n} {t}" for t, n in res["added"].items() if n) or "nothing", res["skipped"])
    return result_text(res), res, slugs
