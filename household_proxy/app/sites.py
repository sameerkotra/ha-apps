"""Sites, who may open them, and their saved form (spec/SPEC.md §4.2, §5.2, §8).

A site is a name, an icon and a target on the home network, opened at /s/<slug>/. Who opens it: admins only (the
default), everyone, or chosen people; admins open every site; a person turned off in Admin → People opens none.
The proxy reads sites from a small in-memory cache that every write (and a restore) empties.
"""
import re
import threading
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from . import config, db, netguard

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
HEADER_VALUE_RE = re.compile(r"^[\x20-\x7e\t]{0,4096}$")
# Headers the proxy itself controls: an extra header can't replace them.
RESERVED_HEADERS = {"host", "connection", "content-length", "transfer-encoding", "upgrade", "te", "trailer",
                    "keep-alive", "proxy-authorization", "proxy-authenticate", "cookie", "origin", "referer",
                    "x-ingress-path"}
RESERVED_PREFIXES = ("x-remote-user-", "x-hass-", "x-forwarded-", "sec-")
ACCESS = ("admins", "everyone", "chosen")
MAX_SITES = 200


class SiteError(ValueError):
    pass


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40].strip("-")
    return s or "site"


def clean_path(p: str) -> str:
    p = (p or "/").strip() or "/"
    if not p.startswith("/") or p.startswith("//") or any(c in p for c in "\\\r\n\t ") or len(p) > 500:
        raise SiteError("Start path must start with a single / (e.g. /dashboard)")
    return p


class HeaderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    value: str | None = Field(default=None, max_length=4096)   # None: keep the saved value


class SiteIn(BaseModel):
    """The Admin → Sites form."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)
    slug: str = Field(default="", max_length=40)
    icon: str = Field(default="🌐", max_length=16)
    target: str = Field(min_length=1, max_length=300)
    start_path: str = Field(default="/", max_length=500)
    access: str = "admins"
    people: list[str] = Field(default_factory=list, max_length=200)
    trusted_ok: StrictBool = False
    spa_mode: StrictBool = False
    show_bar: StrictBool = True
    keep_host: StrictBool = False
    strip_csp: StrictBool = False
    pass_user: StrictBool = False
    allow_public: StrictBool = False
    access_log: StrictBool = False
    timeout_s: int = Field(default=0, ge=0, le=600)
    headers: list[HeaderIn] = Field(default_factory=list, max_length=20)

    @field_validator("access")
    @classmethod
    def _access(cls, v):
        if v not in ACCESS:
            raise ValueError("must be admins, everyone or chosen")
        return v


@dataclass(frozen=True)
class Site:
    """What the proxy needs about a site (one cache entry)."""
    id: int
    slug: str
    name: str
    icon: str
    scheme: str
    host: str
    port: int
    origin: str
    start_path: str
    access: str
    people: frozenset
    spa_mode: bool
    show_bar: bool
    keep_host: bool
    strip_csp: bool
    pass_user: bool
    allow_public: bool
    tls_pem: str | None
    timeout_s: int
    access_log: bool
    updated_at: str
    headers: tuple = field(default=())         # ((name, value), …) — secret

    @property
    def version(self) -> tuple:
        return (self.id, self.updated_at)


_lock = threading.Lock()
_cache: dict = {"gen": None, "by_slug": None}


def invalidate() -> None:
    with _lock:
        _cache["by_slug"] = None


def _load(conn) -> dict:
    out = {}
    people: dict[int, set] = {}
    for r in conn.execute("SELECT site_id, user_id FROM site_people"):
        people.setdefault(r["site_id"], set()).add(r["user_id"])
    headers: dict[int, list] = {}
    for r in conn.execute("SELECT site_id, name, value FROM site_headers ORDER BY name"):
        headers.setdefault(r["site_id"], []).append((r["name"], r["value"]))
    for r in conn.execute("SELECT * FROM sites ORDER BY position, id"):
        try:
            scheme, host, port = netguard.parse_target(r["target"])
        except netguard.TargetError:
            continue
        out[r["slug"]] = Site(
            id=r["id"], slug=r["slug"], name=r["name"], icon=r["icon"], scheme=scheme, host=host, port=port,
            origin=netguard.origin(scheme, host, port), start_path=r["start_path"], access=r["access"],
            people=frozenset(people.get(r["id"], ())), spa_mode=bool(r["spa_mode"]), show_bar=bool(r["show_bar"]),
            keep_host=bool(r["keep_host"]), strip_csp=bool(r["strip_csp"]), pass_user=bool(r["pass_user"]),
            allow_public=bool(r["allow_public"]), tls_pem=r["tls_pem"], timeout_s=r["timeout_s"],
            access_log=bool(r["access_log"]), updated_at=r["updated_at"],
            headers=tuple((n, v) for n, v in headers.get(r["id"], ()) if v))
    return out


def all_sites() -> dict:
    """{slug: Site} in the launcher's order."""
    with _lock:
        if _cache["by_slug"] is not None and _cache["gen"] == db.generation:
            return _cache["by_slug"]
    with db.get_conn() as conn:
        loaded = _load(conn)
    with _lock:
        _cache["by_slug"], _cache["gen"] = loaded, db.generation
    return loaded


