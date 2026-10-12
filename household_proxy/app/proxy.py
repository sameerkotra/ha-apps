"""One HTTP exchange through the proxy (spec/SPEC.md §3.1, §3.2, §4): the browser's request cleaned and sent to the
site, the answer's headers cleaned and its body rewritten (HTML / CSS up to 5 MB) or streamed through untouched.

Never forwarded to a site: Home Assistant's identity headers (X-Remote-User-*), X-Ingress-Path, X-Hass-*, the
Supervisor's X-Forwarded-* / Forwarded / X-Real-IP, Home Assistant's ingress_session cookie, hop-by-hop headers.
Never forwarded to the browser: X-Frame-Options and CSP frame-ancestors (the site is shown in a frame), headers that
would act on Home Assistant's own address (Strict-Transport-Security, Clear-Site-Data, Alt-Svc, NEL, Report-To,
Service-Worker-Allowed), hop-by-hop headers. The extra headers an admin set are added here and never sent back.
"""
import asyncio
import logging
import time
from urllib.parse import unquote, urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from . import config, netguard, rewrite, settings, sites

logger = logging.getLogger("proxy")

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
              "transfer-encoding", "upgrade"}
REQUEST_DROP = HOP_BY_HOP | {"host", "content-length", "x-ingress-path", "forwarded", "x-real-ip", "cookie",
                             "accept-encoding", "origin", "referer"}
REQUEST_DROP_PREFIXES = ("x-remote-user-", "x-hass-", "x-forwarded-", "x-supervisor")
RESPONSE_DROP = HOP_BY_HOP | {"x-frame-options", "strict-transport-security", "public-key-pins",
                              "public-key-pins-report-only", "clear-site-data", "alt-svc", "nel", "report-to",
                              "reporting-endpoints", "service-worker-allowed", "content-length",
                              "content-security-policy", "content-security-policy-report-only", "location",
                              "content-location", "refresh", "link", "set-cookie"}
HA_COOKIES = {"ingress_session"}
METHODS_WITH_BODY = {"POST", "PUT", "PATCH", "DELETE"}
OWN_FRAME_POLICY = "frame-ancestors 'self'"       # only Home Assistant's own pages may frame a proxied page


class TooLarge(Exception):
    pass


# --------------------------------------------------------------------------------------------- clients

_clients: dict = {}
_clients_lock = asyncio.Lock()


def _transport(site: sites.Site) -> httpx.AsyncHTTPTransport:
    t = httpx.AsyncHTTPTransport(verify=netguard.ssl_context(site.tls_pem), retries=0,
                                 limits=httpx.Limits(max_connections=50, max_keepalive_connections=10))
    # every connection goes through the address check (netguard.GuardedBackend)
    t._pool._network_backend = netguard.GuardedBackend(site.allow_public)
    return t


async def client(site: sites.Site) -> httpx.AsyncClient:
    """One pooled client per site version (a saved change makes a new one; the old one is closed)."""
    c = _clients.get(site.id)
    if c is not None and c[0] == site.version:
        return c[1]
    async with _clients_lock:
        c = _clients.get(site.id)
        if c is not None and c[0] == site.version:
            return c[1]
        new = httpx.AsyncClient(transport=_transport(site), follow_redirects=False, trust_env=False)
        _clients[site.id] = (site.version, new)
    if c is not None:
        asyncio.get_running_loop().create_task(c[1].aclose())
    return new


async def close_all() -> None:
    for _, c in list(_clients.values()):
        try:
            await c.aclose()
        except Exception:
            pass
    _clients.clear()


# --------------------------------------------------------------------------------------------- the request

def ha_origin(request) -> tuple[str | None, bool]:
    """Home Assistant's own origin as the browser sees it (from Origin or Referer) and whether it is https. Unknown:
    (None, True) — Secure cookies are then left as the site set them."""
    for h in ("origin", "referer"):
        v = request.headers.get(h)
        if v and v != "null":
            try:
                p = urlsplit(v)
            except ValueError:
                continue
            if p.scheme in ("http", "https") and p.netloc:
                return f"{p.scheme}://{p.netloc}", p.scheme == "https"
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


def forward_headers(request, site: sites.Site, user: dict, ctx: rewrite.Ctx, ha: str | None) -> list:
    """The request's headers as the site gets them (§3.1)."""
    out = []
    conn_named = {t.strip().lower() for t in (request.headers.get("connection") or "").split(",") if t.strip()}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in REQUEST_DROP or lk in conn_named or lk.startswith(REQUEST_DROP_PREFIXES):
            continue
        out.append((k, v))
    out.append(("Host", request.headers.get("host", "") if site.keep_host else
                site.origin.split("://", 1)[1]))
    out.append(("Accept-Encoding", "gzip, deflate"))      # what the rewriting can read (§3.2)
    cookies = []
    for part in (request.headers.get("cookie") or "").split(";"):
        name = part.split("=", 1)[0].strip()
        if part.strip() and name not in HA_COOKIES:
            cookies.append(part.strip())
    if cookies:
        out.append(("Cookie", "; ".join(cookies)))
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
    names = {n.lower() for n, _ in site.headers}
    out = [(k, v) for k, v in out if k.lower() not in names]
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


