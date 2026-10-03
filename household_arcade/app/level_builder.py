"""Building more levels with the AI model (SPEC §11).

A *build* asks the model for some levels of one list (Brick Breaker, Snake ·
Maze), checks every level it returns (levels.validate: the shape, the rules,
no repeats) and stores the good ones. Builds run one at a time in a worker
thread, in the order they were asked for; Admin → Levels shows them.

Builds start in two ways:
- an admin presses Build more levels (request_build);
- automatically (maybe_auto), when someone reaches a level within
  `ai_levels_ahead` of the end of the list, so the next levels are there
  before anyone needs them. At most one build per list waits or runs at a time.

`ai_levels_daily_limit` caps the levels made a day (Home Assistant's day),
across all lists, to protect a paid key. Nothing about the people in the house
is sent to the model: only the game's rules, the format, a target difficulty
and the recent levels (so the new ones differ).
"""
from __future__ import annotations

import json
import logging
import threading

import time

from . import ai_client, ai_usage, config, db, level_kinds, levels, settings

logger = logging.getLogger("level_builder")

PER_REQUEST = 5              # levels asked for in one request
MAX_FAILED_REQUESTS = 3      # requests in a row that add nothing end the build
MAX_LOG = 40


class BuildError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# The model call; tests replace it.
def _generate(prompt: str, cfg: ai_client.Config) -> ai_client.Reply:
    return ai_client.generate(prompt, want_json=True, cfg=cfg)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def _target(set_id: str, number: int) -> dict:
    if set_id == "brick":
        return {"hits": min(140, 40 + 6 * max(0, number - 10))}
    return {"walls": min(150, 30 + 6 * max(0, number - 10)), "foods": min(levels.MAZE_FOODS[1], 4 + number)}


def prompt(set_id: str, count: int, first_number: int, recent: list[dict]) -> str:
    if set_id in level_kinds.KINDS:
        return level_kinds.prompt(level_kinds.KINDS[set_id], count, first_number, recent)
    t = _target(set_id, first_number)
    if set_id == "brick":
        examples = "\n".join(f'level {r["number"]} "{r["name"]}": ' + json.dumps(r["rows"]) for r in recent[-6:])
        return f"""You design levels for a brick-breaking arcade game played by a family.

A level is a grid {levels.BRICK_COLS} columns wide and {levels.BRICK_MIN_ROWS}-{levels.BRICK_MAX_ROWS} rows tall, \
one string per row, {levels.BRICK_COLS} characters each:
"." empty, "1" a brick that breaks in one hit, "2" needs two hits, "3" needs three hits.

Rules for every level:
- at least {levels.BRICK_MIN_BRICKS} bricks, and {levels.BRICK_MIN_HITS}-{levels.BRICK_MAX_HITS} hits in all \
(add up the digits);
- a clear picture or pattern (shapes, letters, symmetry, stripes, a face, an animal ...), different from the \
levels below and from each other;
- a short friendly name, 1-4 words, letters and spaces only, fine for children.

These are levels {first_number} to {first_number + count - 1}. They should get a little harder each time: \
level {first_number} needs about {t["hits"]} hits, later ones a few more (tougher bricks, fewer gaps).

The most recent levels, so yours are different:
{examples}

Answer with JSON only, exactly {count} level(s):
{{"levels": [{{"name": "...", "rows": ["........", "..."]}}]}}"""
    examples = "\n".join(f'level {r["number"]} "{r["name"]}" (foods {r["foods"]}):\n' + "\n".join(r["walls"])
                         for r in recent[-3:])
    return f"""You design mazes for a Snake game played by a family.

The board is {levels.MAZE_SIZE} x {levels.MAZE_SIZE}: {levels.MAZE_SIZE} strings of {levels.MAZE_SIZE} characters, \
"#" a wall and "." free. The edge of the board is already a wall, so don't draw a border.
The snake starts in row index 10 (the 11th row), columns 6-9, moving right: row index 10, columns 6 to 12, must be free.
"foods" is how many foods the snake eats to finish the maze.

Rules for every maze:
- every free cell can be reached from the start (no closed rooms, no sealed corners);
- at least {int(levels.MAZE_MIN_FREE * 100)} % of the board is free;
- walls make corridors, rooms with doorways, pillars or shapes - not one solid block;
- foods {levels.MAZE_FOODS[0]}-{levels.MAZE_FOODS[1]};
- a short friendly name, 1-4 words, letters and spaces only, fine for children;
- different from the mazes below and from each other.

These are mazes {first_number} to {first_number + count - 1}, each a little harder: maze {first_number} has about \
{t["walls"]} wall cells and foods {t["foods"]}, later ones a few more of both.

The most recent mazes, so yours are different:
{examples}

Answer with JSON only, exactly {count} maze(s):
{{"levels": [{{"name": "...", "foods": 10, "walls": ["....................", "..."]}}]}}"""


