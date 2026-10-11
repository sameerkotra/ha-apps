"""The PDF viewer (SPEC §17.24) in headless Chromium (Playwright), against the real app served by uvicorn in this
process, with the page's own security policy: a PDF opens from the file panel's 👁 View and its pages are drawn
(fonts it doesn't embed included); zoom; a password-protected PDF asks for its password; a broken file and one the
person can't see say so; nothing breaks the page's policy. Skipped when Playwright (or a browser) isn't there."""
import _env  # noqa: F401  (must be first)

import glob
import io
import os
import socket
import threading
import time
import unittest

import uvicorn

from app import docpdf
from app.main import app
from base import KABIR, MEERA, ApiBase

try:
    from playwright.sync_api import sync_playwright
except ImportError:          # pragma: no cover
    sync_playwright = None


def chromium(pw):
    try:
        return pw.chromium.launch()
    except Exception:
        hits = sorted(glob.glob(os.path.join(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"),
                                             "chromium-*/chrome-linux*/chrome")))
        if not hits:
            raise unittest.SkipTest("no browser for Playwright")
        try:
            return pw.chromium.launch(executable_path=hits[-1])
        except Exception as e:                 # there, but it won't start here
            raise unittest.SkipTest("the browser for Playwright won't start: " + (str(e).splitlines() or [""])[0])


def a_pdf(lines=150):
    """A few A4 pages of text in the standard Helvetica (not embedded: the viewer's standard fonts draw it)."""
    return docpdf.build("Train tickets", "Household Docs test", "note",
                        text="\n".join(f"Seat {i} — coach B" for i in range(lines))).output()


