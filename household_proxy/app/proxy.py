"""One HTTP exchange through the proxy (spec/SPEC.md §3.1, §3.2, §4): the browser's request cleaned and sent to the
site, the answer's headers cleaned and its body rewritten (HTML / CSS up to 5 MB) or streamed through untouched.

Never forwarded to a site: Home Assistant's identity headers (X-Remote-User-*), X-Ingress-Path, X-Hass-*, the
Supervisor's X-Forwarded-* / Forwarded / X-Real-IP, hop-by-hop headers, and every cookie that isn't this site's own:
a site's cookies are kept in the browser as hp_<slug>_<name> (rewrite.set_cookie), and only those are passed on, with
the prefix taken off — Home Assistant's own cookies (ingress_session) and other sites' never reach it. A header name
with "_" is compared as if it were "-" (some servers treat them alike), so Remote_User can't stand in for Remote-User.
Never forwarded to the browser: X-Frame-Options and CSP frame-ancestors (the site is shown in a frame), headers that
would act on Home Assistant's own address (Strict-Transport-Security, Clear-Site-Data, Alt-Svc, NEL, Report-To,
Service-Worker-Allowed), hop-by-hop headers. The extra headers an admin set are added here and never sent back.
"""
import asyncio
import logging
import re
import threading
import zlib
from urllib.parse import unquote, urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from . import config, decoding, netguard, rewrite, settings, sites

logger = logging.getLogger("proxy")

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
              "transfer-encoding", "upgrade"}
# Header sign-in names a site may trust: only ever set by the proxy itself (Pass the viewer's name), never by a browser.
SIGN_IN_HEADERS = {"remote-user", "remote-name", "remote-email", "remote-groups", "x-forwarded-user",
                   "x-forwarded-email", "x-forwarded-preferred-username", "x-auth-request-user",
                   "x-auth-request-email", "x-webauth-user"}
REQUEST_DROP = HOP_BY_HOP | SIGN_IN_HEADERS | {"host", "content-length", "x-ingress-path", "forwarded", "x-real-ip",
                                               "cookie", "accept-encoding", "origin", "referer"}
REQUEST_DROP_PREFIXES = ("x-remote-user-", "x-hass-", "x-forwarded-", "x-supervisor")
RESPONSE_DROP = HOP_BY_HOP | {"x-frame-options", "strict-transport-security", "public-key-pins",
                              "public-key-pins-report-only", "clear-site-data", "alt-svc", "nel", "report-to",
                              "reporting-endpoints", "service-worker-allowed", "content-length",
                              "content-security-policy", "content-security-policy-report-only", "location",
                              "content-location", "refresh", "link", "set-cookie"}
METHODS_WITH_BODY = {"POST", "PUT", "PATCH", "DELETE"}
OWN_FRAME_POLICY = "frame-ancestors 'self'"       # only Home Assistant's own pages may frame a proxied page


# At most this many requests (streams included, until they end) per person and site at once, and this many
# rewrites (HTML/CSS parsing, a few MB each) in the whole app at once: one person or one page can't take everything.
MAX_IN_FLIGHT = 48
REWRITE_SLOTS = 3
_rewrite_slots = threading.BoundedSemaphore(REWRITE_SLOTS)


class TooLarge(Exception):
    pass


class InFlight:
    """Counts what a person has open on a site (requests, streams, websockets). Used on the event loop only."""

    def __init__(self, limit: int):
        self.limit = limit
        self.open: dict = {}

    def enter(self, key) -> bool:
        n = self.open.get(key, 0)
        if n >= self.limit:
            return False
        self.open[key] = n + 1
        return True

    def leave(self, key) -> None:
        n = self.open.get(key, 0) - 1
        if n > 0:
            self.open[key] = n
        else:
            self.open.pop(key, None)


IN_FLIGHT = InFlight(MAX_IN_FLIGHT)


# --------------------------------------------------------------------------------------------- clients

_clients: dict = {}     # (event loop, site id) -> (site version, client)


