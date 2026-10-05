"""PDF receipts are rendered through app/common/sandbox_run.py: on a copy in a scratch folder, as the
unprivileged pdfworker user when the app runs as root, with resource limits. Runs the real poppler tools
when they are installed (as in the app's image)."""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.common import sandbox_run  # noqa: E402
from app.services import pdf  # noqa: E402

HAVE_POPPLER = bool(shutil.which("pdftoppm") and shutil.which("pdfinfo"))


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


@unittest.skipUnless(HAVE_POPPLER, "needs poppler-utils")
class RenderTests(unittest.TestCase):
    def test_renders_pages(self):
        pages, total = pdf.render_pdf(make_pdf(["Milk 1.99", "Bread 2.49"], pages=3), max_pages=2)
        self.assertEqual(total, 3)
        self.assertEqual(len(pages), 2)
        for page in pages:
            self.assertEqual(page[:2], b"\xff\xd8")                  # JPEG

    def test_not_a_pdf(self):
        with self.assertRaises(pdf.PdfError) as e:
            pdf.render_pdf(b"%PDF-1.4 but nothing else")
        self.assertIn("could not be read as a PDF", str(e.exception))

    def test_runs_in_the_sandbox(self):
        seen = []
        real = sandbox_run.Scratch.run

        def spy(box, cmd, **kw):
            seen.append((cmd[0], box.ids, os.path.dirname(cmd[-2] if cmd[0] == "pdftoppm" else cmd[-1]) == box.path))
            return real(box, cmd, **kw)

        with mock.patch.object(sandbox_run.Scratch, "run", spy):
            pdf.render_pdf(make_pdf(["Milk 1.99"]))
        self.assertEqual([s[0] for s in seen], ["pdfinfo", "pdftoppm"])
        self.assertTrue(all(s[2] for s in seen))                       # the copy in the scratch folder
        for _, ids, _ in seen:
            if os.geteuid() == 0:
                self.assertTrue(ids is not None and ids[0] != 0)
            else:
                self.assertIsNone(ids)

    def test_timeout_is_still_a_readable_error(self):
        import subprocess
        with mock.patch.object(sandbox_run.Scratch, "run", side_effect=subprocess.TimeoutExpired("pdfinfo", 1)):
            with self.assertRaises(pdf.PdfError) as e:
                pdf.render_pdf(make_pdf(["x"]))
        self.assertIn("took too long", str(e.exception))


if __name__ == "__main__":
    unittest.main()