@unittest.skipUnless(sync_playwright, "playwright not installed")
class PdfViewer(ApiBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}/"
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning",
                                                   proxy_headers=False))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        end = time.monotonic() + 10
        while not cls.server.started and time.monotonic() < end:
            time.sleep(0.02)
        cls.pw = sync_playwright().start()
        try:
            cls.browser = chromium(cls.pw)
        except BaseException:
            cls.pw.stop()
            cls.server.should_exit = True
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.should_exit = True
        cls.thread.join(10)
        super().tearDownClass()

    def upload(self, name, data, h=KABIR):
        r = self.c.post(f"/api/nodes/mine/upload?name={name}", content=data, headers=h)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def open_page(self, h=KABIR):
        ctx = self.browser.new_context(extra_http_headers=h, viewport={"width": 900, "height": 900})
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        self.problems = []
        p.on("pageerror", lambda e: self.problems.append(str(e)))
        p.on("console", lambda m: self.problems.append(m.text) if m.type == "error" else None)
        p.add_init_script("document.addEventListener('securitypolicyviolation', "
                          "(e) => console.error('CSP: ' + e.violatedDirective + ' ' + e.blockedURI))")
        p.goto(self.base)
        p.wait_for_function("window.Docs && Docs.state && Docs.state.me")
        return p

    def until(self, p, js, timeout=20.0):
        """Wait for `js` to be true (wait_for_function would need eval, which the page's policy refuses)."""
        end = time.monotonic() + timeout
        while not p.evaluate(js):
            if time.monotonic() > end:
                self.fail(f"never true: {js}")
            time.sleep(0.05)

    INK = """() => { const c = document.querySelector('.pdf-page[data-n="1"] canvas'); if (!c || !c.width) return 0;
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let ink = 0;
      for (let i = 0; i < d.length; i += 4) if (d[i] < 128 && d[i + 3] > 0) ink++; return ink; }"""

    def test_view_from_the_file_panel(self):
        f = self.upload("Tickets.pdf", a_pdf())
        p = self.open_page()
        p.evaluate("(it) => Docs.filePanel(it)", f)
        p.wait_for_selector("#viewPdf")
        p.click("#viewPdf")
        p.wait_for_selector("#pdfViewer .pdf-page canvas", timeout=20000)
        self.until(p, "document.querySelector('#pdfPageNo').textContent.trim() !== ''")
        pages = p.evaluate("document.querySelectorAll('.pdf-page').length")
        self.assertGreaterEqual(pages, 3)
        self.assertEqual(p.inner_text("#pdfPageNo"), f"1 / {pages}")
        self.until(p, "(" + self.INK + ")() > 500")                        # text drawn, with the standard font
        width = p.evaluate("document.querySelector('.pdf-page').getBoundingClientRect().width")
        self.assertGreater(width, 700)                               # fits the width
        p.click("#pdfZoomIn")
        self.assertRegex(p.inner_text("#pdfFit"), r"^\d+ %$")
        self.assertGreater(p.evaluate("document.querySelector('.pdf-page').getBoundingClientRect().width"), width)
        p.click("#pdfFit")
        self.assertEqual(p.inner_text("#pdfFit"), "Fit")
        # scrolling to the end shows the last page's number and draws it
        p.evaluate("document.querySelector('#pdfPages').scrollTop = 1e7")
        self.until(p, f"document.querySelector('#pdfPageNo').textContent === '{pages} / {pages}'")
        self.until(p, f"!!document.querySelector('.pdf-page[data-n=\"{pages}\"] canvas')")
        self.assertEqual(p.get_attribute("#pdfDownload", "download"), "Tickets.pdf")
        p.keyboard.press("Escape")
        self.until(p, "!document.querySelector('#pdfViewer')")
        self.assertEqual(self.problems, [])

    def test_view_action_only_for_pdfs(self):
        f = self.upload("Tickets.pdf", a_pdf(10))
        n = self.upload("notes.txt", b"hello")
        p = self.open_page()
        ids = p.evaluate("(items) => items.map((it) => Docs.actions.filter((a) => a.id === 'view-pdf' && a.show(it, {})).length)",
                         [f, n])
        self.assertEqual(ids, [1, 0])

    def test_password_protected(self):
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter(clone_from=PdfReader(io.BytesIO(a_pdf(20))))
        w.encrypt("open sesame", algorithm="RC4-128")
        out = io.BytesIO()
        w.write(out)
        f = self.upload("Locked.pdf", out.getvalue())
        p = self.open_page()
        p.evaluate("(it) => Docs.viewPdf(it)", f)
        p.wait_for_selector("#pdfPassword", timeout=20000)
        p.fill("#pdfPassword", "wrong")
        p.press("#pdfPassword", "Enter")
        p.wait_for_selector("text=That password isn't right")
        p.fill("#pdfPassword", "open sesame")
        p.press("#pdfPassword", "Enter")
        p.wait_for_selector("#pdfViewer .pdf-page canvas", timeout=20000)
        self.until(p, "(" + self.INK + ")() > 100")
        self.assertEqual(self.problems, [])

    def test_broken_and_not_yours(self):
        f = self.upload("broken.pdf", b"this is not a pdf at all")
        p = self.open_page()
        p.evaluate("(it) => Docs.viewPdf(it)", f)
        p.wait_for_selector("text=isn't a PDF the viewer can read", timeout=20000)
        p.keyboard.press("Escape")
        mine = self.upload("Private.pdf", a_pdf(10))
        q = self.open_page(MEERA)                                    # not shared with her
        q.evaluate("(it) => Docs.viewPdf(it)", mine)
        q.wait_for_selector("#pdfViewer .pdf-message", timeout=20000)
        self.assertNotIn("canvas", q.inner_html("#pdfPages"))
        self.assertEqual(q.evaluate("document.querySelectorAll('.pdf-page canvas').length"), 0)

    def test_too_big_downloads_instead(self):
        p = self.open_page()
        r = p.evaluate("() => Docs.viewPdf({ id: 'x', name: 'Huge.pdf', ext: 'pdf', kind: 'file', size: 60 * 1048576 })")
        self.assertIsNone(r)
        p.wait_for_selector("text=too big to view here")
