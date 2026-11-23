"""The other apps' figures in a sheet, in headless Chromium (Playwright) against the real app (SPEC §8.2):
=SPLITPOT.BALANCE() — #N/A while Splitpot is asked, then the person's own balance (overall and per group) and what
depends on it, a refusal's reason on the cell; =SHOPPING.TOTAL/COUNT/PRICE/STORE() — the items named in the sheet
sent in one question, the list's totals (overall and for a store) and prices. The other apps are played by the page's
network (the server's side is test_connect.SplitpotTests / ShoppingTests). Skipped without Playwright."""
import _env  # noqa: F401  (must be first)

import json
import unittest

from base import KABIR
from test_pdf_view import PdfViewer, sync_playwright
from test_sheets import grid


@unittest.skipUnless(sync_playwright, "playwright not installed")
class SplitpotBalance(PdfViewer):
    def make_sheet(self):
        it = self.ok(self.post("/api/docs", {"kind": "sheet", "name": "Money", "parentId": None}, KABIR), 201)
        etag = self.open(it["id"], KABIR)["etag"]
        cells = {"A1": {"v": "=SPLITPOT.BALANCE()"}, "A2": {"v": '=SPLITPOT.BALANCE("trip")'}, "A3": {"v": "=A1*2"},
                 "A4": {"v": '=SPLITPOT.BALANCE("Nope")'}}
        self.ok(self.patch(f"/api/docs/{it['id']}", {"etag": etag, "sheet": {"tabs": [grid("Sheet1", cells)]}}, KABIR))
        return it["id"]

    def splitpot(self, p, answer):
        """Splitpot is there (/api/me says so) and answers `answer` (a result, or an error string) on the 2nd poll."""
        polls = {"n": 0}
        self.asked = 0

        def me(route):
            r = route.fetch()
            body = r.json()
            body.setdefault("apps", {})["splitpot"] = True
            route.fulfill(response=r, body=json.dumps(body))

        def ask(route):
            self.asked += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"request": "r1", "expiresIn": 120}))

        def poll(route):
            polls["n"] += 1
            if polls["n"] < 2:
                out = {"id": "r1", "kind": "balance", "state": "pending", "error": None, "result": None}
            elif isinstance(answer, str):
                out = {"id": "r1", "kind": "balance", "state": "failed", "error": answer, "result": None}
            else:
                out = {"id": "r1", "kind": "balance", "state": "done", "error": None, "result": answer}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(out))
        p.route("**/api/me", me)
        p.route("**/api/splitpot/balance", ask)
        p.route("**/api/bus/requests/r1", poll)

    def cell(self, p, c, r):
        return p.locator(f'td.cell[data-c="{c}"][data-r="{r}"]').first

    def test_my_balance_shows_and_feeds_other_cells(self):
        nid = self.make_sheet()
        ctx = self.browser.new_context(extra_http_headers=KABIR, viewport={"width": 900, "height": 900})
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        problems = []
        p.on("pageerror", lambda e: problems.append(str(e)))
        self.splitpot(p, {"overall": -125.5, "currency": "INR",
                          "groups": [{"name": "Flat", "amount": 74.5}, {"name": "Trip", "amount": -200}]})
        p.goto(self.base + "#/doc/" + nid)
        a1 = self.cell(p, 0, 0)
        a1.wait_for()
        self.until(p, "document.querySelector('td.cell[data-c=\"0\"][data-r=\"0\"]').textContent.includes('125.5')")
        self.assertIn("-125.5", self.cell(p, 0, 0).inner_text().replace("−", "-"))
        self.assertIn("-200", self.cell(p, 0, 1).inner_text().replace("−", "-"))       # a group, any case
        self.assertIn("-251", self.cell(p, 0, 2).inner_text().replace("−", "-"))       # what depends on it
        self.assertEqual(self.cell(p, 0, 3).inner_text(), "#N/A")
        self.assertIn("aren't in a Splitpot group called “Nope”", self.cell(p, 0, 3).get_attribute("title"))
        self.assertEqual(self.asked, 1)                                                 # one question for all four
        self.assertEqual(problems, [])

    def test_a_refusal_says_why(self):
        nid = self.make_sheet()
        ctx = self.browser.new_context(extra_http_headers=KABIR, viewport={"width": 900, "height": 900})
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        self.splitpot(p, "Open Splitpot once first (or ask an admin to turn it on for you) — it doesn't know you yet.")
        p.goto(self.base + "#/doc/" + nid)
        self.cell(p, 0, 0).wait_for()
        self.until(p, "(document.querySelector('td.cell[data-c=\"0\"][data-r=\"0\"]').title || '').includes('Open Splitpot')")
        self.assertEqual(self.cell(p, 0, 0).inner_text(), "#N/A")


    def test_the_shopping_list(self):
        it = self.ok(self.post("/api/docs", {"kind": "sheet", "name": "Groceries", "parentId": None}, KABIR), 201)
        etag = self.open(it["id"], KABIR)["etag"]
        cells = {"A1": {"v": "=SHOPPING.TOTAL()"}, "A2": {"v": '=SHOPPING.TOTAL("valuco")'}, "A3": {"v": "=SHOPPING.COUNT()"},
                 "A4": {"v": '=SHOPPING.PRICE("Milk")'}, "A5": {"v": '=SHOPPING.STORE("milk")'},
                 "A6": {"v": '=SHOPPING.PRICE("saffron")'}, "A7": {"v": "=A4*2"}}
        self.ok(self.patch(f"/api/docs/{it['id']}", {"etag": etag, "sheet": {"tabs": [grid("Sheet1", cells)]}}, KABIR))
        ctx = self.browser.new_context(extra_http_headers=KABIR, viewport={"width": 900, "height": 900})
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        problems, sent = [], []
        p.on("pageerror", lambda e: problems.append(str(e)))
        known = {"milk": ("2% Milk", 2.99, "Valuco #1"), "saffron": ("Saffron", None, None)}

        def me(route):
            r = route.fetch()
            body = r.json()
            body.setdefault("apps", {})["receipts"] = True
            route.fulfill(response=r, body=json.dumps(body))

        def ask(route):
            sent.append(json.loads(route.request.post_data or "{}").get("items", []))
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"request": f"s{len(sent)}", "expiresIn": 120}))

        def poll(route):
            items = sent[int(route.request.url.rsplit("/s", 1)[1]) - 1]
            prices = [{"item": n, "name": known.get(n.lower(), (None,))[0],
                       "price": known.get(n.lower(), (None, None))[1], "store": known.get(n.lower(), (None, None, None))[2]}
                      for n in items]
            out = {"state": "done", "error": None, "result": {
                "list": {"count": 3, "total": 17.48, "unpriced": 1, "stores": [{"name": "Valuco #1", "count": 1, "total": 5.98},
                                                                              {"name": "Freshmart #2", "count": 1, "total": 11.5}]},
                "prices": prices, "home": "Maple Street", "currency": "USD"}}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(out))
        p.route("**/api/me", me)
        p.route("**/api/receipts/shopping", ask)
        p.route("**/api/bus/requests/s*", poll)
        p.goto(self.base + "#/doc/" + it["id"])
        self.cell(p, 0, 0).wait_for()
        self.until(p, "document.querySelector('td.cell[data-c=\"0\"][data-r=\"6\"]').textContent.includes('5.98')")
        texts = [self.cell(p, 0, r).inner_text() for r in range(7)]
        self.assertIn("17.48", texts[0])
        self.assertIn("5.98", texts[1])                     # what's cheapest at Valuco
        self.assertEqual(texts[2], "3")
        self.assertIn("2.99", texts[3])
        self.assertEqual(texts[4], "Valuco #1")
        self.assertEqual(texts[5], "#N/A")
        self.assertIn("No price known for Saffron", self.cell(p, 0, 5).get_attribute("title"))
        self.assertEqual([sorted(n.lower() for n in s) for s in sent][-1], ["milk", "saffron"])
        self.assertLessEqual(len(sent), 2)                  # the names go together, not one question per cell
        self.assertEqual(problems, [])


# the borrowed class's own tests run in their own file
for _n in [n for n in dir(PdfViewer) if n.startswith("test_")]:
    setattr(SplitpotBalance, _n, None)
del PdfViewer

if __name__ == "__main__":
    unittest.main()
