"""Reading an HTTP answer with a size cap and an overall time limit.

A socket timeout only limits each wait for the next bytes: a server sending one byte every few seconds can keep a
plain ``response.read()`` going for hours, and an answer with no length can be any size. ``read_capped`` reads in
pieces and stops at ``limit`` bytes (``TooLarge``, or the first ``limit`` bytes with ``truncate``) and after
``deadline`` seconds in all (``TooSlow``).
"""

from __future__ import annotations

import time

CHUNK = 64 * 1024


class TooLarge(ValueError):
    pass


class TooSlow(TimeoutError):
    pass


def read_capped(response, limit: int, deadline: float, *, truncate: bool = False) -> bytes:
    end = time.monotonic() + deadline
    parts, total = [], 0
    while True:
        if time.monotonic() > end:
            raise TooSlow("the answer took too long to arrive")
        chunk = response.read(min(CHUNK, limit + 1 - total))
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
        if total > limit:
            if truncate:
                return b"".join(parts)[:limit]
            raise TooLarge("the answer is too large")
    return b"".join(parts)