def _transport(site: sites.Site) -> httpx.AsyncHTTPTransport:
    # every connection goes through the address check (netguard.GuardedBackend), and that is checked
    return netguard.guarded_transport(site.tls_pem, site.allow_public,
                                      limits=httpx.Limits(max_connections=50, max_keepalive_connections=10))


async def client(site: sites.Site) -> httpx.AsyncClient:
    """One pooled client per site version (a saved change makes a new one; the old one is closed, and so are the
    clients of deleted sites). Nothing awaits between the lookup and the store, so two requests can't both make one."""
    loop = asyncio.get_running_loop()
    if _prune["due"]:
        _drop_deleted(loop)
    key = (id(loop), site.id)
    c = _clients.get(key)
    if c is not None and c[0] == site.version:
        return c[1]
    new = httpx.AsyncClient(transport=_transport(site), follow_redirects=False, trust_env=False)
    _clients[key] = (site.version, new)
    if c is not None:
        loop.create_task(c[1].aclose())
    return new


_prune = {"due": False}
sites.on_invalidate(lambda: _prune.__setitem__("due", True))      # sites changed: look for deleted ones once


def _drop_deleted(loop) -> None:
    """Close the clients of sites that no longer exist."""
    known = sites.peek()
    if known is None:                       # not loaded right now: next time
        return
    _prune["due"] = False
    alive = {s.id for s in known.values()}
    for key in [k for k in _clients if k[0] == id(loop) and k[1] not in alive]:
        loop.create_task(_clients.pop(key)[1].aclose())


async def close_all() -> None:
    for _, c in list(_clients.values()):
        try:
            await c.aclose()
        except Exception:
            pass
    _clients.clear()


# --------------------------------------------------------------------------------------------- the request

def ha_origin(request) -> tuple[str | None, bool]:
    """Home Assistant's own origin as the browser sees it and whether it is https: from Origin or Referer, else from
    the X-Forwarded-Proto / X-Forwarded-Host that Home Assistant's ingress sets (trusted: only the Supervisor reaches
    this app; they are read here and never passed on). Unknown: (None, True) — Secure cookies are then left as the
    site set them."""
    for h in ("origin", "referer"):
        v = request.headers.get(h)
        if v and v != "null":
            try:
                p = urlsplit(v)
            except ValueError:
                continue
            if p.scheme in ("http", "https") and p.netloc:
                return f"{p.scheme}://{p.netloc}", p.scheme == "https"
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto in ("http", "https"):
        host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        return (f"{proto}://{host}" if host and "/" not in host else None), proto == "https"
    return None, True


def split_path(raw_path: bytes, slug: str) -> str | None:
    """The part of the raw request path after /s/<slug> ("/" at least), or None when it would leave the site's
    path space ("..", "." segments, also percent-encoded)."""
    raw = raw_path.decode("latin-1")
    head = f"/s/{slug}"
    rest = raw[len(head):] or "/"
    if not rest.startswith("/"):
        return None
    for seg in rest.split("/"):
        d = seg
        for _ in range(3):                    # double / triple encoding too
            d = unquote(d)
        if any(part in (".", "..") for part in d.replace("\\", "/").split("/")):
            return None
    return rest


def header_key(name: str) -> str:
    """A header name as compared here: lower case, "_" read as "-" (many servers — and frameworks that turn headers
    into variables — treat Remote_User as Remote-User)."""
    return name.strip().lower().replace("_", "-")


