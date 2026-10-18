"""Sheet tabs in headless Chromium (Playwright), against the real app: a tab dragged to another place moves there
(and is saved, formulas still pointing at it); the tab menu's Move to start / Move to end. Skipped without Playwright."""
import _env  # noqa: F401  (must be first)

import time
import unittest

from base import KABIR
from test_pdf_view import PdfViewer, sync_playwright
from test_sheets import grid


@unittest.skipUnless(sync_playwright, "playwright not installed")
class SheetTabs(PdfViewer):
    def make_sheet(self):
        it = self.ok(self.post("/api/docs", {"kind": "sheet", "name": "Budget", "parentId": None}, KABIR), 201)
        etag = self.open(it["id"], KABIR)["etag"]
        sheet = {"tabs": [grid("Rent", {"A1": {"v": 1200}}), grid("Food", {"A1": {"v": "='Rent'!A1+1"}}),
                          grid("Car", {"A1": {"v": 3}})]}
        self.ok(self.patch(f"/api/docs/{it['id']}", {"etag": etag, "sheet": sheet}, KABIR))
        return it["id"]

    def order(self, nid):
        return [t["name"] for t in self.open(nid, KABIR)["sheet"]["tabs"]]

    def saved(self, nid, want, timeout=15):
        end = time.monotonic() + timeout
        while self.order(nid) != want:
            if time.monotonic() > end:
                self.fail(f"saved order {self.order(nid)} != {want}")
            time.sleep(0.1)

    def tab(self, p, name):
        return p.locator(f'#sheetTabs .sheet-tab[data-tab="{name}"]')

    def drag(self, p, name, onto, side):
        a, b = self.tab(p, name).bounding_box(), self.tab(p, onto).bounding_box()
        p.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
        p.mouse.down()
        x = b["x"] + (4 if side == "left" else b["width"] - 4)
        for k in range(1, 9):
            p.mouse.move(a["x"] + a["width"] / 2 + (x - a["x"] - a["width"] / 2) * k / 8, b["y"] + b["height"] / 2)
        self.assertEqual(p.locator("#sheetTabs .sheet-tab.dragging").count(), 1)
        p.mouse.up()

    def test_drag_a_tab_to_another_place(self):
        nid = self.make_sheet()
        p = self.open_page()
        p.goto(self.base + "#/doc/" + nid)
        self.tab(p, "Car").wait_for()
        self.drag(p, "Car", "Rent", "left")
        self.saved(nid, ["Car", "Rent", "Food"])
        self.assertEqual(p.locator("#sheetTabs .sheet-tab:not(.add)").all_inner_texts(), ["Car", "Rent", "Food"])
        self.assertIn("active", self.tab(p, "Rent").get_attribute("class"))       # the open tab stays open
        self.drag(p, "Car", "Food", "right")
        self.saved(nid, ["Rent", "Food", "Car"])
        food = [t for t in self.open(nid, KABIR)["sheet"]["tabs"] if t["name"] == "Food"][0]
        self.assertEqual(food["cells"]["A1"]["v"], "='Rent'!A1+1")
        self.tab(p, "Food").click()                                             # a click still opens a tab
        self.assertIn("active", self.tab(p, "Food").get_attribute("class"))
        self.assertEqual(self.problems, [])

    def test_menu_moves_to_start_and_end(self):
        nid = self.make_sheet()
        p = self.open_page()
        p.goto(self.base + "#/doc/" + nid)
        self.tab(p, "Car").click()
        self.tab(p, "Car").click()                                              # the open tab's menu
        p.get_by_role("menuitem", name="Move to start").click()
        self.saved(nid, ["Car", "Rent", "Food"])
        self.tab(p, "Car").click()
        p.get_by_role("menuitem", name="Move to end").click()
        self.saved(nid, ["Rent", "Food", "Car"])
        self.assertEqual(self.problems, [])

    def test_on_a_phone_hold_then_drag(self):
        nid = self.make_sheet()
        ctx = self.browser.new_context(extra_http_headers=KABIR, viewport={"width": 390, "height": 760}, has_touch=True,
                                       is_mobile=True)
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        p.goto(self.base + "#/doc/" + nid)
        self.tab(p, "Car").wait_for()
        cdp = ctx.new_cdp_session(p)

        def touch(pts, hold):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": pts[0][0], "y": pts[0][1]}]})
            time.sleep(hold)
            for x, y in pts[1:]:
                cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
                time.sleep(0.03)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        a, b = self.tab(p, "Car").bounding_box(), self.tab(p, "Rent").bounding_box()
        x0, y = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
        line = [(x0 - (x0 - b["x"] - 4) * k / 8, y) for k in range(9)]
        touch(line, 0.1)                                   # a quick swipe scrolls the tab bar, it doesn't move a tab
        time.sleep(1)
        self.assertEqual(self.order(nid), ["Rent", "Food", "Car"])
        touch(line, 0.6)                                   # hold, then drag
        self.saved(nid, ["Car", "Rent", "Food"])
        touch([line[-1]] * 2, 0.7)                         # hold and let go: the tab's menu
        p.get_by_role("menuitem", name="Move to end").wait_for()


# the borrowed class's own tests run in their own file
for _n in [n for n in dir(PdfViewer) if n.startswith("test_")]:
    setattr(SheetTabs, _n, None)
del PdfViewer

if __name__ == "__main__":
    unittest.main()
