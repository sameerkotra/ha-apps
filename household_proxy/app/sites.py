"""Sites, who may open them, and their saved form (spec/SPEC.md §4.2, §5.2, §8).

A site is a name, an icon and a target on the home network, opened at /s/<slug>/. Who opens it: admins only (the
default), everyone, or chosen people; admins open every site; a person turned off in Admin → People opens none.
The proxy reads sites from a small in-memory cache that every write (and a restore) empties.
"""
import json
import re
import threading
import time
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


CONTENT_TYPE_RE = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+*-]+$")
MAX_RULES = 20


class RuleIn(BaseModel):
    """One rewrite rule (§3.5): a regular expression and its replacement; $prefix is the site's prefix."""
    model_config = ConfigDict(extra="forbid")
    where: str = "body"
    types: str = Field(default="", max_length=300)
    match: str = Field(min_length=1, max_length=500)
    replace: str = Field(default="", max_length=500)

    @field_validator("where")
    @classmethod
    def _where(cls, v):
        if v not in ("body", "header"):
            raise ValueError("must be body or header")
        return v

    @field_validator("types")
    @classmethod
    def _types(cls, v):
        parts = [t.strip().lower() for t in v.split(",") if t.strip()]
        for t in parts:
            if not CONTENT_TYPE_RE.match(t):
                raise ValueError(f"{t!r} isn't a content type (e.g. application/javascript)")
        return ",".join(parts)

    @field_validator("match")
    @classmethod
    def _match(cls, v):
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"isn't a valid regular expression: {e}")
        return v


@dataclass(frozen=True)
class Rule:
    where: str
    types: frozenset
    pattern: re.Pattern
    replace: str

    def applies(self, content_type: str | None) -> bool:
        if not self.types:
            return True
        main = (content_type or "").split(";")[0].strip().lower()
        return main in self.types or any(t.endswith("/*") and main.startswith(t[:-1]) for t in self.types)

    def apply(self, text: str, prefix: str) -> str:
        return self.pattern.sub(self.replace.replace("$prefix", prefix.replace("\\", "\\\\")), text)


EVENTS = ("down", "restarting", "back_up", "gave_up", "couldnt_restart")
DEFAULT_EVENTS = ["down", "back_up", "gave_up", "couldnt_restart"]
SERVICE_RE = re.compile(r"^(notify\.)?[a-z0-9_]{1,100}$")


class CheckIn(BaseModel):
    """§13.2: how a site is checked (kind None = not checked)."""
    model_config = ConfigDict(extra="forbid")
    kind: str | None = None
    path: str = Field(default="", max_length=500)
    every_s: int = Field(default=60, ge=15, le=86400)
    timeout_s: int = Field(default=5, ge=1, le=60)
    down_after: int = Field(default=3, ge=1, le=10)
    ok_codes: str = "below500"
    sensor: StrictBool = True

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in (None, "http", "tcp", "ping"):
            raise ValueError("must be http, tcp or ping")
        return v

    @field_validator("ok_codes")
    @classmethod
    def _codes(cls, v):
        if v not in ("below500", "2xx3xx"):
            raise ValueError("must be below500 or 2xx3xx")
        return v


class AlertsIn(BaseModel):
    """§13.4: who is told, by which extra service, about which events."""
    model_config = ConfigDict(extra="forbid")
    people: list[str] | None = None            # None: every admin (a new site)
    service: str = Field(default="", max_length=110)
    events: list[str] = Field(default_factory=lambda: list(DEFAULT_EVENTS), max_length=5)

    @field_validator("service")
    @classmethod
    def _service(cls, v):
        v = v.strip()
        if v and not SERVICE_RE.match(v):
            raise ValueError("must be a notify service, e.g. notify.living_room_speaker")
        return v

    @field_validator("events")
    @classmethod
    def _events(cls, v):
        for e in v:
            if e not in EVENTS:
                raise ValueError(f"unknown event {e!r}")
        return list(dict.fromkeys(v))