def forward_headers(request, site: sites.Site, user: dict, ctx: rewrite.Ctx) -> list:
    """The request's headers as the site gets them (§3.1)."""
    out = []
    conn_named = {header_key(t) for t in (request.headers.get("connection") or "").split(",") if t.strip()}
    extra = {header_key(n) for n, _ in site.headers}
    for k, v in request.headers.items():
        lk = header_key(k)
        if lk in REQUEST_DROP or lk in conn_named or lk.startswith(REQUEST_DROP_PREFIXES) or lk in extra:
            continue
        out.append((k, v))
    out.append(("Host", request.headers.get("host", "") if site.keep_host else
                site.origin.split("://", 1)[1]))
    out.append(("Accept-Encoding", "gzip, deflate"))      # what the rewriting can read (§3.2)
    cookies = rewrite.site_cookies(request.headers.get("cookie"), site.slug)
    if cookies:
        out.append(("Cookie", cookies))
    origin = request.headers.get("origin")
    if origin:
        out.append(("Origin", site.origin if origin != "null" else "null"))
    referer = request.headers.get("referer")
    if referer:
        out.append(("Referer", _referer(referer, site, ctx)))
    if site.pass_user:
        out.append(("Remote-User", user.get("username") or user["id"]))
        out.append(("X-Forwarded-User", user.get("username") or user["id"]))
        out.append(("Remote-Name", user.get("name") or ""))
    out = [(k, v) for k, v in out if header_key(k) not in extra]
    out += list(site.headers)
    return out


def _referer(value: str, site: sites.Site, ctx: rewrite.Ctx) -> str:
    """Home Assistant's address for a proxied page → the same page on the site's own address."""
    try:
        p = urlsplit(value)
    except ValueError:
        return site.origin + "/"
    path = p.path
    if path.startswith(ctx.prefix + "/") or path == ctx.prefix:
        return site.origin + (path[len(ctx.prefix):] or "/") + (f"?{p.query}" if p.query else "")
    return site.origin + "/"


async def _body(chunks, limit: int, seen: int = 0, first: bytes = b""):
    if first:
        yield first
    async for chunk in chunks:
        seen += len(chunk)
        if seen > limit:
            raise TooLarge()
        if chunk:
            yield chunk


async def request_body(request: Request, limit: int, collect: int):
    """The body to send on, and its Content-Length if the browser gave one. A body sent without a length (chunked) is
    collected up to `collect` bytes first, so the site gets its size (some sites refuse chunked uploads); a bigger one
    streams on as it arrives. Raises TooLarge past `limit`."""
    length = request.headers.get("content-length")
    if length and length.isdigit():
        if int(length) > limit:
            raise TooLarge()
        return _body(request.stream(), limit), length
    chunks = request.stream().__aiter__()
    buf = bytearray()
    async for chunk in chunks:
        buf += chunk
        if len(buf) > limit:
            raise TooLarge()
        if len(buf) > collect:
            return _body(chunks, limit, len(buf), bytes(buf)), None      # streams on, chunked
    return bytes(buf), None                                              # httpx sends its Content-Length


# --------------------------------------------------------------------------------------------- the response

def response_headers(upstream: httpx.Response, ctx: rewrite.Ctx, site: sites.Site, rewritten: bool,
                     need_script: bool) -> tuple[list, str | None]:
    """The answer's headers as the browser gets them (§3.2), and the nonce the shim's tag needs (if any)."""
    out, nonce = [], None
    conn_named = {t.strip().lower() for t in (upstream.headers.get("connection") or "").split(",") if t.strip()}
    for k, v in upstream.headers.multi_items():
        lk = k.lower()
        if lk in conn_named:
            continue
        if lk in ("content-encoding",) and rewritten:
            continue
        if lk in ("content-security-policy", "content-security-policy-report-only"):
            if site.strip_csp:
                continue
            policy, n = rewrite.csp(v, ctx, need_script)
            if lk == "content-security-policy":
                nonce = nonce or n
            if policy:
                out.append((k, policy))
            continue
        if lk == "location" or lk == "content-location":
            out.append((k, rewrite.url(v, ctx)))
            continue
        if lk == "refresh":
            out.append((k, rewrite.refresh(v, ctx)))
            continue
        if lk == "link":
            out.append((k, rewrite.link_header(v, ctx)))
            continue
        if lk == "set-cookie":
            out.append((k, rewrite.set_cookie(v, ctx)))
            continue
        if lk in RESPONSE_DROP:
            continue
        out.append((k, v))
    header_rules = site.header_rules(upstream.headers.get("content-type"))
    if header_rules:
        out = [(k, _apply_all(header_rules, v, ctx.prefix)) for k, v in out]
    out.append(("Content-Security-Policy", OWN_FRAME_POLICY))
    if not rewritten and "content-length" in upstream.headers:
        out.append(("Content-Length", upstream.headers["content-length"]))     # the raw bytes are passed on
    return out, nonce


