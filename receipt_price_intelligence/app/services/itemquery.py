"""What to search the web for, for an item you buy, and which items and stores not to search at all.

Item names come from receipts and from people typing quickly, so they carry noise the web does not know:
"PRODUCE Large Hass Avocados", "1 Chocolate Mousse CC", "Aloo Gobi (GF, DF, V)", misspellings ("Avacado",
"Pomogranate", "peanut chikli"). The brand, which decides what a search finds, is usually only on the receipt
("1 FS LAXMI SOOJI ROASTED", "1164891 SILK ORG.ALM"). And some things on receipts cannot be priced online at all:
fuel, parking and other fees, restaurant dishes, clothing.

* ``search_name(item)``: the item's name cleaned, spelled right, with the brand from its receipts when there is one
* ``skip_reason(item)``: why an item is not looked up online (None if it is)
* ``is_grocery_chain(name, items)``: whether a store sells groceries (a restaurant or a car park does not), so
  items are not looked up there

``item``: the dicts ``webinfo._match_items`` gives: name, aliases (printed receipt descriptions), and, from
``trips.available_items``, category and the names of the stores it was bought at.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# Spelling
# --------------------------------------------------------------------------- #

# Grocery words spelled right (lower case). A word of an item's name that is not here but is one or two letters
# from one of them is taken as a misspelling of it ("avacado" -> "avocado").
VOCABULARY = set("""
apple apples apricot apricots avocado avocados banana bananas basil beans beet beets berries blackberries blueberry
blueberries broccoli brussels cabbage cantaloupe carrot carrots cauliflower celery cherries cherry chilli chillies chili
cilantro clementine clementines coconut corn cranberries cucumber cucumbers dates eggplant fennel figs garlic ginger grape
grapes grapefruit guava honeydew jalapeno kale kiwi leek leeks lemon lemons lettuce lime limes mango mangoes melon mint
mushroom mushrooms nectarine nectarines okra olive olives onion onions orange oranges papaya parsley peach peaches pear
pears peas pepper peppers pineapple plum plums pomegranate pomegranates potato potatoes pumpkin radish raspberries
raspberry romaine scallion scallions shallot spinach squash strawberries strawberry tangerine tomato tomatoes turnip
watermelon yam zucchini herbs dill thyme rosemary oregano sage lemongrass
milk butter cheese cheddar mozzarella parmesan yogurt yoghurt cream eggs egg paneer ghee buttermilk
bread bagel bagels tortilla tortillas bun buns rolls croissant muffin muffins loaf sourdough baguette artesano
rice flour sugar salt pepper oil vinegar honey pasta noodles spaghetti oats oatmeal cereal granola lentils beans
chickpeas quinoa almond almonds walnut walnuts cashew cashews peanuts peanut pistachios raisins sauce ketchup mustard mayonnaise jam jelly
coffee tea juice water soda chips cookies crackers biscuit biscuits chocolate candy popcorn pretzels nuts
chicken beef pork turkey lamb goat salmon shrimp tuna fish bacon sausage ham steak breast thighs ground
sooji suji semolina rava atta besan maida dal daal toor moong masoor chana urad masala garam turmeric cumin coriander
cardamom cloves cinnamon mustard fenugreek methi hing asafoetida jaggery poha makhana papad namkeen chikki tapioca
sago sabudana soya chunks wadi karela bhindi tindora lauki drumstick curry leaves pickle achar murukku mixture bhujia
diapers wipes tissue towels detergent soap handwash shampoo conditioner toothpaste toothbrush lotion vitamin vitamins
organic fresh frozen roasted crushed large small medium green red yellow white brown sweet seedless english hass gala
""".split())


def _edits(a: str, b: str, limit: int) -> int:
    from app.services.normalize import edit_distance
    return edit_distance(a, b, limit)


def close_words(a: str, b: str) -> bool:
    """Whether two words are the same word, allowing a typo or two in longer words ("avacado"/"avocado")."""
    a, b = a.lower(), b.lower()
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or a[0] != b[0]:
        return False
    return _edits(a, b, 1 if min(len(a), len(b)) < 8 else 2) <= (1 if min(len(a), len(b)) < 8 else 2)


def _plural_pair(a: str, b: str) -> bool:
    return a + "s" == b or b + "s" == a or a + "es" == b or b + "es" == a


def correct_word(word: str, extra: set[str] | None = None, preferred: set[str] | None = None) -> str:
    """The word spelled right, if it is a clear misspelling of a grocery word (or of a word on its receipts).

    Only the closest candidates count; a singular and its plural are the same candidate (the word's own form is
    kept). When still unclear, a word from one of your other items that shares a word with this one wins
    ("Crushed peanut chikli": "chikki", from "Peanut chikki", not "chilli"). Otherwise the word is left alone.
    """
    low = word.lower()
    if len(low) < 4 or not low.isalpha() or low in VOCABULARY or low in (preferred or set()):
        return word
    pool = VOCABULARY | (extra or set()) | (preferred or set())
    if any(_plural_pair(low, v) for v in pool):
        return word  # a plural or singular of a known word: spelled right
    scored = [(_edits(low, v, 2), v) for v in pool if v != low and close_words(low, v)]
    if not scored:
        return word
    best = min(d for d, _ in scored)
    options = {v for d, v in scored if d == best}
    # a singular and its plural are one choice: keep the form the word had
    for v in list(options):
        if any(_plural_pair(v, o) for o in options if o != v):
            wants_plural = low.endswith("s")
            if v.endswith("s") != wants_plural:
                options.discard(v)
    if len(options) > 1 and preferred:
        options = options & preferred or options
    if len(options) != 1:
        return word  # unclear which: leave it
    fixed = options.pop()
    return fixed.capitalize() if word[:1].isupper() else fixed


# --------------------------------------------------------------------------- #
# Brands, from the printed receipt descriptions
# --------------------------------------------------------------------------- #

# (pattern on the printed description, brand as it is searched for)
BRANDS = [
    (r"\blaxmi\b", "Laxmi"), (r"sunfeast|\bsf\s+drk|\bsf\s+dark", "Sunfeast"), (r"\bbalaji\b", "Balaji"),
    (r"\banand\b", "Anand"), (r"\bdeep\b", "Deep"), (r"\bswad\b", "Swad"), (r"haldiram", "Haldiram's"),
    (r"\bmdh\b", "MDH"), (r"\beverest\b", "Everest"), (r"\bparle\b", "Parle"), (r"britannia", "Britannia"),
    (r"\bshan\b", "Shan"), (r"\btelu?gu\b", "Telugu Foods"), (r"24\s*mantra", "24 Mantra"), (r"\bgits\b", "Gits"),
    (r"\bmtr\b", "MTR"), (r"\baashirvaad\b", "Aashirvaad"), (r"\bpillsbury\b", "Pillsbury"),
    (r"\bsilk\b", "Silk"), (r"charmin", "Charmin"), (r"huggies", "Huggies"), (r"pampers", "Pampers"),
    (r"\bbounty\b", "Bounty"), (r"\btide\b", "Tide"), (r"members?\s*mar|member'?s mark", "Member's Mark"),
    (r"kirkland|\bks\b", "Kirkland Signature"), (r"great value", "Great Value"), (r"\bnm\s+vit|nature made", "Nature Made"),
    (r"driscoll", "Driscoll's"), (r"artesano", "Sara Lee Artesano"), (r"tillamook", "Tillamook"),
    (r"horizon", "Horizon"), (r"fairlife", "Fairlife"), (r"chobani", "Chobani"), (r"oikos", "Oikos"),
    (r"dave'?s killer", "Dave's Killer Bread"), (r"nature'?s own", "Nature's Own"), (r"\b365\b", "365"),
    (r"good\s*&\s*gather", "Good & Gather"), (r"market pantry", "Market Pantry"), (r"simple truth", "Simple Truth"),
]


def brand_of(item: dict[str, Any]) -> str | None:
    """The brand printed on this item's receipts, when they all agree on one."""
    found = set()
    for text in item.get("aliases") or []:
        for pattern, brand in BRANDS:
            if re.search(pattern, text or "", re.I):
                found.add(brand)
    if len(found) != 1:
        return None  # none, or bought as different brands: search without one
    brand = found.pop()
    name = (item.get("name") or "").lower()
    return None if any(w.lower() in name for w in brand.split() if len(w) > 3) else brand


