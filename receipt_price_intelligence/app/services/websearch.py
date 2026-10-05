"""Web search for store hours, prices and deals, through one of three providers
(the ``web_search_provider`` option):

* ``custom``: your own search server with POST /search and POST /fetch (described below)
* ``tavily``: the Tavily search API (free plan: 1,000 searches a month, no card). Pages are fetched by
  the app itself, so reading them costs no credits; when a store's site refuses that, the page text
  Tavily sent with the search result is used instead
* ``searxng``: a SearXNG instance (free, self-hosted), through its JSON API. Pages are fetched by the
  app itself

For a custom server:

The server is expected to take JSON and answer JSON (or, for /fetch, plain text). Field names
differ between servers, so the request field names are options and the answers are read
tolerantly: a list of results may be under ``results``, ``items``, ``data``, ``hits`` or be the
answer itself, and each result's title, address and text are found under their usual names.

Only search words (store names, towns, product names) and page addresses taken from search
results are sent. Nothing about your receipts or spending leaves the app.
"""

import html
import json
import re
import threading
import time
import functools
import gzip
import http.client
import ipaddress
import socket
import ssl
import zlib
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Any
from urllib.parse import urlencode, urlparse

from app.config import get_settings
from app.logging_config import get_logger
from app.services import webcache, webdebug

logger = get_logger("websearch")

MAX_PAGE_CHARS = 14000

_sema_lock = threading.Lock()
_semaphore: threading.Semaphore | None = None
_semaphore_size = 0


def _slot() -> threading.Semaphore:
    """A semaphore limiting how many requests to the search server are in flight at once.

    Rebuilt if the configured limit has changed since it was last built (this only really
    happens between test runs; the setting can also change while the app runs).
    """
    global _semaphore, _semaphore_size
    size = max(1, get_settings().web_search_concurrency)
    with _sema_lock:
        if _semaphore is None or _semaphore_size != size:
            _semaphore = threading.Semaphore(size)
            _semaphore_size = size
        return _semaphore


class WebSearchError(RuntimeError):
    """The search server could not be used. The message is safe to show."""


PROVIDERS = {"custom": "your search server", "tavily": "Tavily", "searxng": "SearXNG", "parallel": "Parallel"}
TAVILY_URL = "https://api.tavily.com/search"
PARALLEL_URL = "https://api.parallel.ai/v1/search"
HOSTED = ("tavily", "parallel")  # searched with an API key; no server address, no spacing between searches


def provider() -> str:
    value = (getattr(get_settings(), "web_search_provider", "custom") or "custom").lower()
    return value if value in PROVIDERS else "custom"


def configured() -> bool:
    settings = get_settings()
    if provider() in HOSTED:
        return bool(settings.web_search_api_key)
    return bool(settings.web_search_url)


def describe() -> str | None:
    """Who searches, for the status line: "Tavily", or the host of your server."""
    if not configured():
        return None
    kind = provider()
    return PROVIDERS[kind] if kind in HOSTED else urlparse(get_settings().web_search_url).netloc or None


