"""Reads an untrusted PDF in a separate, time-limited process (SPEC §17.5).

Run as `python -I pdf_worker.py '<limits JSON>'` with the file's bytes on stdin; writes one JSON object to stdout.
It needs only pypdf and the standard library, and it limits itself before reading anything: address space, CPU
time, no files written, few open files. The app also kills it after a wall-clock limit — a PDF made to make
the parser loop, or to inflate a stream without end, costs at most that.

Modes:
- "text": {"status": "ok" | "encrypted" | "scanned" | "failed", "pages", "texts": [one string per page]}.
  Password-protected PDFs (one that opens with an empty password is read) are "encrypted"; a PDF with no text
  on any page is "scanned". At most `pages` pages and `chars` characters.
- "images": the JPEG pictures on each page, for reading text with AI (a scanned PDF is usually one JPEG per
  page): {"status", "pages", "images": [{"page", "type", "b64"}]} — at most `images` of them, each ≤ `image_bytes`.
"""
import base64
import io
import json
import sys

try:
    import resource
except ImportError:      # not on Linux
    resource = None

DEFAULT_LIMITS = {"pages": 500, "chars": 2_000_000, "mem": 1024 * 1024 * 1024, "cpu": 60, "mode": "text",
                  "images": 10, "image_bytes": 8 * 1024 * 1024}
SCANNED_BELOW = 3           # fewer letters than this on every page together: a scan without text


def _limit_self(lim: dict) -> None:
    if resource is None:
        return
    for what, value in ((getattr(resource, "RLIMIT_AS", None), lim["mem"]),
                        (getattr(resource, "RLIMIT_CPU", None), lim["cpu"]),
                        (getattr(resource, "RLIMIT_FSIZE", None), 0),
                        (getattr(resource, "RLIMIT_NOFILE", None), 64)):
        if what is None:
            continue
        try:
            resource.setrlimit(what, (value, value))
        except (ValueError, OSError):
            pass


def _clean(text: str) -> str:
    lines = []
    for line in (text or "").replace("\r", "\n").split("\n"):
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _open(data: bytes):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        try:
            ok = reader.decrypt("")
        except Exception:            # e.g. AES without a crypto library: treat as protected
            ok = 0
        if not ok:
            return None
    return reader


def read_text(data: bytes, lim: dict) -> dict:
    reader = _open(data)
    if reader is None:
        return {"status": "encrypted", "pages": None, "texts": []}
    try:
        n = len(reader.pages)
    except Exception:
        return {"status": "encrypted", "pages": None, "texts": []}
    texts, total = [], 0
    for i in range(min(n, lim["pages"])):
        try:
            t = _clean(reader.pages[i].extract_text() or "")
        except Exception:
            t = ""
        if total + len(t) > lim["chars"]:
            t = t[:max(0, lim["chars"] - total)]
        total += len(t)
        texts.append(t)
        if total >= lim["chars"]:
            break
    letters = sum(sum(ch.isalnum() for ch in t) for t in texts)
    return {"status": "scanned" if letters < SCANNED_BELOW else "ok", "pages": n, "texts": texts}


def read_images(data: bytes, lim: dict) -> dict:
    reader = _open(data)
    if reader is None:
        return {"status": "encrypted", "pages": None, "images": []}
    out = []
    n = len(reader.pages)
    for i in range(min(n, lim["pages"])):
        try:
            res = reader.pages[i].get("/Resources") or {}
            xobjs = res.get("/XObject") or {}
            xobjs = xobjs.get_object() if hasattr(xobjs, "get_object") else xobjs
            for key in list(xobjs.keys()):
                obj = xobjs[key].get_object()
                if obj.get("/Subtype") != "/Image":
                    continue
                flt = obj.get("/Filter")
                flt = flt[0] if isinstance(flt, list) and len(flt) == 1 else flt
                if flt != "/DCTDecode":
                    continue
                raw = obj._data          # the JPEG itself (DCTDecode is passed through as it is)
                if not raw.startswith(b"\xff\xd8") or len(raw) > lim["image_bytes"]:
                    continue
                out.append({"page": i + 1, "type": "image/jpeg", "b64": base64.b64encode(raw).decode()})
                break                    # one picture per page is the scan
        except Exception:
            continue
        if len(out) >= lim["images"]:
            break
    return {"status": "ok" if out else "no_images", "pages": n, "images": out}


def main() -> None:
    lim = dict(DEFAULT_LIMITS)
    if len(sys.argv) > 1:
        try:
            lim.update(json.loads(sys.argv[1]))
        except ValueError:
            pass
    data = sys.stdin.buffer.read()
    _limit_self(lim)
    try:
        if not data.lstrip()[:5].startswith(b"%PDF"):
            out = {"status": "failed", "error": "This isn't a PDF."}
        elif lim.get("mode") == "images":
            out = read_images(data, lim)
        else:
            out = read_text(data, lim)
    except MemoryError:
        out = {"status": "failed", "error": "This PDF needs too much memory to read."}
    except RecursionError:
        out = {"status": "failed", "error": "This PDF is nested too deeply to read."}
    except Exception as e:      # a broken or hostile file: say so, never crash the app
        out = {"status": "failed", "error": f"This PDF couldn't be read ({type(e).__name__})."}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
