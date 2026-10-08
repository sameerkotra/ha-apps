"""Telling callers apart without keys (SPEC.md §4).

Each app on the Supervisor's internal network has its own address, but the address changes when the app
restarts, and reverse lookups there aren't assured. So this works forwards: every household app's host name is
this app's own repository prefix plus the app's slug ("a1b2c3d4-household-ai" → the assistant is
"a1b2c3d4-household-assistant"), and the known household slugs are resolved every minute and on start-up.
A request's address is matched against them; one that matches none is "addr:<address>". A reverse lookup is a
second source, used only when it names a known household app.
"""
import asyncio
import logging
import socket

from . import config

logger = logging.getLogger("callers")

APPS = {
    "household_assistant": "Household Assistant",
    "calorie_tracker": "Calorie Tracker",
    "household_docs": "Household Docs",
    "household_arcade": "Household Arcade",
    "receipt_price_intelligence": "Receipt Price Intelligence",
    "finance": "Finance Dashboard",
    "household_todo": "Household Todo",
    "household_chat": "Household Chat",
    "family_tree": "Family Tree",
    "household_vault": "Household Vault",
    "splitpot": "Splitpot",
}

# What each AI app's own settings need for a model on the CPU (SPEC.md §5): the page shows it on its row.
ADVICE = {
    "household_assistant": "Admin → App settings → Limits: Longest a question may take 300 s or more.",
    "receipt_price_intelligence": "Timeout at least 300 s, and Receipts read at the same time 1. Use a vision "
                                  "model.",
    "finance": "Use a vision model for statements; allow several minutes per page.",
    "household_docs": "Allow 300 s or more for text from scans (a vision model).",
    "calorie_tracker": "Allow 120 s or more per answer.",
    "household_arcade": "Allow 120 s or more per answer.",
}

OWN_SUFFIX = "-" + config.SLUG.replace("_", "-")


def host_for(prefix: str, slug: str) -> str:
    return f"{prefix}-{slug.replace('_', '-')}"


def prefix(hostname: str | None = None) -> str | None:
    """The repository prefix from this app's own host name, or None when it isn't "<prefix>-household-ai"."""
    name = (hostname or config.own_hostname()).split(".")[0].lower()
    if name.endswith(OWN_SUFFIX) and len(name) > len(OWN_SUFFIX):
        return name[: -len(OWN_SUFFIX)]
    return None


def label(caller: str) -> str:
    if caller.startswith("addr:"):
        return f"Other app ({caller[5:]})"
    if caller.startswith("key:"):
        return caller
    return APPS.get(caller, caller)


class Resolver:
    """address → household app slug, refreshed with `refresh()` (every minute)."""

    def __init__(self, resolve=None, reverse=None):
        self.by_address: dict[str, str] = {}
        self._reverse_cache: dict[str, str | None] = {}
        self._resolve = resolve or self._getaddrinfo
        self._reverse = reverse or self._gethostbyaddr

    @staticmethod
    async def _getaddrinfo(host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), 3)
        except (OSError, asyncio.TimeoutError):
            return []
        return sorted({i[4][0] for i in infos})

    @staticmethod
    async def _gethostbyaddr(address: str) -> str | None:
        loop = asyncio.get_running_loop()
        try:
            name = await asyncio.wait_for(loop.run_in_executor(None, socket.gethostbyaddr, address), 2)
        except (OSError, asyncio.TimeoutError):
            return None
        return name[0] if name else None

    async def refresh(self, own_hostname: str | None = None) -> dict:
        pre = prefix(own_hostname)
        found: dict[str, str] = {}
        if pre:
            for slug in APPS:
                for addr in await self._resolve(host_for(pre, slug)):
                    found[addr] = slug
        if found != self.by_address:
            logger.info("Household apps on the internal network: %s",
                        ", ".join(f"{APPS[s]} {a}" for a, s in sorted(found.items())) or "none found")
        self.by_address = found
        self._reverse_cache.clear()
        return found

    async def caller(self, address: str | None, own_hostname: str | None = None) -> str:
        """The caller's name for an address: a household slug, or "addr:<address>"."""
        address = address or "unknown"
        slug = self.by_address.get(address)
        if slug:
            return slug
        if address not in self._reverse_cache:
            self._reverse_cache[address] = await self._reverse_slug(address, own_hostname)
        return self._reverse_cache[address] or f"addr:{address}"

    async def _reverse_slug(self, address: str, own_hostname: str | None) -> str | None:
        pre = prefix(own_hostname)
        if not pre or address == "unknown":
            return None
        name = await self._reverse(address)
        if not name:
            return None
        name = name.split(".")[0].lower()
        for slug in APPS:
            if name == host_for(pre, slug):
                return slug
        return None


RESOLVER = Resolver()
