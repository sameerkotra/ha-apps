"""Organise (SPEC §17.1–17.3, §17.12, §17.13): tags and colours (who may change them, search with tag: / -tag: /
color:, the sidebar's counts respect access, they follow renames and moves); Markdown notes (Convert renames
the file); links between documents and backlinks (access respected, "No access" / "Deleted", re-linked by
title); the PDF writer (valid PDFs, read back with pypdf); pins (limit, order, previews) and Quick note naming."""
import _env  # noqa: F401

import io
import os
import unittest

from app import db, pdfwriter, pins
from app.docpdf import inline_runs, md_blocks
from base import ASHA, DEV, KABIR, MEERA, ApiBase, person_dir, read, write

try:
    import pypdf
except ImportError:                     # pragma: no cover — pinned in requirements-dev.txt
    pypdf = None


class Tags(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def tag(self, nid, tags, color="keep", h=KABIR):
        body = {"tags": tags}
        if color != "keep":
            body["color"] = color
        return self.put(f"/api/nodes/{nid}/tags", body, h)

    def search(self, q, h=KABIR, **params):
        p = "&".join(f"{k}={v}" for k, v in params.items())
        return [r["name"] for r in self.ok(self.get(f"/api/search?q={q}" + (f"&{p}" if p else ""), h))["results"]]

    def test_tags_colours_and_who_may_change_them(self):
        f = self.create("folder", "Car")
        n = self.note("Insurance", f["id"], text="policy")
        out = self.ok(self.tag(n["id"], ["Taxes", " car ", "#taxes"], "red"))
        self.assertEqual(out, {"tags": ["car", "Taxes"], "color": "red"})
        self.ok(self.share(f["id"], MEERA, "viewer"))
        self.ok(self.share(f["id"], DEV, "editor"))
        seen = self.ok(self.get(f"/api/nodes/{n['id']}/tags", MEERA))
        self.assertEqual((seen["tags"], seen["color"], seen["canEdit"]), (["car", "Taxes"], "red", False))
        self.assertEqual(self.tag(n["id"], ["mine"], h=MEERA).status_code, 403)
        self.assertEqual(self.get(f"/api/nodes/{n['id']}/tags", ASHA).status_code, 404)
        self.ok(self.post(f"/api/nodes/{n['id']}/tags", {"add": ["TAXES", "2026"], "remove": ["car"]}, DEV))
        self.assertEqual(self.ok(self.get(f"/api/nodes/{n['id']}/tags"))["tags"], ["2026", "Taxes"])
        for bad in (["a,b"], ['say "hi"'], ["x" * 41], [""]):
            self.assertEqual(self.tag(n["id"], bad).status_code, 422, bad)
        self.assertEqual(self.tag(n["id"], [f"t{i}" for i in range(21)]).status_code, 422)
        self.assertEqual(self.tag(n["id"], ["ok"], "pink").status_code, 422)
        self.ok(self.tag(n["id"], ["ok"], None))
        self.assertIsNone(self.ok(self.get(f"/api/nodes/{n['id']}"))["color"])
        # in lists and in the editor
        listed = self.ok(self.get(f"/api/list?node={f['id']}"))["items"][0]
        self.assertEqual(listed["tags"], ["ok"])
        self.assertEqual(self.open(n["id"])["tags"], ["ok"])

    def test_tags_follow_renames_and_moves_and_the_sidebar_counts_respect_access(self):
        f = self.create("folder", "Car")
        n = self.note("Insurance", f["id"], text="policy")
        mine = self.note("Private", text="secret")
        self.ok(self.tag(n["id"], ["taxes"]))
        self.ok(self.tag(mine["id"], ["taxes", "private"]))
        self.ok(self.share(f["id"], MEERA, "viewer"))
        self.ok(self.post(f"/api/nodes/{n['id']}/rename", {"name": "Car insurance"}))
        self.ok(self.post(f"/api/nodes/{n['id']}/move", {"parentId": None}))
        os.rename(self.real(mine["id"]), os.path.join(person_dir("Kabir Rao"), "Car", "Moved outside.txt"))
        self.scan()
        self.assertEqual(self.ok(self.get(f"/api/nodes/{mine['id']}/tags"))["tags"], ["private", "taxes"])
        self.assertEqual(self.ok(self.get("/api/tags"))["tags"], [{"tag": "private", "count": 1}, {"tag": "taxes", "count": 2}])
        self.assertEqual(self.ok(self.get("/api/tags", MEERA))["tags"], [{"tag": "private", "count": 1}, {"tag": "taxes", "count": 1}])
        self.assertEqual(self.ok(self.get("/api/tags", ASHA))["tags"], [])

    def test_search_by_tag_and_colour(self):
        a = self.note("Alpha", text="x")
        b = self.note("Beta", text="x")
        c = self.note("Gamma", text="x")
        self.ok(self.tag(a["id"], ["taxes", "house papers"], "red"))
        self.ok(self.tag(b["id"], ["Taxes"], "blue"))
        self.ok(self.share(c["id"], MEERA, "viewer"))
        self.ok(self.tag(c["id"], ["taxes"]))
        self.assertEqual(sorted(self.search("tag:taxes")), ["Alpha.txt", "Beta.txt", "Gamma.txt"])
        self.assertEqual(self.search("tag:taxes", MEERA), ["Gamma.txt"])            # access inside the query
        self.assertEqual(sorted(self.search("x -tag:taxes")), [])
        self.assertEqual(self.search('tag:"house papers"'), ["Alpha.txt"])
        self.assertEqual(self.search("color:blue"), ["Beta.txt"])
        self.assertEqual(self.search("", tag="taxes", color="red"), ["Alpha.txt"])
        d = self.ok(self.get("/api/search?q=color:pink"))
        self.assertEqual(d["results"], [])
        self.assertTrue(d["chips"][0].get("error"))
        chips = self.ok(self.get("/api/search?q=tag:taxes"))["chips"]
        self.assertEqual(chips[0]["label"], "Tag taxes")
        r = self.ok(self.get("/api/search?q=tag:taxes"))["results"]
        self.assertIn("taxes", [t.lower() for t in r[0]["tags"]])


class MarkdownNotes(ApiBase):
    def test_create_convert_and_keep_the_kind(self):
        m = self.create("markdown", "Recipe")
        self.assertTrue(self.real(m["id"]).endswith("Recipe.md"))
        self.assertEqual(self.open(m["id"])["kind"], "markdown")
        self.save(m["id"], "- [ ] flour\n- [ ] eggs\n")                     # only tick boxes: still a Markdown note
        self.assertEqual(self.open(m["id"])["kind"], "markdown")
        self.ok(self.post(f"/api/docs/{m['id']}/convert"))
        self.assertTrue(self.real(m["id"]).endswith("Recipe.txt"))
        self.assertEqual(self.open(m["id"])["kind"], "note")
        self.assertEqual(self.post(f"/api/docs/{m['id']}/convert").status_code, 409)   # tick boxes only as .md
        self.save(m["id"], "# Cake\n\n- [ ] flour\n")
        self.ok(self.share(m["id"], MEERA, "viewer"))
        out = self.ok(self.post(f"/api/docs/{m['id']}/convert"))
        self.assertEqual(out["name"], "Recipe.md")
        self.assertEqual(self.open(m["id"], MEERA)["kind"], "markdown")
        self.assertEqual(self.post(f"/api/docs/{m['id']}/convert", h=MEERA).status_code, 403)
        write(os.path.join(person_dir("Kabir Rao"), "Recipe.txt"), "taken")
        self.ok(self.post(f"/api/docs/{m['id']}/convert"))
        self.assertTrue(self.real(m["id"]).endswith("Recipe (2).txt"))


class Links(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def links(self, nid, h=KABIR):
        return self.ok(self.get(f"/api/docs/{nid}/links", h))

    def test_links_backlinks_and_access(self):
        budget = self.note("Budget", text="money")
        secret = self.note("Secret", text="hidden")
        plan = self.create("markdown", "Plan")
        self.save(plan["id"], "See [[Budget]] and [[Secret]] and [[Nowhere]].")
        self.ok(self.share(plan["id"], MEERA, "viewer"))
        self.ok(self.share(budget["id"], MEERA, "viewer"))
        mine = self.links(plan["id"])["links"]
        self.assertEqual((mine["Budget"]["state"], mine["Budget"]["id"]), ("ok", budget["id"]))
        self.assertEqual(mine["Secret"]["state"], "ok")
        self.assertEqual(mine["Nowhere"]["state"], "missing")
        theirs = self.links(plan["id"], MEERA)["links"]
        self.assertEqual(theirs["Budget"]["state"], "ok")
        self.assertEqual(theirs["Secret"], {"state": "noaccess"})               # nothing about it
        # Linked from: only from documents the reader can open
        other = self.note("Notes", text="[[Budget]] again")
        back = self.links(budget["id"])["backlinks"]
        self.assertEqual(sorted(b["title"] for b in back), ["Notes", "Plan"])
        self.assertEqual([b["title"] for b in self.links(budget["id"], MEERA)["backlinks"]], ["Plan"])
        # renames keep the link; a deleted target shows Deleted; it comes back with a restore
        self.ok(self.post(f"/api/nodes/{budget['id']}/rename", {"name": "Money 2026"}))
        got = self.links(plan["id"])["links"]["Budget"]
        self.assertEqual((got["state"], got["name"]), ("ok", "Money 2026.txt"))
        self.ok(self.delete(f"/api/nodes/{secret['id']}"))
        self.assertEqual(self.links(plan["id"])["links"]["Secret"]["state"], "deleted")
        self.assertEqual(self.links(plan["id"], MEERA)["links"]["Secret"]["state"], "deleted")
        # the picker's choice wins over a title lookup
        twin = self.create("folder", "Budget")
        self.save(other["id"], "[[Budget]]")
        d = self.open(other["id"])
        self.ok(self.patch(f"/api/docs/{other['id']}", {"etag": d["etag"], "text": "[[Budget]] !", "links": {"Budget": twin["id"]}}))
        self.assertEqual(self.links(other["id"])["links"]["Budget"]["id"], twin["id"])
        # a hint for something the writer can't open is ignored
        asha = self.note("Asha's", h=ASHA, text="x")
        d = self.open(other["id"])
        self.ok(self.patch(f"/api/docs/{other['id']}", {"etag": d["etag"], "text": "[[Asha's]]", "links": {"Asha's": asha["id"]}}))
        self.assertEqual(self.links(other["id"])["links"]["Asha's"]["state"], "missing")

    def test_relinked_by_title_when_the_link_is_lost(self):
        budget = self.note("Budget", text="money")
        plan = self.note("Plan", text="[[Budget]]")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT to_id FROM node_links WHERE from_id = ?", (plan["id"],)).fetchone()[0], budget["id"])
            conn.execute("DELETE FROM node_links")
        self.assertEqual(self.links(plan["id"])["links"]["Budget"]["id"], budget["id"])     # looked up by title
        write(self.real(plan["id"]), "[[Budget]] edited outside")
        self.open(plan["id"])                                                           # an editor opens it
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT to_id FROM node_links WHERE from_id = ?", (plan["id"],)).fetchone()[0], budget["id"])


class Pdf(ApiBase):
    def test_writer_output_is_a_valid_pdf(self):
        doc = pdfwriter.PdfDoc("Tëst – “Title”")
        doc.heading("Heading", 1)
        for i in range(120):
            doc.paragraph([("Line ", "R"), (f"{i} ", "B"), ("with café, € and – dashes ", "I"), ("code()", "M")])
        doc.paragraph("A box", box=False)
        doc.paragraph("Ticked", box=True, indent=18)
        doc.paragraph("Bullet", bullet="•")
        doc.paragraph("Ελληνικά and हिन्दी")
        doc.paragraph("x" * 400)                       # a word longer than a line
        doc.rule()
        data = doc.output()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertGreater(doc.unsupported, 10)
        if pypdf is None:
            self.skipTest("pypdf isn't installed")
        r = pypdf.PdfReader(io.BytesIO(data), strict=True)
        self.assertGreaterEqual(len(r.pages), 3)
        text = r.pages[0].extract_text()
        self.assertIn("Heading", text)
        self.assertIn("café", text)
        self.assertEqual(r.metadata.title, "Tëst – “Title”")
        self.assertIn("1 / ", r.pages[0].extract_text())

    def test_markdown_blocks(self):
        b = md_blocks("# Title\n\nSome *text* here\nand more.\n\n- [x] done\n  - [ ] sub\n1. one\n> quote\n\n```\ncode\n```\n---\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        self.assertEqual([x["t"] for x in b], ["heading", "para", "item", "item", "item", "quote", "code", "hr", "table"])
        self.assertEqual((b[2]["box"], b[3]["level"], b[4]["bullet"]), (True, 1, "1."))
        self.assertEqual(inline_runs("a **b** *c* `d` [e](https://x.y) [[F]]"),
                         [("a ", "R"), ("b", "B"), (" ", "R"), ("c", "I"), (" ", "R"), ("d", "M"), (" ", "R"), ("e", "R"),
                          (" (https://x.y)", "R"), (" ", "R"), ("F", "B")])
        self.assertEqual(inline_runs("[x](javascript:alert(1))")[0], ("x", "R"))

    def test_download_and_save_here(self):
        f = self.create("folder", "Trip")
        n = self.create("markdown", "Plan", f["id"])
        self.save(n["id"], "# Day one\n\n- [x] beach\n- [ ] town\n\nŁódź is fine?")
        c = self.create("checklist", "Packing", f["id"])
        self.ok(self.ops(c["id"], [{"op": "add", "text": "Passports"}, {"op": "add", "text": "Hats"}]))
        r = self.get(f"/api/docs/{n['id']}/pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertIn('attachment; filename="Plan.pdf"', r.headers["content-disposition"])
        self.assertEqual(r.headers["x-docs-unsupported"], "2")                 # Ł, ź
        if pypdf is not None:
            text = pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
            self.assertIn("Day one", text)
            self.assertIn("Kabir Rao", text)
        self.assertEqual(self.get(f"/api/docs/{c['id']}/pdf").status_code, 200)
        self.assertEqual(self.get(f"/api/docs/{n['id']}/pdf", MEERA).status_code, 404)
        out = self.ok(self.post(f"/api/docs/{n['id']}/pdf"), 201)
        self.assertEqual(out["name"], "Plan.pdf")
        self.assertTrue(os.path.isfile(os.path.join(person_dir("Kabir Rao"), "Trip", "Plan.pdf")))
        self.assertEqual(self.ok(self.post(f"/api/docs/{n['id']}/pdf"), 201)["name"], "Plan (2).pdf")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        self.assertEqual(self.get(f"/api/docs/{n['id']}/pdf", MEERA).status_code, 200)
        self.assertEqual(self.post(f"/api/docs/{n['id']}/pdf", h=MEERA).status_code, 403)
        sheet = self.create("sheet", "Money")
        self.assertEqual(self.get(f"/api/docs/{sheet['id']}/pdf").status_code, 415)


class Pins(ApiBase):
    def test_pin_limit_order_and_previews(self):
        ids = [self.note(f"N{i}", text=f"first line {i}\nsecond")["id"] for i in range(8)]
        for i in ids:
            self.ok(self.post(f"/api/nodes/{i}/pin", {"value": True}))
        extra = self.create("checklist", "Shop")
        r = self.post(f"/api/nodes/{extra['id']}/pin", {"value": True})
        self.assertEqual(r.status_code, 409)
        self.assertIn("up to 8", r.json()["detail"])
        self.ok(self.post(f"/api/nodes/{ids[0]}/pin", {"value": False}))
        self.ok(self.post(f"/api/nodes/{extra['id']}/pin", {"value": True}))
        items = self.ok(self.ops(extra["id"], [{"op": "add", "text": t} for t in ("a", "b", "c")]))["items"]
        self.ok(self.ops(extra["id"], [{"op": "tick", "key": items[0]["key"]}]))
        cards = self.ok(self.get("/api/me/pins"))["pins"]
        self.assertEqual(len(cards), 8)
        self.assertEqual(cards[-1]["pinPreview"], {"open": 2, "done": 1})
        self.assertEqual(cards[0]["pinPreview"]["lines"], ["first line 1", "second"])
        self.assertTrue(cards[0]["pinned"])
        order = [c["id"] for c in cards][::-1]
        self.assertEqual([c["id"] for c in self.ok(self.put("/api/me/pins", {"ids": order}))["pins"]], order)
        self.assertEqual(self.put("/api/me/pins", {"ids": order[:-1]}).status_code, 409)
        # a deleted pin leaves the page (and frees its place)
        self.ok(self.delete(f"/api/nodes/{order[0]}"))
        self.assertEqual(len(self.ok(self.get("/api/me/pins"))["pins"]), 7)
        self.ok(self.post(f"/api/nodes/{ids[0]}/pin", {"value": True}))
        # pins are personal; something you can't open can't be pinned
        self.assertEqual(self.ok(self.get("/api/me/pins", MEERA))["pins"], [])
        self.assertEqual(self.post(f"/api/nodes/{ids[0]}/pin", {"value": True}, MEERA).status_code, 404)

    def test_a_sheet_card_shows_the_chosen_cell(self):
        s = self.create("sheet", "Budget")
        d = self.open(s["id"])
        self.ok(self.patch(f"/api/docs/{s['id']}", {"etag": d["etag"], "cells": [
            {"tab": "Sheet1", "ref": "A2", "was": None, "now": {"v": "Left this month"}},
            {"tab": "Sheet1", "ref": "B2", "was": None, "now": {"v": "=100-42", "c": 58, "f": "currency"}}]}))
        self.assertEqual(self.post(f"/api/nodes/{s['id']}/pin", {"value": True, "cell": {"tab": "Sheet1", "ref": "bad"}}).status_code, 422)
        self.ok(self.post(f"/api/nodes/{s['id']}/pin", {"value": True, "cell": {"tab": "Sheet1", "ref": "b2"}}))
        cell = self.ok(self.get("/api/me/pins"))["pins"][0]["pinPreview"]["cell"]
        self.assertEqual((cell["ref"], cell["v"], cell["f"], cell["label"]), ("B2", 58, "currency", "Left this month"))
        n = self.note("Plain")
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/pin", {"value": True, "cell": {"tab": "Sheet1", "ref": "B2"}}).status_code, 422)


class QuickNote(ApiBase):
    def test_quick_note_naming(self):
        q = self.ok(self.post("/api/quick-note"), 201)
        self.assertEqual(q["name"], "Note 2026-10-05 12-00.txt")             # Home Assistant's time zone (UTC here)
        self.assertTrue(q["quick"])
        self.assertTrue(self.real(q["id"]).endswith(os.path.join("Inbox", "Note 2026-10-05 12-00.txt")))
        q2 = self.ok(self.post("/api/quick-note"), 201)
        self.assertEqual(q2["name"], "Note 2026-10-05 12-00 (2).txt")
        self.save(q["id"], "\n  # Call the: plumber? about the *leak* in the kitchen sink before Friday please\nmore")
        out = self.ok(self.post(f"/api/quick-note/{q['id']}/finish"))
        self.assertEqual(out["name"], "Call the_ plumber_ about the leak in the kitchen sink.txt")
        self.assertTrue(out["renamed"])
        self.assertFalse(self.ok(self.post(f"/api/quick-note/{q['id']}/finish"))["renamed"])   # named already
        self.assertTrue(self.ok(self.post(f"/api/quick-note/{q2['id']}/finish"))["deleted"])  # empty: to Trash
        self.assertEqual(self.get(f"/api/docs/{q2['id']}").status_code, 404)
        q3 = self.ok(self.post("/api/quick-note"), 201)
        self.save(q3["id"], "Call the: plumber? about the *leak* in the kitchen sink before Friday please")
        self.assertEqual(self.ok(self.post(f"/api/quick-note/{q3['id']}/finish"))["name"],
                         "Call the_ plumber_ about the leak in the kitchen sink (2).txt")
        # a quick note renamed by hand keeps its name; Inbox is reused
        q4 = self.ok(self.post("/api/quick-note"), 201)
        self.ok(self.post(f"/api/nodes/{q4['id']}/rename", {"name": "Mine"}))
        self.save(q4["id"], "Something else")
        self.assertFalse(self.ok(self.post(f"/api/quick-note/{q4['id']}/finish"))["renamed"])
        inbox = [i for i in self.ok(self.get("/api/space/mine"))["items"] if i["kind"] == "folder"]
        self.assertEqual([i["name"] for i in inbox], ["Inbox"])
        self.assertEqual(self.post(f"/api/quick-note/{q4['id']}/finish", h=MEERA).status_code, 404)

    def test_the_quick_note_address_serves_the_app(self):
        r = self.get("/quick-note")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("script-src 'self'", r.headers["content-security-policy"])
        self.assertIn('src="app.js?v=', r.text)

    def test_name_from_text(self):
        self.assertEqual(pins.name_from_text("- [ ] buy [[Milk]]"), "buy Milk")
        self.assertEqual(pins.name_from_text("\n\n"), "")
        self.assertEqual(pins.name_from_text("CON"), "CON_")


if __name__ == "__main__":
    unittest.main()