def _apply_all(rules: list, value: str, prefix: str) -> str:
    for r in rules:
        value = r.apply(value, prefix)
    return value.replace("\r", " ").replace("\n", " ")      # a rule can never add a header line


def _raw(headers: list) -> list:
    return [(k.lower().encode("latin-1"), str(v).encode("latin-1", "replace")) for k, v in headers]


def error_page(status: int, title: str, detail: str) -> Response:
    """The proxy's own answer when the site can't be reached (a small page: the frame shows it)."""
    import html as h
    body = (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{h.escape(title)}</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'></head>"
            "<body style='font-family:system-ui,sans-serif;padding:2rem;max-width:40rem;color:#ddd;background:#1c1c1f'>"
            f"<h2>{h.escape(title)}</h2><p>{h.escape(detail)}</p></body></html>")
    return Response(body, status_code=status, media_type="text/html; charset=utf-8",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                                        "frame-ancestors 'self'",
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def describe(exc: Exception) -> tuple[int, str]:
    msg = str(exc) or exc.__class__.__name__
    if isinstance(exc, httpx.ConnectTimeout):
        return 504, "The site didn't answer in time (connecting)."
    if isinstance(exc, httpx.TimeoutException):
        return 504, "The site didn't answer in time."
    if isinstance(exc, httpx.ConnectError):
        low = msg.lower()
        if "certificate" in low or "ssl" in low:
            return 502, ("The site's certificate wasn't accepted. If it is a self-signed certificate, an admin can "
                         "run the Test and choose “Trust this certificate”.")
        if "refused" in low or "all connection attempts failed" in low:
            return 502, "The site refused the connection or couldn't be reached (is it on and running, and is the " \
                        "address and port right?)."
        return 502, f"Couldn't connect to the site: {msg}"
    return 502, f"The site couldn't be reached: {msg}"


# Home Assistant's ingress re-encodes the query string on its way to the app (aiohttp/yarl), escaping characters a
# browser sends as they are: "target[type]=Server" arrives as "target%5Btype%5D=Server". Most sites decode both the
# same, but some read brackets only literally — Komodo's terminal ("/ws/terminal?target[type]=…", parsed with
# serde_qs's strict mode) refuses the escaped form, so its terminal closed straight away. Put them back.
_INGRESS_ESCAPED = re.compile(r"%(5B|5D|7B|7D|7C|5E|60)", re.IGNORECASE)


def site_query(scope) -> str:
    """The request's query string as the browser sent it (before Home Assistant's ingress re-encoded it)."""
    raw = scope.get("query_string", b"").decode("latin-1")
    return _INGRESS_ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), raw) if "%" in raw else raw


def ascii_url(text: str) -> str:
    """A raw request path or query (bytes read as latin-1) with every byte outside printable ASCII percent-encoded,
    so the site gets exactly the bytes the browser sent (and no client chokes on them)."""
    if all(0x20 < ord(ch) < 0x7f for ch in text):
        return text
    return "".join(ch if 0x20 < ord(ch) < 0x7f else f"%{ord(ch) & 0xff:02X}" for ch in text)


def _too_large() -> Response:
    return error_page(413, "Too large", f"Uploads through the proxy are limited to "
                                        f"{settings.get('max_upload_mb')} MB (App settings).")


