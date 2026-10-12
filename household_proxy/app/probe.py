"""The Test (spec/SPEC.md §5.3): fetch a site's start page through the same pipeline, server-side, and say in plain
words whether it is likely to work — reachable or not (and why), status and time, redirects, frame-blocking headers
(removed by the proxy), how many root paths the rewriting changed, whether the main scripts contain root-path strings
or a client-side router — with a verdict: Should work · Try single-page app mode · Needs a rule · Probably won't work
through Home Assistant (use a tunnel). Also the site's certificate when it isn't accepted (to trust it), and its icon.

Nothing here is sent to a browser except the report (never the extra headers, never a certificate's private parts).
"""
import asyncio
import hashlib
import re
import ssl
import time
import zlib
from urllib.parse import urljoin, urlsplit

import httpx

from . import decoding, netguard, rewrite, sites

MAX_PAGE = 2 * 1024 * 1024
MAX_SCRIPT = 2 * 1024 * 1024
MAX_ICON = 100 * 1024
SCRIPTS_CHECKED = 3

ROUTER_RE = re.compile(rb"createBrowserRouter|BrowserRouter|createWebHistory|createRouter\(|history\.pushState|"
                       rb"vue-router|react-router|@angular/router|svelte-routing|wouter", re.I)
BASENAME_RE = re.compile(rb"""basename\s*:\s*["']/["']""")
ROOT_STR_RE = re.compile(rb"""["'`]/(api|assets|static|ws|socket\.io|graphql)/""")
SCRIPT_SRC_RE = re.compile(r"""<script[^>]+src\s*=\s*["']?([^"'\s>]+)""", re.I)
ICON_RE = re.compile(r"""<link[^>]+rel\s*=\s*["']?(?:shortcut\s+)?icon["']?[^>]*>""", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']?([^"'\s>]+)""", re.I)
ROOT_URL_RE = re.compile(r"""(?:src|href|action)\s*=\s*["']?/(?!/)""", re.I)

ICON_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\x00\x00\x01\x00", "image/x-icon"), (b"GIF8", "image/gif"),
              (b"\xff\xd8\xff", "image/jpeg"), (b"RIFF", "image/webp"))

VERDICTS = {
    "works": "Should work",
    "try_spa": "Try single-page app mode",
    "needs_rule": "Needs a rule",
    "tunnel": "Probably won't work through Home Assistant — use a tunnel",
    "unreachable": "Can't be reached",
}


def _client(site: sites.Site, timeout: float) -> httpx.AsyncClient:
    t = httpx.AsyncHTTPTransport(verify=netguard.ssl_context(site.tls_pem), retries=0)
    t._pool._network_backend = netguard.GuardedBackend(site.allow_public)
    return httpx.AsyncClient(transport=t, follow_redirects=False, trust_env=False,
                             timeout=httpx.Timeout(timeout, connect=min(timeout, 10)))


async def certificate(site: sites.Site) -> dict | None:
    """The certificate the site presents (read without trusting it), or None: {fingerprint, subject, issuer,
    notAfter, pem}. The connection goes to the checked address, and nothing is sent on it."""
    if site.scheme != "https":
        return None
    addrs = await netguard.resolve(site.host, site.port, site.allow_public)
    reader = writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(
            addrs[0], site.port, ssl=netguard.insecure_context(), server_hostname=site.host), 10)
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        return None
    finally:
        if writer is not None:
            writer.close()
    if not der:
        return None
    return describe_cert(der)


def describe_cert(der: bytes) -> dict:
    fp = hashlib.sha256(der).hexdigest().upper()
    info = {"fingerprint": ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)), "pem": ssl.DER_cert_to_PEM_cert(der),
            "subject": "", "issuer": "", "notAfter": ""}
    try:
        from cryptography import x509
        cert = x509.load_der_x509_certificate(der)
        info["subject"] = cert.subject.rfc4514_string()
        info["issuer"] = cert.issuer.rfc4514_string()
        info["notAfter"] = cert.not_valid_after_utc.strftime("%Y-%m-%d")
    except Exception:                                    # noqa: BLE001 — the fingerprint is what matters
        pass
    return info


def _problem(e: Exception) -> tuple[str, str]:
    """(kind, words) for a failed connection: refused, timeout, dns, certificate, address, other."""
    msg = str(e) or e.__class__.__name__
    low = msg.lower()
    if isinstance(e, netguard.TargetError) or "internet address" in low or "can't be reached through" in low:
        return "address", msg
    if "couldn't be found (dns)" in low or "name or service not known" in low:
        return "dns", msg
    if "certificate" in low or "ssl" in low:
        return "certificate", "The site's certificate isn't trusted (self-signed or for another name)."
    if isinstance(e, httpx.TimeoutException):
        return "timeout", "The site didn't answer in time."
    if "refused" in low or "all connection attempts failed" in low:
        return "refused", "The connection was refused or the device couldn't be reached — is it on, and are the " \
                          "address and port right?"
    return "other", msg


