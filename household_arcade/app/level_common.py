"""What every level list shares: the error, the name check and the field check (SPEC §11.5)."""
from __future__ import annotations

import codecs
import re

NAME_MAX = 40
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&,.!?\-]*$")
# A short list of words a level name may not contain (children see the names). rot13 so this file
# doesn't spell them out.
_BLOCKED = set(codecs.decode(
    "fuvg|shpx|phag|ovgpu|qvpx|pbpx|chffl|fyhg|juber|avttre|snt|encr|anmv|xvyy|cbea|frk|qnza|penc|onfgneq|nefr|"
    "nff|cvff|gjng|jnax|qrnq|qvr|oybbq|tha|qehtf", "rot13").split("|"))


class LevelError(ValueError):
    """Why a level can't be used (shown on Admin → Levels and in the builder's log)."""



def check_name(name) -> str:
    if not isinstance(name, str):
        raise LevelError("the name must be text")
    name = " ".join(name.split())
    if not name or len(name) > NAME_MAX:
        raise LevelError(f"the name must be 1–{NAME_MAX} characters")
    if not _NAME_RE.match(name):
        raise LevelError("the name may only use letters, digits, spaces and simple punctuation")
    if set(re.findall(r"[a-z]+", name.lower())) & _BLOCKED:
        raise LevelError("the name uses a word that isn't allowed")
    return name


def _only(d, keys: set[str]) -> None:
    if not isinstance(d, dict):
        raise LevelError("a level must be an object")
    extra = set(d) - keys
    if extra:
        raise LevelError("unknown field(s): " + ", ".join(sorted(map(str, extra))))
    missing = keys - set(d)
    if missing:
        raise LevelError("missing field(s): " + ", ".join(sorted(missing)))
