"""Typical weight of one piece of things sold both per piece and by weight, so a price "each" at one store and a
price per lb at another can be compared without weighing anything.

Grams per piece as sold (whole, with peel or skin), from typical medium sizes. These are averages: a converted
price is shown as an estimate ("≈ $3.23/lb, assuming about 0.44 lb each"). A weight printed on the receipt line (a
3 lb bag sold each) is used instead when there is one.
"""

from __future__ import annotations

import re

# (words in the item's name, grams per piece): longer names first, so "english cucumber" wins over "cucumber"
_TYPICAL = [
    ("english cucumber", 400), ("seedless cucumber", 400), ("sweet potato", 200), ("yam", 250), ("roma tomato", 70),
    ("plum tomato", 70), ("cherry tomato", 10), ("beefsteak tomato", 250), ("vine tomato", 130), ("red onion", 200),
    ("sweet onion", 250), ("vidalia onion", 250), ("green onion", 100), ("spring onion", 100), ("scallion", 100),
    ("bell pepper", 170), ("green pepper", 170), ("red pepper", 170), ("jalapeno", 15), ("serrano", 7), ("poblano", 80),
    ("chilli", 8), ("chili", 8), ("chile", 8),
    ("butternut squash", 1000), ("acorn squash", 700), ("spaghetti squash", 1300), ("yellow squash", 200),
    ("iceberg", 550), ("romaine heart", 200), ("romaine", 600), ("lettuce", 500), ("cabbage", 1000), ("cauliflower", 600),
    ("broccoli", 300), ("celery", 450), ("corn", 250), ("artichoke", 300), ("eggplant", 450), ("zucchini", 200),
    ("cucumber", 300), ("tomato", 150), ("onion", 200), ("potato", 250), ("carrot", 60), ("beet", 150), ("turnip", 150),
    ("leek", 250), ("fennel", 350), ("garlic", 50), ("shallot", 30), ("radish", 250), ("okra", 12), ("karela", 100),
    ("bitter gourd", 100), ("bottle gourd", 800), ("lauki", 800), ("drumstick", 60),
    ("avocado", 200), ("banana", 160), ("plantain", 280), ("lemon", 110), ("lime", 65), ("orange", 180), ("mandarin", 90),
    ("clementine", 75), ("tangerine", 90), ("grapefruit", 300), ("apple", 180), ("pear", 180), ("peach", 150),
    ("nectarine", 140), ("plum", 70), ("apricot", 40), ("mango", 300), ("kiwi", 75), ("pomegranate", 280), ("papaya", 900),
    ("guava", 150), ("cantaloupe", 1500), ("honeydew", 2000), ("watermelon", 5000), ("pineapple", 1500), ("coconut", 600),
    ("persimmon", 170), ("fig", 50), ("dragon fruit", 400),
]
_PATTERNS = [(re.compile(r"\b" + r"\s+".join(re.escape(w) for w in name.split()) + r"(?:e?s)?\b", re.I), grams, name)
             for name, grams in _TYPICAL]


def typical(item_name: str | None) -> tuple[float, str] | None:
    """(grams per piece, what it was taken as) for an item name, or None when it is not something with a typical size.
    A misspelt name is tried spelled right too ("Avacado")."""
    from app.services.itemquery import correct_word
    name = item_name or ""
    for text in (name, " ".join(correct_word(w) for w in name.split())):
        for pattern, grams, label in _PATTERNS:
            if pattern.search(text):
                return float(grams), label
    return None