async def _get(c: httpx.AsyncClient, site: sites.Site, url: str, limit: int) -> tuple[httpx.Response, bytes]:
    req = c.build_request("GET", url, headers=list(site.headers) + [("Accept-Encoding", "gzip, deflate")])
    r = await c.send(req, stream=True)
    buf = bytearray()
    try:
        if decoding.readable(r.headers.get("content-encoding")):
            async for piece in decoding.pieces(r.aiter_raw(), r.headers.get("content-encoding")):
                buf += piece
                if len(buf) > limit:
                    break
    except zlib.error:
        pass
    finally:
        await r.aclose()
    return r, bytes(buf[:limit])


async def run(site: sites.Site, timeout: float = 15) -> dict:
    """The report for a site (saved or only filled in on the form)."""
    report = {"reachable": False, "problem": None, "problemKind": None, "certificate": None, "status": None,
              "ms": None, "redirects": [], "frameHeaders": [], "rewritten": 0, "scripts": {"checked": 0},
              "favicon": None, "verdict": "unreachable", "suggestedRule": None}
    url = site.origin + site.start_path
    async with _client(site, timeout) as c:
        started = time.monotonic()
        page, body = None, b""
        try:
            for _ in range(6):                               # redirects on the site's own address, at most 5
                page, body = await _get(c, site, url, MAX_PAGE)
                loc = page.headers.get("location")
                if page.status_code in (301, 302, 303, 307, 308) and loc:
                    nxt = urljoin(url, loc)
                    report["redirects"].append(nxt)
                    p = urlsplit(nxt)
                    if netguard.origin(p.scheme or site.scheme, (p.hostname or "").lower(),
                                       p.port or (443 if p.scheme == "https" else 80)) != site.origin:
                        break
                    url = nxt
                    continue
                break
        except (httpx.HTTPError, netguard.TargetError, OSError) as e:
            kind, words = _problem(e)
            report.update(problem=words, problemKind=kind, verdictText=VERDICTS["unreachable"])
            if kind == "certificate":
                report["certificate"] = _public_cert(await certificate(site))
            return report
        report.update(reachable=True, status=page.status_code, ms=round((time.monotonic() - started) * 1000))
        report["frameHeaders"] = [h for h in ("x-frame-options", "content-security-policy")
                                  if h in page.headers and (h == "x-frame-options" or
                                                            "frame-ancestors" in page.headers[h])]
        text = body.decode("utf-8", "replace") if rewrite.kind_of(page.headers.get("content-type")) == "html" else ""
        report["rewritten"] = len(ROOT_URL_RE.findall(text))
        router = basename = root_strings = False
        checked = 0
        for src in SCRIPT_SRC_RE.findall(text)[:10]:
            if checked >= SCRIPTS_CHECKED:
                break
            surl = urljoin(url, src)
            if not surl.startswith(site.origin + "/"):
                continue
            try:
                _, js = await _get(c, site, surl, MAX_SCRIPT)
            except (httpx.HTTPError, OSError):
                continue
            checked += 1
            router = router or bool(ROUTER_RE.search(js))
            basename = basename or bool(BASENAME_RE.search(js))
            root_strings = root_strings or bool(ROOT_STR_RE.search(js))
        report["scripts"] = {"checked": checked, "router": router, "basename": basename, "rootPaths": root_strings}
        report["favicon"] = _icon_url(text, url, site)
    off_site = report["redirects"] and not report["redirects"][-1].startswith(site.origin + "/")
    if off_site:
        verdict = "tunnel"
    elif basename:
        verdict = "needs_rule"
        report["suggestedRule"] = {"where": "body", "types": "application/javascript,text/javascript",
                                   "match": r"""basename:(\s*)(["'])/\2""", "replace": r"""basename:\1\2$prefix/\2"""}
    elif router and not site.spa_mode:
        verdict = "try_spa"
    else:
        verdict = "works"
    report.update(verdict=verdict, verdictText=VERDICTS[verdict])
    return report


def _public_cert(info: dict | None) -> dict | None:
    if not info:
        return None
    return {k: info[k] for k in ("fingerprint", "subject", "issuer", "notAfter")}


def _icon_url(text: str, page_url: str, site: sites.Site) -> str:
    m = ICON_RE.search(text)
    if m:
        h = HREF_RE.search(m.group(0))
        if h:
            u = urljoin(page_url, h.group(1))
            if u.startswith(site.origin + "/"):
                return u
    return site.origin + "/favicon.ico"


async def fetch_icon(site: sites.Site, url: str | None = None) -> tuple[bytes, str] | None:
    """The site's icon, if it is a small PNG, ICO, GIF, JPEG or WebP image (never SVG: it can carry scripts)."""
    async with _client(site, 10) as c:
        if url is None:
            try:
                _, body = await _get(c, site, site.origin + site.start_path, MAX_PAGE)
                url = _icon_url(body.decode("utf-8", "replace"), site.origin + site.start_path, site)
            except (httpx.HTTPError, OSError, netguard.TargetError):
                url = site.origin + "/favicon.ico"
        if not url.startswith(site.origin + "/"):
            return None
        try:
            r, data = await _get(c, site, url, MAX_ICON + 1)
        except (httpx.HTTPError, OSError, netguard.TargetError):
            return None
    if r.status_code != 200 or not data or len(data) > MAX_ICON:
        return None
    for magic, ctype in ICON_MAGIC:
        if data.startswith(magic) and (ctype != "image/webp" or data[8:12] == b"WEBP"):
            return data, ctype
    return None
