"""“Who is this?” photo quiz and kids mode (§13.13).

Modes: who (a photo, pick the name), call (a photo, pick what you call them —
in the relationship language), find (a name, pick the photo). Play as anyone;
relationship names are worked out from the player's point of view. People who
are often missed come up more often. Answers aren't tree changes: nothing goes
to History.
"""
import json
import random
import secrets
import threading
import time
from collections import deque
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from .. import features, config, db, graph as graph_mod, kidmode, kin, relations, upcoming
from ..auth import require_admin, require_user
from ..common import Strict

router = APIRouter(prefix="/api", tags=["quiz"], dependencies=[Depends(features.required("quiz"))])

QUESTION_TTL = 3600
_lock = threading.Lock()
_questions: dict[str, dict] = {}
_recent: dict[str, deque] = {}


# ---------- who can be asked about ----------
def _photos(conn, g, ids: set) -> dict:
    """pid → [(media_id, region_id or None)]: the profile photo first, then tagged boxes."""
    out = {}
    for pid in ids:
        p = g.people[pid]
        if p.photo:
            out.setdefault(pid, []).append((p.photo, p.photo_region))
    if ids and features.on("photo_tagging"):         # tagged faces only while face tagging is on
        for r in conn.execute("SELECT r.person_id, r.media_id, r.id FROM media_regions r JOIN media m ON m.id = r.media_id "
                              "WHERE m.deleted_at IS NULL AND m.kind = 'photo' AND r.person_id IS NOT NULL"):
            if r["person_id"] in ids and (r["media_id"], r["id"]) not in out.get(r["person_id"], []):
                out.setdefault(r["person_id"], []).append((r["media_id"], r["id"]))
    return out


def _pool(conn, g, player: str, scope: str, deceased: bool) -> tuple[dict, dict]:
    ids = set(g.people)
    if scope == "close":
        ids &= upcoming.close_family(g, player, 3)
    ids.discard(player)
    if not deceased:
        ids = {i for i in ids if graph_mod.living(g.people[i])}
    photos = _photos(conn, g, ids)
    rels = {}
    for pid in photos:
        r = relations.relationship(g, player, pid)
        rels[pid] = r
    return photos, rels


def _weight(stats: dict, pid: str, recent) -> float:
    s = stats.get(pid)
    w = 2.0 if not s else (1 + 2 * s["wrong"]) / (1 + s["correct"])
    if pid in recent:
        w *= 0.05
    return max(w, 0.02)


def _pick_distractors(g, target: str, candidates: list, rels: dict, n: int, hard: bool) -> list:
    tp = g.people[target]
    tgen = rels[target].get("gen")

    def closeness(pid):
        p = g.people[pid]
        score = 0.0
        if tp.gender in ("male", "female"):
            score += 0 if p.gender == tp.gender else 3
        gen = rels[pid].get("gen")
        score += abs((gen if gen is not None else 0) - (tgen if tgen is not None else 0)) * (1 if hard else 0.2)
        return score + random.random() * (0.5 if hard else 3)
    others = sorted((c for c in candidates if c != target), key=closeness)
    return others[:n]


def _photo_out(pair) -> dict:
    return {"media": pair[0], "region": pair[1]}


def _term(g, player, pid, rel, lang) -> str | None:
    if rel["kind"] in ("self", "none"):
        return None
    return kin.label_for(g, player, pid, lang, rel)


def _explain(g, player: str, pid: str, me: str | None, lang: str) -> str:
    p = g.people[pid]
    rel = relations.relationship(g, player, pid)
    d = kin.describe(g, player, rel, lang)
    if rel["kind"] in ("self", "none"):
        return p.name
    who = "your" if player == me else f"{g.people[player].given or g.people[player].name}'s"
    text = f"{p.name} — {who} {d.get('label') or rel.get('label')}"
    meaning = d.get("meaning") or (rel.get("label") if d.get("term") else None)
    if meaning and meaning.lower() != (d.get("label") or "").lower():
        text += f" ({meaning})"
    return text


class NextIn(Strict):
    playerId: str | None = None
    mode: str = Field(default="who", pattern="^(who|call|find)$")
    scope: str = Field(default="close", pattern="^(close|all)$")
    deceased: bool = False
    difficulty: str = Field(default="normal", pattern="^(easy|normal)$")


