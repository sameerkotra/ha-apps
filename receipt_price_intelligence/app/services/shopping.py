"""Turning free-text shopping list lines into the items you have bought before.

Pure functions: "2 eggs", "milk x2" and "Bananas" are matched to common item names by comparing
their words. Matches are suggestions for a person to confirm.
"""

import re
from typing import Any

_STOP = {"of", "the", "a", "an", "and", "for", "some", "more", "fresh", "organic", "large", "small",
         "kg", "g", "lb", "lbs", "oz", "l", "ml", "pack", "packs", "bag", "bags", "box", "boxes", "bottle", "bottles",
         "can", "cans", "jar", "jars", "dozen", "pkg"}
_QTY_LEADING = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:x|×)?\s+(?=\D)")
_QTY_TRAILING = re.compile(r"\s*(?:x|×)\s*(\d+(?:[.,]\d+)?)\s*$", re.I)
_QTY_PAREN = re.compile(r"\s*\((\d+(?:[.,]\d+)?)\)\s*$")


def parse_line(text: str) -> tuple[str, float]:
    """(name, quantity) from a list line. Quantity defaults to 1."""
    line = " ".join((text or "").split())
    for pattern in (_QTY_LEADING, _QTY_TRAILING, _QTY_PAREN):
        m = pattern.search(line)
        if m:
            qty = float(m.group(1).replace(",", "."))
            name = (line[m.end():] if pattern is _QTY_LEADING else line[:m.start()]).strip()
            if name and 0 < qty <= 999:
                return name, qty
    return line, 1.0


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "ses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return [_singular(w) for w in words if w not in _STOP and len(w) > 1]


def score(list_name: str, item_name: str) -> float:
    """0 to 1: how well a list line names an item."""
    a, b = tokens(list_name), tokens(item_name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    if not inter:
        return 0.0
    if sa == sb:
        return 1.0
    if sa < sb:
        return 0.85  # "eggs" names "Eggs, dozen"
    if sb < sa:
        return 0.55  # "oat milk" is probably not "Milk": worth a look, not a certainty
    return inter / len(sa | sb)


def match_lines(lines: list[str], items: list[dict[str, Any]], threshold: float = 0.5) -> list[dict[str, Any]]:
    """For each list line: its quantity, the best matching item (or None) and other candidates.

    ``items``: {id, name, purchases}. Ties go to the item bought most often.
    """
    out = []
    for text in lines:
        name, qty = parse_line(text)
        ranked = sorted(
            ((score(name, it["name"]), it.get("purchases", 0), it) for it in items),
            key=lambda x: (-x[0], -x[1]),
        )
        good = [r for r in ranked if r[0] >= threshold]
        best = good[0] if good else None
        out.append({
            "text": text, "name": name, "qty": qty,
            "match": {"id": best[2]["id"], "name": best[2]["name"], "score": round(best[0], 2)} if best else None,
            "candidates": [{"id": r[2]["id"], "name": r[2]["name"], "score": round(r[0], 2)} for r in good[:4]],
        })
    return out
