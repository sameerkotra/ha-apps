"""Jinja filters and helpers shared by every page (each route module installs them on its own environment).

money: one way to show an amount of money everywhere on screen — thousands separators, a $ sign, the minus
before the $, tabular figures and the .negative class (style.css). CSV and other exports keep raw numbers;
non-money numbers (kWh, %, MB) don't use it."""
from urllib.parse import parse_qsl, urlencode

from markupsafe import Markup, escape


def money_text(value, symbol: str = "$", plus: bool = False, decimals: int = 2, blank: str = "—") -> str:
    """-1234.5 → "-$1,234.50" (plain text, for attributes and titles)."""
    if value is None or value == "":
        return blank
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if round(n, decimals) == 0:
        n = 0.0
    sign = "-" if n < 0 else ("+" if plus and n > 0 else "")
    return f"{sign}{symbol}{abs(n):,.{decimals}f}"


def money(value, symbol: str = "$", plus: bool = False, decimals: int = 2, blank: str = "—", cls: str = "") -> Markup:
    """The same as money_text, wrapped in <span class="money[ negative]"> for tabular figures and colour."""
    text = money_text(value, symbol=symbol, plus=plus, decimals=decimals, blank=blank)
    try:
        negative = value is not None and value != "" and round(float(value), decimals) < 0
    except (TypeError, ValueError):
        negative = False
    classes = "money" + (" negative" if negative else "") + (" " + cls if cls else "")
    return Markup('<span class="{}">{}</span>').format(classes, text)


def with_qs(url: str, acting_qs: str | None) -> str:
    """url plus the acting user's query string (as_user=…), with the right ? or &."""
    if not acting_qs:
        return url
    head, hash_, frag = url.partition("#")
    return f"{head}{'&' if '?' in head else '?'}{acting_qs}{hash_}{frag}"


def without_as_user(url: str) -> str:
    """The current address without as_user (the "return to your own view" link). Relative (no leading slash)."""
    path, _, query = url.partition("?")
    page = path.rstrip("/").rsplit("/", 1)[-1] or "dashboard"
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "as_user"]
    return page + ("?" + urlencode(kept) if kept else "")


def period_month(ym: str | None) -> str:
    """"2026-10" → "October 2026" (a list's month header); anything else as it is."""
    import calendar
    try:
        y, m = (ym or "").split("-")[:2]
        return f"{calendar.month_name[int(m)]} {int(y)}"
    except (ValueError, IndexError):
        return ym or "No date"


def request_acting_qs(request) -> str:
    """as_user=… from the address, for an admin-only page whose route doesn't work out the acting user (debug views)."""
    v = request.query_params.get("as_user") if request is not None else None
    return urlencode({"as_user": v}) if v else ""


def install(env) -> None:
    env.globals["request_acting_qs"] = request_acting_qs
    env.globals["period_month"] = period_month
    env.filters["money"] = money
    env.filters["money_text"] = money_text
    env.filters["with_qs"] = with_qs
    env.globals["with_qs"] = with_qs
    env.globals["without_as_user"] = without_as_user
    env.globals["escape"] = escape
