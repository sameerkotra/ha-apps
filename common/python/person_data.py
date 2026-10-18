"""One person's data: the export file, its checks, and the helpers every app's export / import / delete uses
(PERSON_DATA_SPEC.md §2, §9). Each app says which tables and files are a person's; this module writes and reads
the file and keeps the rules the same everywhere.

The file is one .zip:

    manifest.json        format, version, app, app version, schema, when, by whom, whose, counts, options, sha256s
    data/<table>.json    the person's rows: a JSON array of objects, columns by name
    data/<name>.json     records that are read, never imported (e.g. Arcade's matches, Docs' "elsewhere")
    files/…              the person's files (Finance PDFs, Docs documents)
    README.txt           what the file is

    write_package(dest, app=…, app_version=…, schema=…, person=…, exported_by=…, tables={…}, records={…},
                  files=[(arcname, path)], options={…})          → the manifest written
    read_package(path, app=…, schema=…, limits=…)                → Package (checked; raises PackageError)
    Package.tables / .records / .manifest / .file_names / .extract(name, dest) / .open(name)
    IdMap                                                        new ids for imported rows, and their references
    pick(row, columns)                                           a row with only the known columns
    names_match(typed, name)                                     the "type their name to confirm" check
    file_name(app_label, person_name)                            "calorie-tracker-alice-20261018-1235.zip"
    Jobs                                                         big exports in a thread; the file kept an hour
    Busy / one_at_a_time()                                       one import or delete at a time per app
    Uploads                                                      an uploaded file kept an hour, by token

Standard library only (and backup_core for the zip safety rules)."""
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
import unicodedata
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from . import backup_core

FORMAT = "household-person-data"
VERSION = 1
MANIFEST = "manifest.json"
README = "README.txt"
KEEP_SECONDS = 3600            # finished exports and uploaded files are deleted after an hour
TEMP_PREFIX = "person-data-"

README_TEXT = (
    "This file holds one person's data from {title}, exported by an admin on {when} (UTC).\n"
    "It is personal data (it may include {what}) and it is not encrypted: keep it somewhere safe and delete it when "
    "it is no longer needed.\n"
    "To bring it back: {title} → Admin → the people list → Import a person's data.\n"
    "Its contents: manifest.json (what this is), data/*.json (the rows, as JSON), files/ (the person's files, if "
    "any).\n")


class PackageError(ValueError):
    """A file that can't be imported; the message is for the admin."""


class Busy(RuntimeError):
    """Another import or delete is running in this app."""


@dataclass
class Limits:
    """What an uploaded file may be (zip bombs, huge files): defaults fit everything but Docs with files."""
    max_upload: int = 512 * 1024 * 1024        # the .zip itself
    max_unpacked: int = 4 * 1024 ** 3          # all members, unpacked
    max_members: int = 20000
    max_json: int = 256 * 1024 * 1024          # one data/*.json member
    max_rows: int = 2_000_000                  # rows in all tables together


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str, fallback: str = "person") -> str:
    """Lower-case ASCII letters, digits and hyphens (for file names)."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return t[:40] or fallback


def file_name(app_label: str, person_name: str, now: Optional[datetime] = None) -> str:
    """"Calorie Tracker", "Alice" → "calorie-tracker-alice-20261018-1235.zip" (local time of the server)."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"{slug(app_label, 'app')}-{slug(person_name)}-{stamp}.zip"


def names_match(typed, name) -> bool:
    """The delete / replace confirmation: the person's name, any case, extra spaces ignored."""
    norm = lambda s: " ".join(unicodedata.normalize("NFC", str(s or "")).split()).casefold()
    return bool(norm(name)) and norm(typed) == norm(name)


def pick(row: dict, columns: Iterable[str]) -> dict:
    """The row with only `columns` (unknown ones in a file are dropped, missing ones left out)."""
    return {c: row[c] for c in columns if c in row}


def _json_default(v):
    if isinstance(v, (bytes, bytearray)):
        raise TypeError("binary values aren't exported")
    return str(v)


def _dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> bool:
    return not backup_core.unsafe_name(name) and not name.endswith("/") and "\\" not in name


