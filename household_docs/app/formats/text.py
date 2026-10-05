"""Plain text files (SPEC §5.2): UTF-8, a byte-order mark kept if the file had one, line endings kept as
found (new files: "\\n"). The editor always works with "\\n"; `encode` puts the file's own ending back."""
from dataclasses import dataclass

BOM = b"\xef\xbb\xbf"


class NotText(ValueError):
    """The file isn't UTF-8 text (it can still be downloaded)."""


@dataclass
class TextMeta:
    bom: bool = False
    newline: str = "\n"


def decode(data: bytes) -> tuple[str, TextMeta]:
    meta = TextMeta()
    if data.startswith(BOM):
        meta.bom = True
        data = data[len(BOM):]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise NotText("This file isn't UTF-8 text, so the editor can't open it. Download it instead.") from None
    if "\x00" in text:
        raise NotText("This file looks like a binary file, so the editor can't open it. Download it instead.")
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf and crlf >= lf and crlf >= cr:
        meta.newline = "\r\n"
    elif cr > lf:
        meta.newline = "\r"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, meta


def encode(text: str, meta: TextMeta | None = None) -> bytes:
    meta = meta or TextMeta()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if meta.newline != "\n":
        text = text.replace("\n", meta.newline)
    data = text.encode("utf-8")
    return (BOM + data) if meta.bom else data


def index_text(data: bytes, limit_chars: int = 2_000_000) -> str:
    """Best-effort text for the search index (never raises)."""
    if data.startswith(BOM):
        data = data[len(BOM):]
    text = data.decode("utf-8", errors="replace")
    return text[:limit_chars]