class RestartIn(BaseModel):
    """§13.3: the switch that powers the device, and how restarts go (entity None = never restarted)."""
    model_config = ConfigDict(extra="forbid")
    entity: str | None = Field(default=None, max_length=120)
    confirmed: StrictBool = False                 # it doesn't power Home Assistant, the router or the network switch
    off_for_s: int = Field(default=5, ge=1, le=300)
    wait_after_s: int = Field(default=120, ge=10, le=3600)
    max_restarts: int = Field(default=3, ge=0, le=10)
    window_start: str | None = None
    window_end: str | None = None

    @field_validator("entity")
    @classmethod
    def _entity(cls, v):
        v = (v or "").strip() or None
        if v is not None and not re.match(r"^switch\.[a-z0-9_]{1,100}$", v):
            raise ValueError("must be a switch entity, e.g. switch.nas_plug")
        return v

    @field_validator("window_start", "window_end")
    @classmethod
    def _hm(cls, v):
        v = (v or "").strip() or None
        if v is not None and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("must be a time like 02:00")
        return v


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
    rules: list[RuleIn] = Field(default_factory=list, max_length=MAX_RULES)
    use_favicon: StrictBool = False
    use: str = "proxy"
    check: CheckIn = Field(default_factory=CheckIn)
    alerts: AlertsIn = Field(default_factory=AlertsIn)
    restart: RestartIn = Field(default_factory=RestartIn)

    @field_validator("use")
    @classmethod
    def _use(cls, v):
        if v not in ("proxy", "monitor"):
            raise ValueError("must be proxy or monitor")
        return v

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
    rules: tuple = field(default=())           # (Rule, …)
    favicon: bool = False                      # the site's own icon is shown
    use: str = "proxy"                         # 'monitor': never proxied (§13.1)
    checked: bool = False                      # has checks (§13.2)
    state: str | None = None                   # the check state, for the launcher's dot ("paused" while paused)

    def body_rules(self, content_type: str | None) -> list:
        return [r for r in self.rules if r.where == "body" and r.applies(content_type)]

    def header_rules(self, content_type: str | None) -> list:
        return [r for r in self.rules if r.where == "header" and r.applies(content_type)]

    @property
    def version(self) -> tuple:
        """What the site's pooled connections depend on: a change here makes new ones (proxy.client)."""
        return (self.id, self.tls_pem, self.allow_public)


_lock = threading.Lock()
_cache: dict = {"gen": None, "by_slug": None, "version": 0}


def invalidate() -> None:
    with _lock:
        _cache["by_slug"] = None
        _cache["version"] += 1


def _load(conn) -> dict:
    out = {}
    people: dict[int, set] = {}
    for r in conn.execute("SELECT site_id, user_id FROM site_people"):
        people.setdefault(r["site_id"], set()).add(r["user_id"])
    headers: dict[int, list] = {}
    for r in conn.execute("SELECT site_id, name, value FROM site_headers ORDER BY name"):
        headers.setdefault(r["site_id"], []).append((r["name"], r["value"]))
    rules: dict[int, list] = {}
    for r in conn.execute("SELECT * FROM site_rules ORDER BY site_id, position"):
        try:
            pat = re.compile(r["match"])
        except re.error:
            continue
        rules.setdefault(r["site_id"], []).append(Rule(r["where_"], frozenset(t for t in r["types"].split(",") if t),
                                                       pat, r["replace"]))
    for r in conn.execute("SELECT * FROM sites ORDER BY position, id"):
        try:
            if r["use_"] == "monitor":
                scheme, host, port = netguard.parse_monitor_target(r["target"])
            else:
                scheme, host, port = netguard.parse_target(r["target"])
        except netguard.TargetError:
            continue
        out[r["slug"]] = Site(
            id=r["id"], slug=r["slug"], name=r["name"], icon=r["icon"], scheme=scheme, host=host, port=port or 0,
            origin=netguard.origin(scheme, host, port) if scheme else "", start_path=r["start_path"], access=r["access"],
            people=frozenset(people.get(r["id"], ())), spa_mode=bool(r["spa_mode"]), show_bar=bool(r["show_bar"]),
            keep_host=bool(r["keep_host"]), strip_csp=bool(r["strip_csp"]), pass_user=bool(r["pass_user"]),
            allow_public=bool(r["allow_public"]), tls_pem=r["tls_pem"], timeout_s=r["timeout_s"],
            access_log=bool(r["access_log"]), updated_at=r["updated_at"],
            headers=tuple((n, v) for n, v in headers.get(r["id"], ()) if v), rules=tuple(rules.get(r["id"], ())),
            favicon=bool(r["use_favicon"] and r["favicon"]), use=r["use_"], checked=r["check_kind"] is not None,
            state=("paused" if r["paused_until"] and r["paused_until"] > time.time() else r["state"])
            if r["check_kind"] else None)
    return out


