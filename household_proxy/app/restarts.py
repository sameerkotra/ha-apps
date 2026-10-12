"""Auto-restart (spec/SPEC.md §13.3): power-cycling a stuck device with a Home Assistant switch. Phase 4 has only the
hooks the watchdog calls; the restart sequences and their safety rules come with phase 5."""


def on_up(conn, row, upd) -> None:
    """A site answered again."""


def on_failed(conn, row, upd, now, events) -> None:
    """A check failed (after the state was moved)."""


def may_check(row, now) -> bool:
    """Whether a due check may run now (no checks while waiting after a restart)."""
    return True


async def advance(now) -> None:
    """Move restart sequences that are waiting on time."""


def recover() -> None:
    """At start-up: turn back on any switch this app left off."""