def _settings_for(conn, request: Request, user: dict, body: NextIn) -> dict:
    s = kidmode.session(conn, kidmode.device_id(request.headers))
    if s:
        if body.mode not in s["modes"]:
            raise HTTPException(422, "That game isn't switched on for kids mode.")
        return {"player": s["player_person_id"], "scope": s["scope"], "deceased": bool(s["deceased"]),
                "mode": body.mode, "difficulty": body.difficulty}
    player = body.playerId or user["me_person_id"]
    if not player:
        raise HTTPException(422, "Choose who is playing (or set “This is me” in Settings).")
    return {"player": player, "scope": body.scope, "deceased": body.deceased, "mode": body.mode,
            "difficulty": body.difficulty}


@router.post("/quiz/next")
def quiz_next(body: NextIn, request: Request, user: dict = Depends(require_user)):
    lang = kin.current()
    with db.get_conn() as conn:
        opts = _settings_for(conn, request, user, body)
        g = graph_mod.get(conn)
        player = opts["player"]
        if player not in g.people:
            raise HTTPException(404, "The player isn't in the tree.")
        photos, rels = _pool(conn, g, player, opts["scope"], opts["deceased"])
        mode = opts["mode"]
        n_choices = 3 if opts["difficulty"] == "easy" else 4
        pool = list(photos)
        if mode == "call":
            pool = [pid for pid in pool if _term(g, player, pid, rels[pid], lang)]
        if len(pool) < 2:
            raise HTTPException(409, "Not enough people with photos for this game yet. Add profile photos or tag "
                                     "faces in photos, or include everyone / the deceased.")
        stats = {r["person_id"]: dict(r) for r in conn.execute(
            "SELECT person_id, correct, wrong FROM quiz_stats WHERE player_person_id = ? AND mode = ?", (player, mode))}
    recent_key = f"{player}:{mode}"
    with _lock:
        recent = _recent.setdefault(recent_key, deque(maxlen=min(5, len(pool) - 1)))
        if recent.maxlen != min(5, len(pool) - 1):
            recent = _recent[recent_key] = deque(recent, maxlen=min(5, len(pool) - 1))
        target = random.choices(pool, weights=[_weight(stats, pid, recent) for pid in pool])[0]
        recent.append(target)
    hard = opts["difficulty"] != "easy"
    q = {"mode": mode, "playerId": player, "playerName": g.people[player].name}
    if mode == "who":
        others = _pick_distractors(g, target, pool, rels, n_choices - 1, hard)
        choices = [target] + others
        random.shuffle(choices)
        q.update(photo=_photo_out(random.choice(photos[target])),
                 choices=[{"id": c, "label": g.people[c].name, "nameLocal": g.people[c].local_name if features.on("script_names") else None} for c in choices])
        answer = target
    elif mode == "call":
        want = _term(g, player, target, rels[target], lang)
        seen, terms = {want.lower()}, []
        for pid in _pick_distractors(g, target, list(rels), rels, len(rels), hard):
            t = _term(g, player, pid, rels[pid], lang)
            if t and t.lower() not in seen:
                seen.add(t.lower())
                terms.append(t)
            if len(terms) >= n_choices - 1:
                break
        if not terms:
            raise HTTPException(409, "Everyone in this game is called the same thing — include more people.")
        choices = [want] + terms
        random.shuffle(choices)
        q.update(photo=_photo_out(random.choice(photos[target])), choices=[{"id": c, "label": c} for c in choices])
        answer = want
    else:
        others = _pick_distractors(g, target, pool, rels, n_choices - 1, hard)
        choices = [target] + others
        random.shuffle(choices)
        q.update(name=g.people[target].name, nameLocal=g.people[target].local_name if features.on("script_names") else None,
                 choices=[{"id": c, "photo": _photo_out(photos[c][0])} for c in choices])
        answer = target
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        for k in [k for k, v in _questions.items() if now - v["at"] > QUESTION_TTL]:
            _questions.pop(k, None)
        _questions[token] = {"at": now, "player": player, "target": target, "mode": mode, "answer": answer,
                             "user": user["id"]}
    q["token"] = token
    return q