def write_package(dest, *, app: str, app_version: str, schema: int, person: dict, exported_by: str,
                  tables: dict, records: Optional[dict] = None, files: Iterable = (), options: Optional[dict] = None,
                  title: str = "", what: str = "their data", progress: Optional[Callable[[int, int], None]] = None
                  ) -> dict:
    """Writes the .zip to `dest` (a path or a binary file object). `tables` / `records`: {name: [row dicts]};
    `files`: (arcname under files/, path on disk) pairs. Returns the manifest."""
    records = records or {}
    files = list(files)
    when = _now()
    members: list[tuple[str, bytes]] = []
    counts: dict[str, int] = {}
    for name, rows in tables.items():
        rows = list(rows)
        counts[name] = len(rows)
        members.append((f"data/{name}.json", _dumps(rows)))
    for name, value in records.items():
        if name in tables:
            raise ValueError(f"{name} is both a table and a record")
        members.append((f"data/{name}.json", _dumps(value)))
    shas = {n: _sha(b) for n, b in members}
    file_members = []
    for arc, path in files:
        arc = "files/" + arc.lstrip("/")
        if not _safe_member(arc):
            raise ValueError(f"unsafe file name: {arc}")
        file_members.append((arc, path))
    counts["files"] = len(file_members)
    readme = README_TEXT.format(title=title or app, when=_iso(when), what=what).encode("utf-8")
    shas[README] = _sha(readme)
    total = len(file_members)
    for i, (arc, path) in enumerate(file_members):
        shas[arc] = _file_sha(path)
        if progress:
            progress(i + 1, total * 2)
    manifest = {
        "format": FORMAT, "v": VERSION, "app": app, "appVersion": app_version, "schema": int(schema),
        "exportedAt": _iso(when), "exportedBy": exported_by or "",
        "person": {k: person.get(k) for k in ("id", "username", "name") if person.get(k) is not None},
        "counts": counts, "options": options or {}, "sha256": shas,
    }
    own = isinstance(dest, (str, os.PathLike))
    f = open(dest, "wb") if own else dest
    try:
        with zipfile.ZipFile(f, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            z.writestr(README, readme)
            for name, data in members:
                z.writestr(name, data)
            for i, (arc, path) in enumerate(file_members):
                _add_file(z, path, arc)
                if progress:
                    progress(total + i + 1, total * 2)
    finally:
        if own:
            f.close()
    return manifest


def _add_file(z: zipfile.ZipFile, path: str, arc: str) -> None:
    """Adds one plain file. Opened without following a link (where the system allows), so a file swapped for a
    link after the app's own checks can't pull something else into the export."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as src:
        st = os.fstat(src.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"not a plain file: {arc}")
        info = zipfile.ZipInfo(arc, date_time=time.localtime(st.st_mtime)[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        with z.open(info, "w", force_zip64=True) as out:
            shutil.copyfileobj(src, out, 1024 * 1024)


@dataclass
class Package:
    """A checked export file. Close it (or use it in a `with`) when done."""
    manifest: dict
    tables: dict
    records: dict
    file_names: list
    _zip: zipfile.ZipFile = field(repr=False)

    @property
    def person(self) -> dict:
        return self.manifest.get("person") or {}

    @property
    def schema(self) -> int:
        return int(self.manifest.get("schema") or 0)

    def open(self, name: str):
        """A file member (one of file_names, "files/…") as a binary stream; its checksum was checked on reading."""
        if name not in self.file_names:
            raise KeyError(name)
        return self._zip.open(name)

    def size(self, name: str) -> int:
        """A file member's unpacked size in bytes."""
        if name not in self.file_names:
            raise KeyError(name)
        return self._zip.getinfo(name).file_size

    def extract(self, name: str, dest: str) -> None:
        """Copies a file member to `dest` (through a .part file, so a failure leaves no half file)."""
        if name not in self.file_names:
            raise KeyError(name)
        backup_core.copy_out(self._zip, name, dest)

    def close(self) -> None:
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_package(source, *, app: str, schema: int, limits: Optional[Limits] = None,
                 tables: Optional[Iterable[str]] = None) -> Package:
    """Opens and checks an export file (a path or a binary file object): a real zip with safe member names, within
    the limits, the manifest this format's and this app's, a schema no newer than `schema`, every member listed with
    a matching checksum and nothing unlisted, every data member a JSON array of objects. `tables`: the table names
    this app reads (others in data/ are kept as records). Raises PackageError with a sentence for the admin."""
    lim = limits or Limits()
    if isinstance(source, (str, os.PathLike)) and os.path.getsize(source) > lim.max_upload:
        raise PackageError("The file is too big.")
    try:
        z = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise PackageError("That isn't an export file (not a .zip).") from None
    try:
        infos = z.infolist()
        if len(infos) > lim.max_members:
            raise PackageError("The file has too many members.")
        total = 0
        names = set()
        for info in infos:
            n = info.filename
            if info.is_dir():
                continue
            if not _safe_member(n) or n in names:
                raise PackageError(f"The file has an unsafe or repeated name inside: {n[:80]}")
            if info.flag_bits & 0x1:
                raise PackageError("The file is password-protected; export it again without a password.")
            names.add(n)
            total += info.file_size
            if total > lim.max_unpacked:
                raise PackageError("The file unpacks to too much data.")
        if MANIFEST not in names:
            raise PackageError("That isn't an export file (no manifest.json).")
        try:
            manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise PackageError("The export file's manifest can't be read.") from None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise PackageError("That isn't a household person-data export.")
        if manifest.get("v") != VERSION:
            raise PackageError("This export was made by a newer version of the format; update the app first.")
        if manifest.get("app") != app:
            got = str(manifest.get("app") or "another app")[:60]
            raise PackageError(f"This export is from {got}, not this app.")
        try:
            got_schema = int(manifest.get("schema") or 0)
        except (TypeError, ValueError):
            raise PackageError("The export file's manifest can't be read.") from None
        if got_schema > schema:
            raise PackageError("This export was made by a newer version of the app; update this app first.")
        shas = manifest.get("sha256")
        if not isinstance(shas, dict):
            raise PackageError("The export file's checksums are missing.")
        listed = set(shas)
        if names - {MANIFEST} != listed:
            raise PackageError("The export file was changed: its members don't match its manifest.")
        want = set(tables) if tables is not None else None
        tabs, recs, rows_total = {}, {}, 0
        file_names = []
        for n in sorted(names - {MANIFEST}):
            info = z.getinfo(n)
            if n.startswith("data/") and n.endswith(".json"):
                if info.file_size > lim.max_json:
                    raise PackageError(f"{n} is too big.")
                data = z.read(n)
                if _sha(data) != shas[n]:
                    raise PackageError(f"The export file was changed: {n} doesn't match its checksum.")
                try:
                    value = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise PackageError(f"{n} can't be read.") from None
                key = n[len("data/"):-len(".json")]
                if "/" in key:
                    raise PackageError(f"Unexpected member {n}.")
                is_rows = isinstance(value, list) and all(isinstance(r, dict) for r in value)
                if (want is None and is_rows) or (want is not None and key in want):
                    if not is_rows:
                        raise PackageError(f"{n} isn't a list of rows.")
                    rows_total += len(value)
                    if rows_total > lim.max_rows:
                        raise PackageError("The file has too many rows.")
                    tabs[key] = value
                else:
                    recs[key] = value
            elif n.startswith("files/"):
                h = hashlib.sha256()
                with z.open(n) as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if h.hexdigest() != shas[n]:
                    raise PackageError(f"The export file was changed: {n} doesn't match its checksum.")
                file_names.append(n)
            elif n == README:
                if _sha(z.read(n)) != shas[n]:
                    raise PackageError("The export file was changed: README.txt doesn't match its checksum.")
            else:
                raise PackageError(f"Unexpected member {n[:80]}.")
        return Package(manifest=manifest, tables=tabs, records=recs, file_names=file_names, _zip=z)
    except BaseException:
        z.close()
        raise


class IdMap:
    """New ids for imported rows: remember old → new per table, then rewrite the references.

        ids = IdMap()
        ids.put("saved_foods", old_id, new_id)
        ids.get("saved_foods", row["saved_food_id"])     # the new id, or None when it wasn't imported"""

    def __init__(self):
        self._m: dict = {}

    def put(self, table: str, old, new) -> None:
        if old is not None:
            self._m.setdefault(table, {})[str(old)] = new

    def get(self, table: str, old, default=None):
        if old is None:
            return default
        return self._m.get(table, {}).get(str(old), default)

    def has(self, table: str, old) -> bool:
        return old is not None and str(old) in self._m.get(table, {})

    def count(self, table: str) -> int:
        return len(self._m.get(table, {}))


def new_token() -> str:
    return secrets.token_urlsafe(18)


_lock = threading.Lock()


@contextmanager
def one_at_a_time(lock: threading.Lock = _lock, wait: float = 0.0):
    """One import or delete at a time in this app (per process): raises Busy when another is running."""
    got = lock.acquire(timeout=wait) if wait else lock.acquire(blocking=False)
    if not got:
        raise Busy("Another import or delete is running — try again in a minute.")
    try:
        yield
    finally:
        lock.release()


class Uploads:
    """Uploaded export files kept for KEEP_SECONDS between the preview and the import, by token, with who uploaded
    them (only the same admin may use the token)."""

    def __init__(self, folder: str, prefix: str = TEMP_PREFIX + "up-"):
        self.folder, self.prefix = folder, prefix
        self._items: dict = {}
        self._lock = threading.Lock()

    def add(self, path: str, by: str, info: Optional[dict] = None) -> str:
        self.sweep()
        token = new_token()
        with self._lock:
            self._items[token] = {"path": path, "by": by, "at": time.time(), "info": info or {}}
        return token

    def get(self, token: str, by: str) -> Optional[dict]:
        self.sweep()
        with self._lock:
            item = self._items.get(token or "")
        return item if item and item["by"] == by and os.path.exists(item["path"]) else None

    def drop(self, token: str) -> None:
        with self._lock:
            item = self._items.pop(token or "", None)
        if item:
            try:
                os.remove(item["path"])
            except OSError:
                pass

    def sweep(self) -> None:
        cutoff = time.time() - KEEP_SECONDS
        with self._lock:
            old = [t for t, i in self._items.items() if i["at"] < cutoff]
        for t in old:
            self.drop(t)
        backup_core.remove_stale(self.folder, self.prefix, KEEP_SECONDS, suffixes=(".zip", ".part"))


class Jobs:
    """Big exports in a background thread: start(fn) → id; fn(progress) writes the file and returns its path.
    status(id) → {state: running|done|failed, done, total, error?}; the finished file is kept KEEP_SECONDS and then
    deleted. Jobs belong to the admin who started them."""

    def __init__(self):
        self._jobs: dict = {}
        self._lock = threading.Lock()

    def start(self, fn: Callable, *, by: str, filename: str) -> str:
        self.sweep()
        jid = new_token()
        job = {"state": "running", "done": 0, "total": 0, "by": by, "filename": filename, "path": None,
               "error": None, "at": time.time()}
        with self._lock:
            self._jobs[jid] = job

        def progress(done: int, total: int) -> None:
            job["done"], job["total"] = done, total

        def run():
            try:
                job["path"] = fn(progress)
                job["state"] = "done"
            except Exception as e:      # the admin sees the reason; the log has the rest
                job["state"], job["error"] = "failed", str(e) or e.__class__.__name__
            job["at"] = time.time()

        threading.Thread(target=run, name=f"person-export-{jid[:6]}", daemon=True).start()
        return jid

    def get(self, jid: str, by: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(jid or "")
        return job if job and job["by"] == by else None

    def status(self, jid: str, by: str) -> Optional[dict]:
        self.sweep()                     # finished files older than an hour go even if no new job ever starts
        job = self.get(jid, by)
        if not job:
            return None
        out = {"state": job["state"], "done": job["done"], "total": job["total"], "filename": job["filename"]}
        if job["error"]:
            out["error"] = job["error"]
        return out

    def sweep(self) -> None:
        cutoff = time.time() - KEEP_SECONDS
        with self._lock:
            old = [j for j, v in self._jobs.items() if v["state"] != "running" and v["at"] < cutoff]
            gone = [self._jobs.pop(j) for j in old]
        for v in gone:
            if v.get("path"):
                try:
                    os.remove(v["path"])
                except OSError:
                    pass


def temp_path(folder: str, prefix: str = TEMP_PREFIX, suffix: str = ".zip") -> str:
    """A new, empty temporary file in `folder` (the app's data folder, so big files stay on its disk)."""
    os.makedirs(folder, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=folder)
    os.close(fd)
    return path


def count_list(items: Iterable) -> list:
    """[(key, label, count)] → the "counts" list the dialogs show: [{key, label, count}] (zero counts kept)."""
    return [{"key": k, "label": label, "count": int(n or 0)} for k, label, n in items]


def bytes_text(n: int) -> str:
    """1536 → "1.5 KB" (for the dialogs)."""
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def stream(path: str, chunk: int = 1 << 20):
    """A file's bytes in chunks (for a StreamingResponse)."""
    with open(path, "rb") as f:
        for part in iter(lambda: f.read(chunk), b""):
            yield part


def bytes_io_package(**kw) -> bytes:
    """write_package into memory (small exports and tests)."""
    buf = io.BytesIO()
    write_package(buf, **kw)
    return buf.getvalue()
