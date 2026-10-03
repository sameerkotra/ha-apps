"""Pure normalization helpers used when an approved receipt is recorded.

No third-party imports, so they are unit-testable without a database. These produce
*matching keys* (so "KROGER #412" and "Kroger" land on the same chain) and best-effort
address parsing. They never invent data: anything that cannot be worked out is None.
"""

from __future__ import annotations

import re
from typing import Any

# Words that describe a store format or a legal suffix rather than the chain itself.
_GENERIC_STORE_WORDS = {
    "supercenter", "superstore", "super", "center", "inc", "llc", "co", "corp",
    "corporation", "company", "store", "stores", "the",
}
_STORE_NUMBER = re.compile(r"(?i)\b(?:store|str|no|number)?\s*#\s*\d+\b")


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip()) if t]


def _key_token(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", token.lower())


def normalize_store_name(raw: str | None) -> tuple[str, str] | None:
    """Return (display_name, match_key) for a printed store name, or None if blank.

    "KROGER #412"        -> ("Kroger", "kroger")
    "WALMART SUPERCENTER" -> ("Walmart", "walmart")
    "Trader Joe's"        -> ("Trader Joe's", "trader joes")
    """
    if not raw or not raw.strip():
        return None

    cleaned = _STORE_NUMBER.sub(" ", raw)
    tokens = _tokens(cleaned)
    kept = [t for t in tokens if _key_token(t) and _key_token(t) not in _GENERIC_STORE_WORDS]
    if not kept:  # the name was nothing but generic words; keep what was printed
        kept = [t for t in tokens if _key_token(t)]
    if not kept:
        return None

    display = " ".join(kept)
    letters = [c for c in display if c.isalpha()]
    if letters and all(c.isupper() for c in letters):  # receipts print in capitals
        display = " ".join(_title_word(w) for w in kept)

    key = " ".join(_key_token(t) for t in kept)
    return display, key


def _title_word(word: str) -> str:
    # str.title() would turn "joe's" into "Joe'S"
    return "-".join(p[:1].upper() + p[1:].lower() for p in word.split("-"))


_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_STATE_WORDS = re.compile(r"\b(" + "|".join(sorted(_STATE_NAMES, key=len, reverse=True)) + r")\b(?=[,\s]+\d{5}|\s*$|,)", re.I)
# street types, as printed and as written out; the key uses the short form
_STREET_TYPES = {
    "street": "st", "st": "st", "avenue": "ave", "ave": "ave", "av": "ave", "road": "rd", "rd": "rd", "drive": "dr", "dr": "dr",
    "boulevard": "blvd", "blvd": "blvd", "lane": "ln", "ln": "ln", "court": "ct", "ct": "ct", "place": "pl", "pl": "pl",
    "parkway": "pkwy", "pkwy": "pkwy", "pky": "pkwy", "highway": "hwy", "hwy": "hwy", "circle": "cir", "cir": "cir",
    "terrace": "ter", "ter": "ter", "trail": "trl", "trl": "trl", "way": "way", "loop": "loop", "square": "sq", "sq": "sq",
    "plaza": "plz", "plz": "plz", "crossing": "xing", "xing": "xing", "center": "ctr", "ctr": "ctr", "pike": "pike",
    "freeway": "fwy", "fwy": "fwy", "expressway": "expy", "expy": "expy", "turnpike": "tpke", "commons": "cmns",
}
_DIRECTIONS = {"north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne", "northwest": "nw", "southeast": "se",
               "southwest": "sw", "n": "n", "s": "s", "e": "e", "w": "w", "ne": "ne", "nw": "nw", "se": "se", "sw": "sw"}
_UNIT = re.compile(r"\b(?:ste|suite|unit|apt|bldg|building|space|spc|#)\.?\s*#?\s*([a-z0-9-]+)\b", re.I)
_PHONE = re.compile(r"(?:\b(?:tel|phone|ph)\b\.?:?\s*)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b", re.I)
_COUNTRY = re.compile(r"[,\s]*\b(?:usa|u\.s\.a\.|united states(?: of america)?|us)\s*$", re.I)
_ZIP_TAIL = re.compile(
    r"^(?P<city>.*?)[,\s]*\b(?P<state>[A-Za-z]{2})\b[,\s]+(?P<zip>\d{5}(?:-\d{4})?)\s*$"
)
_ZIP_ONLY = re.compile(r"^(?P<state>[A-Za-z]{2})[,\s]+(?P<zip>\d{5}(?:-\d{4})?)$")
_TYPE_WORDS = "|".join(sorted({k for k in _STREET_TYPES if len(k) > 1}, key=len, reverse=True))
# "123 E MAINSTREET SPRINGFIELD": the street runs up to its type word (a word ending in one, like MAINSTREET, too)
_STREET_THEN_CITY = re.compile(
    r"^(?P<street>\d+[A-Za-z]?(?:-\d+)?\s+.*?\b\w*(?:" + _TYPE_WORDS + r")\b\.?(?:\s+(?:n|s|e|w|ne|nw|se|sw)\b\.?)?"
    r"(?:\s+(?:ste|suite|unit|#)\.?\s*#?\s*[\w-]+)?)\s+(?P<city>[A-Za-z][A-Za-z .'-]*)$", re.I)


def parse_address(raw: str | None) -> dict[str, Any]:
    """Best-effort split of a printed US-style address into street, city, state and ZIP.

    Handles addresses on several lines or one ("123 E MAINSTREET SPRINGFIELD IL 62701"), with or without commas, full
    state names ("Springfield, Illinois 62701"), and leaves out phone numbers and "USA". Always returns the raw text; other
    parts are None when they cannot be read. ``unit`` is a suite or unit number, if printed.
    """
    result: dict[str, Any] = {
        "raw": None, "street": None, "city": None, "state": None, "postal_code": None, "unit": None,
    }
    if not raw or not raw.strip():
        return result

    text = re.sub(r"\s+", " ", raw.replace("\n", ", ")).strip(" ,")
    result["raw"] = text
    text = _PHONE.sub(" ", text)
    text = _COUNTRY.sub("", re.sub(r"\s+", " ", text)).strip(" ,")
    text = _STATE_WORDS.sub(lambda m: _STATE_NAMES[m.group(1).lower()], text)

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return result

    tail = _ZIP_TAIL.match(parts[-1])
    only = _ZIP_ONLY.match(parts[-1])
    if only and len(parts) >= 2:
        # "123 Main St, Springfield, IL 62701"
        result["state"] = only["state"].upper()
        result["postal_code"] = only["zip"]
        result["city"] = parts[-2]
        street_parts = parts[:-2]
    elif tail:
        # "123 Main St, Springfield IL 62701"  or  "Springfield IL 62701"
        result["state"] = tail["state"].upper()
        result["postal_code"] = tail["zip"]
        result["city"] = tail["city"].strip(" ,") or None
        street_parts = parts[:-1]
    else:
        street_parts = parts
        if len(parts) >= 2 and not re.search(r"\d", parts[-1]):
            result["city"], street_parts = parts[-1], parts[:-1]  # "5700 Lincoln Rd, Springfield": no state or ZIP printed

    # the city part still holds the street ("123 E MAINSTREET SPRINGFIELD"): split it at the street's type word
    if not street_parts and result["city"] and re.match(r"\s*\d", result["city"]):
        m = _STREET_THEN_CITY.match(result["city"])
        if m:
            street_parts, result["city"] = [m["street"]], m["city"].strip()
        else:
            words = result["city"].split()
            if len(words) >= 3:  # no type word: take the last word as the city
                street_parts, result["city"] = [" ".join(words[:-1])], words[-1]
            else:
                street_parts, result["city"] = [result["city"]], None

    if street_parts:
        numbered = next((i for i, p in enumerate(street_parts) if re.match(r"\d", p)), None)
        if numbered:  # "Store #412, 18901 E Mainstreet": the street starts at its house number
            street_parts = street_parts[numbered:]
        if len(street_parts) > 1 and re.fullmatch(r"\d+[A-Za-z]?", street_parts[0]):
            street_parts = [f"{street_parts[0]} {street_parts[1]}", *street_parts[2:]]  # "18901, East Mainstreet"
        street = ", ".join(street_parts)
        unit = _UNIT.search(street)
        if unit:
            result["unit"] = unit.group(1).lower()
        result["street"] = street
    return result


def _street_words(street: str) -> list[str]:
    """A street in comparable words: short types and directions, no suite ("18901 East Main Street, Ste 100" ->
    ["18901", "e", "main", "st"])."""
    text = _UNIT.sub(" ", street.lower()).replace(".", " ")
    words = []
    for w in re.findall(r"[a-z0-9]+", text):
        if w in _DIRECTIONS:
            words.append(_DIRECTIONS[w])
        elif w in _STREET_TYPES:
            words.append(_STREET_TYPES[w])
        else:
            # a type run into the name ("mainstreet" -> "main" "st")
            for full in ("street", "avenue", "road", "drive", "boulevard", "parkway", "lane", "court", "place", "highway"):
                if w.endswith(full) and len(w) > len(full) + 1:
                    words += [w[: -len(full)], _STREET_TYPES[full]]
                    break
            else:
                words.append(w)
    return words


def address_key(parsed: dict[str, Any]) -> str | None:
    """Matching key for a store address: street (in comparable words) + ZIP. None if too little is known.

    "18901 E MAINSTREET", "18901 East Main Street" and "18901 E. Main St." give the same key."""
    street = parsed.get("street")
    if not street:
        return None
    street_key = "".join(_street_words(street))
    zip_key = (parsed.get("postal_code") or "")[:5]
    return f"{street_key}|{zip_key}" if street_key else None


def edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance between two words, stopping early (returning limit + 1) once it is over ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _close(a: str, b: str) -> bool:
    """Two street names that differ by a misread letter or two ("cottonwood" / "cottonwod")."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    limit = 1 if min(len(a), len(b)) < 7 else 2
    return edit_distance(a, b, limit) <= limit


def same_address(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two parsed addresses are the same place: the same street number, the same street name (allowing a
    misread letter or two, and "Dr" / "Drive"), the same ZIP (or, without one, the same city), and not different
    suites (two shops in one shopping center are different places)."""
    if not a.get("street") or not b.get("street"):
        return False
    if a.get("unit") and b.get("unit") and a["unit"] != b["unit"]:
        return False
    za, zb = (a.get("postal_code") or "")[:5], (b.get("postal_code") or "")[:5]
    if za and zb:
        if za != zb:
            return False
    elif not (a.get("city") and b.get("city") and a["city"].strip().lower() == b["city"].strip().lower()):
        return False
    wa, wb = _street_words(a["street"]), _street_words(b["street"])
    if not wa or not wb or not wa[0].isdigit() or wa[0] != wb[0]:
        return False  # the street number must agree
    names_a = [w for w in wa[1:] if w not in _DIRECTIONS.values() and w not in _STREET_TYPES.values()]
    names_b = [w for w in wb[1:] if w not in _DIRECTIONS.values() and w not in _STREET_TYPES.values()]
    return bool(names_a and names_b) and _close("".join(names_a), "".join(names_b))


def similar_store_names(a: str, b: str) -> bool:
    """Whether two store name keys can be the same business ("king soopers" / "kingsoopers fuel" / "king soopers
    marketplace"), not two businesses at one address ("king soopers" / "starbucks")."""
    la, lb = re.sub(r"[^a-z0-9]", "", a.lower()), re.sub(r"[^a-z0-9]", "", b.lower())
    if not la or not lb:
        return False
    if la in lb or lb in la:
        return True
    first_a, first_b = (a.split() or [""])[0].lower(), (b.split() or [""])[0].lower()
    if len(first_a) >= 4 and first_a == first_b:
        return True
    return _close(la[:12], lb[:12])


def item_key(description: str | None) -> str | None:
    """Matching key for a printed item description ("GV 2% Milk  1GAL" -> "gv 2% milk 1gal")."""
    if not description:
        return None
    key = re.sub(r"[^a-z0-9% ]", " ", description.lower())
    key = re.sub(r"\s+", " ", key).strip()
    return key or None


def is_priceable(item: dict[str, Any]) -> bool:
    """True when a receipt line describes something bought at a positive price."""
    if (item.get("receipt_description") or "").strip() in ("", "(unreadable line)"):
        return False
    total = item.get("line_total")
    price = item.get("unit_price")
    return (total is not None and total > 0) or (total is None and price is not None and price > 0)


def derive_price(item: dict[str, Any]) -> tuple[float | None, str | None]:
    """The per-unit price to record: as printed when available, else worked out from the line.

    Returns (unit_price, unit). Weighted lines use the weight unit; others use "each".
    """
    price = item.get("unit_price")
    unit = item.get("unit_price_unit")
    if price is not None and price > 0:
        if not unit:
            unit = item.get("weight_unit") if item.get("weight_value") else "each"
        return price, unit

    total = item.get("line_total")
    if total is None or total <= 0:
        return None, None

    weight = item.get("weight_value")
    if weight and weight > 0:
        return round(total / weight, 4), item.get("weight_unit")

    quantity = item.get("quantity")
    if quantity is None or quantity <= 0:
        quantity = 1
    return round(total / quantity, 4), "each"


def derive_line_total(item: dict[str, Any], unit_price: float | None = None) -> float | None:
    """The amount paid for a line: as printed, else the unit price times the weight or quantity.

    A line can arrive with a unit price and quantity but no printed total (for example the
    "3 @ 1.99" line of an item bought several times). Without this its cost would be missing
    from spending and purchase history.
    """
    total = item.get("line_total")
    if total is not None:
        return total
    price = unit_price if unit_price is not None else item.get("unit_price")
    if price is None or price <= 0:
        return None
    weight = item.get("weight_value")
    if weight and weight > 0:
        return round(price * weight, 2)
    quantity = item.get("quantity")
    return round(price * (quantity if quantity and quantity > 0 else 1), 2)


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, giving up (returning limit + 1) once it must exceed ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def similar_chains(chains: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Pairs of chains that are probably the same store printed differently.

    ``chains`` is a list of (id, match_key). A pair is suggested when one key is the other
    plus whole extra words ("king soopers" / "king soopers marketplace") or the keys differ by
    a typo or two ("kroger" / "krogar"). Suggestions only; a person decides whether to merge.
    """
    pairs = []
    for i, (id_a, key_a) in enumerate(chains):
        for id_b, key_b in chains[i + 1:]:
            if not key_a or not key_b or key_a == key_b:
                continue
            short, long_ = sorted((key_a, key_b), key=len)
            reason = None
            if len(short) >= 4 and long_.startswith(short + " "):
                reason = "One name is the other plus extra words"
            else:
                limit = 1 if min(len(key_a), len(key_b)) < 9 else 2
                if min(len(key_a), len(key_b)) >= 5 and _edit_distance(key_a, key_b, limit) <= limit:
                    reason = "The names differ by only a letter or two"
            if reason:
                pairs.append({"a": id_a, "b": id_b, "reason": reason})
    return pairs


MAX_TAGS = 12
MAX_TAG_LENGTH = 30


def clean_tags(value) -> list[str]:
    """Tags typed by a person, as a tidy list: trimmed, no leading #, one copy of each (ignoring
    capitals), at most 12 of at most 30 characters. Accepts a list or a comma-separated string."""
    if value is None:
        return []
    parts = value.split(",") if isinstance(value, str) else list(value)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = " ".join(str(part).replace("#", " ").split())[:MAX_TAG_LENGTH].strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
        if len(out) >= MAX_TAGS:
            break
    return out
