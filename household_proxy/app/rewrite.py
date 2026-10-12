"""Making a root-path site work under a sub-path (spec/SPEC.md §3.2): server-side rewriting of HTML, CSS and the
response headers that carry addresses (Location, Refresh, Link, Content-Location, Set-Cookie), and the cleaning of
the site's Content-Security-Policy.

`P` is the site's public prefix: Home Assistant's ingress path (X-Ingress-Path) + "/s/<slug>". A URL that is
root-relative ("/x"), protocol-relative or absolute on the target's own origin becomes P + its path; anything else
(relative URLs, other hosts, data:, blob:, javascript:) is left exactly as it was.
"""
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

INGRESS_RE = re.compile(r"^/api/hassio_ingress/[A-Za-z0-9_-]{1,128}$")
MAX_REWRITE_BYTES = 5 * 1024 * 1024        # bigger HTML/CSS is passed on untouched (§3.2)


@dataclass
class Ctx:
    """What one response's rewriting needs."""
    ingress: str                 # Home Assistant's ingress path for this app ("" outside Home Assistant)
    slug: str
    scheme: str                  # the target's
    host: str
    port: int
    secure: bool = True          # Home Assistant was reached over https (Secure cookies keep working)
    shim: str | None = None      # the <script> tag to insert first in <head> (phase 2)
    nonce: str | None = None     # the site's CSP nonce, when its policy needs one for the shim
    body_rules: list = field(default_factory=list)

    @property
    def prefix(self) -> str:
        return f"{self.ingress}/s/{self.slug}"


def ingress_path(value: str | None) -> str:
    """X-Ingress-Path as sent by the Supervisor, or "" when missing or not the expected shape."""
    v = (value or "").rstrip("/")
    return v if INGRESS_RE.match(v) else ""


def _same_origin(netloc_host: str | None, port: int | None, scheme: str, ctx: Ctx) -> bool:
    if not netloc_host:
        return False
    if port is None:
        port = 443 if scheme in ("https", "wss") else 80
    return netloc_host.lower().rstrip(".") == ctx.host and port == ctx.port


def url(u: str, ctx: Ctx) -> str:
    """One URL, rewritten when it points at the target (root-relative, protocol-relative or absolute)."""
    if not u:
        return u
    s = u.strip()
    lead = u[:len(u) - len(u.lstrip())]
    if s.startswith("//"):
        try:
            p = urlsplit("http:" + s)
            ok = _same_origin(p.hostname, p.port, ctx.scheme, ctx)
        except ValueError:
            return u
        if not ok:
            return u
        rest = s[2 + len(p.netloc):] or "/"
        return lead + ctx.prefix + (rest if rest.startswith("/") else "/" + rest)
    if s.startswith("/"):
        if s.startswith("/\\"):
            return u
        if ctx.ingress and (s == ctx.ingress or s.startswith(ctx.ingress + "/")):
            return u                      # already inside Home Assistant's ingress path
        return lead + ctx.prefix + s
    low = s[:8].lower()
    if low.startswith(("http://", "https://")):
        try:
            p = urlsplit(s)
            ok = _same_origin(p.hostname, p.port, p.scheme.lower(), ctx)
        except ValueError:
            return u
        if not ok:
            return u
        rest = s[len(p.scheme) + 3 + len(p.netloc):] or "/"
        return lead + ctx.prefix + (rest if rest.startswith("/") else "/" + rest)
    return u


# --------------------------------------------------------------------------------------------- CSS

_CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s'"]*))\s*\)""", re.I)
_CSS_IMPORT = re.compile(r"""@import\s+(?:"([^"]*)"|'([^']*)')""", re.I)


def css(text: str, ctx: Ctx) -> str:
    def one_url(m):
        if m.group(1) is not None:
            return f'url("{url(m.group(1), ctx)}")'
        if m.group(2) is not None:
            return f"url('{url(m.group(2), ctx)}')"
        return f"url({url(m.group(3), ctx)})"

    def one_import(m):
        if m.group(1) is not None:
            return f'@import "{url(m.group(1), ctx)}"'
        return f"@import '{url(m.group(2), ctx)}'"

    return _CSS_URL.sub(one_url, _CSS_IMPORT.sub(one_import, text))


