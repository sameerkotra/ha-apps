"""Password health (SPEC §12.1): weak, reused and old passwords across
the vaults a person has open — worked out on the server, never returning a
password — and the opt-in breach check against Have I Been Pwned's
Pwned Passwords range API (k-anonymity: only the first 5 hex characters of
the SHA-1 leave the app).

Breach results live in memory for the unlocked session only.
"""
import datetime
import hashlib
import os
import hmac
import secrets
import threading
import time
import urllib.error
import urllib.request

from . import items, kdbx, passwords, sessions

OLD_DAYS = 365
WEAK_BELOW = 2                      # strength score 0 ("very weak") or 1 ("weak")
RANGE_URL = "https://api.pwnedpasswords.com/range/"
REQUESTS_PER_SECOND = 5
TIMEOUT = 10
GIVE_UP_AFTER = 3                   # consecutive failed requests
_PEPPER = secrets.token_bytes(32)   # reuse is compared on keyed hashes, never on passwords

ISSUES = ("breached", "reused", "weak", "old", "no2fa")
_TWOFA_FILE = os.path.join(os.path.dirname(__file__), "data", "twofa_domains.txt")
_twofa: set | None = None


def twofa_domains() -> set:
    global _twofa
    if _twofa is None:
        try:
            with open(_TWOFA_FILE, encoding="utf-8") as f:
                _twofa = {ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")}
        except OSError:
            _twofa = set()
    return _twofa


def offers_2fa(host: str) -> bool:
    """Does this website offer authenticator-app codes (per the small built-in list)?"""
    h = (host or "").lower().rstrip(".")
    if h.startswith("www."):
        h = h[4:]
    parts = h.split(".")
    return any(".".join(parts[i:]) in twofa_domains() for i in range(len(parts) - 1))


# ---------- facts per open vault (cached until the vault changes) ----------

def _kp_time(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return kdbx.parse_time(text).timestamp()
    except Exception:
        return None


def password_set_at(d, e, current: str) -> float | None:
    """When the current password was set: the oldest version in the unbroken run of versions
    (newest backwards) that already had it."""
    when = _kp_time(e.findtext("Times/LastModificationTime"))
    for h in reversed(d.history(e)):
        if d.get_field(h, "Password") != current:
            break
        when = _kp_time(h.findtext("Times/LastModificationTime")) or when
    else:
        when = _kp_time(e.findtext("Times/CreationTime")) or when
    return when


def facts(ov: sessions.OpenVault) -> list:
    """[{id, reuse, sha1, score, label, setAt}] for every live login with a password. Call with ov.lock held."""
    if ov.health is not None:
        return ov.health
    d = ov.db
    out = []
    for e in items.all_live_entries(d):
        pw = d.get_field(e, "Password")
        if not pw or items.entry_type(d, e) != "login":
            continue
        st = passwords.strength(pw)
        out.append({"id": items.to_id(e.findtext("UUID")),
                    "reuse": hmac.new(_PEPPER, pw.encode(), hashlib.sha256).hexdigest(),
                    "sha1": hashlib.sha1(pw.encode()).hexdigest().upper(),
                    "score": st["score"], "label": st["label"],
                    "setAt": password_set_at(d, e, pw),
                    "no2fa": items.otp_params(d, e) is None and offers_2fa(items.host_of(d.get_field(e, "URL") or ""))})
    ov.health = out
    return out


# ---------- breach results per session ----------

_jobs: dict = {}                    # session token → job
_jobs_lock = threading.Lock()


def fetch_range(prefix: str) -> str:
    """One Pwned Passwords range request (padded, so the response size says nothing)."""
    req = urllib.request.Request(RANGE_URL + prefix, headers={
        "Add-Padding": "true", "User-Agent": "household-vault-home-assistant-add-on"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _counts(body: str) -> dict:
    out = {}
    for line in body.splitlines():
        suffix, _, n = line.strip().partition(":")
        if suffix and n.strip().isdigit():
            c = int(n)
            if c > 0:                   # padding lines have a count of 0
                out[suffix.upper()] = c
    return out


def check_hashes(sha1s: set, progress=None) -> dict:
    """{sha1: count (0 = not found) or None (couldn't check)} — one request per prefix, ≤ 5 a second."""
    by_prefix: dict = {}
    for h in sha1s:
        by_prefix.setdefault(h[:5], set()).add(h)
    out = {}
    last = 0.0
    failures = 0
    for i, (prefix, hs) in enumerate(sorted(by_prefix.items())):
        if failures >= GIVE_UP_AFTER:              # offline: don't wait for every request to time out
            for h in hs:
                out[h] = None
            if progress:
                progress(i + 1, len(by_prefix))
            continue
        wait = 1 / REQUESTS_PER_SECOND - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        try:
            counts = _counts(fetch_range(prefix))
            for h in hs:
                out[h] = counts.get(h[5:], 0)
            failures = 0
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            failures += 1
            for h in hs:
                out[h] = None
        if progress:
            progress(i + 1, len(by_prefix))
    return out


def job(token: str) -> dict | None:
    _cleanup()
    with _jobs_lock:
        return _jobs.get(token)


def _cleanup() -> None:
    with _jobs_lock:
        for t in [t for t in _jobs if t not in sessions._sessions]:
            _jobs.pop(t, None)


def start(token: str, sha1s: set) -> dict:
    """Start (or keep) a breach check for this session in the background."""
    _cleanup()
    with _jobs_lock:
        j = _jobs.get(token)
        if j and j["state"] == "running":
            return j
        known = dict(j["results"]) if j else {}
        todo = {h for h in sha1s if known.get(h) is None}
        j = {"state": "running", "done": 0, "total": len({h[:5] for h in todo}), "results": known,
             "started": time.time(), "finished": None}
        _jobs[token] = j

    def run():
        def prog(done, total):
            j["done"] = done
        try:
            res = check_hashes(todo, prog)
            j["results"].update(res)
        finally:
            j["state"] = "done"
            j["finished"] = time.time()
    threading.Thread(target=run, daemon=True, name="breach-check").start()
    return j


def status(token: str) -> dict:
    j = job(token)
    if not j:
        return {"state": "idle", "done": 0, "total": 0, "errors": 0, "finished": None}
    errors = sum(1 for v in j["results"].values() if v is None)
    return {"state": j["state"], "done": j["done"], "total": j["total"], "errors": errors,
            "finished": datetime.datetime.fromtimestamp(j["finished"], datetime.timezone.utc).isoformat()
            if j["finished"] else None}


def results(token: str) -> dict:
    j = job(token)
    return dict(j["results"]) if j else {}


# ---------- the report ----------

def report(open_vaults: list, token: str | None, now: float | None = None) -> dict:
    """open_vaults: [(vault dict {id, name}, OpenVault)]. Returns counts and the items with issues."""
    now = time.time() if now is None else now
    breach = results(token) if token else {}
    rows = []
    for vault, ov in open_vaults:
        with ov.lock:
            fs = facts(ov)
            by_id = {f["id"]: f for f in fs}
            for e in items.all_live_entries(ov.db):
                i = items.to_id(e.findtext("UUID"))
                f = by_id.get(i)
                if f is None:
                    continue
                s = items.summary(ov.db, e, vault)
                rows.append((s, f))
    reuse_count: dict = {}
    for _, f in rows:
        reuse_count[f["reuse"]] = reuse_count.get(f["reuse"], 0) + 1
    groups: dict = {}
    out = []
    counts = {k: 0 for k in ISSUES}
    for s, f in rows:
        issues = []
        b = breach.get(f["sha1"])
        if b:
            issues.append("breached")
        if reuse_count[f["reuse"]] > 1:
            issues.append("reused")
            groups.setdefault(f["reuse"], []).append(s)
        if f["score"] < WEAK_BELOW:
            issues.append("weak")
        age = int((now - f["setAt"]) // 86400) if f["setAt"] else None
        if age is not None and age > OLD_DAYS:
            issues.append("old")
        if f.get("no2fa"):
            issues.append("no2fa")
        for k in issues:
            counts[k] += 1
        if issues:
            out.append(dict(s, issues=issues, strength=f["label"], ageDays=age,
                            breachCount=b if b else (None if f["sha1"] not in breach else breach[f["sha1"]]),
                            reuseGroup=None))
    # number reuse groups (biggest first) so the page can show "same password as …"
    ordered = sorted(groups.values(), key=lambda g: (-len(g), items_sort(g[0])))
    group_of = {}
    for n, g in enumerate(ordered, 1):
        for s in g:
            group_of[(s["vaultId"], s["id"])] = n
    for o in out:
        o["reuseGroup"] = group_of.get((o["vaultId"], o["id"]))
    rank = {k: n for n, k in enumerate(ISSUES)}
    out.sort(key=lambda o: (min(rank[k] for k in o["issues"]), items_sort(o)))
    checked = sum(1 for _, f in rows if f["sha1"] in breach and breach[f["sha1"]] is not None)
    return {"total": len(rows), "counts": counts, "items": out,
            "reuseGroups": [[{"vaultId": s["vaultId"], "id": s["id"], "title": s["title"], "vaultName": s["vaultName"]}
                             for s in g] for g in ordered],
            "breachChecked": checked}


def items_sort(s: dict) -> str:
    return (s.get("title") or "").lower()


def issue_keys(open_vaults: list, token: str | None) -> dict:
    """{issue: {(vaultId, id)}} — for search filters like is:weak."""
    rep = report(open_vaults, token)
    out = {k: set() for k in ISSUES}
    for o in rep["items"]:
        for k in o["issues"]:
            out[k].add((o["vaultId"], o["id"]))
    return out