def get(slug: str) -> Site | None:
    return all_sites().get(slug)


def can_open(user: dict, site: Site) -> bool:
    if not user or not user.get("enabled"):
        return False
    if user.get("is_admin"):
        return True
    if site.access == "everyone":
        return True
    return site.access == "chosen" and user["id"] in site.people


def for_user(user: dict) -> list[Site]:
    return [s for s in all_sites().values() if can_open(user, s)]


def public(site: Site) -> dict:
    """A site as the launcher shows it (nothing about its target's credentials)."""
    return {"slug": site.slug, "name": site.name, "icon": site.icon, "startPath": site.start_path,
            "showBar": site.show_bar, "spaMode": site.spa_mode}


# --------------------------------------------------------------------------------------------- admin form

def admin_row(conn, r) -> dict:
    people = [x["user_id"] for x in conn.execute("SELECT user_id FROM site_people WHERE site_id = ?", (r["id"],))]
    headers = [{"name": x["name"], "saved": bool(x["value"])}
               for x in conn.execute("SELECT name, value FROM site_headers WHERE site_id = ? ORDER BY name",
                                     (r["id"],))]
    return {"id": r["id"], "slug": r["slug"], "name": r["name"], "icon": r["icon"], "target": r["target"],
            "start_path": r["start_path"], "access": r["access"], "people": people,
            "trusted_ok": bool(r["trusted_ok_at"]), "trusted_ok_at": r["trusted_ok_at"],
            "spa_mode": bool(r["spa_mode"]), "show_bar": bool(r["show_bar"]), "keep_host": bool(r["keep_host"]),
            "strip_csp": bool(r["strip_csp"]), "pass_user": bool(r["pass_user"]),
            "allow_public": bool(r["allow_public"]), "access_log": bool(r["access_log"]),
            "timeout_s": r["timeout_s"], "tls_fingerprint": r["tls_fingerprint"], "headers": headers,
            "position": r["position"]}


def admin_list() -> list[dict]:
    with db.get_conn() as conn:
        return [admin_row(conn, r) for r in conn.execute("SELECT * FROM sites ORDER BY position, id").fetchall()]


def _validate(body: SiteIn) -> tuple[str, str]:
    try:
        scheme, host, port = netguard.parse_target(body.target)
        netguard.check_saved(host, body.allow_public)
    except netguard.TargetError as e:
        raise SiteError(f"Address {e}")
    slug = body.slug or slugify(body.name)
    if not SLUG_RE.match(slug):
        raise SiteError("Short name: lower-case letters, digits and - (e.g. komodo)")
    if not body.trusted_ok:
        raise SiteError("Confirm that this is software you run and keep updated (a site opened here runs on Home "
                        "Assistant's own address).")
    for h in body.headers:
        n = h.name.strip()
        if not HEADER_NAME_RE.match(n) or n.lower() in RESERVED_HEADERS or n.lower().startswith(RESERVED_PREFIXES):
            raise SiteError(f"Extra header {n!r} isn't allowed")
        if h.value is not None and not HEADER_VALUE_RE.match(h.value):
            raise SiteError(f"Extra header {n!r}: the value must be one line of plain text")
    if body.access == "chosen" and not body.people:
        raise SiteError("Choose at least one person, or let admins only open it")
    return netguard.origin(scheme, host, port), slug


