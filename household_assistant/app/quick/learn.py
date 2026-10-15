"""Learning the household's own words (HOUSEHOLD_ASSISTANT_SPEC.md §17.8): phrases stored by their template (the
normalised question with its slots and names as placeholders), and the questions it couldn't place.

    quick_phrases (phrase, intent, args, slot_args, source admin|tapped|ai, seen, hits, created_at, last_used)
    quick_misses  (phrase, count, last_seen)

- an exact learned template wins at once (admin's first, then the person's taps, then the model's);
- an 'ai' phrase counts only once it was seen twice, unless the admin chose *Use what the AI learned straight away*;
- an admin's phrase of two words or more also matches inside a longer question ("trash day" in "when is trash day");
- children's taps aren't learned; *Not what I asked* deletes a learned phrase (never an admin's);
- housekeeping drops 'tapped' and 'ai' phrases unused for 180 days, and misses older than 90 days.
"""
from __future__ import annotations

import json
from datetime import timedelta

from .. import config
from ..common import app_bus as bus

UNUSED_DAYS = 180
MISS_DAYS = 90
MAX_MISSES = 500                # rows kept (the admin sees the top 50)
MAX_PHRASES = 2000
SOURCE_ORDER = {"admin": 0, "tapped": 1, "ai": 2}


def _iso(dt=None) -> str:
    return (dt or config.utcnow()).isoformat(timespec="seconds")


def _row(r) -> dict:
    return {"id": r["id"], "phrase": r["phrase"], "intent": r["intent"], "args": json.loads(r["args"] or "{}"),
            "slot_args": json.loads(r["slot_args"] or "[]"), "source": r["source"], "seen": r["seen"],
            "hits": r["hits"], "createdAt": r["created_at"], "lastUsed": r["last_used"]}


def lookup(conn, template: str, *, ai_now: bool = False) -> dict | None:
    """The learned phrase for this exact template, or an admin's phrase inside it (longest first)."""
    rows = conn.execute("SELECT * FROM quick_phrases WHERE phrase = ?", (template,)).fetchall()
    rows = [r for r in rows if r["source"] != "ai" or ai_now or r["seen"] >= 2]
    if rows:
        return _row(min(rows, key=lambda r: SOURCE_ORDER.get(r["source"], 9)))
    words = f" {template} "
    best = None
    for r in conn.execute("SELECT * FROM quick_phrases WHERE source = 'admin' AND instr(phrase, ' ') > 0"):
        if f" {r['phrase']} " in words and (best is None or len(r["phrase"]) > len(best["phrase"])):
            best = r
    return _row(best) if best else None


def remember(conn, template: str, intent: str, args: dict, slot_args, source: str) -> str | None:
    """Store (or count again) a phrase; returns its id. A tap or an admin's phrase replaces other learned meanings of
    the same words."""
    if not template or len(template) > 300 or source not in SOURCE_ORDER:
        return None
    args_json = json.dumps(args or {}, sort_keys=True, separators=(",", ":"))
    slots_json = json.dumps(sorted(set(slot_args or ())))
    if source in ("tapped", "admin"):
        conn.execute("DELETE FROM quick_phrases WHERE phrase = ? AND intent != ? AND source IN ('tapped', 'ai')",
                     (template, intent))
    row = conn.execute("SELECT id, source FROM quick_phrases WHERE phrase = ? AND intent = ?",
                       (template, intent)).fetchone()
    if row:
        keep = min((row["source"], source), key=lambda s: SOURCE_ORDER[s])
        conn.execute("UPDATE quick_phrases SET seen = seen + 1, args = ?, slot_args = ?, source = ?, last_used = ? "
                     "WHERE id = ?", (args_json, slots_json, keep, _iso(), row["id"]))
        return row["id"]
    if conn.execute("SELECT COUNT(*) FROM quick_phrases").fetchone()[0] >= MAX_PHRASES:
        conn.execute("DELETE FROM quick_phrases WHERE id IN (SELECT id FROM quick_phrases WHERE source != 'admin' "
                     "ORDER BY COALESCE(last_used, created_at) LIMIT 1)")
    pid = bus.new_ulid()
    conn.execute("INSERT INTO quick_phrases (id, phrase, intent, args, slot_args, source, seen, hits, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)", (pid, template, intent, args_json, slots_json, source, _iso()))
    return pid


def used(conn, pid: str | None) -> None:
    if pid:
        conn.execute("UPDATE quick_phrases SET hits = hits + 1, last_used = ? WHERE id = ?", (_iso(), pid))


def forget(conn, pid: str | None) -> bool:
    """*Not what I asked* on a learned phrase: gone (an admin's stays; it is removed on the admin page)."""
    if not pid:
        return False
    return conn.execute("DELETE FROM quick_phrases WHERE id = ? AND source != 'admin'", (pid,)).rowcount > 0


def miss(conn, norm: str) -> None:
    """A question it couldn't place: counted by its normalised words, never with who asked."""
    norm = " ".join(str(norm or "").split())[:300]
    if not norm:
        return
    conn.execute("INSERT INTO quick_misses (phrase, count, last_seen) VALUES (?, 1, ?) ON CONFLICT(phrase) DO UPDATE "
                 "SET count = count + 1, last_seen = excluded.last_seen", (norm, _iso()))


def misses(conn, limit: int = 50) -> list[dict]:
    since = _iso(config.utcnow() - timedelta(days=MISS_DAYS))
    return [{"phrase": r["phrase"], "count": r["count"], "lastSeen": r["last_seen"]} for r in conn.execute(
        "SELECT phrase, count, last_seen FROM quick_misses WHERE last_seen >= ? ORDER BY count DESC, last_seen DESC "
        "LIMIT ?", (since, limit))]


def phrases(conn) -> list[dict]:
    return [_row(r) for r in conn.execute("SELECT * FROM quick_phrases ORDER BY source = 'admin' DESC, "
                                          "COALESCE(last_used, created_at) DESC LIMIT 500")]


def delete(conn, pid: str) -> bool:
    return conn.execute("DELETE FROM quick_phrases WHERE id = ?", (pid,)).rowcount > 0


def housekeeping(conn) -> None:
    now = config.utcnow()
    conn.execute("DELETE FROM quick_phrases WHERE source IN ('tapped', 'ai') AND COALESCE(last_used, created_at) < ?",
                 (_iso(now - timedelta(days=UNUSED_DAYS)),))
    conn.execute("DELETE FROM quick_misses WHERE last_seen < ?", (_iso(now - timedelta(days=MISS_DAYS)),))
    conn.execute("DELETE FROM quick_misses WHERE phrase NOT IN (SELECT phrase FROM quick_misses ORDER BY count DESC, "
                 "last_seen DESC LIMIT ?)", (MAX_MISSES,))
