# Shared file: edit common/python/app_bus.py and run tools/sync_common.py; don't edit this copy. sha256=780f4ea8e36ac678452283594d276c1a53faf656d093cad3f95b7b21a4848dc4
"""Messages between the household apps over Home Assistant's event bus (APP_MESSAGES_SPEC.md).

Shared by the household apps (common/python/app_bus.py, copied into an app's
app/common/ with ha_ws.py by tools/sync_common.py once the app uses it).
Standard library only; nothing is imported from the app. What it needs is
passed in:

- `db`: the app's connection factory — `db.get_conn` (a context manager
  yielding a sqlite3 connection, like every app's) or a function returning a
  plain sqlite3.Connection (closed after use). The bus manages its own
  transactions on that connection (BEGIN IMMEDIATE … COMMIT).
- Home Assistant: `SUPERVISOR_TOKEN`, `SUPERVISOR_CORE_API`
  (default http://supervisor/core/api) and `SUPERVISOR_CORE_WS`
  (default ws://supervisor/core/websocket) from the environment, or the
  `token`, `api_base`, `ws_url` options; `post=` replaces the REST sender
  (e.g. `lambda env: ha_client.request("POST", "/events/household_apps", env)[0]`).

Usage (one bus per app; the module-level functions use a default instance):

    from .common import app_bus as bus

    @bus.handler("poll.create")                      # kv 1; versions=(1, 2) for more
    def on_poll_create(msg, conn):                   # runs inside the ack transaction
        ...                                          # use conn for the action
        msg.after_commit(lambda: ...)                # optional: after the commit (live updates, pushes)
        return {"poll_id": ..., "chat_id": ...}      # → ack {result}; None → ack {}
        # return/raise bus.Nack("not_allowed")       # → nack; anything the handler wrote is rolled back

    bus.on_reply("duel:", on_answer)                 # on_answer(msg, conn): acks/nacks of my messages and
                                                     # answers (poll.closed …) whose kind, answered kind or
                                                     # ref prefix matches
    bus.start("household_arcade", "Household Arcade", "1.5.0", can=[...], wants=["poll.closed"],
              db=db.get_conn)                        # in lifespan; bus.stop() on shutdown
                                                     # (ws=<the app's ha_ws.HAWebSocket> to share one)
    if bus.available("household_chat", "poll.create"):
        bus.send("household_chat", "poll.create", {...}, ref="duel:3f2c")

Delivery (spec §4): every message goes to the `bus_outbox` table first, then
`POST /api/events/household_apps`; re-sent after 30 s, 2 min, 10 min, then every
30 min until answered or expired (expiry → the reply callbacks get a local
`nack expired`). The receiver runs the handler and records the id in `bus_seen`
in one transaction, then answers `ack`/`nack`; a handler that raises answers
nothing, so the sender re-sends. Ids already in `bus_seen` (kept 7 days) get
their stored answer again and are not acted on twice. `nack busy` (the
receiver's rate limit, or a handler) isn't stored: the sender keeps retrying.

Threads: the WebSocket client (ha_ws.HAWebSocket, receiving and answering) and
a small outbox thread (`outbox_thread=False` to drive `run_outbox_once()` from
the app's housekeeping instead). `stop()` ends both.

Logs show id, from, to, kind and result only — never `data`.
"""
import collections
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import ha_ws

logger = logging.getLogger("app_bus")

EVENT_TYPE = "household_apps"
ENVELOPE_VERSION = 1
MAX_EVENT_BYTES = 8 * 1024
RETRY_DELAYS = (30, 120, 600)            # seconds after attempt 1, 2, 3 …
RETRY_EVERY = 30 * 60                    # … then every 30 min
SEEN_KEEP = timedelta(days=7)
MAX_EXPIRES_IN = SEEN_KEEP               # longer would outlive de-duplication
OUTBOX_KEEP = timedelta(days=7)          # finished outbox rows
AVAILABLE_FOR = timedelta(hours=24)
HELLO_EVERY = timedelta(hours=6)
HELLO_ANSWER_EVERY = timedelta(hours=1)
SEND_LIMIT = 30                          # outbox sends per minute to one app
RECEIVE_LIMIT = 60                       # handled messages per minute from one app (more → nack busy)
NACK_REASONS = ("not_found", "not_allowed", "invalid", "unsupported_kind", "unsupported_version",
                "expired", "busy")