# --------------------------------------------------------------------------------------------- HTML

URL_ATTRS = {"href", "src", "action", "formaction", "poster", "data", "background", "xlink:href", "manifest",
             "ping", "cite", "longdesc", "icon", "itemtype"}
SRCSET_ATTRS = {"srcset", "imagesrcset"}
_REFRESH = re.compile(r"^(\s*\d+\s*[;,]\s*url\s*=\s*)(['\"]?)(.*?)\2\s*$", re.I | re.S)


def srcset(value: str, ctx: Ctx) -> str:
    out = []
    for part in value.split(","):
        bits = part.strip().split(None, 1)
        if not bits:
            out.append(part)
            continue
        bits[0] = url(bits[0], ctx)
        out.append(" ".join(bits))
    return ", ".join(out)


def refresh(value: str, ctx: Ctx) -> str:
    m = _REFRESH.match(value or "")
    if not m:
        return value
    return f"{m.group(1)}{m.group(2)}{url(m.group(3), ctx)}{m.group(2)}"


class _Rewriter(HTMLParser):
    def __init__(self, ctx: Ctx):
        super().__init__(convert_charrefs=False)
        self.ctx = ctx
        self.out: list[str] = []
        self.in_style = False
        self.injected = ctx.shim is None

    # the shim goes first in <head> (or before the first element if the page has no <head>)
    def _inject(self, before: bool, tag: str) -> None:
        if self.injected:
            return
        if before:
            self.out.append(self.ctx.shim)
            self.injected = True
        elif tag == "head":
            self.out.append(self.ctx.shim)
            self.injected = True

    def _attr(self, tag: str, name: str, value: str, attrs: list) -> str | None:
        ctx = self.ctx
        if name in URL_ATTRS:
            return url(value, ctx)
        if name in SRCSET_ATTRS:
            return srcset(value, ctx)
        if name == "style":
            return css(value, ctx)
        if name == "content" and tag == "meta":
            if any(k == "http-equiv" and (v or "").lower() == "refresh" for k, v in attrs):
                return refresh(value, ctx)
        return value

    def _tag(self, tag: str, attrs: list, closed: bool) -> None:
        raw = self.get_starttag_text() or ""
        if not self.injected and tag not in ("html", "head"):
            self._inject(True, tag)           # a page without <head>: before its first element
        new = []
        changed = False
        for name, value in attrs:
            if value is None:
                new.append((name, None))
                continue
            v = self._attr(tag, name, value, attrs)
            if name == "integrity" and tag == "link":
                changed = True                # the stylesheet is rewritten, so its hash no longer matches
                continue
            if v != value:
                changed = True
            new.append((name, v))
        if not changed:
            self.out.append(raw)
        else:
            parts = [f"<{tag}"]
            for name, value in new:
                parts.append(f" {name}" if value is None else f' {name}="{html.escape(value, quote=True)}"')
            parts.append(" />" if closed else ">")
            self.out.append("".join(parts))
        if tag == "head" and not closed:
            self._inject(False, tag)
        if tag == "style" and not closed:
            self.in_style = True

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs, True)

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(css(data, self.ctx) if self.in_style else data)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.out.append(f"<?{data}>")

    def unknown_decl(self, data):
        self.out.append(f"<![{data}]>")


def html_text(text: str, ctx: Ctx) -> str:
    p = _Rewriter(ctx)
    p.feed(text)
    p.close()
    if not p.injected:                       # a page with no element at all
        p.out.insert(0, ctx.shim)
    return "".join(p.out)


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)


def charset(content_type: str | None, body: bytes) -> str:
    """The body's encoding: the Content-Type's charset, else a <meta charset> near the top, else UTF-8 (or
    windows-1252 when the bytes aren't UTF-8)."""
    import codecs
    for cand in (_ct_charset(content_type), _meta(body[:2048])):
        if cand:
            try:
                return codecs.lookup(cand).name
            except LookupError:
                pass
    try:
        body.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def _ct_charset(content_type: str | None) -> str | None:
    m = re.search(r"charset\s*=\s*\"?([A-Za-z0-9_.:-]+)", content_type or "", re.I)
    return m.group(1) if m else None


