"""How names are put together (§13.7): name order and names in
Telugu / Hindi script.

The household's order is the App setting `name_order` (`given_first`, or
`surname_first` — the Telugu "Sharma Arjun"); any person can override it.
"""
import re

ORDERS = ("given_first", "surname_first")
_TELUGU = re.compile(r"[ఀ-౿]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def order(person_order: str | None) -> str:
    if person_order in ORDERS:
        return person_order
    from . import settings
    return settings.get("name_order")


def full(given: str | None, surname: str | None, nickname: str | None = None, person_order: str | None = None,
         fallback: str | None = "Unnamed") -> str | None:
    parts = (surname, given) if order(person_order) == "surname_first" else (given, surname)
    return " ".join(p for p in parts if p) or nickname or fallback


def script_of(text: str | None) -> str | None:
    """"te" for Telugu, "hi" for Devanagari (Hindi), None otherwise."""
    if not text:
        return None
    if _TELUGU.search(text):
        return "te"
    if _DEVANAGARI.search(text):
        return "hi"
    return None


def row_name(row) -> str:
    """A people row (any mapping with the name columns) → its display name."""
    get = row.get if hasattr(row, "get") else (lambda k: row[k] if k in row.keys() else None)
    return full(get("given_names"), get("surname"), get("nickname"), get("name_order"))


def people_names(conn, ids, include_deleted: bool = True) -> dict:
    """{person_id: display name} for the given ids."""
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    live = "" if include_deleted else " AND deleted_at IS NULL"
    return {r["id"]: row_name(r) for r in conn.execute(
        f"SELECT id, given_names, surname, nickname, name_order FROM people WHERE id IN ({q}){live}", list(ids))}