async def exchange(request: Request, site: sites.Site, user: dict, ctx: rewrite.Ctx, rest: str,
                   log_access=None) -> Response:
    """Send the request to the site and give back its answer."""
    method = request.method.upper()
    query = ascii_url(site_query(request.scope))
    rest = ascii_url(rest)
    target = site.origin + rest + (f"?{query}" if query else "")
    _, secure = ha_origin(request)
    ctx.secure = secure
    headers = forward_headers(request, site, user, ctx)
    limit = int(settings.get("max_upload_mb")) * 1024 * 1024
    content = None
    if method in METHODS_WITH_BODY or request.headers.get("content-length") or \
            request.headers.get("transfer-encoding"):
        collect = min(limit, int(settings.get("collect_upload_mb")) * 1024 * 1024)
        try:
            content, length = await request_body(request, limit, collect)
        except TooLarge:
            return _too_large()
        if length is not None:
            headers.append(("Content-Length", length))
    timeout = site.timeout_s or int(settings.get("request_timeout_s"))
    c = await client(site)
    try:
        req = c.build_request(method, target, headers=headers, content=content,
                              timeout=httpx.Timeout(timeout, connect=min(timeout, 15)))
        upstream = await c.send(req, stream=True)
    except TooLarge:
        return _too_large()
    except (httpx.HTTPError, netguard.TargetError, OSError) as e:
        status, detail = describe(e)
        logger.info("%s: %s %s → %s", site.slug, method, rest.split("?")[0][:200], detail)
        if log_access:
            log_access(status, 0)
        return error_page(status, f"{site.name} isn't answering", detail)
    return await _answer(upstream, site, ctx, method, log_access, rest + (f"?{query}" if query else ""))


def _rewrite(kind: str, data: bytearray, content_type: str | None, ctx: rewrite.Ctx) -> bytes:
    """One rewrite (in a worker thread), at most REWRITE_SLOTS at once."""
    with _rewrite_slots:
        return rewrite.body(kind, data, content_type, ctx)


def should_rewrite(kind: str | None, rules: list, content_type: str | None, encoding: str | None,
                   length: str | None, method: str, status: int) -> bool:
    """Whether an answer is read whole and rewritten (HTML, CSS, or a type a rule names) instead of streamed: never
    a HEAD or a body-less answer, never part of a file (206), never live events (text/event-stream), never one that
    says it is over the cap; a rule's own target (not HTML or CSS) only when it says its length."""
    main = (content_type or "").split(";")[0].strip().lower()
    known = bool(length and length.isdigit())
    if not (kind or rules) or not decoding.readable(encoding) or method == "HEAD" or status in (204, 206, 304):
        return False
    if main == "text/event-stream" or (known and int(length) > rewrite.MAX_REWRITE_BYTES):
        return False
    return bool(kind) or known


async def _answer(upstream: httpx.Response, site: sites.Site, ctx: rewrite.Ctx, method: str, log_access,
                  site_path: str) -> Response:
    content_type = upstream.headers.get("content-type")
    kind = rewrite.kind_of(content_type)
    rules = site.body_rules(content_type)
    ctx.body_rules = [(lambda text, r=r: r.apply(text, ctx.prefix)) for r in rules]
    ctx.strip_script_integrity = any(not r.types or any("javascript" in t for t in r.types)
                                     for r in site.rules if r.where == "body")
    ctx.strip_csp = site.strip_csp
    encoding = upstream.headers.get("content-encoding")
    length = upstream.headers.get("content-length")
    if should_rewrite(kind, rules, content_type, encoding, length, method, upstream.status_code):
        buf = bytearray()
        it = decoding.pieces(upstream.aiter_raw(), encoding)        # bounded pieces: no decompression bomb
        too_big = False
        try:
            async for chunk in it:
                buf += chunk
                if len(buf) > rewrite.MAX_REWRITE_BYTES:
                    too_big = True
                    break
        except (httpx.HTTPError, ValueError, zlib.error) as e:
            await upstream.aclose()
            status, detail = describe(e) if isinstance(e, httpx.HTTPError) else (502, "The site's answer couldn't be read.")
            return error_page(status, f"{site.name} isn't answering", detail)
        if not too_big:
            await upstream.aclose()
            headers, nonce = response_headers(upstream, ctx, site, True, kind == "html")
            if kind == "html":
                ctx.nonce = nonce
                ctx.shim = rewrite.shim_tag(ctx, config.APP_VERSION, site.spa_mode, site_path)
            # parsing a page of a few MB takes a moment: off the event loop, so other requests keep flowing
            data = await asyncio.to_thread(_rewrite, kind or "", buf, content_type, ctx)
            del buf
            headers.append(("Content-Length", str(len(data))))
            r = Response(content=b"", status_code=upstream.status_code)
            r.body = data
            r.raw_headers = _raw(headers)
            if log_access:
                log_access(upstream.status_code, len(data))
            return r
        # too big to rewrite after all: what was read goes out as it is, then the rest (decoded) streams after it
        headers, _ = response_headers(upstream, ctx, site, True, False)
        headers = [(k, v) for k, v in headers if k.lower() != "content-length"]
        return _stream(upstream, headers, site, log_access, first=bytes(buf), rest=it)
    headers, _ = response_headers(upstream, ctx, site, False, False)
    _no_read_limit(upstream)
    return _stream(upstream, headers, site, log_access)