def _meta(head: bytes) -> str | None:
    m = _META_CHARSET.search(head)
    return m.group(1).decode("ascii", "replace") if m else None


def body(kind: str, data: bytes, content_type: str | None, ctx: Ctx) -> bytes:
    """Rewrite an HTML or CSS body (bytes in, bytes out, the same encoding; bytes that don't decode survive)."""
    enc = charset(content_type, data)
    text = data.decode(enc, "surrogateescape")
    if kind == "html":
        text = html_text(text, ctx)
    elif kind == "css":
        text = css(text, ctx)
    for rule in ctx.body_rules:
        text = rule(text)
    return text.encode(enc, "surrogateescape")


def kind_of(content_type: str | None) -> str | None:
    """'html', 'css' or None (not rewritten) from a Content-Type."""
    main = (content_type or "").split(";")[0].strip().lower()
    if main in ("text/html", "application/xhtml+xml"):
        return "html"
    if main == "text/css":
        return "css"
    return None


# --------------------------------------------------------------------------------------------- headers

_LINK_URL = re.compile(r"<([^>]*)>")


def link_header(value: str, ctx: Ctx) -> str:
    return _LINK_URL.sub(lambda m: f"<{url(m.group(1), ctx)}>", value)


def set_cookie(value: str, ctx: Ctx) -> str:
    """Domain removed; Path put under P (so the site's cookies reach only this site through the proxy, never Home
    Assistant or another site); Secure and SameSite=None dropped when Home Assistant itself is plain http (else the
    browser would refuse the cookie)."""
    parts = [p.strip() for p in value.split(";")]
    head, attrs = parts[0], parts[1:]
    out, path = [head], "/"
    for a in attrs:
        if not a:
            continue
        k, _, v = a.partition("=")
        key = k.strip().lower()
        if key == "domain":
            continue
        if key == "path":
            path = v.strip() or "/"
            continue
        if not ctx.secure and key == "secure":
            continue
        if not ctx.secure and key == "samesite" and v.strip().lower() == "none":
            out.append("SameSite=Lax")
            continue
        out.append(a)
    if not path.startswith("/"):
        path = "/"
    out.insert(1, f"Path={ctx.prefix}{path}" if path != "/" else f"Path={ctx.prefix}/")
    return "; ".join(out)


_NONCE = re.compile(r"'nonce-([A-Za-z0-9+/=_-]+)'")


def csp(value: str, ctx: Ctx, need_script: bool) -> tuple[str | None, str | None]:
    """The site's policy with frame-ancestors removed (the site is shown in a frame of Home Assistant's own page),
    upgrade-insecure-requests / block-all-mixed-content removed when Home Assistant is plain http, and — when the
    shim is inserted — the app's own scripts allowed ('self' added to script-src, or the site's nonce used when it
    relies on 'strict-dynamic'). Returns (policy or None if nothing is left, the nonce for the shim's tag)."""
    nonce = None
    kept = []
    directives = [d.strip() for d in value.split(";") if d.strip()]
    names = {d.split()[0].lower() for d in directives}
    for d in directives:
        bits = d.split()
        name = bits[0].lower()
        if name == "frame-ancestors":
            continue
        if not ctx.secure and name in ("upgrade-insecure-requests", "block-all-mixed-content"):
            continue
        script_dir = name in ("script-src", "script-src-elem") or (name == "default-src" and
                                                                    "script-src" not in names and
                                                                    "script-src-elem" not in names)
        if need_script and script_dir:
            low = [b.lower() for b in bits[1:]]
            if "'strict-dynamic'" in low:
                m = _NONCE.search(d)
                nonce = m.group(1) if m else nonce
            elif "'self'" not in low and "*" not in low:
                if "'none'" in low:
                    bits = [b for b in bits if b.lower() != "'none'"]
                bits.append("'self'")
            d = " ".join(bits)
        kept.append(d)
    return ("; ".join(kept) or None), nonce