async def _body(request: Request, limit: int):
    seen = 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > limit:
            raise TooLarge()
        if chunk:
            yield chunk


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
    out.append(("Content-Security-Policy", OWN_FRAME_POLICY))
    if not rewritten and "content-length" in upstream.headers:
        out.append(("Content-Length", upstream.headers["content-length"]))     # the raw bytes are passed on
    return out, nonce


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


async def exchange(request: Request, site: sites.Site, user: dict, ctx: rewrite.Ctx, rest: str,
                   log_access=None) -> Response:
    """Send the request to the site and give back its answer."""
    method = request.method.upper()
    target = site.origin + rest + (("?" + request.scope["query_string"].decode("latin-1"))
                                   if request.scope.get("query_string") else "")
    ha, secure = ha_origin(request)
    ctx.secure = secure
    headers = forward_headers(request, site, user, ctx, ha)
    limit = int(settings.get("max_upload_mb")) * 1024 * 1024
    content = None
    if method in METHODS_WITH_BODY or request.headers.get("content-length") or \
            request.headers.get("transfer-encoding"):
        length = request.headers.get("content-length")
        if length and length.isdigit():
            if int(length) > limit:
                return error_page(413, "Too large", f"Uploads through the proxy are limited to "
                                                    f"{settings.get('max_upload_mb')} MB (App settings).")
            headers.append(("Content-Length", length))
        content = _body(request, limit)
    timeout = site.timeout_s or int(settings.get("request_timeout_s"))
    c = await client(site)
    started = time.monotonic()
    try:
        req = c.build_request(method, target, headers=headers, content=content,
                              timeout=httpx.Timeout(timeout, connect=min(timeout, 15)))
        upstream = await c.send(req, stream=True)
    except TooLarge:
        return error_page(413, "Too large", f"Uploads through the proxy are limited to "
                                            f"{settings.get('max_upload_mb')} MB (App settings).")
    except (httpx.HTTPError, netguard.TargetError, OSError) as e:
        status, detail = describe(e)
        logger.info("%s: %s %s → %s", site.slug, method, rest.split("?")[0][:200], detail)
        if log_access:
            await asyncio.to_thread(log_access, status, 0)
        return error_page(status, f"{site.name} isn't answering", detail)
    return await _answer(upstream, site, ctx, method, log_access, started)


async def _answer(upstream: httpx.Response, site: sites.Site, ctx: rewrite.Ctx, method: str, log_access,
                  started: float) -> Response:
    kind = rewrite.kind_of(upstream.headers.get("content-type"))
    encoding = (upstream.headers.get("content-encoding") or "identity").lower().strip()
    length = upstream.headers.get("content-length")
    readable = encoding in ("identity", "gzip", "deflate", "x-gzip")
    small = not (length and length.isdigit() and int(length) > rewrite.MAX_REWRITE_BYTES)
    if kind and readable and small and method != "HEAD" and upstream.status_code not in (204, 304):
        buf = bytearray()
        it = upstream.aiter_bytes()
        too_big = False
        try:
            async for chunk in it:
                buf += chunk
                if len(buf) > rewrite.MAX_REWRITE_BYTES:
                    too_big = True
                    break
        except httpx.HTTPError as e:
            await upstream.aclose()
            status, detail = describe(e)
            return error_page(status, f"{site.name} isn't answering", detail)
        if not too_big:
            await upstream.aclose()
            need_script = kind == "html" and ctx.shim is not None
            headers, nonce = response_headers(upstream, ctx, site, True, need_script)
            if nonce:
                ctx.nonce = nonce
            data = rewrite.body(kind, bytes(buf), upstream.headers.get("content-type"), ctx)
            headers.append(("Content-Length", str(len(data))))
            r = Response(content=b"", status_code=upstream.status_code)
            r.body = data
            r.raw_headers = _raw(headers)
            if log_access:
                await asyncio.to_thread(log_access, upstream.status_code, len(data))
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
        except httpx.HTTPError as e:
            logger.info("%s: the answer was cut off: %s", site.slug, e)
        finally:
            await upstream.aclose()
            if log_access:
                await asyncio.to_thread(log_access, status, sent)

    r = StreamingResponse(gen(), status_code=upstream.status_code)
    r.raw_headers = _raw(headers)
    return r


def log_writer(site: sites.Site, user: dict, method: str, path: str):
    """The access-log line writer for one request (None when the site's log is off). No query strings, no bodies,
    no header values (§4.6)."""
    if not site.access_log:
        return None

    def write(status: int, size: int) -> None:
        from . import db
        try:
            with db.get_conn() as conn:
                conn.execute("INSERT INTO access_log (site_id, at, user_id, method, path, status, bytes) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", (site.id, config.iso(), user["id"], method,
                                                              path.split("?")[0][:300], status, size))
        except Exception:
            logger.exception("writing the access log failed")
    return write
