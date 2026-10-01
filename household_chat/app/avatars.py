"""People's photos come from Home Assistant (SPEC §15.3.1).

A person's photo is the picture of their `person.*` entity — the one linked to their Home Assistant login,
or the one an admin chose in People → 🏠 instead (the same entity as home/away; presence.ENTITY_SQL).
Nobody uploads a photo in the app. The picture is copied into /data/avatars (256 px square JPEG,
orientation applied, EXIF and location removed) so pages never load anything from Home Assistant directly. When the picture changes in HA the copy follows; when it's
removed in HA — or the entity is deleted or no longer theirs — the copy is removed and initials show.

Checked at start-up, after an admin changes the entity, and every 10 minutes. If Home Assistant can't
be reached, nothing changes.
"""
import io
import logging
import os
import re
import urllib.request

from . import config, db, ha_client, presence
from .live import hub

logger = logging.getLogger("avatars")
MAX_BYTES = 8 * 1024 * 1024
SIZE = 256
LOCAL_HA = os.environ.get("HA_LOCAL_URL", "http://homeassistant:8123")


def file_name(uid: str) -> str:
    return f"{re.sub(r'[^A-Za-z0-9_-]', '_', uid)[:80]}.jpg"


def process(data: bytes) -> bytes:
    """Any image Pillow reads → a 256 px square JPEG without EXIF (ValueError if it isn't an image)."""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 80_000_000
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            w, h = im.size
            side = min(w, h)
            im = im.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
            im = im.resize((SIZE, SIZE), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)
            return out.getvalue()
    except Exception as e:
        raise ValueError("not an image") from e


def download(picture: str) -> bytes | None:
    """The bytes of an `entity_picture`. `/api/…` (a picture uploaded in Settings → People) goes through the
    Supervisor with the app's token; `/local/…` is read from Home Assistant's www folder without it.
    Pictures on other sites aren't fetched."""
    if picture.startswith("/api/"):
        status, body = ha_client.request("GET", picture[len("/api"):], timeout=15)
        return body if status == 200 and len(body) <= MAX_BYTES else None
    if picture.startswith("/local/"):
        try:
            with urllib.request.urlopen(LOCAL_HA + picture, timeout=15) as resp:
                body = resp.read(MAX_BYTES + 1)
                return body if resp.status == 200 and len(body) <= MAX_BYTES else None
        except Exception:
            return None
    return None


def _pictures() -> dict | None:
    """entity id → entity_picture (or None) for every person.*; None if HA can't be read."""
    states = ha_client.fetch_states_blocking(max_age=45)
    if states is None:
        return None
    out = {}
    for s in states:
        eid = str(s.get("entity_id", ""))
        if eid.startswith("person."):
            pic = (s.get("attributes") or {}).get("entity_picture")
            out[eid] = pic if isinstance(pic, str) and pic else None
    return out


def _remove(uid: str, name: str | None) -> None:
    if name:
        try:
            os.remove(os.path.join(config.AVATAR_DIR, name))
        except OSError:
            pass
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET avatar_file = NULL, avatar_src = NULL WHERE id = ?", (uid,))


def sync_blocking() -> bool:
    """Make every photo match Home Assistant. → True if any changed (and a refresh was published)."""
    if not ha_client.has_token():
        return False
    pictures = _pictures()
    if pictures is None:
        return False
    with db.get_conn() as conn:
        rows = conn.execute(f"SELECT id, {presence.ENTITY_SQL} AS entity, avatar_file, avatar_src FROM users").fetchall()
    changed = False
    for r in rows:
        pic = pictures.get(r["entity"]) if r["entity"] else None
        have = bool(r["avatar_file"]) and os.path.isfile(os.path.join(config.AVATAR_DIR, r["avatar_file"]))
        if not pic:
            if r["avatar_file"] or r["avatar_src"]:
                _remove(r["id"], r["avatar_file"])
                changed = True
            continue
        if pic == r["avatar_src"] and have:
            continue
        data = download(pic)
        if data is None:
            logger.warning("Couldn't download the picture of %s.", r["entity"])
            continue                      # keep what we have; try again next time
        try:
            jpeg = process(data)
        except ValueError:
            logger.warning("The picture of %s isn't an image this app can read.", r["entity"])
            continue
        os.makedirs(config.AVATAR_DIR, exist_ok=True)
        name = file_name(r["id"])
        path = os.path.join(config.AVATAR_DIR, name)
        with open(path + ".part", "wb") as f:
            f.write(jpeg)
        os.replace(path + ".part", path)
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET avatar_file = ?, avatar_src = ?, avatar_version = avatar_version + 1 WHERE id = ?",
                         (name, pic, r["id"]))
        changed = True
    if changed:
        hub.publish_all("presence", {"refresh": True})
    return changed