def _post(path: str, body: dict[str, Any]) -> Any:
    settings = get_settings()
    if not settings.web_search_url:
        raise WebSearchError("No search server is set up")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain;q=0.8"}
    if settings.web_search_token:
        headers["Authorization"] = f"Bearer {settings.web_search_token}"
    request = urllib.request.Request(settings.web_search_url + path, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")

    # Up to `web_search_concurrency` requests can be waiting on the search server at once; this
    # blocks here until a slot is free, so a lookup with several pages to fetch genuinely runs
    # some of them at the same time rather than one at a time.
    slot = _slot()
    slot.acquire()
    started = time.time()
    trace = {"method": "POST", "url": settings.web_search_url + path, "sent": {"headers": headers, "body": body}, "started": started}
    action = "search" if path == "/search" else "fetch"
    try:
        with urllib.request.urlopen(request, timeout=settings.web_search_timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
            webdebug.record("custom", action, status=response.status, received=raw, content_type=content_type, **trace)
    except urllib.error.HTTPError as e:
        webdebug.record("custom", action, status=e.code, received=_error_body(e), error=f"HTTP {e.code}", **trace)
        raise WebSearchError(f"The search server answered {path} with an error ({e.code})") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        webdebug.record("custom", action, error=f"{type(e).__name__}: {e}", **trace)
        raise WebSearchError("The search server could not be reached") from e
    finally:
        slot.release()

    if "json" in content_type.lower() or raw.lstrip()[:1] in ("{", "["):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


# --------------------------------------------------------------------------- #
# Reading answers
# --------------------------------------------------------------------------- #

_RESULT_KEYS = ("results", "items", "data", "hits", "organic", "organic_results", "web", "links", "documents", "sources")
_TITLE_KEYS = ("title", "name", "heading")
_URL_KEYS = ("url", "link", "href", "source", "uri")
_TEXT_KEYS = ("snippet", "content", "description", "text", "summary", "body", "excerpt")
_PAGE_KEYS = ("text", "content", "markdown", "body", "page", "page_content", "result", "data", "html", "plain_text")


def _first_str(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def safe_url(url: Any) -> str:
    """``url`` if it is an http(s) address, else "". Search results are untrusted and their addresses are
    stored and shown as links, so anything else (``javascript:``, ``data:`` ...) must never get through."""
    text = url.strip() if isinstance(url, str) else ""
    parsed = urlparse(text)
    return text if parsed.scheme.lower() in ("http", "https") and parsed.netloc else ""


def as_hits(data: Any) -> list[dict[str, str]]:
    """Search results from whatever shape the server answers with: [{title, url, snippet}]."""
    items: Any = data
    if isinstance(data, dict):
        items = None
        for key in _RESULT_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
            if isinstance(value, dict):  # {"data": {"results": [...]}}
                nested = as_hits(value)
                if nested:
                    return nested
        if items is None:
            text = _first_str(data, ("answer", "text", "content", "summary"))
            return [{"title": "", "url": "", "snippet": text}] if text else []
    if not isinstance(items, list):
        return []

    hits = []
    for item in items:
        if isinstance(item, str):
            url = safe_url(item)
            hits.append({"title": "", "url": url, "snippet": "" if url else item})
        elif isinstance(item, dict):
            hits.append({"title": _first_str(item, _TITLE_KEYS), "url": safe_url(_first_str(item, _URL_KEYS)), "snippet": _first_str(item, _TEXT_KEYS)})
    return [h for h in hits if h["url"] or h["snippet"]]


_TAG = re.compile(r"<(script|style|noscript)\b.*?</\1>|<[^>]+>", re.S | re.I)


def clean_page_text(value: Any, limit: int = MAX_PAGE_CHARS) -> str:
    """Plain text of a fetched page: HTML tags removed, whitespace collapsed, length limited."""
    if isinstance(value, dict):
        value = _first_str(value, _PAGE_KEYS) or json.dumps(value)[:limit]
    elif isinstance(value, list):
        value = "\n".join(str(v) for v in value)
    text = value if isinstance(value, str) else ""
    if "<" in text and re.search(r"</?(html|body|div|p|span|table|li|ul|h\d)\b", text, re.I):
        text = html.unescape(_TAG.sub(" ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit]


# --------------------------------------------------------------------------- #
# The two operations
# --------------------------------------------------------------------------- #

def site_domain(url: str | None) -> str | None:
    """The domain of a store's website, without "www.", for limiting a search to it."""
    host = urlparse(url or "").hostname if url else None
    return host[4:] if host and host.startswith("www.") else host


def search(query: str, limit: int = 5, site: str | None = None, use_cache: bool = True) -> list[dict[str, str]]:
    """Search the web, answering from the cache when the same search was made recently (see ``webcache``)."""
    query = " ".join((query or "").split())[:300]
    if not query:
        return []
    key = webcache.search_key(provider(), query, site, limit)
    if use_cache:
        cached = webcache.get(key)
        if cached is not None:
            hits, saved = cached
            webdebug.record("cache", "search", url=f"{query}{' (site: ' + site + ')' if site else ''}", status=200,
                            received=json.dumps(hits, indent=1), note=f"Answered from the cache (saved {_ago(saved)}); nothing was sent")
            return hits
    hits = _search(query, limit, site)
    webcache.put(key, "search", hits, webcache.search_seconds(bool(hits)), label=query)
    return hits


def _ago(saved: float) -> str:
    minutes = max(0, int((time.time() - saved) / 60))
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


_pace_lock = threading.Lock()
_last_search = {"at": 0.0}


def _pace() -> None:
    """Wait so searches to SearXNG or a custom server are at least ``web_search_min_interval_seconds`` apart.

    Search engines block bursts of automated searches from one address far sooner than a steady trickle;
    lookups run in the background, so a few seconds between searches costs nothing that matters.
    """
    gap = max(0, get_settings().web_search_min_interval_seconds)
    if not gap:
        return
    with _pace_lock:
        wait = _last_search["at"] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_search["at"] = time.time()


def _search(query: str, limit: int = 5, site: str | None = None) -> list[dict[str, str]]:
    """Search the web. Returns up to ``limit`` hits: [{title, url, snippet}].

    ``site`` (a domain) limits the search to that website: Tavily's domain filter, or ``site:`` in the query
    for SearXNG and custom servers. Hits from other sites are dropped either way.
    """
    settings = get_settings()
    query = " ".join((query or "").split())[:300]
    if not query:
        return []
    kind = provider()
    pause = paused()
    if pause:
        raise SearchBlocked(f"Web searches are paused for {pause['minutes_left']} more minute(s) because {pause['reason']}.")
    if site and kind not in HOSTED:
        query = f"{query} site:{site}"
    if kind == "tavily":
        hits = _tavily_search(query, limit, site)
    elif kind == "parallel":
        hits = _parallel_search(query, limit, site)
    elif kind == "searxng":
        hits = _searxng_search(query, limit)
    else:
        hits = None
    if hits is not None:
        return [h for h in hits if _on_site(h["url"], site)] if site else hits
    # Only the query is sent (a strict server would refuse fields it does not know); the list is cut here.
    _pace()
    data = _post("/search", {settings.web_query_field: query})
    if site:
        return [h for h in as_hits(data) if _on_site(h["url"], site)][:limit]
    if isinstance(data, str) and re.search(r"<(!doctype html|html|head|body)\b", data[:2000], re.I):
        # A web app's page instead of search results: the address is not a search API (for example
        # the LLocalSearch or SearXNG web page). Saying so beats silently finding nothing.
        raise WebSearchError(
            "The search server answered with a web page, not search results: its address is not a search API. "
            "For SearXNG (also the one inside LLocalSearch) choose the searxng provider; LLocalSearch's own page "
            "cannot be used.")
    return as_hits(data)[:limit]


def _on_site(url: str, site: str | None) -> bool:
    host = urlparse(url or "").hostname or ""
    return bool(site) and (host == site or host.endswith("." + site))


def fetch(url: str, use_cache: bool = True) -> dict[str, str]:
    """Fetch a page: {url, text, raw}, from the cache when it was opened in the last day."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise WebSearchError("Only web addresses can be fetched")
    key = webcache.page_key(url)
    if use_cache:
        cached = webcache.get(key)
        if cached is not None:
            raw, saved = cached
            webdebug.record("cache", "fetch", url=url, status=200, received=raw,
                            note=f"Answered from the cache (saved {_ago(saved)}); the page was not opened again")
            return {"url": url, "text": clean_page_text(raw), "raw": raw}
    page = _fetch(url)
    if page.get("raw"):
        webcache.put(key, "page", page["raw"][:MAX_RAW_CHARS], webcache.page_seconds(), label=url)
    return page


def _fetch(url: str) -> dict[str, str]:
    settings = get_settings()
    if provider() != "custom":
        try:
            body = fetch_direct(url)
            return {"url": url, "text": clean_page_text(body), "raw": body[:MAX_RAW_CHARS]}
        except WebSearchError:
            cached = _page_cache_get(url)  # what the search provider sent with the result, if anything
            if cached:
                return {"url": url, "text": clean_page_text(cached), "raw": cached[:MAX_RAW_CHARS]}
            raise
    # Your server opens the page, but the address came from a search result: the same rule applies as for
    # pages the app opens itself, so a result pointing into your home network is never passed on.
    _check_url(url)
    data = _post("/fetch", {settings.web_url_field: url})
    return {"url": url, "text": clean_page_text(data), "raw": raw_page(data)}


# --------------------------------------------------------------------------- #
# Hosted and self-hosted search providers
# --------------------------------------------------------------------------- #

_PAGE_CACHE_SIZE = 200
_page_cache: "OrderedDict[str, str]" = OrderedDict()
_page_cache_lock = threading.Lock()


def _page_cache_put(url: str, text: str) -> None:
    with _page_cache_lock:
        _page_cache[url] = text[:MAX_RAW_CHARS]
        _page_cache.move_to_end(url)
        while len(_page_cache) > _PAGE_CACHE_SIZE:
            _page_cache.popitem(last=False)


def _page_cache_get(url: str) -> str | None:
    with _page_cache_lock:
        return _page_cache.get(url)


def _error_body(error: urllib.error.HTTPError) -> str:
    try:
        return error.read(20_000).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - only for the debug record
        return ""


def _request_json(request: urllib.request.Request, who: str) -> Any:
    slot = _slot()
    slot.acquire()
    started = time.time()
    body = request.data
    sent: dict[str, Any] = {"headers": dict(request.header_items())}
    if body:
        try:
            sent["body"] = json.loads(body)
        except ValueError:
            sent["body"] = body.decode("utf-8", "replace")
    trace = {"method": request.get_method(), "url": request.full_url, "sent": sent, "started": started}
    service = who.lower()
    try:
        with urllib.request.urlopen(request, timeout=get_settings().web_search_timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            webdebug.record(service, "search", status=response.status, received=raw, content_type=response.headers.get("Content-Type"), **trace)
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        webdebug.record(service, "search", status=e.code, received=_error_body(e), error=f"HTTP {e.code}", **trace)
        if e.code in (401, 403):
            raise WebSearchError(f"{who} refused the request ({e.code}): check the API key or address in Admin → App settings") from e
        if e.code in (402, 429, 432, 433):
            reason = f"{who}: the search limit has been reached (monthly credits used up, or too many at once)"
            _pause(reason)
            raise SearchBlocked(reason) from e
        raise WebSearchError(f"{who} answered with an error ({e.code})") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        webdebug.record(service, "search", error=f"{type(e).__name__}: {e}", **trace)
        raise WebSearchError(f"{who} could not be reached") from e
    except ValueError as e:
        raise WebSearchError(f"{who} sent an answer that could not be read") from e
    finally:
        slot.release()


def _tavily_search(query: str, limit: int, site: str | None = None) -> list[dict[str, str]]:
    """Tavily basic search (1 credit). Page text comes back with each result for when a site refuses a direct fetch."""
    body: dict[str, Any] = {"query": query, "max_results": max(1, min(limit, 10)), "search_depth": "basic", "include_raw_content": "text"}
    if site:
        body["include_domains"] = [site]
    request = urllib.request.Request(TAVILY_URL, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {get_settings().web_search_api_key}"})
    data = _request_json(request, "Tavily")
    for item in data.get("results") or [] if isinstance(data, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("raw_content"), str) and item["raw_content"].strip():
            url = safe_url(item.get("url"))
            if url:
                _page_cache_put(url, item["raw_content"])
    return as_hits(data)[:limit]


class SearchBlocked(WebSearchError):
    """The search engines behind the provider are refusing automated searches for now (rate limits, CAPTCHAs).
    Searching again straight away only makes the block last longer, so searches pause for a while."""


PAUSE_SECONDS = 20 * 60
_paused: dict[str, Any] = {"until": 0.0, "reason": ""}


def paused() -> dict[str, Any] | None:
    """{until, reason, minutes_left} while searches are paused, else None."""
    left = _paused["until"] - time.time()
    if left <= 0:
        return None
    return {"until": _paused["until"], "reason": _paused["reason"], "minutes_left": max(1, round(left / 60))}


def resume() -> None:
    """End a pause early (the Web debug page's *Search again now*)."""
    _paused["until"] = 0.0


def _pause(reason: str) -> None:
    _paused["until"], _paused["reason"] = time.time() + PAUSE_SECONDS, reason
    logger.warning("Web searches paused for %d minutes: %s", PAUSE_SECONDS // 60, reason)


def _blocked_engines(data: Any) -> list[str]:
    """SearXNG's list of engines that did not answer, as "name (reason)"."""
    out = []
    for entry in data.get("unresponsive_engines") or [] if isinstance(data, dict) else []:
        if isinstance(entry, (list, tuple)) and entry:
            out.append(f"{entry[0]} ({entry[1]})" if len(entry) > 1 and entry[1] else str(entry[0]))
    return out


def _parallel_search(query: str, limit: int, site: str | None = None) -> list[dict[str, str]]:
    """Parallel's Search API (Fast mode: $1 per 1,000 searches; every account gets $5 of free credit a month).

    Parallel has its own index, so it is not blocked the way scraped engines are. Each result carries text
    excerpts from its page; they are kept for when the store's site refuses to be opened directly.
    """
    settings: dict[str, Any] = {"max_results": max(1, min(limit, 10))}
    if site:
        settings["source_policy"] = {"include_domains": [site]}
    body = {"objective": f"Web pages that answer: {query}", "search_queries": [query], "mode": "fast",
            "advanced_settings": settings}
    request = urllib.request.Request(PARALLEL_URL, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "x-api-key": get_settings().web_search_api_key})
    data = _request_json(request, "Parallel")
    hits = []
    for item in data.get("results") or [] if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        url = safe_url(item.get("url"))
        excerpts = [x for x in item.get("excerpts") or [] if isinstance(x, str) and x.strip()]
        if url and excerpts:
            _page_cache_put(url, "\n\n".join(excerpts))
        snippet = " ".join(" ".join(excerpts).split())[:600]
        if url or snippet:
            hits.append({"title": str(item.get("title") or "")[:300], "url": url, "snippet": snippet})
    return hits[:limit]


def _searxng_search(query: str, limit: int) -> list[dict[str, str]]:
    """SearXNG's JSON API (``json`` must be listed under ``search: formats`` in its settings.yml)."""
    settings = get_settings()
    _pace()
    url = settings.web_search_url.rstrip("/") + "/search?" + urlencode({"q": query, "format": "json", "safesearch": 1})
    headers = {"Accept": "application/json", "User-Agent": _AGENT}
    if settings.web_search_api_key:
        headers["Authorization"] = f"Bearer {settings.web_search_api_key}"
    try:
        data = _request_json(urllib.request.Request(url, headers=headers), "SearXNG")
    except WebSearchError as e:
        if "(403)" in str(e):
            raise WebSearchError("SearXNG refused a JSON search (403): add json under search: formats in its settings.yml") from e
        raise
    hits = as_hits(data)
    blocked = _blocked_engines(data)
    if not hits and blocked:
        reasons = " ".join(blocked).lower()
        if any(w in reasons for w in ("captcha", "too many", "suspended", "access denied", "blocked", "429", "403")):
            reason = f"SearXNG's search engines are refusing automated searches: {', '.join(blocked)}"
            _pause(reason)
            raise SearchBlocked(f"{reason}. Web searches are paused for {PAUSE_SECONDS // 60} minutes so the blocks can clear "
                                "(searching more only makes them last longer). Set Search requests at once to 1, and consider "
                                "enabling more engines in SearXNG.")
    return hits[:limit]


# --------------------------------------------------------------------------- #
# Fetching pages directly (for the hosted providers)
# --------------------------------------------------------------------------- #

MAX_FETCH_BYTES = 3_000_000
FETCH_TIMEOUT = 20
_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 "
          "ReceiptPriceIntelligence")


def _public_address(host: str) -> str | None:
    """The address to connect to for ``host`` — resolved once — or None unless every address it resolves to
    is on the public internet.

    Page addresses come from search results, which anyone can influence, and the app runs inside
    your home network: a result pointing at your router or Home Assistant itself must never be fetched.
    The connection then goes to exactly this address (see ``_PinnedHTTPConnection``), so a name that
    answers differently the second time it is looked up (DNS rebinding) can't lead it anywhere else.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    if not infos:
        return None
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast:
            return None
    return infos[0][4][0].split("%")[0]


def _public_host(host: str) -> bool:
    """Whether every address ``host`` resolves to is on the public internet."""
    return _public_address(host) is not None


def _check_url(url: str) -> tuple[str, str]:
    """Refuse anything but a web address on a usual port of a public host. Returns (host name, the checked
    address) for the connection to be pinned to."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebSearchError("Only web addresses can be fetched")
    if parsed.port not in (None, 80, 443, 8080, 8443):
        raise WebSearchError("it is on an unusual port, so it was not opened")
    address = _public_address(parsed.hostname)
    if address is None:
        raise WebSearchError("it is not on the public internet, so it was not opened")
    return parsed.hostname, address


def _pinned_create_connection(pin):
    """socket.create_connection that connects to the checked address when asked for the checked host name
    (anything else — a proxy from the environment — is connected to as asked)."""
    def create(address, *args, **kwargs):
        host, port = address[0], address[1]
        if pin is not None and host.strip("[]").lower() == pin[0].strip("[]").lower():
            return socket.create_connection((pin[1], port), *args, **kwargs)
        return socket.create_connection(address, *args, **kwargs)
    return create


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Connects to the address ``_check_url`` checked (``pin`` = (host name, address)); the Host header
    keeps the name."""

    def __init__(self, *args, pin=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _pinned_create_connection(pin)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """As _PinnedHTTPConnection; TLS still sends the host name (SNI) and checks the certificate against it."""

    def __init__(self, *args, pin=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _pinned_create_connection(pin)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(functools.partial(_PinnedHTTPConnection, pin=getattr(req, "pin", None)), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(functools.partial(_PinnedHTTPSConnection, pin=getattr(req, "pin", None)), req,
                            context=self._context)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to public addresses (a redirect must not lead into the home network); each one
    is checked again and its connection pinned to the address checked."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib's signature
        pin = _check_url(newurl)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.pin = pin
        return new


_opener = urllib.request.build_opener(_PinnedHTTPHandler(), _PinnedHTTPSHandler(), _CheckedRedirects())


def _fetch_reason(error: Exception) -> str:
    """A short, specific reason a page could not be opened, for the log and the connection test."""
    reason = getattr(error, "reason", error)
    text = str(reason).lower()
    if isinstance(reason, ssl.SSLCertVerificationError) or "certificate" in text:
        return "its security certificate could not be checked"
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in text:
        return "the site took too long to answer"
    if isinstance(reason, socket.gaierror) or "name or service" in text or "nodename" in text:
        return "the site's name could not be looked up (no DNS or no internet?)"
    if isinstance(reason, ssl.SSLError):
        return "a secure connection could not be made"
    if isinstance(reason, ConnectionRefusedError) or "refused" in text:
        return "the site refused the connection"
    if "unreachable" in text:
        return "the site could not be reached (no internet?)"
    return f"the page could not be fetched ({type(reason).__name__})"


def fetch_direct(url: str) -> str:
    """The page at ``url`` as text (HTML kept), fetched by the app itself. Raises WebSearchError."""
    started = time.time()
    headers = {"User-Agent": _AGENT, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
               "Accept-Language": "en-US,en;q=0.8", "Accept-Encoding": "gzip, deflate"}
    trace = {"method": "GET", "url": url, "sent": {"headers": headers}, "started": started}
    try:
        text, status, kind, final_url = _fetch_direct(url, headers)
    except WebSearchError as e:
        webdebug.record("page", "fetch", error=str(e), **trace)
        raise
    webdebug.record("page", "fetch", status=status, received=text, content_type=kind,
                    note=f"redirected to {final_url}" if final_url and final_url != url else None, **trace)
    return text


def _fetch_direct(url: str, headers: dict[str, str]) -> tuple[str, int, str, str]:
    pin = _check_url(url)
    request = urllib.request.Request(url, headers=headers)
    request.pin = pin
    slot = _slot()
    slot.acquire()
    try:
        with _opener.open(request, timeout=FETCH_TIMEOUT) as response:
            kind = response.headers.get("Content-Type", "")
            if kind and not re.search(r"text/|html|json|xml", kind, re.I):
                raise WebSearchError("it is not a web page")
            data = response.read(MAX_FETCH_BYTES + 1)[:MAX_FETCH_BYTES]
            charset = response.headers.get_content_charset() or "utf-8"
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            status, final_url = response.status, response.geturl()
    except WebSearchError:
        raise
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429, 503):
            raise WebSearchError(f"the site refused automated visits ({e.code})") from e
        raise WebSearchError(f"the page answered with an error ({e.code})") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise WebSearchError(_fetch_reason(e)) from e
    finally:
        slot.release()
    try:
        if "gzip" in encoding:
            data = gzip.decompress(data)
        elif "deflate" in encoding:
            data = zlib.decompress(data) if data[:1] == b"\x78" else zlib.decompress(data, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error) as e:
        raise WebSearchError("the page could not be unpacked") from e
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    return text, status, kind, final_url


MAX_RAW_CHARS = 1_500_000


def raw_page(value: Any) -> str:
    """The page as the server sent it (HTML kept), for reading prices from its structured data."""
    if isinstance(value, dict):
        for key in ("html", "raw", "content", "text", "markdown", "body", "page", "page_content", "result", "data"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key][:MAX_RAW_CHARS]
        return json.dumps(value)[:MAX_RAW_CHARS]
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)[:MAX_RAW_CHARS]
    return (value if isinstance(value, str) else "")[:MAX_RAW_CHARS]
