"""Checklists as Markdown task lists (SPEC §5.2, §7.2).

A `.md` file is a checklist when every non-blank line is `- [ ] text` or `- [x] text`, two spaces per indent
level (the app uses one level). Any other `.md` is a Markdown note; a checklist edited elsewhere into
something else opens as a note, and back again once it is a pure task list.

Items are found by a key: a hash of the item's text plus its occurrence number among items with the same
text ("Milk" twice → two keys), so two people changing different items never conflict, and an item someone
else just edited or deleted is reported as gone (409) instead of changing the wrong line.

Operations (applied to the current file, under the file's lock, by the docs router):
  add {text, after?: key|null, indent?: 0|1}   tick / untick {key}   edit {key, text}
  move {key, before?: key|null}                indent {key, level: 0|1}   delete {key}   untickAll {}
"""
import hashlib
import re
from dataclasses import dataclass

ITEM_RE = re.compile(r"^( *)- \[( |x|X)\](?: (.*))?$")
MAX_ITEMS = 2000
MAX_TEXT = 2000
OPS = ("add", "edit", "tick", "untick", "move", "indent", "delete", "untickAll")
TICK_OPS = ("tick", "untick", "untickAll")      # what a viewer may do when the share allows it


class OpError(ValueError):
    """An operation that can't be applied. `gone` = the item isn't there any more (a 409)."""

    def __init__(self, message: str, gone: bool = False):
        super().__init__(message)
        self.gone = gone


@dataclass
class Item:
    text: str
    done: bool = False
    level: int = 0
    key: str = ""
    tick: tuple | None = None        # (done_by, done_at) from the database, for ticks made in the app


def is_checklist(text: str) -> bool:
    """Every non-blank line is a task item, and there is at least one."""
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    return bool(lines) and all(ITEM_RE.match(ln.rstrip()) for ln in lines)


def md_kind(text: str, previous: str | None = None) -> str:
    """'checklist' or 'markdown' for a .md file's text. An empty file keeps the kind it had (a new checklist
    starts empty)."""
    if not text.strip():
        return previous if previous in ("checklist", "markdown") else "markdown"
    return "checklist" if is_checklist(text) else "markdown"


def text_key(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def assign_keys(items: list[Item]) -> list[Item]:
    seen: dict[str, int] = {}
    for it in items:
        k = text_key(it.text)
        n = seen.get(k, 0)
        seen[k] = n + 1
        it.key = f"{k}.{n}"
    return items


def parse(text: str) -> list[Item]:
    items = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        if not ln.strip():
            continue
        m = ITEM_RE.match(ln.rstrip())
        if not m:
            raise ValueError("Not a checklist: every line must be a task item.")
        items.append(Item(text=(m.group(3) or "").strip(), done=m.group(2) in "xX", level=1 if len(m.group(1)) >= 2 else 0))
    return assign_keys(items)


def serialise(items: list[Item]) -> str:
    if not items:
        return ""
    return "\n".join(f"{'  ' * it.level}- [{'x' if it.done else ' '}] {it.text}".rstrip() for it in items) + "\n"


def _clean_text(text) -> str:
    if not isinstance(text, str):
        raise OpError("An item's text must be text.")
    t = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if len(t) > MAX_TEXT:
        raise OpError(f"An item can be at most {MAX_TEXT} characters.")
    return t


def _find(items, key) -> int:
    for i, it in enumerate(items):
        if it.key == key:
            return i
    raise OpError("That item isn't there any more — someone may have changed it.", gone=True)


def apply(items: list[Item], op: dict) -> dict:
    """Apply one operation to `items` (in place; keys are re-assigned afterwards by the caller via
    assign_keys). Returns what changed for the tick records: {"ticked": [new item], "unticked": [...],
    "renamed": (old_key, item) or None, "removed": [keys]}. Raises OpError."""
    kind = op.get("op")
    out = {"ticked": [], "unticked": [], "renamed": None, "removed": [], "added": None}
    if kind not in OPS:
        raise OpError(f"Unknown operation {kind!r}.")
    if kind == "add":
        if len(items) >= MAX_ITEMS:
            raise OpError(f"A checklist can have at most {MAX_ITEMS} items.")
        text = _clean_text(op.get("text", ""))
        if not text:
            raise OpError("Type the item first.")
        level = 1 if op.get("indent") in (1, True) else 0
        it = Item(text=text, level=level)
        after = op.get("after")
        if after:
            i = _find(items, after)
            items.insert(i + 1, it)
        elif op.get("first"):
            items.insert(0, it)
        else:
            items.append(it)
        out["added"] = it
        return out
    if kind == "untickAll":
        for it in items:
            if it.done:
                it.done = False
                out["unticked"].append(it)
        return out
    i = _find(items, op.get("key"))
    it = items[i]
    if kind == "tick":
        if not it.done:
            it.done = True
            out["ticked"].append(it)
    elif kind == "untick":
        if it.done:
            it.done = False
            out["unticked"].append(it)
    elif kind == "edit":
        text = _clean_text(op.get("text", ""))
        if not text:
            raise OpError("An item can't be empty — delete it instead.")
        if text != it.text:
            out["renamed"] = (it.key, it)
            it.text = text
    elif kind == "indent":
        level = op.get("level")
        if level not in (0, 1):
            raise OpError("The indent level is 0 or 1.")
        it.level = level
    elif kind == "delete":
        out["removed"].append(it.key)
        del items[i]
    elif kind == "move":
        before = op.get("before")
        del items[i]
        if before:
            if before == it.key:
                items.insert(i, it)
            else:
                try:
                    j = _find(items, before)
                except OpError:
                    items.insert(i, it)
                    raise
                items.insert(j, it)
        else:
            items.append(it)
    return out
