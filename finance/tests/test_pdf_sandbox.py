"""The PDF tools (poppler) run through common/python/sandbox_run.py: on a copy of the file, as the
unprivileged pdfworker user when the app runs as root, with resource limits. These run the real tools
when poppler-utils is installed (as in the app's image)."""
import os
import shutil

import pytest

from app import pdfview
from app.common import sandbox_run
from app.parser import deterministic, vision

needs_poppler = pytest.mark.skipif(not (shutil.which("pdftotext") and shutil.which("pdftoppm")),
                                   reason="needs poppler-utils")


def make_pdf(lines, pages=1):
    """A minimal valid PDF with `lines` of Helvetica text on each page."""
    objs = []
    kids = []
    n_pages = pages
    # object numbers: 1 catalog, 2 pages, 3 font, then per page: page, content
    page_objs = []
    for p in range(n_pages):
        text = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({l} p{p+1}) '" for l in lines) + " ET"
        page_objs.append(text.encode())
    out = [None, b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, content in enumerate(page_objs):
        page_no = 4 + 2 * i
        out.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_no + 1} 0 R >>".encode())
        out.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
        kids.append(f"{page_no} 0 R")
    out[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n_pages} >>".encode()
    body = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(out[1:], start=1):
        offsets.append(len(body))
        body += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(out)}\n0000000000 65535 f \n".encode()
    for off in offsets:
        body += f"{off:010d} 00000 n \n".encode()
    body += f"trailer\n<< /Size {len(out)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return body


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "statement.pdf"
    path.write_bytes(make_pdf(["Opening balance 1,234.56", "Closing balance 99.10"], pages=2))
    os.chmod(path, 0o600)             # as the app keeps it: the tool only ever sees its own copy
    return str(path)


@needs_poppler
def test_pdftotext(pdf):
    text = deterministic.run_pdftotext(pdf)
    assert "Opening balance 1,234.56 p1" in text and "Closing balance 99.10 p2" in text
    assert deterministic.extract_balances(text)[0] == 1234.56


@needs_poppler
def test_rasterize(pdf):
    out_dir, pages = vision.rasterize_pdf(pdf)
    try:
        assert [os.path.basename(p) for p in pages] == ["page-1.png", "page-2.png"]
        for p in pages:
            assert os.path.dirname(p) == out_dir
            with open(p, "rb") as f:
                assert f.read(8) == b"\x89PNG\r\n\x1a\n"
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


@needs_poppler
def test_rasterize_failure_cleans_up(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(vision.VisionExtractionError, match="pdftoppm failed"):
        vision.rasterize_pdf(str(bad))


@needs_poppler
def test_viewer(pdf):
    pdfview._words_cache.clear()
    pdfview._image_cache.clear()
    pages = pdfview.load_pages(pdf)
    assert len(pages) == 2
    assert any(w[0] == "1,234.56" for w in pages[0]["words"])
    png = pdfview.render_page(pdf, 2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@needs_poppler
def test_tools_run_unprivileged_when_the_app_is_root(pdf, monkeypatch):
    seen = {}
    real = sandbox_run.Scratch.run

    def spy(self, cmd, **kw):
        seen["ids"] = self.ids
        seen["cmd"] = cmd
        return real(self, cmd, **kw)

    monkeypatch.setattr(sandbox_run.Scratch, "run", spy)
    deterministic.run_pdftotext(pdf)
    assert seen["cmd"][0] == "pdftotext" and seen["cmd"][2] != pdf          # a copy, not the stored file
    assert os.path.basename(seen["cmd"][2]) == "in.pdf"
    if os.geteuid() == 0:
        assert seen["ids"] is not None and seen["ids"][0] != 0
    else:
        assert seen["ids"] is None