def parse(text: str) -> list:
    """The level objects in the model's answer (an object with "levels", a list, or one level)."""
    text = (text or "").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("the answer had no JSON in it")
    end = max(text.rfind("}"), text.rfind("]"))
    data = json.loads(text[start:end + 1])
    if isinstance(data, dict) and isinstance(data.get("levels"), list):
        return data["levels"]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise ValueError("the answer wasn't a list of levels")


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------

def made_today(conn) -> int:
    start, end = config.local_day_bounds_utc(config.today())
    return conn.execute("SELECT COUNT(*) FROM levels WHERE source = 'ai' AND created_at >= ? AND created_at < ?",
                        (start, end)).fetchone()[0]


def left_today(conn) -> int | None:
    limit = settings.get("ai_levels_daily_limit")
    return None if not limit else max(0, limit - made_today(conn))


def _open_build(conn, set_id: str):
    return conn.execute("SELECT * FROM level_builds WHERE game = ? AND status IN ('queued', 'running') "
                        "ORDER BY created_at LIMIT 1", (set_id,)).fetchone()


def public(r) -> dict:
    return {"id": r["id"], "game": r["game"], "requestedBy": r["requested_by"], "count": r["count"],
            "status": r["status"], "made": r["made"], "rejected": r["rejected"], "requests": r["requests"],
            "tokensIn": r["tokens_in"], "tokensOut": r["tokens_out"], "model": r["model"], "error": r["error"],
            "log": json.loads(r["log"]) if r["log"] else [], "createdAt": r["created_at"],
            "startedAt": r["started_at"], "endedAt": r["ended_at"]}


