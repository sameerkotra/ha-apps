"""Photo fixes for scans (§13.19): rotate by quarter turns, straighten
(±15°), crop and auto contrast — non-destructive. The original file is never
changed; the recipe lives in `media.edit` and the edited display file and
thumbnails are rendered from original + recipe (cached per recipe, so an Undo
simply shows the older recipe's files again).

Tag boxes (§13.2) are stored against the original. `box_to_display` /
`box_to_original` move them through the same steps, so rotating or cropping
never puts a tag on the wrong face.

Recipe: {"rotate": 0|90|180|270 (clockwise), "angle": -15…15 (degrees,
positive = counter-clockwise, like Pillow), "crop": {x, y, w, h} fractions of
the straightened picture, or null, "autocontrast": bool}.
"""
import hashlib
import json
import math

MAX_ANGLE = 15


class EditError(ValueError):
    pass


def clean(edit) -> dict | None:
    """Validate a recipe; None when it changes nothing."""
    if edit is None:
        return None
    if not isinstance(edit, dict) or set(edit) - {"rotate", "angle", "crop", "autocontrast"}:
        raise EditError("An edit has rotate, angle, crop and autocontrast.")
    rot = edit.get("rotate", 0) or 0
    if rot not in (0, 90, 180, 270):
        raise EditError("Rotate by 0, 90, 180 or 270 degrees.")
    angle = edit.get("angle", 0) or 0
    if not isinstance(angle, (int, float)) or isinstance(angle, bool) or not -MAX_ANGLE <= angle <= MAX_ANGLE \
            or angle != angle:
        raise EditError(f"Straighten by at most {MAX_ANGLE}° either way.")
    crop = edit.get("crop")
    if crop is not None:
        try:
            x, y, w, h = (float(crop[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise EditError("A crop has x, y, w and h (fractions of the picture).")
        if not (0 <= x < 1 and 0 <= y < 1 and 0.05 <= w <= 1 and 0.05 <= h <= 1):
            raise EditError("The crop has to stay inside the picture and keep at least 5% of it.")
        w, h = min(w, 1 - x), min(h, 1 - y)
        crop = {"x": round(x, 5), "y": round(y, 5), "w": round(w, 5), "h": round(h, 5)}
        if crop == {"x": 0, "y": 0, "w": 1, "h": 1}:
            crop = None
    auto = bool(edit.get("autocontrast", False))
    out = {"rotate": rot, "angle": round(float(angle), 2), "crop": crop, "autocontrast": auto}
    if not rot and not out["angle"] and not crop and not auto:
        return None
    return out


def key(edit: dict | None) -> str | None:
    return hashlib.sha1(json.dumps(edit, sort_keys=True).encode()).hexdigest()[:12] if edit else None


def _inner_scale(w: float, h: float, angle: float) -> float:
    """How much of a w×h picture turned by `angle` can be kept without corners."""
    a = abs(math.radians(angle))
    if not a:
        return 1.0
    c, s = math.cos(a), math.sin(a)
    return min(w / (w * c + h * s), h / (w * s + h * c))


def render(img, edit: dict | None):
    """Original PIL image → edited PIL image."""
    from PIL import Image, ImageOps
    if not edit:
        return img
    rot = edit.get("rotate") or 0
    if rot:
        img = img.transpose({90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                             270: Image.Transpose.ROTATE_90}[rot])          # Pillow's names are counter-clockwise
    angle = edit.get("angle") or 0
    if angle:
        w, h = img.size
        img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white")
        s = _inner_scale(w, h, angle)
        cw, ch = w * s, h * s
        img = img.crop((round((w - cw) / 2), round((h - ch) / 2), round((w + cw) / 2), round((h + ch) / 2)))
    crop = edit.get("crop")
    if crop:
        w, h = img.size
        img = img.crop((round(crop["x"] * w), round(crop["y"] * h), round((crop["x"] + crop["w"]) * w),
                        round((crop["y"] + crop["h"]) * h)))
    if edit.get("autocontrast"):
        img = ImageOps.autocontrast(img.convert("RGB"), cutoff=1)
    return img


# ---- moving points and boxes between original and display ----
def _sizes(edit, W, H):
    """(size after rotation, size after straightening) in pixels."""
    rot = edit.get("rotate") or 0
    w1, h1 = (H, W) if rot in (90, 270) else (W, H)
    s = _inner_scale(w1, h1, edit.get("angle") or 0)
    return (w1, h1), s


def point_to_display(u: float, v: float, edit: dict | None, W: int, H: int):
    """A point on the original (fractions) → the same point on the edited picture (fractions)."""
    if not edit:
        return u, v
    rot = edit.get("rotate") or 0
    for _ in range(rot // 90):                     # clockwise quarter turn: (u, v) → (1 − v, u)
        u, v = 1 - v, u
    (w1, h1), s = _sizes(edit, W, H)
    a = math.radians(edit.get("angle") or 0)
    if a:
        dx, dy = (u - 0.5) * w1, (v - 0.5) * h1
        # Pillow turns the picture counter-clockwise (y down): a point moves by (dx cos + dy sin, −dx sin + dy cos)
        rx, ry = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
        u, v = 0.5 + rx / (w1 * s), 0.5 + ry / (h1 * s)
    crop = edit.get("crop")
    if crop:
        u, v = (u - crop["x"]) / crop["w"], (v - crop["y"]) / crop["h"]
    return u, v


def point_to_original(u: float, v: float, edit: dict | None, W: int, H: int):
    if not edit:
        return u, v
    crop = edit.get("crop")
    if crop:
        u, v = crop["x"] + u * crop["w"], crop["y"] + v * crop["h"]
    (w1, h1), s = _sizes(edit, W, H)
    a = math.radians(edit.get("angle") or 0)
    if a:
        rx, ry = (u - 0.5) * w1 * s, (v - 0.5) * h1 * s
        dx, dy = rx * math.cos(a) - ry * math.sin(a), rx * math.sin(a) + ry * math.cos(a)
        u, v = 0.5 + dx / w1, 0.5 + dy / h1
    rot = edit.get("rotate") or 0
    for _ in range(rot // 90):                     # undo a clockwise quarter turn: (u, v) → (v, 1 − u)
        u, v = v, 1 - u
    return u, v


def _box(fn, box, edit, W, H):
    pts = [fn(box["x"] + dx * box["w"], box["y"] + dy * box["h"], edit, W, H) for dx in (0, 1) for dy in (0, 1)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def box_to_display(box: dict, edit: dict | None, W: int, H: int) -> dict:
    return _box(point_to_display, box, edit, W, H) if edit else dict(box)


def box_to_original(box: dict, edit: dict | None, W: int, H: int) -> dict:
    return _box(point_to_original, box, edit, W, H) if edit else dict(box)


def visible(box: dict) -> bool:
    """Is any of a display-space box inside the picture (not cropped away)?"""
    return box["x"] < 1 and box["y"] < 1 and box["x"] + box["w"] > 0 and box["y"] + box["h"] > 0