def all_sites() -> dict:
    """{slug: Site} in the launcher's order."""
    with _lock:
        if _cache["by_slug"] is not None and _cache["gen"] == db.generation:
            return _cache["by_slug"]
        version, gen = _cache["version"], db.generation
    with db.get_conn() as conn:
        loaded = _load(conn)
    with _lock:
        if _cache["version"] == version:          # nothing was saved while this was read: keep it
            _cache["by_slug"], _cache["gen"] = loaded, gen
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
    """The launcher: the proxied sites this person may open."""
    return [s for s in all_sites().values() if s.use == "proxy" and can_open(user, s)]


def proxied(slug: str) -> Site | None:
    s = get(slug)
    return s if s is not None and s.use == "proxy" else None


def checked_for_user(user: dict) -> list[Site]:
    """The Monitor page: the checked sites (proxied or monitor only) this person may see (§4.2 applies to status)."""
    return [s for s in all_sites().values() if s.checked and can_open(user, s)]


def public(site: Site) -> dict:
    """A site as the launcher shows it (nothing about its target's credentials)."""
    return {"slug": site.slug, "name": site.name, "icon": site.icon, "startPath": site.start_path,
            "showBar": site.show_bar, "spaMode": site.spa_mode,
            "favicon": f"api/sites/{site.slug}/favicon?v={site.updated_at}" if site.favicon else None,
            "state": (site.state or "not_checked") if site.checked else None}


# --------------------------------------------------------------------------------------------- admin form