class AnswerIn(Strict):
    token: str = Field(min_length=8, max_length=64)
    choice: str = Field(min_length=1, max_length=200)


@router.post("/quiz/answer")
def quiz_answer(body: AnswerIn, user: dict = Depends(require_user)):
    with _lock:
        q = _questions.pop(body.token, None)
    if not q or q["user"] != user["id"]:
        raise HTTPException(404, "That question has expired — here's a new one.")
    right = body.choice == q["answer"]
    with db.get_conn() as conn:
        conn.execute("INSERT INTO quiz_stats (player_person_id, person_id, mode, correct, wrong, last_seen) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(player_person_id, person_id, mode) DO UPDATE SET correct = correct + excluded.correct, "
                     "wrong = wrong + excluded.wrong, last_seen = excluded.last_seen",
                     (q["player"], q["target"], q["mode"], 1 if right else 0, 0 if right else 1, config.now_iso()))
        g = graph_mod.get(conn)
        totals = conn.execute("SELECT COALESCE(SUM(correct), 0) c, COALESCE(SUM(wrong), 0) w FROM quiz_stats "
                              "WHERE player_person_id = ?", (q["player"],)).fetchone()
    t = g.people.get(q["target"])
    return {"correct": right, "answer": q["answer"], "personId": q["target"],
            "explanation": _explain(g, q["player"], q["target"], user["me_person_id"], kin.current()) if t else None,
            "photo": {"media": t.photo, "region": t.photo_region} if t and t.photo else None,
            "score": {"correct": totals["c"], "wrong": totals["w"]}}