RESERVED_KINDS = ("hello", "who", "ack", "nack")

SLUG_RE = re.compile(r"^[a-z0-9_]{1,64}$")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){0,2}$")   # area.verb or area.thing.verb
ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
MAX_REF = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_outbox (id TEXT PRIMARY KEY, to_app TEXT NOT NULL, kind TEXT NOT NULL,
  body TEXT NOT NULL, ref TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT NOT NULL,
  expires TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','acked','nacked','expired')), result TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS bus_outbox_due ON bus_outbox (state, next_try);
CREATE TABLE IF NOT EXISTS bus_seen (id TEXT PRIMARY KEY, from_app TEXT NOT NULL, kind TEXT NOT NULL,
  result TEXT, seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bus_apps (slug TEXT PRIMARY KEY, name TEXT, version TEXT, can TEXT NOT NULL,
  last_seen TEXT NOT NULL);
"""


def migrate(conn) -> None:
    """Create the bus tables if missing (safe to call on every start; also from an app's migrations)."""
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
class BusError(Exception):
    """The message can't be sent (not started, bad arguments)."""


class NotAvailable(BusError):
    """The receiving app isn't there, or hasn't said it can do this kind in the last 24 hours."""


class TooLarge(BusError):
    """The whole event would be over 8 KB."""


class Nack(Exception):
    """Return or raise from a handler to answer `nack`; anything the handler wrote is rolled back."""

    def __init__(self, reason: str, detail: str | None = None):
        if reason not in NACK_REASONS:
            raise ValueError(f"unknown nack reason {reason!r}")
        super().__init__(reason)
        self.reason, self.detail = reason, detail

    def data(self) -> dict:
        return {"reason": self.reason, **({"detail": str(self.detail)[:200]} if self.detail else {})}


class Message:
    """A received message (or an answer to one of mine), as handed to handlers and reply callbacks."""

    __slots__ = ("id", "v", "from_app", "to_app", "kind", "kv", "reply_to", "ref", "sent", "expires",
                 "data", "answers", "local", "_after")

    def __init__(self, env: dict, answers: str | None = None, local: bool = False):
        self.id = env.get("id")
        self.v = env.get("v")
        self.from_app = env.get("from")
        self.to_app = env.get("to")
        self.kind = env.get("kind")
        self.kv = env.get("kv")
        self.reply_to = env.get("reply_to")
        self.ref = env.get("ref")
        self.sent = _parse_time(env.get("sent"))
        self.expires = _parse_time(env.get("expires"))
        self.data = env.get("data") if isinstance(env.get("data"), dict) else {}
        self.answers = answers          # for ack/nack: the kind of my message it answers
        self.local = local              # made up by this app's own outbox (expired before an answer)
        self._after = []

    def after_commit(self, fn) -> None:
        """Call `fn()` once the handler's transaction has been committed — for what must not happen
        before (live updates, notifications). Not called when the handler nacks or fails. Errors are
        logged, never answered."""
        self._after.append(fn)

    @property
    def reason(self) -> str | None:
        """The nack reason (None for anything else)."""
        return self.data.get("reason") if self.kind == "nack" else None

    @property
    def result(self):
        """The ack's result (None for anything else)."""
        return self.data.get("result") if self.kind == "ack" else None

    def __repr__(self):
        return f"<Message {self.kind} id={self.id} from={self.from_app} to={self.to_app}>"


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(now: datetime | None = None) -> str:
    """A ULID: 48-bit millisecond time + 80 random bits, 26 Crockford base-32 characters."""
    ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
    n = (ms << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def event_size(env: dict) -> int:
    return len(_dumps(env).encode("utf-8"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Window:
    """Per-key sliding one-minute counters on the bus clock."""

    def __init__(self):
        self._hits = collections.defaultdict(collections.deque)

    def allow(self, key: str, now: float, limit: int, span: float = 60) -> bool:
        q = self._hits[key]
        while q and q[0] <= now - span:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


# ---------------------------------------------------------------------------
# the bus
# ---------------------------------------------------------------------------
class AppBus:
    def __init__(self, **options):
        self._handlers: dict[str, tuple] = {}        # kind -> (fn, versions)
        self._reply_cbs: list[tuple[str, object]] = []
        self._lock = threading.RLock()
        self._outbox_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._outbox_thread: threading.Thread | None = None
        self._ws: ha_ws.HAWebSocket | None = None
        self._own_ws = True
        self._send_window = _Window()
        self._recv_window = _Window()
        self._hello_answered: dict[str, datetime] = {}
        self._last_hello: datetime | None = None
        self.slug = None
        self.started = False
        self.api_base = self.ws_url = self.token = None
        self.post = None
        self.clock = _utcnow
        self.outbox_thread = True
        self.outbox_interval = 10.0
        self.send_limit = SEND_LIMIT
        self.receive_limit = RECEIVE_LIMIT
        self.ws_options: dict = {}
        self.configure(**options)

    def configure(self, *, api_base=None, ws_url=None, token=None, post=None, clock=None, outbox_thread=None,
                  outbox_interval=None, send_limit=None, receive_limit=None, ws_options=None) -> None:
        """Set transport/test options (all optional; defaults come from the environment)."""
        if api_base is not None:
            self.api_base = api_base.rstrip("/")
        if ws_url is not None:
            self.ws_url = ws_url
        if token is not None:
            self.token = token
        if post is not None:
            self.post = post
        if clock is not None:
            self.clock = clock
        if outbox_thread is not None:
            self.outbox_thread = outbox_thread
        if outbox_interval is not None:
            self.outbox_interval = outbox_interval
        if send_limit is not None:
            self.send_limit = send_limit
        if receive_limit is not None:
            self.receive_limit = receive_limit
        if ws_options is not None:
            self.ws_options = dict(ws_options)

    # -- registration ----------------------------------------------------------
    def handler(self, kind: str, versions=(1,)):
        """Decorator: `fn(msg, conn) -> dict | None | Nack` handles `kind` (data versions `versions`)."""
        if not KIND_RE.match(kind or "") or kind in RESERVED_KINDS:
            raise ValueError(f"bad kind {kind!r}")

        def deco(fn):
            with self._lock:
                self._handlers[kind] = (fn, tuple(versions))
            return fn
        return deco

    def on_reply(self, kind_or_ref_prefix: str, callback) -> None:
        """`callback(msg, conn)` for answers to my messages: `ack`/`nack` (msg.answers is the kind of my
        message), the local `nack expired` when my outbox gives up, and answer kinds (e.g. `poll.closed`)
        that have no handler of their own. Matches when the key equals msg.kind or msg.answers, or msg.ref
        starts with it. Runs inside the transaction that records the answer; raising undoes it (the answer
        comes again)."""
        if not kind_or_ref_prefix:
            raise ValueError("on_reply needs a kind or ref prefix")
        with self._lock:
            self._reply_cbs.append((kind_or_ref_prefix, callback))

    # -- life cycle --------------------------------------------------------------
    def start(self, app_slug: str, name: str, version: str, can=None, wants=(), handlers=None, db=None,
              ws=None, **options) -> None:
        """Create the tables, connect to Home Assistant and start the outbox. `can` defaults to the kinds
        with handlers; `handlers` is an optional {kind: fn} (version 1) besides @handler. `ws` shares the
        app's own ha_ws.HAWebSocket (one connection for everything): the bus adds its subscription and
        on-connect callback to it but never starts or stops it — the app does."""
        if self.started:
            raise BusError("already started")
        if not SLUG_RE.match(app_slug or ""):
            raise ValueError(f"bad app slug {app_slug!r}")
        if db is None:
            raise ValueError("start() needs db (the app's connection factory)")
        self.configure(**options)
        if self.api_base is None:
            self.api_base = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")
        if self.ws_url is None:
            self.ws_url = os.environ.get("SUPERVISOR_CORE_WS", ha_ws.DEFAULT_URL)
        if self.token is None:
            self.token = os.environ.get("SUPERVISOR_TOKEN", "")
        for kind, fn in (handlers or {}).items():
            self.handler(kind)(fn)
        self.slug, self.name, self.version = app_slug, name, version
        self.can = sorted(set(can if can is not None else self._handlers))
        self.wants = sorted(set(wants or ()))
        self._db = db
        with self._tx() as conn:
            migrate(conn)
        self._stop.clear()
        self._wake.clear()
        self._last_hello = None
        self.started = True
        if self.token and ws is not None:
            self._ws, self._own_ws = ws, False
            ws.subscribe(EVENT_TYPE, self._on_event)
            ws.on_connect(self._on_connect)
            if ws.connected.is_set():                     # already connected: say hello now
                self._on_connect(True)
        elif self.token:
            self._ws, self._own_ws = ha_ws.HAWebSocket(self.ws_url, self.token, name=f"bus-ws-{app_slug}",
                                                       **self.ws_options), True
            self._ws.subscribe(EVENT_TYPE, self._on_event)
            self._ws.on_connect(self._on_connect)
            self._ws.start()
        else:
            logger.warning("SUPERVISOR_TOKEN not set — app messages are off. Expected outside Home Assistant.")
        if self.outbox_thread:
            self._outbox_thread = threading.Thread(target=self._outbox_loop, daemon=True,
                                                   name=f"bus-outbox-{app_slug}")
            self._outbox_thread.start()

    def stop(self, timeout: float = 5) -> None:
        """Disconnect and end the bus threads (messages in the outbox stay for the next start)."""
        self._stop.set()
        self._wake.set()
        if self._ws:
            if self._own_ws:
                self._ws.stop(timeout)
            else:                                         # shared: leave the app's connection running
                self._ws.unsubscribe(EVENT_TYPE, self._on_event)
                self._ws.off_connect(self._on_connect)
            self._ws = None
        t = self._outbox_thread
        if t and t is not threading.current_thread():
            t.join(timeout)
        self._outbox_thread = None
        self.started = False

    @property
    def connected(self) -> bool:
        return bool(self._ws and self._ws.connected.is_set())

    # -- database ------------------------------------------------------------------
    @contextmanager
    def _conn(self):
        obj = self._db()
        if isinstance(obj, sqlite3.Connection):
            try:
                yield obj
            finally:
                obj.close()
        else:
            with obj as conn:
                yield conn

    @contextmanager
    def _tx(self):
        with self._conn() as conn:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            conn.commit()

    def _now(self) -> datetime:
        return self.clock()

    # -- finding each other -------------------------------------------------------------
    def available(self, app: str, kind: str) -> bool:
        """True if `app` said it can do `kind` in the last 24 hours."""
        if not self.started:
            return False
        with self._conn() as conn:
            row = conn.execute("SELECT can, last_seen FROM bus_apps WHERE slug = ?", (app,)).fetchone()
        if not row:
            return False
        seen = _parse_time(row[1])
        try:
            can = json.loads(row[0])
        except ValueError:
            can = []
        return bool(seen and seen >= self._now() - AVAILABLE_FOR and kind in can)

    def apps(self) -> list[dict]:
        """Connected apps for an admin page: slug, name, version, can, last_seen, active (24 h)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT slug, name, version, can, last_seen FROM bus_apps ORDER BY slug").fetchall()
        cutoff = self._now() - AVAILABLE_FOR
        out = []
        for slug, name, version, can, last_seen in rows:
            try:
                can = json.loads(can)
            except ValueError:
                can = []
            seen = _parse_time(last_seen)
            out.append({"slug": slug, "name": name, "version": version, "can": can, "last_seen": last_seen,
                        "active": bool(seen and seen >= cutoff)})
        return out

    # -- sending ---------------------------------------------------------------------
    def _envelope(self, to: str, kind: str, data: dict, *, kv: int = 1, ref=None, reply_to=None,
                  expires_in: timedelta = timedelta(hours=24)) -> dict:
        now = self._now()
        return {"id": new_ulid(now), "v": ENVELOPE_VERSION, "from": self.slug, "to": to, "kind": kind, "kv": kv,
                "reply_to": reply_to, "ref": ref, "sent": _iso(now), "expires": _iso(now + expires_in),
                "data": data}

    def send(self, to: str, kind: str, data: dict, ref: str | None = None,
             expires_in: timedelta = timedelta(hours=24), reply_to: str | None = None, kv: int = 1) -> str:
        """Queue a message in the outbox and send it; returns its id. Raises NotAvailable when `to` can't
        take `kind` (answers, i.e. with reply_to, only need `to` to be valid), TooLarge over 8 KB,
        BusError/ValueError for bad arguments."""
        if not self.started:
            raise BusError("the app bus isn't started")
        if not SLUG_RE.match(to or "") or to == self.slug:
            raise ValueError(f"bad receiver {to!r}")
        if not KIND_RE.match(kind or "") or kind in RESERVED_KINDS:
            raise ValueError(f"bad kind {kind!r}")
        if not isinstance(data, dict):
            raise ValueError("data must be a dict")
        if ref is not None and (not isinstance(ref, str) or not ref or len(ref) > MAX_REF):
            raise ValueError("ref must be a short string")
        if reply_to is not None and not ID_RE.match(str(reply_to)):
            raise ValueError("bad reply_to")
        if not isinstance(kv, int) or kv < 1:
            raise ValueError("bad kv")
        if expires_in <= timedelta(0) or expires_in > MAX_EXPIRES_IN:
            raise ValueError("expires_in must be more than 0 and at most 7 days")
        if reply_to is None and not self.available(to, kind):
            raise NotAvailable(f"{to} can't take {kind}")
        try:
            env = self._envelope(to, kind, data, kv=kv, ref=ref, reply_to=reply_to, expires_in=expires_in)
            body = _dumps(env)
        except (TypeError, ValueError) as e:
            raise ValueError(f"data isn't JSON: {e}") from None
        if len(body.encode("utf-8")) > MAX_EVENT_BYTES:
            raise TooLarge(f"{kind} message is {len(body.encode('utf-8'))} bytes (at most {MAX_EVENT_BYTES})")
        with self._tx() as conn:
            conn.execute("INSERT INTO bus_outbox (id, to_app, kind, body, ref, attempts, next_try, expires, state,"
                         " created_at) VALUES (?,?,?,?,?,0,?,?,'pending',?)",
                         (env["id"], to, kind, body, ref, env["sent"], env["expires"], env["sent"]))
        self._log("queued", env)
        self._wake.set()
        return env["id"]

    def _post(self, env: dict) -> bool:
        try:
            if self.post is not None:
                status = self.post(env)
            else:
                status = self._http_post(env)
        except Exception as e:
            logger.debug("Sending %s failed: %s", env.get("id"), type(e).__name__)
            return False
        if isinstance(status, bool):
            return status
        return isinstance(status, int) and 200 <= status < 300

    def _http_post(self, env: dict) -> int | None:
        req = urllib.request.Request(f"{self.api_base}/events/{EVENT_TYPE}", data=_dumps(env).encode("utf-8"),
                                     method="POST", headers={"Authorization": f"Bearer {self.token}",
                                                             "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return None

    @staticmethod
    def _log(what: str, env: dict, result: str | None = None) -> None:
        logger.info("bus %s id=%s from=%s to=%s kind=%s result=%s", what, env.get("id"), env.get("from"),
                    env.get("to"), env.get("kind"), result or "-")

    # -- the outbox ---------------------------------------------------------------------
    @staticmethod
    def retry_delay(attempts: int) -> int:
        """Seconds until the next try after `attempts` sends: 30 s, 2 min, 10 min, then every 30 min."""
        return RETRY_DELAYS[attempts - 1] if 1 <= attempts <= len(RETRY_DELAYS) else RETRY_EVERY

    def run_outbox_once(self, resend_all: bool = False) -> dict:
        """Expire, send what's due, prune old rows and say hello every 6 hours. Called by the outbox
        thread (or the app's housekeeping); `resend_all` sends every pending message now (start-up)."""
        if not self.started:
            return {"sent": 0, "expired": 0, "failed": 0, "limited": 0}
        with self._outbox_lock:
            return self._run_outbox(resend_all)

    def _run_outbox(self, resend_all: bool) -> dict:
        now = self._now()
        stats = {"sent": 0, "expired": 0, "failed": 0, "limited": 0}
        # 1. expired, unanswered messages: tell the feature
        with self._conn() as conn:
            gone = conn.execute("SELECT id, to_app, kind, ref FROM bus_outbox WHERE state = 'pending' AND expires <= ?",
                                (_iso(now),)).fetchall()
        for mid, to_app, kind, ref in gone:
            try:
                with self._tx() as conn:
                    cur = conn.execute("UPDATE bus_outbox SET state = 'expired', result = ? WHERE id = ? AND "
                                       "state = 'pending'", (_dumps({"reason": "expired"}), mid))
                    if cur.rowcount:
                        msg = Message({"id": None, "v": 1, "from": to_app, "to": self.slug, "kind": "nack", "kv": 1,
                                       "reply_to": mid, "ref": ref, "data": {"reason": "expired",
                                                                              "detail": "no answer"}},
                                      answers=kind, local=True)
                        self._run_reply_callbacks(msg, conn)
                stats["expired"] += 1
                logger.info("bus expired id=%s from=%s to=%s kind=%s result=expired", mid, self.slug, to_app, kind)
            except Exception:
                logger.exception("bus: a reply callback failed for expired message %s", mid)
        # 2. what's due
        with self._conn() as conn:
            if resend_all:
                due = conn.execute("SELECT id, to_app, body, attempts FROM bus_outbox WHERE state = 'pending' "
                                   "ORDER BY id").fetchall()
            else:
                due = conn.execute("SELECT id, to_app, body, attempts FROM bus_outbox WHERE state = 'pending' "
                                   "AND next_try <= ? ORDER BY next_try, id", (_iso(now),)).fetchall()
        for mid, to_app, body, attempts in due:
            if self._stop.is_set():
                break
            if not self._send_window.allow(to_app, now.timestamp(), self.send_limit):
                stats["limited"] += 1
                continue                                    # stays due; sent on a later run
            env = json.loads(body)
            ok = self._post(env)
            attempts += 1
            with self._tx() as conn:                     # the ack may already be in (another thread): still count it
                conn.execute("UPDATE bus_outbox SET attempts = ?, "
                             "next_try = CASE WHEN state = 'pending' THEN ? ELSE next_try END WHERE id = ?",
                             (attempts, _iso(now + timedelta(seconds=self.retry_delay(attempts))), mid))
            stats["sent" if ok else "failed"] += 1
            self._log("sent" if ok else "send-failed", env, f"attempt {attempts}")
        # 3. housekeeping
        with self._tx() as conn:
            conn.execute("DELETE FROM bus_seen WHERE seen_at < ?", (_iso(now - SEEN_KEEP),))
            conn.execute("DELETE FROM bus_outbox WHERE state != 'pending' AND created_at < ?",
                         (_iso(now - OUTBOX_KEEP),))
        if self.connected and (self._last_hello is None or now - self._last_hello >= HELLO_EVERY):
            self._send_hello("*")
        return stats

    def _outbox_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.outbox_interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.run_outbox_once()
            except Exception:
                logger.exception("bus: outbox run failed")

    # -- receiving ------------------------------------------------------------------------
    def _on_connect(self, first: bool) -> None:
        if first:
            self._send_hello("*")
            self._post_direct("*", "who", {})
            self.run_outbox_once(resend_all=True)           # restart: re-send what's still waiting
        else:
            self.run_outbox_once()

    def _post_direct(self, to: str, kind: str, data: dict, *, reply_to=None, ref=None,
                     expires_in=timedelta(hours=1)) -> bool:
        """Send without the outbox (hello, who, ack, nack)."""
        env = self._envelope(to, kind, data, reply_to=reply_to, ref=ref, expires_in=expires_in)
        ok = self._post(env)
        self._log("sent" if ok else "send-failed", env, data.get("reason") if kind == "nack" else None)
        return ok

    def _send_hello(self, to: str) -> None:
        self._last_hello = self._now() if to == "*" else self._last_hello
        self._post_direct(to, "hello", {"name": self.name, "version": self.version, "can": self.can,
                                        "wants": self.wants})

    def _on_event(self, event: dict) -> None:
        try:
            self.receive(event.get("data"))
        except Exception:
            logger.exception("bus: handling a message failed")

    def receive(self, env) -> None:
        """Handle one household_apps event's data (called from the WebSocket thread)."""
        if not isinstance(env, dict) or not self.started:
            return
        frm, to, kind, mid = env.get("from"), env.get("to"), env.get("kind"), env.get("id")
        if frm == self.slug or to not in (self.slug, "*"):
            return
        if not isinstance(frm, str) or not SLUG_RE.match(frm) or not isinstance(mid, str) or not ID_RE.match(mid):
            logger.info("bus ignored a message without a valid id/from (to=%s kind=%s)", to,
                        kind if isinstance(kind, str) else "?")
            return
        if to == "*" and kind not in ("hello", "who"):
            return
        if kind in ("ack", "nack"):
            if env.get("v") == ENVELOPE_VERSION:
                self._on_answer(env)
        elif kind == "hello":
            if env.get("v") == ENVELOPE_VERSION:
                self._on_hello(env)
        elif kind == "who":
            if env.get("v") == ENVELOPE_VERSION:
                self._answer_hello(frm)
        else:
            self._on_request(env)

    def _on_hello(self, env: dict) -> None:
        d = env.get("data") if isinstance(env.get("data"), dict) else {}
        can = [k for k in (d.get("can") or []) if isinstance(k, str) and KIND_RE.match(k)][:100] \
            if isinstance(d.get("can"), list) else []
        name = d.get("name") if isinstance(d.get("name"), str) else None
        version = d.get("version") if isinstance(d.get("version"), str) else None
        now = self._now()
        with self._tx() as conn:
            row = conn.execute("SELECT last_seen FROM bus_apps WHERE slug = ?", (env["from"],)).fetchone()
            conn.execute("INSERT INTO bus_apps (slug, name, version, can, last_seen) VALUES (?,?,?,?,?) "
                         "ON CONFLICT(slug) DO UPDATE SET name = excluded.name, version = excluded.version, "
                         "can = excluded.can, last_seen = excluded.last_seen",
                         (env["from"], (name or "")[:80] or None, (version or "")[:40] or None, _dumps(can),
                          _iso(now)))
        self._log("received", env, "hello")
        prev = _parse_time(row[0]) if row else None
        if env.get("to") == "*" and (prev is None or now - prev >= HELLO_ANSWER_EVERY):
            self._answer_hello(env["from"])

    def _answer_hello(self, app: str) -> None:
        now = self._now()
        with self._lock:
            last = self._hello_answered.get(app)
            if last is not None and now - last < HELLO_ANSWER_EVERY:
                return
            self._hello_answered[app] = now
        self._send_hello(app)

    def _matching_callbacks(self, msg: Message) -> list:
        with self._lock:
            cbs = list(self._reply_cbs)
        out = []
        for key, cb in cbs:
            if key in (msg.kind, msg.answers) or (isinstance(msg.ref, str) and msg.ref.startswith(key)):
                if cb not in out:
                    out.append(cb)
        return out

    def _run_reply_callbacks(self, msg: Message, conn) -> int:
        cbs = self._matching_callbacks(msg)
        for cb in cbs:
            cb(msg, conn)
        return len(cbs)

    def _on_answer(self, env: dict) -> None:
        reply_to = env.get("reply_to")
        if not isinstance(reply_to, str):
            return
        data = env.get("data") if isinstance(env.get("data"), dict) else {}
        reason = data.get("reason") if env["kind"] == "nack" else None
        if env["kind"] == "nack" and reason not in NACK_REASONS:
            reason = "invalid"
        try:
            with self._tx() as conn:
                row = conn.execute("SELECT to_app, kind, ref, state FROM bus_outbox WHERE id = ?",
                                   (reply_to,)).fetchone()
                if not row or row[0] != env["from"] or row[3] != "pending":
                    return                                  # not mine, or answered already
                if reason == "busy":
                    self._log("received", env, "nack busy (will retry)")
                    return
                result = {"result": data.get("result")} if env["kind"] == "ack" else \
                    {"reason": reason, **({"detail": str(data["detail"])[:200]} if data.get("detail") else {})}
                conn.execute("UPDATE bus_outbox SET state = ?, result = ? WHERE id = ?",
                             ("acked" if env["kind"] == "ack" else "nacked", _dumps(result), reply_to))
                msg = Message(dict(env, ref=row[2]), answers=row[1])
                self._run_reply_callbacks(msg, conn)
            self._log("received", env, "ack" if env["kind"] == "ack" else f"nack {reason}")
        except Exception:
            logger.exception("bus: a reply callback failed for %s (the answer will come again)", reply_to)

    def _validate(self, env: dict) -> str | None:
        """The name of the first bad envelope field, or None."""
        if env.get("to") != self.slug:
            return "to"
        if not isinstance(env.get("kind"), str) or not KIND_RE.match(env["kind"]):
            return "kind"
        if not isinstance(env.get("kv"), int) or isinstance(env.get("kv"), bool) or env["kv"] < 1:
            return "kv"
        if env.get("reply_to") is not None and (not isinstance(env["reply_to"], str) or not ID_RE.match(env["reply_to"])):
            return "reply_to"
        if env.get("ref") is not None and (not isinstance(env["ref"], str) or len(env["ref"]) > MAX_REF):
            return "ref"
        if _parse_time(env.get("sent")) is None:
            return "sent"
        if _parse_time(env.get("expires")) is None:
            return "expires"
        if not isinstance(env.get("data"), dict):
            return "data"
        return None

    def _answer(self, env: dict, reply: dict) -> None:
        """Send a stored answer ({"kind": "ack"|"nack", "data": …}) for a received message."""
        self._post_direct(env["from"], reply["kind"], reply.get("data") or {}, reply_to=env["id"],
                          ref=env.get("ref") if isinstance(env.get("ref"), str) else None,
                          expires_in=timedelta(days=1))

    def _on_request(self, env: dict) -> None:
        mid, frm = env["id"], env["from"]
        now = self._now()
        kind = env.get("kind") if isinstance(env.get("kind"), str) else "?"
        with self._conn() as conn:
            row = conn.execute("SELECT result FROM bus_seen WHERE id = ?", (mid,)).fetchone()
        if row:                                             # duplicate: same answer again, no second action
            reply = json.loads(row[0]) if row[0] else {"kind": "ack", "data": {}}
            self._log("duplicate", env, reply.get("kind"))
            self._answer(env, reply)
            return

        def refuse(reason, detail=None, record=True):
            reply = {"kind": "nack", "data": Nack(reason, detail).data()}
            if record:
                with self._tx() as c:
                    c.execute("INSERT OR IGNORE INTO bus_seen (id, from_app, kind, result, seen_at) "
                              "VALUES (?,?,?,?,?)", (mid, frm, kind[:64], _dumps(reply), _iso(now)))
            self._log("received", env, f"nack {reason}")
            self._answer(env, reply)

        if env.get("v") != ENVELOPE_VERSION:
            return refuse("unsupported_version", "envelope")
        bad = self._validate(env)
        if bad:
            return refuse("invalid", bad)
        if _parse_time(env["expires"]) <= now:
            return refuse("expired")
        if not self._recv_window.allow(frm, now.timestamp(), self.receive_limit):
            return refuse("busy", "too many messages", record=False)
        msg = Message(env)
        with self._lock:
            h = self._handlers.get(kind)
        if h is None and not ((msg.reply_to or msg.ref) and self._matching_callbacks(msg)):
            return refuse("unsupported_kind")
        if h is not None and env["kv"] not in h[1]:
            return refuse("unsupported_version")
        try:
            with self._tx() as conn:
                if conn.execute("SELECT 1 FROM bus_seen WHERE id = ?", (mid,)).fetchone():
                    return
                conn.execute("SAVEPOINT bus_handler")
                try:
                    if h is not None:
                        result = h[0](msg, conn)
                    else:
                        self._run_reply_callbacks(msg, conn)
                        result = None
                except Nack as n:
                    result = n
                if result is not None and not isinstance(result, (dict, Nack)):
                    raise TypeError(f"handler for {kind} returned {type(result).__name__}")
                if isinstance(result, dict) and event_size(self._envelope(frm, "ack", {"result": result})) \
                        > MAX_EVENT_BYTES:
                    result = Nack("invalid", "answer too large")
                if isinstance(result, Nack):
                    conn.execute("ROLLBACK TO bus_handler")
                    reply = {"kind": "nack", "data": result.data()}
                else:
                    reply = {"kind": "ack", "data": {} if result is None else {"result": result}}
                conn.execute("RELEASE bus_handler")
                busy = isinstance(result, Nack) and result.reason == "busy"
                if not busy:
                    conn.execute("INSERT INTO bus_seen (id, from_app, kind, result, seen_at) VALUES (?,?,?,?,?)",
                                 (mid, frm, kind, _dumps(reply), _iso(now)))
        except Exception:
            logger.exception("bus: the %s handler failed for id=%s (no answer; the sender will re-send)", kind, mid)
            return
        if reply["kind"] == "ack":
            for fn in msg._after:
                try:
                    fn()
                except Exception:
                    logger.exception("bus: an after-commit step failed for id=%s", mid)
        self._log("received", env, reply["kind"] if reply["kind"] == "ack" else f"nack {reply['data']['reason']}")
        self._answer(env, reply)


# ---------------------------------------------------------------------------
# the app's bus (module-level API)
# ---------------------------------------------------------------------------
default = AppBus()
start = default.start
stop = default.stop
available = default.available
apps = default.apps
send = default.send
handler = default.handler
on_reply = default.on_reply
run_outbox_once = default.run_outbox_once
configure = default.configure
