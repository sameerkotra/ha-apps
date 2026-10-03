"""Read product prices straight from a web page, without asking the model (pure, no network).

The same page always gives the same answer. Sources, most reliable first:

1. **Structured data** (``structured``): the schema.org Product/Offer JSON-LD most store sites publish
   for search engines: exact product name, price and (often) the sale end date.
2. **Microdata and meta tags** (``structured``): ``itemprop="price"`` and ``product:price:amount``.
3. **Page text** (``text``): lines like ``Great Value 2% Milk 1 gal  $3.79`` or ``Bananas $0.58/lb``.

Each listing is ``{product, price, unit, on_sale, method, valid_to}``, the same shape
``webparse.clean_prices`` gives, so ``webparse.pick_price`` works on either.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from typing import Any

MAX_LISTINGS = 200
MAX_PRICE = 1000.0

_UNIT_WORDS = {
    "lb": "LB", "lbs": "LB", "pound": "LB", "oz": "OZ", "ounce": "OZ", "kg": "KG", "g": "G",
    "gal": "GAL", "gallon": "GAL", "l": "L", "liter": "L", "litre": "L", "ml": "ML",
    "ct": "CT", "count": "CT", "ea": "each", "each": "each", "item": "each", "fl oz": "FLOZ", "floz": "FLOZ",
    # UN/CEFACT common codes, which schema.org ``unitCode`` uses (e.g. bananas priced per "LBR")
    "lbr": "LB", "kgm": "KG", "grm": "G", "onz": "OZ", "ltr": "L", "mlt": "ML", "gll": "GAL", "oza": "FLOZ",
    "h87": "each", "c62": "each",
}

_JSONLD = re.compile(r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
_BLOCK_TAG = re.compile(r"</?(?:div|p|li|tr|td|th|h\d|section|article|br|ul|ol|table|header|footer|span)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def _price(value: Any) -> float | None:
    """A price from a number or text like "$3.79", "3,79" or "3.79 USD"; None if it is not one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        # "1,299.00" and "1.299,00" are thousands, "3,79" is a decimal comma
        m = re.search(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?", str(value).replace("\u00a0", " "))
        if not m:
            return None
        digits = m.group(0).replace(" ", "")
        decimal = re.search(r"[.,](\d{1,2})$", digits)
        whole = re.sub(r"[.,]", "", digits[: decimal.start()] if decimal else digits)
        number = float(f"{whole}.{decimal.group(1)}" if decimal else whole)
    return round(number, 2) if 0 < number < MAX_PRICE else None


def _unit(text: Any) -> str:
    key = re.sub(r"[.\s]+", " ", str(text or "").strip().lower()).strip()
    return _UNIT_WORDS.get(key, _UNIT_WORDS.get(key.replace(" ", ""), "each"))


def _listing(name: Any, price: float | None, unit: str = "each", on_sale: bool = False,
             method: str = "structured", valid_to: str | None = None) -> dict[str, Any] | None:
    product = " ".join(htmllib.unescape(str(name or "")).split())[:160]
    if not product or price is None:
        return None
    return {"product": product, "price": price, "unit": unit, "on_sale": on_sale, "method": method, "valid_to": valid_to}


# --------------------------------------------------------------------------- #
# 1. JSON-LD
# --------------------------------------------------------------------------- #

def _walk(node: Any):
    """Every dict inside a JSON-LD document (handles @graph, ItemList, nested lists)."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _types(node: dict[str, Any]) -> set[str]:
    t = node.get("@type")
    values = t if isinstance(t, list) else [t]
    return {str(v).split("/")[-1].lower() for v in values if v}


_REGULAR_PRICE_TYPES = {"strikethroughprice", "listprice", "msrp"}


def _is_regular_price(spec: dict[str, Any]) -> bool:
    """Whether a priceSpecification is the crossed-out regular price rather than what it costs now."""
    return str(spec.get("priceType") or "").split("/")[-1].lower() in _REGULAR_PRICE_TYPES


def _offer_prices(offers: Any) -> list[tuple[float, bool, str | None, str]]:
    """(price, on_sale, valid_to, unit) for each offer of a product."""
    out = []
    for offer in offers if isinstance(offers, list) else [offers]:
        if not isinstance(offer, dict):
            continue
        spec = offer.get("priceSpecification")
        specs = [x for x in (spec if isinstance(spec, list) else [spec]) if isinstance(x, dict)]
        price = _price(offer.get("price")) or _price(offer.get("lowPrice"))
        unit = "each"
        for s in specs:
            if not _is_regular_price(s):
                price = price or _price(s.get("price"))
            ref = s.get("referenceQuantity") if isinstance(s.get("referenceQuantity"), dict) else {}
            if ref.get("unitText") or ref.get("unitCode"):
                unit = _unit(ref.get("unitText") or ref.get("unitCode"))
        valid_to = str(offer.get("priceValidUntil") or "")[:10] or None
        # priceValidUntil alone is not a sale: stores set it on regular prices too (search engines ask
        # for it), often a year ahead. Nor is an AggregateOffer's lowPrice-highPrice range (that is
        # several sellers or sizes). A sale needs a crossed-out regular price above the current one.
        on_sale = False
        if price is not None:
            regulars = [_price(s.get("price")) for s in specs if _is_regular_price(s)]
            if "aggregateoffer" not in _types(offer):
                regulars.append(_price(offer.get("highPrice")))
            on_sale = any(r is not None and price < r for r in regulars)
            out.append((price, on_sale, valid_to, unit))
    return out


def from_jsonld(raw: str) -> list[dict[str, Any]]:
    listings = []
    for block in _JSONLD.findall(raw or ""):
        data = None
        # Raw first: unescaping could break a valid document (a name like 12&quot; pizza inside a string);
        # some sites do HTML-escape the whole block, so that is the fallback.
        for candidate in (block.strip(), htmllib.unescape(block.strip())):
            try:
                data = json.loads(candidate)
                break
            except ValueError:
                continue
        if data is None:
            continue
        for node in _walk(data):
            if not ({"product", "productgroup", "individualproduct"} & _types(node)):
                continue
            name = node.get("name")
            size = node.get("size")
            if not size and isinstance(node.get("weight"), dict):
                size = " ".join(str(x) for x in (node["weight"].get("value"), node["weight"].get("unitText")) if x)
            if not name:
                continue  # a product with no name cannot be matched to anything
            if size and isinstance(size, (str, int, float)) and str(size) not in str(name):
                name = f"{name} {size}"
            for price, on_sale, valid_to, unit in _offer_prices(node.get("offers")):
                item = _listing(name, price, unit, on_sale, "structured", valid_to)
                if item:
                    listings.append(item)
    return listings


# --------------------------------------------------------------------------- #
# 2. Microdata and meta tags (single-product pages)
# --------------------------------------------------------------------------- #

def _attr(tag: str, name: str) -> str | None:
    m = re.search(name + r"\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
    return m.group(1) if m else None


def from_meta(raw: str) -> list[dict[str, Any]]:
    metas = {}
    for tag in re.findall(r"<meta\b[^>]*>", raw or "", re.I):
        key = (_attr(tag, "property") or _attr(tag, "name") or _attr(tag, "itemprop") or "").lower()
        if key:
            metas.setdefault(key, _attr(tag, "content"))
    price = _price(metas.get("product:price:amount") or metas.get("og:price:amount") or metas.get("price"))
    if price is None:
        # microdata: <span itemprop="price" content="3.79"> and <... itemprop="name">Milk</...>
        m = re.search(r"itemprop\s*=\s*[\"']price[\"'][^>]*content\s*=\s*[\"']([^\"']+)[\"']", raw or "", re.I) or \
            re.search(r"content\s*=\s*[\"']([^\"']+)[\"'][^>]*itemprop\s*=\s*[\"']price[\"']", raw or "", re.I)
        price = _price(m.group(1)) if m else None
    if price is None:
        return []
    name = metas.get("og:title") or metas.get("twitter:title")
    if not name:
        m = re.search(r"itemprop\s*=\s*[\"']name[\"'][^>]*>([^<]{3,160})<", raw or "", re.I) or re.search(r"<title[^>]*>([^<]{3,160})</title>", raw or "", re.I)
        name = m.group(1) if m else None
    item = _listing(name, price, "each", False, "structured")
    return [item] if item else []


# --------------------------------------------------------------------------- #
# 3. Page text
# --------------------------------------------------------------------------- #

_PRICE_IN_LINE = re.compile(
    r"(?P<cur>[$£€])\s?(?P<price>\d{1,3}(?:[.,]\d{2}))(?!\d)"
    r"(?:\s*(?:/|per|each|ea\.?)\s*(?P<unit>fl\.?\s?oz|lbs?|oz|kg|g|gal(?:lon)?|l|ml|ct|each|ea)\b)?",
    re.I,
)
_CENTS_PER = re.compile(r"(?P<cents>\d{1,3})\s?¢\s?(?:/|per)\s?(?P<unit>lbs?|oz|kg|g|ea|each)\b", re.I)
_NOISE_LINE = re.compile(r"\b(shipping|delivery fee|free delivery|coupon code|gift card|reward|points|membership|subscribe|spend \$)\b", re.I)
_WAS = re.compile(r"\b(was|reg\.?|regular|orig(?:inal)?|compare at|list price)\b", re.I)
_WAS_BEFORE = re.compile(r"\b(was|reg\.?|regular(?: price)?|orig(?:inal)?(?: price)?|compare at|list price)\s*:?\s*$", re.I)
_LEAD_WORDS = re.compile(r"\s*\b(was|now|reg\.?|regular(?: price)?|sale(?: price)?|only)\s*:?\s*$", re.I)


def page_text(raw: str) -> str:
    """Readable lines from HTML (or the text unchanged when it is not HTML)."""
    text = raw or ""
    if re.search(r"</?(html|body|div|span|p|li|td)\b", text, re.I):
        text = _SCRIPT_STYLE.sub(" ", text)
        text = _BLOCK_TAG.sub("\n", text)
        text = htmllib.unescape(_TAG.sub(" ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


_LABEL_NAME = re.compile(
    r"^(?:(?:current|our|sale|regular|reg\.?|now|only|special|member|club|your|price|each|ea\.?|per|unit)\s*)+:?$", re.I)
_BOILERPLATE = re.compile(
    r"add to (?:cart|list)|\bsnap\b|\bebt\b|in stock|out of stock|pick ?up|delivery|shipping|sold by|see details|"
    r"\breviews?\b|out of 5|\bstars?\b|\brating\b|clip coupon|sponsored|results? for|free returns|view all", re.I)


def _clean_line(line: str) -> str:
    """A line of page text without markdown: headings, bullets, bold marks, table borders."""
    line = re.sub(r"\*\*|__|`", "", line)
    line = re.sub(r"^\s*(?:#+|[-*•·>]|\d+[.)])\s+", "", line)
    return line.strip(" |\t")


_UNIT_PRICE_TEXT = re.compile(r"¢|\bper\s+(?:fl\s*)?(?:oz|lb|ounce|pound|count|ct)\b|/\s*(?:fl\s*)?(?:oz|lb|ct)\b", re.I)


def _name_like(text: str) -> bool:
    return (len(re.findall(r"[A-Za-z]", text)) >= 3 and not _PRICE_IN_LINE.search(text) and not _CENTS_PER.search(text)
            and not _BOILERPLATE.search(text) and not _LABEL_NAME.match(text.strip()) and not _UNIT_PRICE_TEXT.search(text))


def from_text(text: str) -> list[dict[str, Any]]:
    """Listings from page text: a product name and its price on one line, or a price on a line of its own with the
    product named on the line(s) just above it ("Kroger 2% Milk" / "1 gal" / "$3.29") or, when the price comes
    first, on the line below. A line that named one product is never used for another."""
    lines = [c for c in (_clean_line(l) for l in (text or "").split("\n")) if c][:4000]
    listings, used = [], set()
    for i, line in enumerate(lines):
        if _NOISE_LINE.search(line):
            continue
        matches = list(_PRICE_IN_LINE.finditer(line))
        cents = None if matches else _CENTS_PER.search(line)
        if not matches and not cents:
            continue
        # A price right after "was"/"reg." is the old price, not what it costs now ("Was $4.98 Now $4.12").
        old = [bool(_WAS_BEFORE.search(line[max(0, x.start() - 20):x.start()])) for x in matches]
        first_start = matches[0].start() if matches else cents.start()
        name = _LEAD_WORDS.sub("", line[:first_start].strip(" -:|·•—–")).strip(" -:|·•—–")
        if not _name_like(name):
            name = ""
            # the product named on the line(s) above: up to two (a name and its size), stopping at another price
            back, j = [], i - 1
            while j >= 0 and len(back) < 2 and j not in used and not _PRICE_IN_LINE.search(lines[j]) and not _CENTS_PER.search(lines[j]):
                if _name_like(lines[j]) or (back and re.search(r"[A-Za-z]{2,}", lines[j]) and len(lines[j]) < 60 and not _BOILERPLATE.search(lines[j])):
                    back.insert(0, j)
                elif not (_BOILERPLATE.search(lines[j]) or _LABEL_NAME.match(lines[j])):
                    break
                j -= 1
            if back and _name_like(" ".join(lines[k] for k in back)):
                name = " ".join(lines[k] for k in back)
                used.update(back)
            else:
                # the price comes first: the product is named on the next line
                for k in range(i + 1, min(i + 3, len(lines))):
                    if k in used or _PRICE_IN_LINE.search(lines[k]):
                        break
                    if _name_like(lines[k]):
                        name = lines[k]
                        used.add(k)
                        break
        if not name:
            continue
        if cents:
            price, unit, on_sale = round(int(cents.group("cents")) / 100, 2), _unit(cents.group("unit")), False
        else:
            # "$2.99 was $3.49" / "Was $3.49 Now $2.99": the current price is the first one not marked as
            # the old price; a "was" price next to it marks a sale
            current = next((x for x, is_old in zip(matches, old) if not is_old), None)
            if current is None:
                continue  # only an old price on this line
            price, unit = _price(current.group("price")), _unit(current.group("unit") or "each")
            on_sale = len(matches) > 1 and (any(old) or bool(_WAS.search(line[current.end():])))
        used.add(i)
        item = _listing(name.strip(" -:|·•—–"), price, unit, on_sale, "text")
        if item:
            listings.append(item)
    return listings


# --------------------------------------------------------------------------- #

def extract(raw: str) -> list[dict[str, Any]]:
    """All listings on a page, structured sources first, duplicates removed, in a fixed order."""
    raw = raw or ""
    seen, out = set(), []
    for item in from_jsonld(raw) + from_meta(raw) + from_text(page_text(raw)):
        key = (item["product"].lower(), item["price"], item["unit"])
        if key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= MAX_LISTINGS:
            break
    return out


# --------------------------------------------------------------------------- #
# Opening hours from a store's own page
# --------------------------------------------------------------------------- #

_DAY_CODES = {"monday": "Mo", "tuesday": "Tu", "wednesday": "We", "thursday": "Th", "friday": "Fr",
              "saturday": "Sa", "sunday": "Su", "publicholidays": "PH"}
_ITEMPROP_HOURS = re.compile(r"<[^>]*itemprop\s*=\s*[\"']openingHours[\"'][^>]*>", re.I)


def _jsonld_documents(raw: str):
    for block in _JSONLD.findall(raw or ""):
        for candidate in (block.strip(), htmllib.unescape(block.strip())):
            try:
                yield json.loads(candidate)
                break
            except ValueError:
                continue


def _hhmm(value: Any) -> str | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(value or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _spec_rules(specs: Any) -> list[str] | None:
    """openingHoursSpecification as OpenStreetMap-style rules ("Mo 06:00-23:00"), or None if unreadable."""
    rules = []
    for spec in specs if isinstance(specs, list) else [specs]:
        if not isinstance(spec, dict):
            continue
        if spec.get("validFrom") or spec.get("validThrough"):
            continue  # a holiday or a one-off change, not the regular week
        days = spec.get("dayOfWeek")
        codes = [_DAY_CODES.get(str(d).rstrip("/").split("/")[-1].lower()) for d in (days if isinstance(days, list) else [days])]
        if not codes or None in codes:
            return None
        codes = [c for c in codes if c != "PH"]
        opens, closes = _hhmm(spec.get("opens")), _hhmm(spec.get("closes"))
        if not codes or not opens or not closes:
            continue
        if opens == closes == "00:00":
            times = "off"  # schema.org's way of saying closed that day
        elif closes in ("23:59", "00:00") and opens == "00:00":
            times = "00:00-24:00"
        else:
            times = f"{opens}-{'24:00' if closes == '23:59' else closes}"
        rules.append(f"{','.join(codes)} {times}")
    return rules or None


def extract_hours(raw: str) -> dict[str, Any] | None:
    """A store's weekly opening hours from its own page, in the app's format, or None.

    Read from the structured data store sites publish for search engines: schema.org
    ``openingHoursSpecification`` or ``openingHours`` in JSON-LD, or an ``itemprop="openingHours"`` tag.
    Nothing is guessed: text that cannot be read with certainty gives None, and then the model reads the page.
    """
    from app.services import osm  # the OpenStreetMap hours reader understands the same "Mo-Fr 08:00-20:00" form

    for data in _jsonld_documents(raw):
        for node in _walk(data):
            if node.get("openingHoursSpecification"):
                rules = _spec_rules(node["openingHoursSpecification"])
                hours = osm.parse_opening_hours("; ".join(rules)) if rules else None
                if hours:
                    return hours
            text = node.get("openingHours")
            if text:
                hours = osm.parse_opening_hours("; ".join(text) if isinstance(text, list) else str(text))
                if hours:
                    return hours
    tags = [_attr(t, "content") or _attr(t, "datetime") for t in _ITEMPROP_HOURS.findall(raw or "")]
    tags = [t for t in tags if t]
    if tags:
        return osm.parse_opening_hours("; ".join(tags))
    return None


# --------------------------------------------------------------------------- #
# Fuel prices posted by a station ("Regular $3.899", "$4.19⁹ Premium", "Diesel: 4.459")
# --------------------------------------------------------------------------- #

_GRADES = {"regular": "regular", "unleaded": "regular", "reg": "regular", "regular unleaded": "regular",
           "premium": "premium", "super": "premium", "mid-grade": "midgrade", "midgrade": "midgrade", "mid grade": "midgrade",
           "plus": "midgrade", "diesel": "diesel"}
_GRADE = r"(regular unleaded|regular|unleaded|reg|premium|super|mid-?\s?grade|plus|diesel)"
_FUEL_PRICE = r"\$?\s*(\d\.\d{2})(\d|⁹|\s*9/10)?(?!\d)"
_GRADE_THEN_PRICE = re.compile(_GRADE + r"\b[^$\d\n]{0,25}" + _FUEL_PRICE, re.I)
_PRICE_THEN_GRADE = re.compile(r"\$\s*(\d\.\d{2})(\d|⁹|\s*9/10)?(?!\d)\s*(?:/\s*gal(?:lon)?)?\s*[-–:|]?\s*" + _GRADE + r"\b", re.I)


def _fuel_value(base: str, tenth: str | None) -> float | None:
    value = float(base)
    if tenth:
        value += 0.009 if ("9" in tenth or "⁹" in tenth) else int(tenth) / 1000
    return round(value, 3) if 1.5 <= value <= 9.99 else None


def _grade_of(word: str) -> str | None:
    word = re.sub(r"\s+", " ", word.lower())
    return _GRADES.get(word) or _GRADES.get(word.replace(" ", "")) or _GRADES.get(word.replace("- ", "-"))


AVERAGE_BEFORE = re.compile(
    r"\b(?:average[sd]?|avg\.?|mean|statewide|national(?:ly)?|typical(?:ly)?|last\s+year|was)\b(?:\W+[A-Za-z-]+){0,2}\W{0,6}$", re.I)
_GRADE_TOKEN = re.compile(r"\b" + _GRADE + r"\b", re.I)
_PRICE_TOKEN = re.compile(r"(\$)?\s?(\d\.\d{2})(\d|⁹|\s?9/10)?(?!\d)(?!\.\d)")


def fuel_prices(text: str) -> dict[str, float]:
    """{grade: price per gallon} posted in a page or search snippet.

    Grade names and prices are paired like a person reads a price board: each grade with its nearest price, next to
    it on the same line ("Regular $3.899", "$3.59⁹ Regular", "Regular: $3.64, Mid-Grade: $3.94"), each price used once;
    a pair split over two lines counts as far apart, and a price more than 25 characters away is not paired."""
    clean = (text or "").replace("\u00a0", " ")
    tokens = [(m.start(), m.end(), "grade", _grade_of(m.group(1))) for m in _GRADE_TOKEN.finditer(clean)]
    # a price is "sure" when it has a $ sign or a fuel-style third decimal; a bare 4.50 may be a rating or anything
    sure: dict[int, bool] = {}
    for m in _PRICE_TOKEN.finditer(clean):
        if AVERAGE_BEFORE.search(clean[max(0, m.start() - 30):m.start()]):
            continue  # an area average or an old price, not what this station charges now
        tokens.append((m.start(), m.end(), "price", _fuel_value(m.group(2), m.group(3))))
        sure[m.start()] = bool(m.group(1) or m.group(3))
    tokens = [t for t in sorted(tokens) if t[3]]
    pairs = []
    for a, b in zip(tokens, tokens[1:]):
        if a[2] == b[2]:
            continue
        between = clean[a[1]:b[0]]
        if len(between) > 25 or between.count("\n") > 1:
            continue  # too far apart, or across a table's rows
        grade_first = a[2] == "grade"
        price = b if grade_first else a
        if not sure.get(price[0]) and not (grade_first and len(between.strip(" :-–|")) == 0 and len(between) <= 3):
            continue  # a bare number only counts right after the grade's name ("Unleaded 3.79")
        gap = len(between) + (50 if "\n" in between else 0)
        pairs.append((gap, 0 if grade_first else 1, a[0], a, b))
    used, found = set(), {}
    for _, _, _, a, b in sorted(pairs):
        if a[0] in used or b[0] in used:
            continue
        grade, value = (a[3], b[3]) if a[2] == "grade" else (b[3], a[3])
        if grade in found:
            continue
        used.update({a[0], b[0]})
        found[grade] = value
    return found