@router.get("/quiz/stats")
def quiz_stats(playerId: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM quiz_stats WHERE player_person_id = ?", (playerId,))]
    agg = {}
    for r in rows:
        a = agg.setdefault(r["person_id"], {"correct": 0, "wrong": 0})
        a["correct"] += r["correct"]
        a["wrong"] += r["wrong"]
    missed = sorted(((pid, a) for pid, a in agg.items() if pid in g.people and a["wrong"]),
                    key=lambda x: -(x[1]["wrong"] - x[1]["correct"]))[:8]
    return {"correct": sum(a["correct"] for a in agg.values()), "wrong": sum(a["wrong"] for a in agg.values()),
            "oftenMissed": [{"id": pid, "name": g.people[pid].name, **a} for pid, a in missed]}


# ---------- kids mode ----------
class StartIn(Strict):
    playerId: str
    modes: list[str] = Field(min_length=1, max_length=3)
    scope: str = Field(default="close", pattern="^(close|all)$")
    deceased: bool = False
    minutes: int = 0


def _state(conn, s: dict | None, user: dict | None = None) -> dict:
    if not s:
        return {"locked": False}
    g = graph_mod.get(conn)
    p = g.people.get(s["player_person_id"])
    owner = conn.execute("SELECT kid_pin_hash FROM users WHERE id = ?", (s["user_id"],)).fetchone()
    return {"locked": True, "playerId": s["player_person_id"], "playerName": p.name if p else None,
            "playerGiven": (p.given or p.name) if p else None,
            "modes": s["modes"], "scope": s["scope"], "deceased": bool(s["deceased"]), "startedAt": s["started_at"],
            "endsAt": s["ends_at"], "timeUp": kidmode.time_up(s), "blockedUntil": kidmode.blocked_until(s),
            "pinSet": bool(owner and owner["kid_pin_hash"])}


@router.get("/kidmode")
def kidmode_state(request: Request, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return _state(conn, kidmode.session(conn, kidmode.device_id(request.headers)))


@router.post("/kidmode/start")
def kidmode_start(body: StartIn, request: Request, user: dict = Depends(require_user)):
    device = kidmode.device_id(request.headers)
    if not device:
        raise HTTPException(422, "This browser didn't send a device id; reload the page and try again.")
    if any(m not in kidmode.MODES for m in body.modes):
        raise HTTPException(422, "Unknown game.")
    if body.minutes not in kidmode.TIME_LIMITS:
        raise HTTPException(422, "Choose no time limit, or 10, 15, 30 or 60 minutes.")
    with db.get_conn() as conn:
        if not conn.execute("SELECT 1 FROM people WHERE id = ? AND deleted_at IS NULL", (body.playerId,)).fetchone():
            raise HTTPException(404, "The player isn't in the tree.")
        now = config.utcnow()
        ends = (now + timedelta(minutes=body.minutes)).isoformat() if body.minutes else None
        conn.execute("INSERT OR REPLACE INTO kid_sessions (device_id, user_id, player_person_id, modes, scope, deceased, "
                     "started_at, ends_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (device, user["id"], body.playerId, json.dumps(sorted(set(body.modes), key=kidmode.MODES.index)),
                      body.scope, 1 if body.deceased else 0, now.isoformat(), ends))
        return _state(conn, kidmode.session(conn, device))


@router.post("/kidmode/challenge")
def kidmode_challenge(request: Request, user: dict = Depends(require_user)):
    """What the grown-up must answer: the PIN of whoever started kids mode, or a sum."""
    with db.get_conn() as conn:
        s = kidmode.session(conn, kidmode.device_id(request.headers))
        if not s:
            return {"locked": False}
        until = kidmode.blocked_until(s)
        if until:
            return {"locked": True, "blockedUntil": until}
        owner = conn.execute("SELECT kid_pin_hash FROM users WHERE id = ?", (s["user_id"],)).fetchone()
        if owner and owner["kid_pin_hash"]:
            conn.execute("UPDATE kid_sessions SET challenge = NULL WHERE device_id = ?", (s["device_id"],))
            return {"locked": True, "kind": "pin"}
        question, answer = kidmode.new_question()
        conn.execute("UPDATE kid_sessions SET challenge = ? WHERE device_id = ?", (answer, s["device_id"]))
        return {"locked": True, "kind": "question", "question": f"What is {question}?"}


class UnlockIn(Strict):
    answer: str = Field(min_length=1, max_length=12)


@router.post("/kidmode/unlock")
def kidmode_unlock(body: UnlockIn, request: Request, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        s = kidmode.session(conn, kidmode.device_id(request.headers))
        if not s:
            return {"locked": False}
        until = kidmode.blocked_until(s)
        if until:
            raise HTTPException(429, "Too many wrong tries. Try again in a few minutes.")
        owner = conn.execute("SELECT kid_pin_hash FROM users WHERE id = ?", (s["user_id"],)).fetchone()
        pin_hash = owner["kid_pin_hash"] if owner else None
        ans = body.answer.strip()
        if pin_hash:
            ok = kidmode.check_pin(ans, pin_hash)
        else:
            ok = bool(s["challenge"]) and secrets.compare_digest(ans, s["challenge"])
        if not ok:
            info = kidmode.block_after_fail(conn, s)
            conn.commit()
            detail = ("Too many wrong tries. Try again in 5 minutes." if info["blockedUntil"]
                      else f"That's not right. {info['triesLeft']} tries left.")
            raise HTTPException(403, detail)
        conn.execute("DELETE FROM kid_sessions WHERE device_id = ?", (s["device_id"],))
        return {"locked": False}


class PinIn(Strict):
    pin: str | None = None


@router.put("/me/kid-pin")
def set_kid_pin(body: PinIn, user: dict = Depends(require_user)):
    if body.pin is not None and not kidmode.valid_pin(body.pin):
        raise HTTPException(422, "The PIN must be 4 to 6 digits.")
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET kid_pin_hash = ? WHERE id = ?",
                     (kidmode.hash_pin(body.pin) if body.pin else None, user["id"]))
    return {"pinSet": bool(body.pin)}


@router.get("/admin/kidmode")
def admin_kid_sessions(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        rows = conn.execute("SELECT k.*, u.name AS user_name FROM kid_sessions k LEFT JOIN users u ON u.id = k.user_id "
                            "ORDER BY started_at DESC").fetchall()
        out = []
        for r in rows:
            p = g.people.get(r["player_person_id"])
            out.append({"deviceId": r["device_id"], "userId": r["user_id"], "userName": r["user_name"],
                        "playerName": p.name if p else None, "startedAt": r["started_at"], "endsAt": r["ends_at"]})
    return {"items": out}


@router.delete("/kidmode/{device}")
def admin_end_kidmode(device: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        if not conn.execute("DELETE FROM kid_sessions WHERE device_id = ?", (device,)).rowcount:
            raise HTTPException(404, "That device isn't in kids mode.")
    return {"ok": True}
