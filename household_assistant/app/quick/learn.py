"""Learning the household's own words (HOUSEHOLD_ASSISTANT_SPEC.md §17.8): phrases stored by their template (the
normalised question with its slots and names as placeholders), and the questions it couldn't place.

    quick_phrases (phrase, intent, args, slot_args, source admin|tapped|rephrased|ai, seen, hits, created_at,
                   last_used)
    quick_misses  (phrase, count, last_seen)
    quick_words   (word, means, created_at): the admin's words — a concept ("chores" -> TASK) or another name
                  ("dahi" -> item:Curd)

- an exact learned template wins at once (admin's first, then the person's taps, then rephrasings, then the model's);
- an 'ai' phrase counts only once it was seen twice, unless the admin chose *Use what the AI learned straight away*;
- a 'rephrased' one (a question it couldn't place, asked again in other words that it could) also counts only once
  it was seen twice;
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
SOURCE_ORDER = {"admin": 0, "tapped": 1, "rephrased": 2, "ai": 3}
NAME_KINDS = {"item": "item", "store": "store", "list": "list_name", "person": "person", "group": "group",
              "category": "category", "account": "account", "game": "game"}
MAX_WORDS = 300


def _iso(dt=None) -> str:
    return (dt or config.utcnow()).isoformat(timespec="seconds")


def _row(r) -> dict:
    return {"id": r["id"], "phrase": r["phrase"], "intent": r["intent"], "args": json.loads(r["args"] or "{}"),
            "slot_args": json.loads(r["slot_args"] or "[]"), "source": r["source"], "seen": r["seen"],
            "hits": r["hits"], "createdAt": r["created_at"], "lastUsed": r["last_used"]}


def lookup(conn, template: str, *, ai_now: bool = False) -> dict | None:
    """The learned phrase for this exact template, or an admin's phrase inside it (longest first)."""
    rows = conn.execute("SELECT * FROM quick_phrases WHERE phrase = ?", (template,)).fetchall()
    rows = [r for r in rows if (r["source"] != "ai" or ai_now or r["seen"] >= 2)
            and (r["source"] != "rephrased" or r["seen"] >= 2)]
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
        conn.execute("DELETE FROM quick_phrases WHERE phrase = ? AND intent != ? AND source IN ('tapped', "
                     "'rephrased', 'ai')",
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
    conn.execute("DELETE FROM quick_phrases WHERE source IN ('tapped', 'rephrased', 'ai') AND "
                 "COALESCE(last_used, created_at) < ?",
                 (_iso(now - timedelta(days=UNUSED_DAYS)),))
    conn.execute("DELETE FROM quick_misses WHERE last_seen < ?", (_iso(now - timedelta(days=MISS_DAYS)),))
    conn.execute("DELETE FROM quick_misses WHERE phrase NOT IN (SELECT phrase FROM quick_misses ORDER BY count DESC, "
                 "last_seen DESC LIMIT ?)", (MAX_MISSES,))


# ----------------------------------------------------------------------------------------- the admin's words

def _clean_word(word: str) -> str:
    """The word as the admin typed it, in lower case (matched in its normalised form, like the questions)."""
    import re
    return " ".join(re.sub(r"[^\w' -]", " ", str(word or "").casefold()).split())[:40]


def check_means(means: str) -> str:
    """A concept name ("TASK") or "<kind>:<name>" ("item:Curd"); ValueError otherwise."""
    from . import lexicon
    means = " ".join(str(means or "").split())
    if means in lexicon.CONCEPTS and means not in lexicon.HELPERS and means != "QUESTION_WORDS":
        return means
    kind, _, name = means.partition(":")
    if kind in NAME_KINDS and name.strip() and len(name) <= 80:
        return f"{kind}:{name.strip()}"
    raise ValueError("Choose what the word means: a kind of question, or another name for something.")


def words(conn) -> list[dict]:
    return [{"word": r["word"], "means": r["means"], "createdAt": r["created_at"]}
            for r in conn.execute("SELECT * FROM quick_words ORDER BY word")]


def add_word(conn, word: str, means: str) -> dict:
    w = _clean_word(word)
    if not w or len(w.split()) > 3:
        raise ValueError("A word or a short phrase of up to 3 words.")
    means = check_means(means)
    if conn.execute("SELECT COUNT(*) FROM quick_words").fetchone()[0] >= MAX_WORDS and not conn.execute(
            "SELECT 1 FROM quick_words WHERE word = ?", (w,)).fetchone():
        raise ValueError(f"At most {MAX_WORDS} words.")
    conn.execute("INSERT INTO quick_words (word, means, created_at) VALUES (?, ?, ?) ON CONFLICT(word) DO UPDATE SET "
                 "means = excluded.means", (w, means, _iso()))
    return {"word": w, "means": means}


def delete_word(conn, word: str) -> bool:
    return conn.execute("DELETE FROM quick_words WHERE word = ?", (_clean_word(word) or word,)).rowcount > 0


def taught(conn) -> tuple[dict, list]:
    """The admin's words for the matcher: ({word: concept}, [(kind, word, name)])."""
    concepts, aliases = {}, []
    for r in conn.execute("SELECT word, means FROM quick_words"):
        kind, _, name = r["means"].partition(":")
        if name and kind in NAME_KINDS:
            aliases.append((NAME_KINDS[kind], r["word"], name))
        elif not name:
            concepts[r["word"]] = r["means"]
    return concepts, aliases


def _canonical(concept: str) -> str:
    from . import lexicon
    phrases = lexicon.CONCEPTS[concept]
    return next((p for p in phrases if " " not in p), phrases[0])


def word_suggestions(conn, ctx, limit: int = 10) -> list[dict]:
    """Words in the misses it doesn't know, each with the kind of question that would place most of the misses it is
    in ("chores" in 4 misses -> TASK places 3): offered on the admin page as *Treat "chores" as tasks?*"""
    from . import lexicon
    from .match import _KNOWN, _TARGETS, _name_tokens
    from .understand import understand
    rows = misses(conn, limit=MAX_MISSES)
    have = {r["word"] for r in conn.execute("SELECT word FROM quick_words")}
    names = set()
    for _k, values in ctx.vocab.kinds() if ctx.vocab else ():
        for v in values:
            names.update(_name_tokens(v))
    seen: dict[str, list] = {}
    for m in rows:
        for w in set(m["phrase"].split()):
            if (len(w) > 2 and w.isalpha() and w not in _KNOWN and w not in _TARGETS and w not in lexicon.STOPWORDS
                    and w not in names and w not in have):
                seen.setdefault(w, []).append(m)
    out = []
    candidates = [c for c in lexicon.CONCEPTS if c not in lexicon.HELPERS and c not in ("QUESTION_WORDS", "KIN")]
    for w, ms in sorted(seen.items(), key=lambda kv: -sum(m["count"] for m in kv[1]))[:40]:
        total = sum(m["count"] for m in ms)
        if total < 2:
            continue
        best, placed = None, 0
        for c in candidates:
            n = 0
            for m in ms:
                q = " ".join(_canonical(c) if x == w else x for x in m["phrase"].split())
                if understand(q, ctx).kind == "answer":
                    n += m["count"]
            if n > placed:
                best, placed = c, n
        if best:
            out.append({"word": w, "means": best, "misses": total, "placed": placed, "example": ms[0]["phrase"]})
        if len(out) >= limit:
            break
    return out


# ----------------------------------------------------------------------------------------- export and import

def export(conn) -> dict:
    """The household's own words, to bring to another install: the admin's phrases and words, the taps and the
    confirmed rephrasings and AI phrases (never who asked: phrases are templates)."""
    rows = [r for r in phrases(conn) if r["source"] in ("admin", "tapped") or r["seen"] >= 2]
    return {"app": "household_assistant", "kind": "quick-answers", "version": 1, "exportedAt": _iso(),
            "phrases": [{"phrase": r["phrase"], "intent": r["intent"], "args": r["args"], "slot_args": r["slot_args"],
                         "source": r["source"]} for r in rows],
            "words": [{"word": w["word"], "means": w["means"]} for w in words(conn)]}


def import_(conn, data, check_phrase) -> dict:
    """Merge an export in; `check_phrase(intent, args)` raises ValueError for a phrase this install can't keep.
    Returns {phrases, words, skipped}."""
    if not isinstance(data, dict) or data.get("kind") != "quick-answers" or data.get("version") != 1:
        raise ValueError("That isn't a Quick answers export.")
    got = {"phrases": 0, "words": 0, "skipped": 0}
    for p in (data.get("phrases") or [])[:MAX_PHRASES]:
        try:
            if not isinstance(p, dict) or not isinstance(p.get("phrase"), str) or not isinstance(p.get("args", {}), dict):
                raise ValueError
            check_phrase(p.get("intent"), p.get("args") or {})
            source = "admin" if p.get("source") == "admin" else "tapped"
            slots = [k for k in (p.get("slot_args") or []) if isinstance(k, str)][:6]
            if remember(conn, " ".join(p["phrase"].split())[:300], p["intent"], p.get("args") or {}, slots, source):
                got["phrases"] += 1
            else:
                got["skipped"] += 1
        except (ValueError, TypeError):
            got["skipped"] += 1
    for w in (data.get("words") or [])[:MAX_WORDS]:
        try:
            add_word(conn, str(w.get("word") or ""), str(w.get("means") or ""))
            got["words"] += 1
        except (ValueError, AttributeError):
            got["skipped"] += 1
    return got
