"""Search across open vaults (SPEC §9.4).

Each open vault keeps an index (rebuilt after any change) of what may be
searched — never passwords, TOTP secrets, card numbers (except the last 4),
CVVs, PINs or other protected fields. Notes are searched only when the person
turned that on.

Query language: words (all must match, at the start of a word, case- and
accent-insensitive), "quoted phrases", and filters tag:, folder:, vault:,
type:, has:2fa, is:favourite, and the password-health filters is:weak,
is:reused, is:old, is:breached, is:no2fa (worked out by the caller, see health.py).
"""
import re
import shlex
import unicodedata

from . import items

MAX_RESULTS = 200
FILTERS = ("tag", "folder", "vault", "type", "has", "is")
HEALTH_FILTERS = ("weak", "reused", "old", "breached", "no2fa")


def health_filters(q: str) -> list:
    """The is:weak / is:reused / is:old / is:breached / is:no2fa filters in a query."""
    _, _, f = parse(q)
    return [h for h in HEALTH_FILTERS if any(i.startswith(h[:4]) for i in f.get("is", []))]


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _words(text: str) -> list:
    return [w for w in re.split(r"[^\w]+", fold(text)) if w]


def build(d, vault: dict) -> list:
    out = []
    for e in items.all_live_entries(d):
        s = items.summary(d, e, vault)
        f = d.get_fields(e)
        prot = d.protected_keys(e) | items.ALWAYS_PROTECTED
        custom_names = [k for k in f if k not in set(items.kdbx.STANDARD_FIELDS) | set(items.CARD_FIELDS)
                        | items.OTP_FIELDS]
        custom_values = [v for k, v in f.items() if k in custom_names and k not in prot]
        out.append({
            "summary": s,
            "title": fold(s["title"]), "title_words": _words(s["title"]),
            "host": fold(s["host"]), "url": fold(s["url"]), "url_words": _words(s["url"]),
            "username": fold(f.get("UserName", "") or f.get("Cardholder", "")),
            "user_words": _words(f.get("UserName", "") + " " + f.get("Cardholder", "")),
            "tags": [fold(t) for t in s["tags"]],
            "folder": [fold(x) for x in s["folderPath"]],
            "folder_words": _words(" ".join(s["folderPath"])),
            "extra_words": _words(" ".join(custom_names + custom_values + ([s["cardLast4"]] if s["cardLast4"] else []))),
            "notes_words": _words(f.get("Notes", "")),
            "notes": fold(f.get("Notes", "")),
        })
    return out


def parse(q: str) -> tuple:
    try:
        parts = shlex.split(q or "")
    except ValueError:
        parts = (q or "").split()
    terms, phrases, filters = [], [], {}
    raw_quoted = set(re.findall(r'"([^"]+)"', q or ""))
    for p in parts:
        key, sep, val = p.partition(":")
        if sep and key.lower() in FILTERS and val:
            filters.setdefault(key.lower(), []).append(fold(val))
        elif p in raw_quoted or " " in p:
            phrases.append(fold(p))
        else:
            terms.extend(_words(p))
    return terms, phrases, filters


def _prefix(words: list, term: str) -> bool:
    return any(w.startswith(term) for w in words)


def score(rec: dict, terms: list, phrases: list, notes: bool) -> int | None:
    total = 0
    for t in terms:
        best = 0
        if rec["title"].startswith(t):
            best = 100
        elif _prefix(rec["title_words"], t):
            best = 80
        elif t in rec["host"] or _prefix(rec["url_words"], t):
            best = 60
        elif _prefix(rec["user_words"], t):
            best = 40
        elif any(x.startswith(t) for x in rec["tags"]) or _prefix(rec["folder_words"], t):
            best = 30
        elif _prefix(rec["extra_words"], t):
            best = 20
        elif notes and _prefix(rec["notes_words"], t):
            best = 10
        if not best:
            return None
        total += best
    for p in phrases:
        hay = " ".join([rec["title"], rec["url"], rec["username"], " ".join(rec["folder"]), " ".join(rec["tags"])]
                       + ([rec["notes"]] if notes else []))
        if p not in hay:
            return None
        total += 50
    return total


def _filters_ok(rec: dict, filters: dict, health: dict) -> bool:
    s = rec["summary"]
    for t in filters.get("tag", []):
        if not any(x.startswith(t) for x in rec["tags"]):
            return False
    for f in filters.get("folder", []):
        if not any(x.startswith(f) for x in rec["folder"]):
            return False
    for v in filters.get("vault", []):
        if not fold(s["vaultName"]).startswith(v):
            return False
    for t in filters.get("type", []):
        if not s["type"].startswith(t.rstrip("s")):
            return False
    for h in filters.get("has", []):
        if h in ("2fa", "totp", "otp") and not s["hasTotp"]:
            return False
    for i in filters.get("is", []):
        if i.startswith("fav") and not s["favourite"]:
            return False
        for hname in HEALTH_FILTERS:
            if i.startswith(hname[:4]) and (s["vaultId"], s["id"]) not in health.get(hname, ()):
                return False
    return True


def query(indexes: list, q: str, notes: bool, recent: list | None = None, folder_scope: set | None = None,
          type_: str | None = None, favourites: bool = False, health: dict | None = None) -> list:
    """indexes: [(vault dict, [records])]. Returns summaries, best first."""
    terms, phrases, filters = parse(q)
    recent = recent or []
    hits = []
    for _vault, recs in indexes:
        for rec in recs:
            s = rec["summary"]
            if s["inTrash"]:
                continue
            if folder_scope is not None and (s["folderId"] or "") not in folder_scope:
                continue
            if type_ and s["type"] != type_:
                continue
            if favourites and not s["favourite"]:
                continue
            if not _filters_ok(rec, filters, health or {}):
                continue
            sc = score(rec, terms, phrases, notes)
            if sc is None:
                continue
            if s["favourite"]:
                sc += 15
            key = (s["vaultId"], s["id"])
            if key in recent:
                sc += 10 + max(0, 10 - recent.index(key))
            hits.append((-sc, fold(s["title"]), s))
    hits.sort(key=lambda x: (x[0], x[1]))
    return [h[2] for h in hits[:MAX_RESULTS]]
