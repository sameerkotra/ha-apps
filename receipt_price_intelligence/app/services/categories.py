"""Item categories: a starting guess from the item's name (pure, no database).

A person can always change an item's category; this only saves typing. Matching is by whole
words (plurals allowed), and the longest matching phrase wins, so "peanut butter" is Pantry
and not Dairy, and "orange juice" is Beverages and not Produce.
"""

from __future__ import annotations

import re

CATEGORIES = [
    "Produce", "Dairy & eggs", "Meat & seafood", "Bakery", "Pantry", "Frozen", "Beverages",
    "Snacks & sweets", "Household", "Personal care", "Baby", "Pets", "Other",
]

_RULES: dict[str, list[str]] = {
    "Produce": [
        "banana", "apple", "orange", "lemon", "lime", "tomato", "potato", "sweet potato", "onion", "garlic", "lettuce",
        "spinach", "kale", "carrot", "cucumber", "pepper", "bell pepper", "broccoli", "cauliflower", "avocado", "berry",
        "strawberry", "blueberry", "raspberry", "grape", "melon", "watermelon", "mushroom", "celery", "cabbage", "zucchini",
        "squash", "pear", "peach", "plum", "mango", "pineapple", "cilantro", "parsley", "basil", "salad", "produce", "corn",
        "asparagus", "beet", "radish", "ginger", "cherry", "kiwi", "grapefruit", "arugula", "scallion", "leek", "herb",
    ],
    "Dairy & eggs": [
        "milk", "oat milk", "almond milk", "soy milk", "cheese", "cream cheese", "yogurt", "yoghurt", "butter", "cream",
        "sour cream", "egg", "cottage cheese", "kefir", "half and half", "creamer", "whipped cream", "margarine",
    ],
    "Meat & seafood": [
        "chicken", "beef", "pork", "turkey", "bacon", "sausage", "ham", "steak", "ground beef", "salmon", "shrimp", "tuna fillet",
        "fish", "lamb", "hot dog", "deli", "brisket", "ribs", "cod", "tilapia", "crab", "lobster", "meatball",
    ],
    "Bakery": ["bread", "bagel", "bun", "roll", "tortilla", "muffin", "croissant", "cake", "donut", "doughnut", "pastry", "baguette", "pita", "sourdough"],
    "Pantry": [
        "rice", "pasta", "noodle", "flour", "sugar", "salt", "oil", "olive oil", "vinegar", "sauce", "tomato sauce", "ketchup", "mustard",
        "mayo", "mayonnaise", "peanut butter", "jam", "jelly", "honey", "syrup", "cereal", "oat", "oatmeal", "granola", "bean",
        "lentil", "soup", "broth", "stock", "canned", "spice", "seasoning", "baking soda", "baking powder", "yeast", "cocoa", "coffee",
        "tea", "chicken soup", "chicken broth", "chicken stock", "beef broth", "beef stock", "tomato soup", "nut", "almond", "walnut", "cashew", "peanut", "dressing", "salsa", "pickle", "olive", "tuna", "quinoa", "couscous",
    ],
    "Frozen": ["frozen", "ice cream", "pizza", "fries", "popsicle", "sorbet", "gelato", "waffle", "frozen dinner", "nugget"],
    "Beverages": [
        "water", "sparkling water", "juice", "orange juice", "apple juice", "soda", "cola", "coke", "pepsi", "sprite", "lemonade", "beer",
        "wine", "seltzer", "kombucha", "energy drink", "sports drink", "gatorade", "drink", "cider", "liquor", "vodka", "whiskey",
    ],
    "Snacks & sweets": [
        "chip", "cracker", "cookie", "candy", "chocolate", "snack", "pretzel", "popcorn", "gum", "bar", "granola bar", "gummy",
        "jerky", "trail mix", "brownie", "pudding", "potato chip", "tortilla chip", "corn chip", "veggie chip", "banana chip", "fruit snack",
    ],
    "Household": [
        "paper towel", "toilet paper", "tissue", "napkin", "detergent", "soap dish", "dish soap", "laundry", "bleach", "cleaner",
        "sponge", "trash bag", "garbage bag", "foil", "wrap", "plastic bag", "battery", "light bulb", "candle", "air freshener",
        "dishwasher", "disinfectant", "wipe", "storage bag", "zip bag",
    ],
    "Personal care": [
        "shampoo", "conditioner", "body wash", "toothpaste", "toothbrush", "deodorant", "razor", "lotion", "sunscreen", "floss", "mouthwash",
        "hand soap", "soap", "makeup", "cosmetic", "vitamin", "medicine", "ibuprofen", "tampon", "pad", "cotton", "bandage", "lip balm",
    ],
    "Baby": ["diaper", "baby", "formula", "baby food", "wipes baby", "pacifier"],
    "Pets": ["dog food", "cat food", "cat litter", "litter", "pet", "dog treat", "cat treat", "kibble"],
}


def _compile() -> list[tuple[re.Pattern, str, int]]:
    rules = []
    for category, phrases in _RULES.items():
        for phrase in phrases:
            pattern = re.compile(r"\b" + re.escape(phrase).replace(r"\ ", r"[\s-]+") + r"(?:s|es)?\b")
            rules.append((pattern, category, len(phrase)))
    rules.sort(key=lambda r: -r[2])  # longest phrase first
    return rules


_COMPILED = _compile()


def suggest_category(name: str | None) -> str | None:
    """A category for an item name, or None when nothing in the name is recognised."""
    if not name:
        return None
    text = re.sub(r"[^a-z0-9%&\s-]", " ", name.lower())
    if re.search(r"\b(?:soup|broth|stock)s?\b", text):  # "chicken noodle soup" is a pantry item, not meat
        return "Pantry"
    for pattern, category, _ in _COMPILED:
        if pattern.search(text):
            return category
    return None


def clean_category(value: str | None) -> str | None:
    """A category name as typed by a person: trimmed, single spaces, at most 60 characters."""
    cleaned = " ".join((value or "").split())[:60]
    return cleaned or None
