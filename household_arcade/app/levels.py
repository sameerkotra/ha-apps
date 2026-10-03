"""Level lists for the games that play through levels (SPEC §11).

Brick Breaker (both modes share one list) and Snake's Maze mode each have a
list: the built-in levels (1–10, the same as in the game files, level_data.py)
and the ones the AI model made (level_builder.py), all in the `levels` table.
A level is data only — a brick grid, a maze of walls — and every level is
checked here (`validate`) before anyone can play it, whoever made it.

A game is given the playable levels (status `ready`) in number order; its
"level 3" is the third of those. Admins can reorder the list (`move`) and
delete levels the AI made (`delete`); built-in levels are known by a key
(`brick:1`), so they keep the place they were given. Retired levels are skipped; levels waiting for
an admin's OK (`waiting`) aren't played yet.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import re

from . import config, db, games, level_data, level_kinds

MAX_LEVELS = 500          # per list; the builder stops there

# The level lists: which game, and which of its modes play it.
SETS: dict[str, dict] = {
    "brick": {"label": "Brick Breaker", "modes": ["powerups", "classic"], "builtin": level_data.BRICK_BUILTIN},
    "snake": {"label": "Snake · Maze", "modes": ["maze"], "builtin": level_data.MAZE_BUILTIN},
}
# Every other game's list is described in level_kinds/<game>.py.
for _id, _k in level_kinds.KINDS.items():
    SETS[_id] = {"label": _k["label"], "modes": list(_k["modes"]), "builtin": _k["builtin"], "kind": _k}

BRICK_COLS, BRICK_MIN_ROWS, BRICK_MAX_ROWS = 8, 3, 10
BRICK_MIN_BRICKS, BRICK_MIN_HITS, BRICK_MAX_HITS = 12, 30, 150
MAZE_SIZE = 20
MAZE_START = (9, 10)                 # the snake's head; its body runs left of it, heading right
MAZE_MIN_FREE = 0.6
MAZE_FOODS = (5, 30)
from .level_common import NAME_MAX, LevelError, _only, check_name  # noqa: E402,F401  (shared with level_kinds)

def set_for(game: str, mode: str | None = None) -> str | None:
    """The level list a game (and mode) plays, or None."""
    if game not in SETS:
        return None
    if mode is not None and mode not in SETS[game]["modes"]:
        return None
    return game


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _brick(data: dict) -> tuple[dict, int, list[str]]:
    _only(data, {"name", "rows"})
    rows = data["rows"]
    if not isinstance(rows, list) or not all(isinstance(r, str) for r in rows):
        raise LevelError("rows must be a list of strings")
    rows = [r.strip() for r in rows]
    while rows and set(rows[-1]) <= {"."}:            # empty rows at the bottom mean nothing
        rows.pop()
    if not BRICK_MIN_ROWS <= len(rows) <= BRICK_MAX_ROWS:
        raise LevelError(f"a layout has {BRICK_MIN_ROWS}–{BRICK_MAX_ROWS} rows")
    for r in rows:
        if len(r) != BRICK_COLS or not set(r) <= set(".123"):
            raise LevelError(f'each row is {BRICK_COLS} characters of ".", "1", "2" or "3"')
    bricks = sum(1 for r in rows for c in r if c != ".")
    hits = sum(int(c) for r in rows for c in r if c != ".")
    if bricks < BRICK_MIN_BRICKS:
        raise LevelError(f"a layout needs at least {BRICK_MIN_BRICKS} bricks")
    if not BRICK_MIN_HITS <= hits <= BRICK_MAX_HITS:
        raise LevelError(f"the bricks must need {BRICK_MIN_HITS}–{BRICK_MAX_HITS} hits in all (this one needs {hits})")
    clean = {"name": check_name(data["name"]), "rows": rows}
    difficulty = min(100, round(hits * 100 / BRICK_MAX_HITS))
    mirror = "/".join(r[::-1] for r in rows)
    return clean, difficulty, ["/".join(rows), mirror]


def _maze(data: dict) -> tuple[dict, int, list[str]]:
    _only(data, {"name", "walls", "foods"})
    walls, foods = data["walls"], data["foods"]
    if not isinstance(walls, list) or len(walls) != MAZE_SIZE or not all(isinstance(r, str) for r in walls):
        raise LevelError(f"walls must be {MAZE_SIZE} rows")
    walls = [r.strip() for r in walls]
    if any(len(r) != MAZE_SIZE or not set(r) <= set(".#") for r in walls):
        raise LevelError(f'each row is {MAZE_SIZE} characters of "." or "#"')
    if isinstance(foods, bool) or not isinstance(foods, int) or not MAZE_FOODS[0] <= foods <= MAZE_FOODS[1]:
        raise LevelError(f"foods must be a whole number {MAZE_FOODS[0]}–{MAZE_FOODS[1]}")
    hx, hy = MAZE_START
    need = [(hx - i, hy) for i in range(4)] + [(hx + i, hy) for i in range(1, 4)]
    if any(walls[y][x] == "#" for x, y in need):
        raise LevelError("the snake's start (row 10, columns 6–12) must be free")
    free = {(x, y) for y in range(MAZE_SIZE) for x in range(MAZE_SIZE) if walls[y][x] == "."}
    if len(free) < MAZE_MIN_FREE * MAZE_SIZE * MAZE_SIZE:
        raise LevelError(f"at least {int(MAZE_MIN_FREE * 100)} % of the board must be free")
    seen, todo = {(hx, hy)}, [(hx, hy)]
    while todo:
        x, y = todo.pop()
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n in free and n not in seen:
                seen.add(n)
                todo.append(n)
    if len(seen) != len(free):
        raise LevelError("every free cell must be reachable (no closed rooms)")
    clean = {"name": check_name(data["name"]), "walls": walls, "foods": foods}
    wall_count = MAZE_SIZE * MAZE_SIZE - len(free)
    difficulty = min(100, round(wall_count * 100 / (MAZE_SIZE * MAZE_SIZE * (1 - MAZE_MIN_FREE)) * 0.7 + foods))
    mirror = "/".join(r[::-1] for r in walls)
    return clean, difficulty, ["/".join(walls), mirror]


_CHECKS = {"brick": _brick, "snake": _maze}


def validate(set_id: str, data) -> tuple[dict, int, str]:
    """(clean data, difficulty 0–100, fingerprint). Raises LevelError. The fingerprint is the same for a
    layout and its mirror image, so neither can be added twice."""
    if set_id in _CHECKS:
        clean, difficulty, prints = _CHECKS[set_id](data)
    elif set_id in level_kinds.KINDS:
        clean, difficulty, prints = level_kinds.validate(level_kinds.KINDS[set_id], data)
    else:
        raise LevelError("that game has no level list")
    return clean, difficulty, hashlib.sha256(min(prints).encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def seed_builtins(conn) -> None:
    """Write the built-in levels (startup, and after an import). A built-in level is known by its key
    (`brick:1` …) and keeps the place an admin gave it; its layout follows the game files. One that is
    missing (a new built-in level) is added at the end."""
    now = config.now_iso()
    for set_id, s in SETS.items():
        for i, data in enumerate(s["builtin"], 1):
            clean, difficulty, fp = validate(set_id, data)
            key = f"{set_id}:{i}"
            row = conn.execute("SELECT id FROM levels WHERE builtin_key = ?", (key,)).fetchone()
            if row:
                conn.execute("UPDATE levels SET name = ?, data = ?, difficulty = ?, fingerprint = ? WHERE id = ?",
                             (clean["name"], json.dumps(clean), difficulty, fp, row["id"]))
                continue
            number = (conn.execute("SELECT MAX(number) FROM levels WHERE game = ?", (set_id,)).fetchone()[0] or 0) + 1
            conn.execute("INSERT INTO levels (id, game, number, name, data, source, status, difficulty, fingerprint, "
                         "created_at, builtin_key) VALUES (?, ?, ?, ?, ?, 'builtin', 'ready', ?, ?, ?, ?)",
                         (db.new_id(), set_id, number, clean["name"], json.dumps(clean), difficulty, fp, now, key))


def _renumber(conn, set_id: str, ids: list[str]) -> None:
    """Give the list's levels the numbers 1, 2, 3 … in the order of `ids` (in two passes, so no two levels
    ever share a number on the way)."""
    for n, lid in enumerate(ids, 1):
        conn.execute("UPDATE levels SET number = ? WHERE id = ?", (-n, lid))
    conn.execute("UPDATE levels SET number = -number WHERE game = ? AND number < 0", (set_id,))


def move(conn, level_id: str, to: int) -> dict:
    """Put a level at place `to` (1-based) in its list; the others close up around it."""
    row = conn.execute("SELECT game FROM levels WHERE id = ?", (level_id,)).fetchone()
    if not row:
        raise LevelError("that level isn't known")
    ids = [r["id"] for r in conn.execute("SELECT id FROM levels WHERE game = ? ORDER BY number", (row["game"],))]
    if isinstance(to, bool) or not isinstance(to, int) or not 1 <= to <= len(ids):
        raise LevelError(f"the place must be 1–{len(ids)}")
    ids.remove(level_id)
    ids.insert(to - 1, level_id)
    _renumber(conn, row["game"], ids)
    return get(conn, level_id)


def delete(conn, level_id: str) -> None:
    """Remove a level the AI made (built-in levels can be retired, not deleted). Scores stay."""
    row = conn.execute("SELECT * FROM levels WHERE id = ?", (level_id,)).fetchone()
    if not row:
        raise LevelError("that level isn't known")
    if row["source"] == "builtin":
        raise LevelError("built-in levels can't be deleted; retire it instead")
    if row["status"] == "ready" and playable_count(conn, row["game"]) <= 1:
        raise LevelError("the last playable level can't be deleted")
    conn.execute("DELETE FROM levels WHERE id = ?", (level_id,))
    ids = [r["id"] for r in conn.execute("SELECT id FROM levels WHERE game = ? ORDER BY number", (row["game"],))]
    _renumber(conn, row["game"], ids)


def _public(r, with_data: bool = True) -> dict:
    out = {"id": r["id"], "number": r["number"], "name": r["name"], "source": r["source"], "status": r["status"],
           "difficulty": r["difficulty"], "model": r["model"], "createdAt": r["created_at"],
           "approvedBy": r["approved_by"]}
    if with_data:
        out["data"] = json.loads(r["data"])
    return out


def playable(conn, set_id: str) -> list[dict]:
    """The levels a game is given, in order (data only)."""
    return [json.loads(r["data"]) for r in conn.execute(
        "SELECT data FROM levels WHERE game = ? AND status = 'ready' ORDER BY number", (set_id,))]


def playable_count(conn, set_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM levels WHERE game = ? AND status = 'ready'", (set_id,)).fetchone()[0]


def all_levels(conn, set_id: str) -> list[dict]:
    return [_public(r, with_data=True) for r in conn.execute(
        "SELECT * FROM levels WHERE game = ? ORDER BY number", (set_id,))]


def get(conn, level_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM levels WHERE id = ?", (level_id,)).fetchone()
    return dict(_public(r), game=r["game"]) if r else None


def fingerprints(conn, set_id: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT fingerprint FROM levels WHERE game = ?", (set_id,))}


def summaries(conn, set_id: str, limit: int = 12) -> list[dict]:
    """The most recent levels, briefly, for the model to make something different."""
    rows = conn.execute("SELECT number, data, difficulty FROM levels WHERE game = ? AND status != 'retired' "
                        "ORDER BY number DESC LIMIT ?", (set_id, limit)).fetchall()
    return [dict(json.loads(r["data"]), number=r["number"], difficulty=r["difficulty"]) for r in reversed(rows)]


def add(conn, set_id: str, data: dict, *, model: str, status: str, build_id: str | None = None) -> dict:
    """Store a level the builder made (already validated). Raises LevelError for a repeat."""
    clean, difficulty, fp = validate(set_id, data)
    if fp in fingerprints(conn, set_id):
        raise LevelError("that layout is already in the list (or its mirror image is)")
    number = (conn.execute("SELECT MAX(number) FROM levels WHERE game = ?", (set_id,)).fetchone()[0] or 0) + 1
    if number > MAX_LEVELS:
        raise LevelError(f"the list already has {MAX_LEVELS} levels")
    lid = db.new_id()
    conn.execute("INSERT INTO levels (id, game, number, name, data, source, status, difficulty, fingerprint, model, "
                 "build_id, created_at) VALUES (?, ?, ?, ?, ?, 'ai', ?, ?, ?, ?, ?, ?)",
                 (lid, set_id, number, clean["name"], json.dumps(clean), status, difficulty, fp, model, build_id,
                  config.now_iso()))
    return get(conn, lid)


def set_status(conn, level_id: str, status: str, admin: dict) -> dict:
    row = conn.execute("SELECT * FROM levels WHERE id = ?", (level_id,)).fetchone()
    if not row:
        raise LevelError("that level isn't known")
    if status == "ready" and row["status"] == "ready":
        return get(conn, level_id)
    if status == "retired" and playable_count(conn, row["game"]) <= 1 and row["status"] == "ready":
        raise LevelError("the last playable level can't be retired")
    conn.execute("UPDATE levels SET status = ?, approved_by = ? WHERE id = ?",
                 (status, (admin.get("username") or admin.get("id")) if status == "ready" else row["approved_by"],
                  level_id))
    return get(conn, level_id)


def reached(conn, set_id: str) -> dict | None:
    """The highest level anyone has reached in this list (saved games), and who."""
    modes = SETS[set_id]["modes"]
    marks = ",".join("?" * len(modes))
    r = conn.execute(f"SELECT s.level, u.name FROM scores s JOIN users u ON u.id = s.user_id "
                     f"WHERE s.game = ? AND s.mode IN ({marks}) ORDER BY s.level DESC, s.ended_at LIMIT 1",
                     (set_id, *modes)).fetchone()
    return {"level": r["level"], "name": r["name"]} if r else None


def mode_info(game: str, mode: str) -> dict:
    """For POST /api/sessions: whether this game and mode use a level list, and which."""
    return {"set": set_for(game, mode) if games.uses_levels(game, mode) else None}
