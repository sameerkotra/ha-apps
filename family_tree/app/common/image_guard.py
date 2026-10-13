# Shared file: edit common/python/image_guard.py and run tools/sync_common.py; don't edit this copy. sha256=aa0cf132df24f366a5a024ae9562eb3b7e498f1b76ce70a5f15276dd2fdf6566
"""Checking an uploaded image before it is decoded (shared by the household apps: common/python/image_guard.py,
copied into each app's app/common/ by tools/sync_common.py).

Decoding an image is where an upload can hurt: a 100-byte PNG can declare 100 000 × 100 000 pixels and
make Pillow allocate gigabytes; ten uploads decoded at once do it ten times. So:

    from .common import image_guard

    fmt, w, h = image_guard.check_image(data_or_fileobj, max_pixels=40_000_000)   # header only, no decode
    with image_guard.decode_slot():                              # at most 2 decodes at a time, process-wide
        img = Image.open(io.BytesIO(data)); img.load(); ...

`check_image` opens the file with Pillow and reads its header only (`Image.open` is lazy: the size comes
from the header; nothing is decoded), raising `NotAnImage` when Pillow doesn't know the format (or it isn't
one of `formats`, when given) and `ImageTooBig` when width × height is over `max_pixels` (or a side is over
`max_side`). It returns (format, width, height) — format is Pillow's name: "JPEG", "PNG", "WEBP", "GIF" …
For a JPEG, `draft()` lets a later `load()` decode at a smaller scale: pass `draft=("RGB", (w, h))` to
`open_for_decode()` to get the image already drafted.

`decode_slot(timeout=30)` is a context manager around the actual decode (`load()`, `thumbnail()`,
`save()`): a process-wide semaphore of `DECODE_SLOTS` (2), so uploads queue instead of decoding together;
raises `DecodeBusy` after `timeout` seconds. Use it in a thread (sync route / run_in_threadpool), never on
the event loop.

On import (when Pillow is there) `Image.MAX_IMAGE_PIXELS` is set to `MAX_IMAGE_PIXELS` (40 million) and
Pillow's DecompressionBombWarning (which it only *warns* with between the limit and twice the limit) is
turned into an error, so no code path decodes a bomb by accident.

Pillow is optional: an app without it imports this module fine and `check_image` raises `PillowMissing`.
"""
from __future__ import annotations

import contextlib
import io
import threading
import warnings

MAX_IMAGE_PIXELS = 40_000_000
MAX_SIDE = 20_000
DECODE_SLOTS = 2

try:
    from PIL import Image
except ImportError:                                  # an app without Pillow
    Image = None

if Image is not None:
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)


class ImageError(ValueError):
    """Base of the errors below (the message can be shown to the person)."""


class NotAnImage(ImageError):
    pass


class ImageTooBig(ImageError):
    pass


class PillowMissing(ImageError):
    pass


class DecodeBusy(RuntimeError):
    """decode_slot(): no decode slot came free within the timeout."""


_slots = threading.BoundedSemaphore(DECODE_SLOTS)
_open_lock = threading.Lock()


def _open(source, formats, max_pixels: int):
    """Image.open (header only). When a caller allows more than Pillow's limit, Pillow's own bomb check is
    raised to `max_pixels` for the call (under a lock: it is a module global), so that caller gets its own
    limit; the size is checked against `max_pixels` by check_image right after. (The warning is an error
    process-wide since import, so nothing here touches the warnings filters, which aren't thread-safe.)"""
    if Image is None:
        raise PillowMissing("Image support (Pillow) isn't installed in this app.")
    fp = io.BytesIO(source) if isinstance(source, (bytes, bytearray, memoryview)) else source
    if hasattr(fp, "seek"):
        fp.seek(0)
    with _open_lock:
        before = Image.MAX_IMAGE_PIXELS
        try:
            if before and int(max_pixels) > before:
                Image.MAX_IMAGE_PIXELS = int(max_pixels)      # this caller's own, larger limit
            return Image.open(fp, formats=list(formats) if formats else None)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
            raise ImageTooBig("That image is too large (too many pixels).") from e
        except (OSError, ValueError, SyntaxError) as e:        # UnidentifiedImageError is an OSError
            raise NotAnImage("That file isn't an image this app can read.") from e
        finally:
            Image.MAX_IMAGE_PIXELS = before


def check_image(source, max_pixels: int = MAX_IMAGE_PIXELS, *, max_side: int = MAX_SIDE,
                formats=None) -> tuple[str, int, int]:
    """(format, width, height) from the header only — nothing is decoded. `source`: bytes or a binary file
    object (left at position 0 afterwards when it can seek). Raises NotAnImage / ImageTooBig / PillowMissing."""
    img = _open(source, formats, max_pixels)
    try:
        w, h = img.size
        fmt = img.format or ""
    finally:
        if not hasattr(source, "read"):
            img.close()                                   # our own BytesIO; a caller's file stays open
        else:
            img.fp = None                                 # the image is dropped, the caller's file is theirs
            source.seek(0)
    if w <= 0 or h <= 0:
        raise NotAnImage("That image has no size.")
    if w > max_side or h > max_side or w * h > max_pixels:
        raise ImageTooBig(f"That image is too large ({w} × {h} pixels).")
    return fmt, w, h


def open_for_decode(source, max_pixels: int = MAX_IMAGE_PIXELS, *, max_side: int = MAX_SIDE, formats=None,
                    draft: tuple | None = None):
    """check_image, then the still-undecoded Pillow image (close it yourself). `draft=("RGB", (w, h))`
    asks a JPEG decoder for the smallest scale at least that size (much less memory for thumbnails)."""
    check_image(source, max_pixels, max_side=max_side, formats=formats)
    img = _open(source, formats, max_pixels)
    if draft and img.format == "JPEG":
        img.draft(*draft)
    return img


@contextlib.contextmanager
def decode_slot(timeout: float | None = 30):
    """At most DECODE_SLOTS image decodes at a time in this process (DecodeBusy after `timeout` s)."""
    if not _slots.acquire(timeout=timeout):
        raise DecodeBusy("Too many images are being processed right now; try again in a moment.")
    try:
        yield
    finally:
        _slots.release()