def _no_read_limit(upstream: httpx.Response) -> None:
    """Once the site has started answering, a streamed body (downloads, server-sent events, logs) may take as long
    as it needs: the request timeout covers only the wait for the answer to start."""
    t = upstream.request.extensions.get("timeout")
    if isinstance(t, dict):
        t["read"] = None


def _stream(upstream: httpx.Response, headers: list, site: sites.Site, log_access, first: bytes = b"",
            rest=None) -> StreamingResponse:
    async def gen():
        sent = 0
        status = upstream.status_code
        try:
            if first:
                sent += len(first)
                yield first
            source = rest if rest is not None else upstream.aiter_raw()
            async for chunk in source:
                sent += len(chunk)
                yield chunk
        except (httpx.HTTPError, zlib.error) as e:
            logger.info("%s: the answer was cut off: %s", site.slug, e)
        finally:
            await upstream.aclose()
            if log_access:
                log_access(status, sent)

    r = StreamingResponse(gen(), status_code=upstream.status_code)
    r.raw_headers = _raw(headers)
    return r


# Access-log lines are kept in memory and written together (flush_access_log: every few seconds, before the log is
# read, and at shutdown), not one commit per request.
_log_lines: list = []
_log_lock = threading.Lock()
LOG_BUFFER_MAX = 10000


def log_writer(site: sites.Site, user: dict, method: str, path: str):
    """The access-log line writer for one request or websocket (None when the site's log is off): write(status,
    bytes, seconds=None). No query strings, no bodies, no header values (§4.6). Cheap: safe on the event loop."""
    if not site.access_log:
        return None
    clean = path.split("?")[0][:300]

    def write(status: int, size: int, seconds: float | None = None) -> None:
        line = (site.id, config.iso(), user["id"], method, clean, status, size,
                None if seconds is None else round(seconds, 1))
        with _log_lock:
            if len(_log_lines) >= LOG_BUFFER_MAX:
                del _log_lines[:LOG_BUFFER_MAX // 10]
                logger.warning("access log lines dropped: the database didn't keep up")
            _log_lines.append(line)
    return write


def flush_access_log() -> int:
    """Write the waiting access-log lines; how many."""
    from . import db
    with _log_lock:
        lines = _log_lines[:]
        del _log_lines[:]
    if not lines:
        return 0
    try:
        with db.get_conn() as conn:
            known = {r[0] for r in conn.execute("SELECT id FROM sites")}
            conn.executemany("INSERT INTO access_log (site_id, at, user_id, method, path, status, bytes, seconds) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [x for x in lines if x[0] in known])
    except Exception:
        logger.exception("writing the access log failed")
    return len(lines)