def get(conn, build_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM level_builds WHERE id = ?", (build_id,)).fetchone()
    return public(r) if r else None


def latest(conn, set_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM level_builds WHERE game = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                     (set_id,)).fetchone()
    return public(r) if r else None


def request_build(set_id: str, count: int, requested_by: str) -> dict:
    """Queue a build. Raises BuildError (409 not set up / already building / list full, 429 daily limit)."""
    if set_id not in levels.SETS:
        raise BuildError(404, "That game has no level list.")
    problem = settings.ai_problem()
    if problem:
        raise BuildError(409, problem)
    with db.get_conn() as conn:
        if _open_build(conn, set_id):
            raise BuildError(409, "More levels are already being built for this game.")
        total = conn.execute("SELECT COUNT(*) FROM levels WHERE game = ?", (set_id,)).fetchone()[0]
        if total >= levels.MAX_LEVELS:
            raise BuildError(409, f"This game already has {levels.MAX_LEVELS} levels.")
        left = left_today(conn)
        if left == 0:
            raise BuildError(429, "Today's limit of AI levels is used up (Admin → App settings).")
        count = min(count, levels.MAX_LEVELS - total, left if left is not None else count)
        bid = db.new_id()
        conn.execute("INSERT INTO level_builds (id, game, requested_by, count, status, created_at) "
                     "VALUES (?, ?, ?, ?, 'queued', ?)", (bid, set_id, requested_by, count, config.now_iso()))
        out = get(conn, bid)
    logger.info("Level build queued for %s: %d level(s), asked by %s", set_id, count, requested_by)
    _wake()
    return out


def maybe_auto(set_id: str | None, level_reached, level_count: int | None) -> dict | None:
    """After a game (or a heartbeat): queue a build if someone is near the end of the list."""
    if not set_id or set_id not in levels.SETS or not isinstance(level_reached, int) or isinstance(level_reached, bool):
        return None
    v = settings.all()
    if not v["ai_levels_auto"] or settings.ai_problem():
        return None
    with db.get_conn() as conn:
        count = levels.playable_count(conn, set_id)
        if level_reached < count - v["ai_levels_ahead"] + 1 or _open_build(conn, set_id):
            return None
        if left_today(conn) == 0:
            return None
    try:
        return request_build(set_id, v["ai_levels_batch"], "auto")
    except BuildError as e:
        logger.info("No automatic build for %s: %s", set_id, e)
        return None


def building(set_id: str | None) -> bool:
    if not set_id:
        return False
    with db.get_conn() as conn:
        return _open_build(conn, set_id) is not None


def _note(lines: list, text: str) -> None:
    lines.append(text[:200])
    del lines[:-MAX_LOG]


def run_build(build_id: str) -> dict | None:
    """Do one queued build (the worker; tests call it directly)."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM level_builds WHERE id = ?", (build_id,)).fetchone()
        if not row or row["status"] != "queued":
            return None
        cfg = ai_client.current()
        conn.execute("UPDATE level_builds SET status = 'running', started_at = ?, model = ? WHERE id = ?",
                     (config.now_iso(), cfg.model, build_id))
    set_id, want = row["game"], row["count"]
    made = rejected = requests = tok_in = tok_out = failed_in_a_row = 0
    log: list[str] = []
    error = None
    status = "waiting" if settings.get("ai_levels_review") else "ready"

    def save_progress(final: str | None = None):
        with db.get_conn() as conn:
            conn.execute("UPDATE level_builds SET made = ?, rejected = ?, requests = ?, tokens_in = ?, tokens_out = ?, "
                         "log = ?, error = ?, status = COALESCE(?, status), ended_at = CASE WHEN ? IS NULL THEN "
                         "ended_at ELSE ? END WHERE id = ?",
                         (made, rejected, requests, tok_in, tok_out, json.dumps(log), error, final, final,
                          config.now_iso(), build_id))

    try:
        while made < want:
            if settings.ai_problem():
                error = settings.ai_problem()
                break
            with db.get_conn() as conn:
                left = left_today(conn)
                first = (conn.execute("SELECT MAX(number) FROM levels WHERE game = ?", (set_id,)).fetchone()[0] or 0) + 1
                recent = levels.summaries(conn, set_id)
            if left == 0:
                error = "Today's limit of AI levels was reached."
                break
            ask = min(PER_REQUEST, want - made, left if left is not None else PER_REQUEST)
            requests += 1
            t0 = time.monotonic()
            note = dict(purpose="build", provider=cfg.provider, model=cfg.model, game=set_id, build_id=build_id)
            try:
                reply = _generate(prompt(set_id, ask, first, recent), cfg)
            except ai_client.AIError as e:
                error = str(e)
                ai_usage.record(ok=False, error=error, ms=int((time.monotonic() - t0) * 1000), **note)
                break
            ms = int((time.monotonic() - t0) * 1000)
            made_before, rejected_before = made, rejected
            tok_in += reply.input_tokens or 0
            tok_out += reply.output_tokens or 0
            added = 0
            try:
                items = parse(reply.text)
            except ValueError as e:
                items = []
                rejected += 1
                _note(log, f"Answer {requests}: {e}")
            for item in items[:ask * 3]:          # extras are fine; a flood isn't
                if made >= want:
                    break
                try:
                    with db.get_conn() as conn:
                        lv = levels.add(conn, set_id, item, model=cfg.model, status=status, build_id=build_id)
                    made += 1
                    added += 1
                    _note(log, f"Level {lv['number']} “{lv['name']}” added")
                except levels.LevelError as e:
                    rejected += 1
                    name = item.get("name") if isinstance(item, dict) and isinstance(item.get("name"), str) else "?"
                    _note(log, f"“{name[:40]}” turned down: {e}")
            ai_usage.record(ok=True, tokens_in=reply.input_tokens, tokens_out=reply.output_tokens, ms=ms,
                            made=made - made_before, rejected=rejected - rejected_before, **note)
            failed_in_a_row = 0 if added else failed_in_a_row + 1
            save_progress()
            if failed_in_a_row >= MAX_FAILED_REQUESTS:
                error = (f"The model's last {MAX_FAILED_REQUESTS} answers had no usable levels "
                         "(see the notes). Try again, or another model.")
                break
    except Exception as e:  # a bug must not leave the build "running"
        logger.exception("Level build %s failed", build_id)
        error = f"The build stopped unexpectedly: {e}"
    final = "done" if made else "failed"       # some levels made: done (any error is shown with it)
    save_progress(final)
    logger.info("Level build %s for %s: %d made, %d turned down, %d request(s)%s", build_id, set_id, made, rejected,
                requests, f" — {error}" if error else "")
    with db.get_conn() as conn:
        return get(conn, build_id)


def run_pending() -> int:
    """Run every queued build, oldest first. Returns how many ran."""
    n = 0
    while True:
        with db.get_conn() as conn:
            r = conn.execute("SELECT id FROM level_builds WHERE status = 'queued' ORDER BY created_at, rowid "
                             "LIMIT 1").fetchone()
        if not r:
            return n
        run_build(r["id"])
        n += 1


# ---------------------------------------------------------------------------
# The worker thread
# ---------------------------------------------------------------------------
_event = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def _worker() -> None:
    while True:
        _event.wait()
        _event.clear()
        try:
            run_pending()
        except Exception:
            logger.exception("Level builder failed")


def _wake() -> None:
    """Start (once) and nudge the worker; with BACKGROUND_LOOPS off (the tests) builds wait for run_pending()."""
    global _thread
    if not config.BACKGROUND_LOOPS:
        return
    with _thread_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="level-builder", daemon=True)
            _thread.start()
    _event.set()