# --------------------------------------------------------------------------- #
# The name to search for
# --------------------------------------------------------------------------- #

_LEADING_COUNT = re.compile(r"^\s*\d+\s*[x×]?\s+(?=[A-Za-z])")
_DEPARTMENT = re.compile(r"^\s*(?:produce|deli|bakery|meat|seafood|dairy|frozen|grocery|grocer)\s+", re.I)
_TAGS = re.compile(r"\s*\([^)]*\)")                    # "(GF, DF, V)"
_TRAILING_CODE = re.compile(r"\s+\b[A-Z]{1,3}\b\s*$")  # "... CC"


def _receipt_words(item: dict[str, Any]) -> set[str]:
    """Whole words (5+ letters) on the item's receipts: the receipt often spells the product right."""
    words = set()
    for text in item.get("aliases") or []:
        words.update(w.lower() for w in re.findall(r"[A-Za-z]{5,}", text or ""))
    return words


def context_words(item: dict[str, Any], all_items: list[dict[str, Any]]) -> set[str]:
    """Words of the home's other items that share a word with this one: they decide unclear spellings."""
    own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", item.get("name") or "")}
    out = set()
    for other in all_items:
        if other is item or (item.get("id") and other.get("id") == item.get("id")):
            continue
        words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", other.get("name") or "")}
        if words & own:
            out |= words - own
    return out


