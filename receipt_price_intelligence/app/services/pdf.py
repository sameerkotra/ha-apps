"""PDF receipts: each page is rendered to a JPEG so the model reads it like a photo.

Rendering uses poppler's ``pdftoppm`` and ``pdfinfo`` (installed in the app image) in a
subprocess with a time limit, so a bad PDF can never hang or crash the app. The tools run as the
unprivileged ``pdfworker`` user with resource limits, on a copy of the file in a scratch folder of their
own (``app/common/sandbox_run.py``), so they can't reach the app's database or photos. The PDF itself
is never kept; only the rendered page images are stored on the draft.
"""

import glob
import os
import re
import subprocess

from app.common import sandbox_run
from app.logging_config import get_logger

logger = get_logger("pdf")

MAX_PDF_SIZE = 20 * 1024 * 1024   # 20 MB
MAX_PDF_PAGES = 8                 # same cap as the model gets for photos
RENDER_LONG_SIDE = 2200           # pixels on the longest side of each page
INFO_TIMEOUT = 30
RENDER_TIMEOUT = 90


class PdfError(ValueError):
    """The PDF could not be read. The message is safe to show to a person."""


def looks_like_pdf(content_type: str | None, head: bytes) -> bool:
    """True for a PDF by content type or by its ``%PDF-`` signature (near the start of the file)."""
    if b"%PDF-" in head[:1024]:
        return True
    return (content_type or "").lower() in ("application/pdf", "application/x-pdf")


def _run(box: sandbox_run.Scratch, cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return box.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise PdfError("PDF support is not installed in this version of the app")
    except subprocess.TimeoutExpired:
        raise PdfError("The PDF took too long to read. Try a smaller file or a photo instead")


def _explain(stderr: str) -> str:
    text = (stderr or "").lower()
    if "password" in text or "encrypted" in text:
        return "This PDF is password protected. Remove the password and try again"
    return "This file could not be read as a PDF"


def render_pdf(data: bytes, max_pages: int = MAX_PDF_PAGES) -> tuple[list[bytes], int]:
    """Render up to ``max_pages`` pages to JPEG. Returns (page images, total pages in the PDF)."""
    if max_pages < 1:
        raise PdfError("There is no room for more pages on this receipt")

    with sandbox_run.Scratch(prefix="receipt-pdf-") as box:
        tmp = box.path
        source = box.add_bytes(data, "in.pdf")

        info = _run(box, ["pdfinfo", source], INFO_TIMEOUT)
        if info.returncode != 0:
            raise PdfError(_explain(info.stderr))
        match = re.search(r"^Pages:\s+(\d+)", info.stdout, re.M)
        total = int(match.group(1)) if match else 0
        if total < 1:
            raise PdfError("This PDF has no pages")

        last = min(total, max_pages)
        prefix = os.path.join(tmp, "page")
        result = _run(
            box,
            ["pdftoppm", "-jpeg", "-jpegopt", "quality=90", "-scale-to", str(RENDER_LONG_SIDE),
             "-f", "1", "-l", str(last), source, prefix],
            RENDER_TIMEOUT,
        )

        def page_number(path: str) -> int:
            m = re.search(r"-(\d+)\.jpg$", path)
            return int(m.group(1)) if m else 0

        files = sorted(glob.glob(prefix + "-*.jpg"), key=page_number)
        if result.returncode != 0 or not files:
            logger.warning("pdftoppm failed: %s", (result.stderr or "").strip()[:300])
            raise PdfError(_explain(result.stderr))

        pages = []
        for path in files:
            with open(path, "rb") as f:
                pages.append(f.read())

    logger.info("Rendered %d of %d PDF page(s)", len(pages), total)
    return pages, total