def save(body: SiteIn, admin: dict, site_id: int | None = None) -> dict:
    target, slug = _validate(body)
    start = clean_path(body.start_path)
    now = config.iso()
    with db.get_conn() as conn:
        clash = conn.execute("SELECT id FROM sites WHERE slug = ? AND id IS NOT ?", (slug, site_id)).fetchone()
        if clash:
            raise SiteError(f"Another site already uses the short name {slug!r}")
        cols = dict(slug=slug, name=body.name, icon=body.icon or "🌐", target=target, start_path=start,
                    access=body.access, spa_mode=int(body.spa_mode), show_bar=int(body.show_bar),
                    keep_host=int(body.keep_host), strip_csp=int(body.strip_csp), pass_user=int(body.pass_user),
                    allow_public=int(body.allow_public), access_log=int(body.access_log), timeout_s=body.timeout_s,
                    updated_at=now)
        if site_id is None:
            if conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] >= MAX_SITES:
                raise SiteError(f"At most {MAX_SITES} sites")
            pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM sites").fetchone()[0]
            cols.update(position=pos, created_at=now, trusted_ok_by=admin["id"], trusted_ok_at=now)
            site_id = conn.execute(f"INSERT INTO sites ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                   tuple(cols.values())).lastrowid
        else:
            old = conn.execute("SELECT target, tls_pem FROM sites WHERE id = ?", (site_id,)).fetchone()
            if old is None:
                raise LookupError("No such site.")
            if old["target"] != target:            # a trusted certificate belongs to the old address
                cols.update(tls_pem=None, tls_fingerprint=None)
            cols.update(trusted_ok_by=admin["id"], trusted_ok_at=now)
            conn.execute(f"UPDATE sites SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                         (*cols.values(), site_id))
        conn.execute("DELETE FROM site_people WHERE site_id = ?", (site_id,))
        if body.access == "chosen":
            conn.executemany("INSERT OR IGNORE INTO site_people (site_id, user_id) VALUES (?, ?)",
                             [(site_id, p) for p in body.people[:200] if isinstance(p, str) and p])
        saved = {r["name"]: r["value"] for r in conn.execute("SELECT name, value FROM site_headers WHERE site_id = ?",
                                                             (site_id,))}
        conn.execute("DELETE FROM site_headers WHERE site_id = ?", (site_id,))
        for h in body.headers:
            n = h.name.strip()
            value = h.value if h.value is not None else saved.get(n, "")
            conn.execute("INSERT OR REPLACE INTO site_headers (site_id, name, value) VALUES (?, ?, ?)",
                         (site_id, n, value))
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        out = admin_row(conn, row)
    invalidate()
    return out


def delete(site_id: int) -> bool:
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM sites WHERE id = ?", (site_id,)).rowcount
    invalidate()
    return bool(n)


def reorder(ids: list[int]) -> None:
    with db.get_conn() as conn:
        for pos, sid in enumerate(ids):
            conn.execute("UPDATE sites SET position = ? WHERE id = ?", (pos, sid))
    invalidate()


def set_certificate(site_id: int, pem: str | None, fingerprint: str | None) -> None:
    with db.get_conn() as conn:
        if not conn.execute("UPDATE sites SET tls_pem = ?, tls_fingerprint = ?, updated_at = ? WHERE id = ?",
                            (pem, fingerprint, config.iso(), site_id)).rowcount:
            raise LookupError("No such site.")
    invalidate()