def search_name(item: dict[str, Any], with_brand: bool = True, context: set[str] | None = None) -> str:
    """The item's name as it should be searched for: receipt noise removed, spelling fixed, brand added."""
    name = item.get("name") or ""
    name = _TAGS.sub("", name)
    name = _LEADING_COUNT.sub("", name)
    name = _DEPARTMENT.sub("", name)
    if _TRAILING_CODE.search(name) and len(name.split()) > 1:
        name = _TRAILING_CODE.sub("", name)
    extra = {w for w in _receipt_words(item) if w in VOCABULARY}
    name = " ".join(correct_word(w, extra, context) if w.isalpha() else w for w in name.split())
    name = " ".join(name.split())
    if with_brand:
        brand = brand_of(item)
        if brand:
            name = f"{brand} {name}"
    return name or (item.get("name") or "")


# --------------------------------------------------------------------------- #
# What not to look up
# --------------------------------------------------------------------------- #

_RESTAURANT = re.compile(
    r"\b(?:restaurant|cuisine|cuisi|cheesecake factory|grill|grille|cafe|café|bistro|diner|pizzeria|pizza|tavern|eatery|"
    r"taqueria|sushi|steakhouse|bbq|barbecue|burger|kitchen|biryani|dhaba|chaat|bakery cafe|coffee|starbucks|dunkin|"
    r"chipotle|mcdonald|wendy|subway|panera|taco bell|kfc|popeyes|domino|olive garden|applebee|chili'?s|ihop|denny)\b", re.I)
_FUEL = re.compile(r"^\s*(?:gas|gasoline|fuel|diesel|petrol|unleaded|regular unleaded|premium|e85)\s*$|\bpump\s*#?\s*\d+|\bunlead", re.I)
_FEE = re.compile(r"\b(?:fee|fees|parking|deposit|tip|gratuity|service charge|surcharge|bottle deposit|crv|toll|admission|ticket)\b", re.I)
_CLOTHING = re.compile(
    r"\b(?:shirt|t-?shirt|tee|dress|night ?dress|pajamas?|pj|pants|jeans|shorts|skirt|socks?|shoes?|sneakers|jacket|"
    r"sweater|hoodie|sweatshirt|underwear|bra|leggings|blouse|coat|scarf|gloves|hat|cap|apparel|clothing)\b", re.I)
_DISH_TAGS = re.compile(r"\((?:[^)]*\b(?:GF|DF|V|VG|VEGAN|SPICY)\b[^)]*)\)", re.I)


def fuel_grade(item: dict[str, Any]) -> str | None:
    """"regular", "midgrade", "premium" or "diesel" when the item is fuel (from its name, receipts and category),
    else None. Fuel is looked up on its own path (see ``webinfo.find_fuel_price``): per gallon, by grade."""
    name, category = item.get("name") or "", (item.get("category") or "").lower()
    texts = [name, *(item.get("aliases") or [])]
    if not (category in ("fuel", "gas", "gasoline") or any(_FUEL.search(t) for t in texts)):
        return None
    joined = " ".join(texts).lower()
    if re.search(r"diesel", joined):
        return "diesel"
    if re.search(r"premium|super|\b9[1-4]\b", joined):
        return "premium"
    if re.search(r"mid-?grade|\bplus\b|\b89\b", joined):
        return "midgrade"
    return "regular"


def is_restaurant(store_name: str | None) -> bool:
    return bool(_RESTAURANT.search(store_name or ""))


def skip_reason(item: dict[str, Any]) -> str | None:
    """Why this item is not looked up online, or None. Fuel, fees, restaurant dishes and clothing have no online
    price that can be compared with what you paid."""
    name, category = item.get("name") or "", (item.get("category") or "").lower()
    texts = [name, *(item.get("aliases") or [])]
    stores = [s for s in item.get("store_names") or [] if s]
    if category in ("fees", "services") or _FEE.search(name):
        return "a fee"
    if category in ("dining", "restaurant", "restaurants", "food & dining", "takeout") or _DISH_TAGS.search(name) \
            or (stores and all(is_restaurant(s) for s in stores)):
        return "a restaurant dish"
    if category in ("clothing", "apparel") or _CLOTHING.search(name):
        return "clothing (sizes and styles vary too much to compare)"
    return None


def is_grocery_chain(chain_name: str, items_bought: list[dict[str, Any]] | None = None) -> bool:
    """Whether items should be looked up at this store: not a restaurant, and (when known) not a store where everything
    bought was fuel, a fee, a dish or clothing (a car park, a petrol station)."""
    if is_restaurant(chain_name):
        return False
    if items_bought:
        # fuel does not make a store a grocery store (a petrol station); it is looked up on its own path
        return any(skip_reason(i) is None and not fuel_grade(i) for i in items_bought)
    return True