def admin_row(conn, r) -> dict:
    people = [x["user_id"] for x in conn.execute("SELECT user_id FROM site_people WHERE site_id = ?", (r["id"],))]
    headers = [{"name": x["name"], "saved": bool(x["value"])}
               for x in conn.execute("SELECT name, value FROM site_headers WHERE site_id = ? ORDER BY name",
                                     (r["id"],))]
    rules = [{"where": x["where_"], "types": x["types"], "match": x["match"], "replace": x["replace"]}
             for x in conn.execute("SELECT * FROM site_rules WHERE site_id = ? ORDER BY position", (r["id"],))]
    alert_people = [x["user_id"] for x in conn.execute("SELECT user_id FROM site_alert_people WHERE site_id = ?",
                                                        (r["id"],))]
    return {"use": r["use_"],
            "check": {"kind": r["check_kind"], "path": r["check_path"] or "", "every_s": r["check_every_s"],
                      "timeout_s": r["check_timeout_s"], "down_after": r["down_after"], "ok_codes": r["ok_codes"],
                      "sensor": bool(r["expose_sensor"])},
            "restart": {"entity": r["restart_entity"], "confirmed": bool(r["restart_ok_at"]),
                        "off_for_s": r["off_for_s"], "wait_after_s": r["wait_after_s"],
                        "max_restarts": r["max_restarts"], "window_start": r["window_start"] or "",
                        "window_end": r["window_end"] or ""},
            "alerts": {"people": alert_people, "service": r["alert_service"] or "",
                       "events": json.loads(r["alert_events"] or "[]")},
            "rules": rules, "use_favicon": bool(r["use_favicon"]), "has_favicon": r["favicon"] is not None,
            "id": r["id"], "slug": r["slug"], "name": r["name"], "icon": r["icon"], "target": r["target"],
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
        if body.use == "monitor":
            scheme, host, port = netguard.parse_monitor_target(body.target)
        else:
            scheme, host, port = netguard.parse_target(body.target)
        netguard.check_saved(host, body.allow_public)
    except netguard.TargetError as e:
        raise SiteError(f"Address {e}")
    slug = body.slug or slugify(body.name)
    if not SLUG_RE.match(slug):
        raise SiteError("Short name: lower-case letters, digits and - (e.g. komodo)")
    kind = body.check.kind
    if body.use == "monitor" and kind is None:
        raise SiteError("A monitor-only site needs a check")
    if kind == "http" and not scheme:
        raise SiteError("A web page check needs an address starting with http:// or https://")
    if kind == "tcp" and not (port or scheme):
        raise SiteError("A port check needs host:port")
    if body.check.path:
        clean_path(body.check.path)
    rs = body.restart
    if rs.entity:
        if kind is None:
            raise SiteError("Auto-restart needs a check (it restarts when the check says the site is down)")
        if not rs.confirmed:
            raise SiteError("Confirm that the switch doesn't power Home Assistant, your router or your network switch")
        if bool(rs.window_start) != bool(rs.window_end) or (rs.window_start and rs.window_start == rs.window_end):
            raise SiteError("A restart window needs a start and a different end (e.g. 02:00 to 06:00)")
    if body.use == "proxy" and not body.trusted_ok:
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
    if not scheme:                            # monitor only: host, or host:port
        return (f"[{host}]" if ":" in host else host) + (f":{port}" if port else ""), slug
    return netguard.origin(scheme, host, port), slug


def preview(body: SiteIn, site_id: int | None = None) -> Site:
    """The form's site as the proxy would use it, without saving (the Test on an unsaved form). Extra headers left
    blank use the saved values of `site_id`; its trusted certificate too while the address is unchanged."""
    try:
        if body.use == "monitor":
            scheme, host, port = netguard.parse_monitor_target(body.target)
        else:
            scheme, host, port = netguard.parse_target(body.target)
        netguard.check_saved(host, body.allow_public)
    except netguard.TargetError as e:
        raise SiteError(f"Address {e}")
    saved_headers, pem = {}, None
    if site_id is not None:
        with db.get_conn() as conn:
            row = conn.execute("SELECT target, tls_pem FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row and scheme and row["target"] == netguard.origin(scheme, host, port):
                # saved credentials and certificate only while the address is the same
                pem = row["tls_pem"]
                saved_headers = {r["name"]: r["value"] for r in
                                 conn.execute("SELECT name, value FROM site_headers WHERE site_id = ?", (site_id,))}
    headers = tuple((h.name.strip(), h.value if h.value is not None else saved_headers.get(h.name.strip(), ""))
                    for h in body.headers)
    return Site(id=site_id or 0, slug=body.slug or slugify(body.name), name=body.name, icon=body.icon, scheme=scheme,
                host=host, port=port or 0, origin=netguard.origin(scheme, host, port) if scheme else "",
                start_path=clean_path(body.start_path),
                access=body.access, people=frozenset(), spa_mode=body.spa_mode, show_bar=body.show_bar,
                keep_host=body.keep_host, strip_csp=body.strip_csp, pass_user=body.pass_user,
                allow_public=body.allow_public, tls_pem=pem, timeout_s=body.timeout_s, access_log=False,
                updated_at="", headers=tuple((n, v) for n, v in headers if v), use=body.use,
                checked=body.check.kind is not None)


def by_id(site_id: int) -> Site | None:
    return next((s for s in all_sites().values() if s.id == site_id), None)


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
                    use_favicon=int(body.use_favicon), use_=body.use, check_kind=body.check.kind,
                    check_path=body.check.path or None, check_every_s=body.check.every_s,
                    check_timeout_s=body.check.timeout_s, down_after=body.check.down_after,
                    ok_codes=body.check.ok_codes, expose_sensor=int(body.check.sensor),
                    alert_service=body.alerts.service or None, alert_events=json.dumps(body.alerts.events),
                    restart_entity=body.restart.entity, off_for_s=body.restart.off_for_s,
                    wait_after_s=body.restart.wait_after_s, max_restarts=body.restart.max_restarts,
                    window_start=body.restart.window_start if body.restart.entity else None,
                    window_end=body.restart.window_end if body.restart.entity else None,
                    updated_at=now)
        if body.restart.entity:
            cols.update(restart_ok_by=admin["id"], restart_ok_at=now)
        else:
            cols.update(restart_due=0, restart_ok_by=None, restart_ok_at=None)
        is_new = site_id is None
        target_changed = False
        if site_id is None:
            if conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] >= MAX_SITES:
                raise SiteError(f"At most {MAX_SITES} sites")
            pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM sites").fetchone()[0]
            cols.update(position=pos, created_at=now)
            if body.use == "proxy":
                cols.update(trusted_ok_by=admin["id"], trusted_ok_at=now)
            site_id = conn.execute(f"INSERT INTO sites ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                   tuple(cols.values())).lastrowid
        else:
            old = conn.execute("SELECT target, tls_pem, check_kind, check_every_s FROM sites WHERE id = ?",
                               (site_id,)).fetchone()
            if old is None:
                raise LookupError("No such site.")
            target_changed = old["target"] != target
            if target_changed:                     # a trusted certificate belongs to the old address
                cols.update(tls_pem=None, tls_fingerprint=None)
            if body.use == "proxy":
                cols.update(trusted_ok_by=admin["id"], trusted_ok_at=now)
            if old["check_kind"] != body.check.kind or old["check_every_s"] != body.check.every_s:
                cols.update(next_check_at=None)       # checked again soon with the new settings
            if body.check.kind is None and old["check_kind"] is not None:
                cols.update(state=None, state_since=None, fails_in_row=0, incident_id=None)
            conn.execute(f"UPDATE sites SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                         (*cols.values(), site_id))
        conn.execute("DELETE FROM site_people WHERE site_id = ?", (site_id,))
        if body.access == "chosen":
            conn.executemany("INSERT OR IGNORE INTO site_people (site_id, user_id) VALUES (?, ?)",
                             [(site_id, p) for p in body.people[:200] if isinstance(p, str) and p])
        saved = {r["name"]: r["value"] for r in conn.execute("SELECT name, value FROM site_headers WHERE site_id = ?",
                                                             (site_id,))} if not target_changed else {}
        for h in body.headers:
            if h.value is None and target_changed and h.name.strip():
                raise SiteError(f"The address changed: enter the value of the extra header {h.name.strip()!r} again "
                                "(saved values never follow a site to a new address)")
        conn.execute("DELETE FROM site_headers WHERE site_id = ?", (site_id,))
        for h in body.headers:
            n = h.name.strip()
            value = h.value if h.value is not None else saved.get(n, "")
            conn.execute("INSERT OR REPLACE INTO site_headers (site_id, name, value) VALUES (?, ?, ?)",
                         (site_id, n, value))
        if body.alerts.people is not None:
            conn.execute("DELETE FROM site_alert_people WHERE site_id = ?", (site_id,))
            conn.executemany("INSERT OR IGNORE INTO site_alert_people (site_id, user_id) VALUES (?, ?)",
                             [(site_id, p) for p in body.alerts.people[:200] if isinstance(p, str) and p])
        elif is_new:                              # a new site alerts every admin until someone chooses
            conn.executemany("INSERT OR IGNORE INTO site_alert_people (site_id, user_id) VALUES (?, ?)",
                             [(site_id, u) for u in admin_ids(conn)])
        conn.execute("DELETE FROM site_rules WHERE site_id = ?", (site_id,))
        conn.executemany("INSERT INTO site_rules (site_id, position, where_, types, match, replace) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         [(site_id, i, x.where, x.types, x.match, x.replace) for i, x in enumerate(body.rules)])
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        out = admin_row(conn, row)
    invalidate()
    return out


def admin_ids(conn) -> list[str]:
    """The people who have opened the app and are admins (by user id or login name)."""
    from . import auth
    return [r["id"] for r in conn.execute("SELECT id, name, username FROM users")
            if auth.is_admin(r["id"], r["name"], r["username"])]


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


def set_favicon(site_id: int, data: bytes | None, content_type: str | None) -> None:
    with db.get_conn() as conn:
        if not conn.execute("UPDATE sites SET favicon = ?, favicon_type = ?, updated_at = ? WHERE id = ?",
                            (data, content_type, config.iso(), site_id)).rowcount:
            raise LookupError("No such site.")
    invalidate()


def favicon(slug: str) -> tuple[bytes, str] | None:
    with db.get_conn() as conn:
        r = conn.execute("SELECT favicon, favicon_type FROM sites WHERE slug = ? AND use_favicon = 1", (slug,)).fetchone()
    return (bytes(r["favicon"]), r["favicon_type"]) if r and r["favicon"] else None


def set_certificate(site_id: int, pem: str | None, fingerprint: str | None) -> None:
    with db.get_conn() as conn:
        if not conn.execute("UPDATE sites SET tls_pem = ?, tls_fingerprint = ?, updated_at = ? WHERE id = ?",
                            (pem, fingerprint, config.iso(), site_id)).rowcount:
            raise LookupError("No such site.")
    invalidate()
