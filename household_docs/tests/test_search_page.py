"""The Search page (SPEC §10): scopes, every name mode (contains / starts / exact / ends / wildcard / regex), content
words, phrases, prefixes, exclusions and OR, content patterns, the path filter, every filter of §10.3 (dates in Home
Assistant's time zone, sizes with units, types, people, sharing, access, favourites, checklists, empty folders,
duplicates), the box's syntax with chips, sorting, grouping and paging, regex in the worker (time limit, one at a
time), saved / pinned / recent searches, the CSV of a result list — and the access check: every answer is scanned
for anything the caller can't open."""
import _env  # noqa: F401

import csv
import io
import json
import os
import unittest
from datetime import datetime, timezone
from unittest import mock
from urllib.parse import urlencode

from app import config, db, sharing
from app.search import filters as flt, pattern, regex_worker
from base import ASHA, DEV, KABIR, MEERA, SHARE, ApiBase, person_dir, write

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def ts(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


class SearchBase(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def setUp(self):
        super().setUp()
        k = person_dir("Kabir Rao")
        self.budget = self.note("Budget 2026", text="car insurance 450\nrent due on the first\nthe old town")
        self.invoice = self.note("invoice-2026-03 draft", text="invoice number 1234-5678 for the car")
        self.up("bill-2026-01.pdf", b"%PDF one" + b"x" * 2000)
        self.up("bill-2026-02.pdf", b"%PDF two")
        self.up("IMG_0001.jpg", PNG)
        self.up("copy-a.txt", b"same words twice")
        self.up("copy-b.txt", b"same words twice")
        taxes = self.create("folder", "Taxes")
        y = self.create("folder", "2026", taxes["id"])
        self.ret = self.note("return", y["id"], text="tax return for the year")
        self.empty = self.create("folder", "Empty")
        self.packing = self.create("checklist", "Packing")
        self.ok(self.ops(self.packing["id"], [{"op": "add", "text": "tent"}, {"op": "add", "text": "stove"}]))
        self.done = self.create("checklist", "Done list")
        items = self.ok(self.ops(self.done["id"], [{"op": "add", "text": "passports"}]))["items"]
        self.ok(self.ops(self.done["id"], [{"op": "tick", "key": items[0]["key"]}]))
        write(os.path.join(k, "outside.log"), "written over samba")
        self.diary = self.note("Diary", h=MEERA, text="my secret car plans")
        self.recipes = self.note("Recipes", h=MEERA, text="car shaped cake")
        self.ok(self.share(self.recipes["id"], KABIR, "editor", h=MEERA))
        self.save(self.recipes["id"], "car shaped cake, edited by Kabir")
        self.wifi = self.note("Wifi", h=DEV, text="the router password is in the vault")
        self.ok(self.share(self.wifi["id"], "*", "viewer", h=DEV))
        self.mine_shared = self.note("Shared out", text="for meera")
        self.ok(self.share(self.mine_shared["id"], MEERA, "viewer"))
        write(os.path.join(SHARE, "House", "Car", "insurance car.txt"), "house car insurance")
        self.house = self.ok(self.post("/api/admin/shared-folders", {"path": "/share/House", "label": "House papers",
                                                                      "access": {"u_kabir": "ro"}}, ASHA), 201)["id"]
        self.scan()

    def up(self, name, data, ref="mine", h=KABIR):
        return self.ok(self.c.post(f"/api/nodes/{ref}/upload?name={name}", content=data, headers=h), 201)

    def search(self, h=KABIR, code=200, **params):
        r = self.get("/api/search?" + urlencode(params, doseq=True), h)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def names(self, h=KABIR, **params):
        return sorted(x["name"] for x in self.search(h, **params)["results"])


class NameModes(SearchBase):
    def test_contains_starts_exact_ends(self):
        self.assertEqual(self.names(q="bud 20", match="name"), ["Budget 2026.txt"])
        self.assertEqual(self.names(q="2026 bud", match="name"), ["Budget 2026.txt"])        # any order
        self.assertEqual(self.names(q="bill", nameMode="starts"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertEqual(self.names(q="2026", nameMode="starts"), ["2026"])
        self.assertEqual(self.names(q="budget 2026", nameMode="exact"), ["Budget 2026.txt"])
        self.assertEqual(self.names(q="Budget 2026.TXT", nameMode="exact"), ["Budget 2026.txt"])
        self.assertEqual(self.names(q="budget", nameMode="exact"), [])
        self.assertEqual(self.names(q="01", nameMode="ends"), ["IMG_0001.jpg", "bill-2026-01.pdf"])
        self.assertEqual(self.names(q=".pdf", nameMode="ends"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])

    def test_wildcards(self):
        self.assertEqual(self.names(q="*.pdf", nameMode="wildcard"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertEqual(self.names(q="*.pdf"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])     # a bare pattern
        self.assertEqual(self.names(q="bill-2026-??.pdf"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertEqual(self.names(q="bill-2026-?1.PDF"), ["bill-2026-01.pdf"])               # any case
        self.assertEqual(self.names(q="IMG_[0-9]*"), ["IMG_0001.jpg"])
        self.assertEqual(self.names(q="copy-[!a].txt"), ["copy-b.txt"])
        self.assertEqual(self.names(q="name:*.jpg"), ["IMG_0001.jpg"])
        self.assertEqual(self.names(q="bill", nameMode="wildcard"), [])                       # anchored
        self.assertEqual(self.names(q="invoice-2026-03 *", nameMode="wildcard"), ["invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(q="invoice-2026-03 * type:note", nameMode="wildcard"), ["invoice-2026-03 draft.txt"])
        r = self.search(q="bill-*-01.pdf")["results"][0]
        self.assertEqual(r["nameMarked"], "⁅bill-2026-01.pdf⁆")

    def test_wildcard_translation(self):
        cases = {"*.csv": r"^.*\.csv$", "bill-2026-??.pdf": r"^bill\-2026\-..\.pdf$", "IMG_[0-9]*": r"^IMG_[0-9].*$",
                 "[!a]b": r"^[^a]b$", "a[b": r"^a\[b$", "x]y": r"^x\]y$"}
        import re
        for wc, rx in cases.items():
            self.assertEqual(pattern.wildcard_to_regex(wc), rx, wc)
            re.compile(rx)
        self.assertEqual(pattern.wildcard_glob("IMG_[A-Z]*"), "img_[a-z]*")
        self.assertEqual(pattern.wildcard_glob("[!x]É*"), "[^x]e*")
        self.assertTrue(pattern.is_prefix_word("budg*"))
        self.assertFalse(pattern.is_prefix_word("*.pdf"))
        self.assertEqual(pattern.path_like("taxes/20*"), "%/taxes/20%/%")
        self.assertEqual(pattern.path_like("a_b%/c"), "%/a\\_b\\%/c/%")

    def test_regex_names(self):
        self.assertEqual(self.names(q="^invoice-\\d{4}", nameMode="regex"), ["invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(q="name:/^bill-\\d+-0[12]/"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertEqual(self.names(q="name:/^BILL/ type:pdf"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        r = self.search(q="name:/2026-0\\d/")
        self.assertEqual(sorted(x["nameMarked"] for x in r["results"]),
                         ["bill-⁅2026-01⁆.pdf", "bill-⁅2026-02⁆.pdf", "invoice-⁅2026-03⁆ draft.txt"])
        self.assertTrue(self.search(q="(unclosed", nameMode="regex", code=422)["detail"].startswith("That pattern doesn't work"))
        self.search(q="x" * 201, nameMode="regex", code=422)
        self.settings({"regex_search": False})
        self.search(q="^bill", nameMode="regex", code=403)
        self.search(q="name:/^bill/", code=403)
        self.assertEqual(self.names(q="bill", nameMode="starts"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])


class Content(SearchBase):
    def test_words_phrase_prefix_exclude_or(self):
        self.assertEqual(self.names(q="insurance"), ["Budget 2026.txt", "insurance car.txt"])
        self.assertEqual(self.names(q="insurance", match="content"), ["Budget 2026.txt", "insurance car.txt"])
        self.assertEqual(self.names(q="insurance", match="name"), ["insurance car.txt"])
        self.assertEqual(self.names(q='"old town"'), ["Budget 2026.txt"])
        self.assertEqual(self.names(q='"town old"'), [])
        self.assertEqual(self.names(q="insur*", match="content"), ["Budget 2026.txt", "insurance car.txt"])
        self.assertEqual(self.names(q="car -draft"), ["Budget 2026.txt", "Car", "Recipes.txt", "insurance car.txt"])
        self.assertEqual(self.names(q="car -house"), ["Budget 2026.txt", "Car", "Recipes.txt", "invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(q="tent OR passports"), ["Done list.md", "Packing.md"])
        self.assertEqual(self.names(q="car tent OR rent"), ["Budget 2026.txt"])            # car AND (tent OR rent)
        r = next(x for x in self.search(q="rent due")["results"])
        self.assertIn("⁅rent⁆", r["snippet"])
        self.assertEqual(r["line"], 2)                                                    # open at the line
        self.assertEqual(self.names(q="content:invoice"), ["invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(q="-name:invoice car"), ["Budget 2026.txt", "Car", "Recipes.txt", "insurance car.txt"])

    def test_content_regex(self):
        r = self.search(q="\\b\\d{4}-\\d{4}\\b", contentMode="regex")
        self.assertEqual([x["name"] for x in r["results"]], ["invoice-2026-03 draft.txt"])
        self.assertEqual(r["results"][0]["line"], 1)
        self.assertIn("⁅1234-5678⁆", r["results"][0]["snippet"])
        self.assertEqual(self.names(q="content:/insur\\w+ \\d+/"), ["Budget 2026.txt"])
        self.assertEqual(self.names(q="content:/SECRET/"), [])                         # Meera's diary: never
        self.assertEqual(self.names(q="content:/house car/"), ["insurance car.txt"])     # a shared folder's file
        self.assertEqual(pattern.required_literals("\\binvoice-\\d{4}"), ["invoice-"])
        self.assertEqual(pattern.required_literals("a|bcd"), [])
        self.assertEqual(pattern.required_literals("(abc)?defg"), ["defg"])

    def test_regex_time_limit_and_one_at_a_time(self):
        self.up("a" * 40 + ".txt", b"x")
        with mock.patch.object(regex_worker, "REGEX_SECONDS", 1.0):
            r = self.search(q="^(a+)+b", nameMode="regex")
        self.assertTrue(r["stopped"])
        self.assertLess(r["ms"], 4000)
        with regex_worker.busy("u_kabir"):
            self.search(q="^bill", nameMode="regex", code=429)
            self.assertEqual(self.names(q="bill", match="name"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])  # words still work
            self.assertEqual(self.names(MEERA, q="^diary", nameMode="regex"), ["Diary.txt"])                 # others too
        self.assertEqual(self.names(q="^bill-2026-01", nameMode="regex"), ["bill-2026-01.pdf"])

    def test_worker_directly(self):
        res = regex_worker.run("b(c)", "name", [("1", "abcd"), ("2", "xyz")])
        self.assertEqual(res.matches, [{"id": "1", "s": 1, "e": 3}])
        self.assertFalse(res.stopped)
        bad = regex_worker.run("(", "name", [("1", "x")])
        self.assertIn("doesn't work", bad.error)
        p = os.path.join(SHARE, "w.txt")
        write(p, "one\ntwo 42 three\n")
        res = regex_worker.run("\\d+", "content", [("9", p)], limit_bytes=1000)
        self.assertEqual(res.matches, [{"id": "9", "line": 2, "snip": "two ⁅42⁆ three"}])


class Filters(SearchBase):
    def touch(self, node, iso):
        os.utime(self.real(node["id"]), (ts(iso), ts(iso)))

    def test_dates_and_time_zone_edges(self):
        for base in (os.path.join(SHARE, "household_docs", "people"), os.path.join(SHARE, "House")):
            for dirpath, dirnames, filenames in os.walk(base):
                for n in dirnames + filenames:
                    os.utime(os.path.join(dirpath, n), (ts("2026-01-01T12:00:00"), ts("2026-01-01T12:00:00")))
        # NOW is 2026-10-05 12:00 UTC = 2026-10-06 01:00 in Auckland (NZDT, +13)
        self.touch(self.budget, "2026-10-05T11:30:00")      # Auckland: 6 Oct 00:30 (today); UTC: 5 Oct (today)
        self.touch(self.invoice, "2026-10-05T10:59:00")     # Auckland: 5 Oct 23:59 (yesterday); UTC: 5 Oct (today)
        self.touch(self.ret, "2026-09-15T12:00:00")
        self.touch(self.packing, "2025-06-01T12:00:00")
        self.scan()
        self.assertEqual(self.names(q="modified:today", type="note"), ["Budget 2026.txt", "invoice-2026-03 draft.txt"])
        config.set_timezone("Pacific/Auckland")
        self.assertEqual(self.names(q="modified:today", type="note"), ["Budget 2026.txt"])
        self.assertEqual(self.names(q="modified:yesterday", type="note"), ["invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(q="modified:2026-10-06", type="note"), ["Budget 2026.txt"])
        self.assertEqual(self.names(q="modified:2026-10-05..2026-10-05", type="note"), ["invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(modified="2d", type="note,checklist"),
                         ["Budget 2026.txt", "invoice-2026-03 draft.txt"])
        config.set_timezone("UTC")
        self.assertEqual(self.names(q="modified:>2026-09-15", type="note"), ["Budget 2026.txt", "invoice-2026-03 draft.txt"])
        self.assertIn("return.txt", self.names(q="modified:>=2026-09-15", type="note"))
        self.assertEqual(self.names(q="modified:<2026-09", type="checklist"), ["Done list.md", "Packing.md"])
        self.assertEqual(self.names(q="modified:2026-09-02..2026-09-15 type:note"), ["return.txt"])
        self.assertEqual(self.names(q="modified:<=2026-09-14 type:note modified:>2025-12-31"), sorted(self.names(q="modified:2026-01-01 type:note")))
        self.assertNotIn("return.txt", self.names(q="modified:<2026-09-15 type:note"))
        self.assertIn("return.txt", self.names(q="modified:<=2026-09-15 type:note"))
        self.assertEqual(self.names(q="modified:2026-09 type:note"), ["return.txt"])
        self.assertEqual(self.names(q="modified:lastyear"), ["Packing.md"])
        self.assertIn("return.txt", self.names(q="modified:30d type:note"))
        self.assertNotIn("return.txt", self.names(q="modified:thismonth type:note"))
        self.assertEqual(self.names(q="modified:lastmonth type:note"), ["return.txt"])
        self.assertEqual(self.names(q="modified:thisyear type:checklist"), ["Done list.md"])
        self.assertIn("Packing.md", self.names(q="created:today type:checklist"))         # first seen by the index
        self.assertEqual(self.names(q="created:<2025"), [])
        r = self.search(q="modified:2026-13-45 bill")
        self.assertEqual(r["results"], [])
        self.assertTrue(any(c.get("error") for c in r["chips"]))
        with self.assertRaises(flt.FilterError):
            flt.date_range("2026-03-01..2026-01-01")

    def test_sizes_and_units(self):
        self.assertEqual(self.names(q="size:>1kb"), ["bill-2026-01.pdf"])
        self.assertEqual(self.names(q="size:<10b type:pdf"), ["bill-2026-02.pdf"])
        self.assertEqual(self.names(q="size:1kb..3kb"), ["bill-2026-01.pdf"])
        self.assertEqual(self.names(size="tiny", type="pdf"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertEqual(self.names(size="huge"), [])
        self.assertEqual(flt.parse_size("5mb"), (5 * 1024 ** 2, 6 * 1024 ** 2))
        self.assertEqual(flt.parse_size(">1.5GB"), (int(1.5 * 1024 ** 3) + 1, None))
        self.assertEqual(flt.parse_size("<100kb"), (None, 100 * 1024))
        self.assertEqual(flt.parse_size("<=1k"), (None, 1025))
        self.assertEqual(flt.parse_size("1,5 mb.."), (int(1.5 * 1024 ** 2), None))
        self.assertEqual(flt.parse_size("..2b"), (None, 3))
        for bad in ("lots", "5 parsecs", "10mb..1mb", ""):
            with self.assertRaises(flt.FilterError):
                flt.parse_size(bad)
        r = self.search(q="size:lots")
        self.assertTrue(r["chips"][0]["error"])

    def test_types_and_extensions(self):
        self.assertEqual(self.names(q="type:image"), ["IMG_0001.jpg"])
        self.assertEqual(self.names(q="type:pdf"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertEqual(self.names(q="type:checklist"), ["Done list.md", "Packing.md"])
        self.assertEqual(self.names(q="type:folder"), ["2026", "Car", "Empty", "Taxes"])
        self.assertEqual(self.names(q="ext:pdf"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertEqual(self.names(q="ext:jpg,pdf bill"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertNotIn("bill-2026-01.pdf", self.names(q="-ext:pdf type:other"))
        self.assertNotIn("Packing.md", self.names(q="-type:checklist modified:thisyear"))
        r = self.search(q="type:spaceship")
        self.assertTrue(r["chips"][0]["error"])
        self.assertEqual(self.names(q="type:other"), ["outside.log"])

    def test_people_sharing_access(self):
        self.assertEqual(self.names(q="by:outside"), ["Car", "insurance car.txt", "outside.log"])
        self.assertIn("Recipes.txt", self.names(q="by:me type:note"))
        self.assertEqual(self.names(q="by:meera"), [])                             # Meera made it; Kabir saved last
        self.save(self.recipes["id"], "meera again", MEERA)
        self.assertEqual(self.names(q="by:Meera"), ["Recipes.txt"])
        self.assertEqual(self.names(q='by:"Meera Rao"'), ["Recipes.txt"])
        self.assertEqual(self.names(q="by:notme type:note"), ["Recipes.txt", "Wifi.txt", "insurance car.txt"])
        self.assertEqual(self.names(q="by:nobodyatall")[:1], [])
        self.assertEqual(self.names(q="owner:meera"), ["Recipes.txt"])
        self.assertNotIn("Recipes.txt", self.names(q="owner:me type:note"))
        self.assertEqual(self.names(q="shared:withme"), ["Recipes.txt"])
        self.assertEqual(self.names(q="shared:everyone"), ["Wifi.txt"])
        self.assertEqual(self.names(q="shared:byme"), ["Shared out.txt"])
        self.assertNotIn("Shared out.txt", self.names(q="shared:not type:note"))
        self.assertIn("Budget 2026.txt", self.names(q="shared:not type:note"))
        self.assertEqual(self.names(q="access:read type:note"), ["Wifi.txt", "insurance car.txt"])
        self.assertIn("Recipes.txt", self.names(q="access:edit type:note"))
        self.assertEqual(self.names(access="read", q="car"), ["Car", "insurance car.txt"])

    def test_is_filters(self):
        self.ok(self.post(f"/api/nodes/{self.budget['id']}/favourite", {"value": True}))
        self.assertEqual(self.names(q="is:fav"), ["Budget 2026.txt"])
        self.assertEqual(self.names(q="is:fav", h=MEERA), [])
        self.assertEqual(self.names(q="is:open"), ["Packing.md"])
        self.assertEqual(self.names(q="is:done"), ["Done list.md"])
        self.assertEqual(self.names(q="is:empty"), ["Empty"])
        self.assertEqual(self.names(q="is:dup"), ["copy-a.txt", "copy-b.txt"])
        self.assertIn("Wifi.txt", self.names(q="is:shared type:note"))
        self.assertIn("insurance car.txt", self.names(q="is:shared"))
        self.assertNotIn("Packing.md", self.names(q="-is:open type:checklist"))
        # a duplicate the caller can't see doesn't count
        self.note("dupe", h=MEERA, text="same words twice")
        self.up("copy-c.txt", b"tax return for the year")
        self.assertEqual(self.names(q="is:dup"), ["copy-a.txt", "copy-b.txt", "copy-c.txt", "return.txt"])
        self.assertEqual(self.names(q="is:dup", h=MEERA), [])
        r = self.search(q="is:shiny")
        self.assertTrue(r["chips"][0]["error"])

    def test_path_tags_and_the_filters_hook(self):
        self.assertEqual(self.names(q="path:taxes/2026"), ["return.txt"])
        self.assertEqual(self.names(q="path:Tax*"), ["2026", "return.txt"])
        self.assertEqual(self.names(path="house papers/car"), ["insurance car.txt"])
        r = self.search(q="tag:taxes bill")                     # tags are built in since step 7 (test_organise.py)
        self.assertEqual([c["label"] for c in r["chips"]], ["Tag taxes"])
        self.assertEqual(r["results"], [])
        from app.search import engine
        with mock.patch.dict(engine.FILTERS, {"tag": lambda ctx, v, neg: ("n.name_folded LIKE ?", ["%" + v + "%"])}):
            self.assertEqual(self.names(q="tag:bill"), ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertEqual(self.names(q="color:red"), [])

    def test_scopes(self):
        self.assertEqual(self.names(q="car"), ["Budget 2026.txt", "Car", "Recipes.txt", "insurance car.txt", "invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(q="car", scope="mine"), ["Budget 2026.txt", "invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(q="car", scope="shared"), ["Recipes.txt"])
        self.assertEqual(self.names(q="router", scope="everyone"), ["Wifi.txt"])
        self.assertEqual(self.names(q="car", scope="folders"), ["Car", "insurance car.txt"])
        self.assertEqual(self.names(q="car", scope="root:" + self.house), ["Car", "insurance car.txt"])
        self.assertEqual(self.names(q='car in:"House papers"'), ["Car", "insurance car.txt"])
        self.assertEqual(self.names(q="car", scope="mine,shared"), ["Budget 2026.txt", "Recipes.txt", "invoice-2026-03 draft.txt"])
        taxes = next(x for x in self.search(q="Taxes", match="name")["results"])
        self.assertEqual(self.names(q="return", scope="folder:" + taxes["id"]), ["return.txt"])
        self.assertEqual(self.names(q="return", scope="folder:" + taxes["id"], sub="0"), [])
        r = self.search(q="car", scope="root:nope")
        self.assertEqual(r["results"], [])
        self.assertTrue(r["chips"][0]["error"])
        diary_folder = self.create("folder", "Private", h=MEERA)
        self.assertEqual(self.search(q="x", scope="folder:" + diary_folder["id"])["results"], [])
        self.assertEqual(self.names(q="in:nowhere car"), [])
        # Trash
        self.ok(self.delete(f"/api/nodes/{self.invoice['id']}"))
        self.assertEqual(self.names(q="invoice"), [])
        r = self.search(q="invoice", trash="1")
        self.assertEqual([x["name"] for x in r["results"]], ["invoice-2026-03 draft.txt"])
        self.assertTrue(r["results"][0]["inTrash"])
        self.assertEqual(r["results"][0]["location"], "Trash")
        self.assertEqual(self.names(q="in:trash invoice"), ["invoice-2026-03 draft.txt"])
        self.assertEqual(self.names(MEERA, q="in:trash invoice"), [])


class ResultsPage(SearchBase):
    def test_columns_chips_and_options(self):
        r = self.search(q="type:pdf size:<1mb bill", sort="name")
        x = r["results"][0]
        for k in ("name", "location", "modified", "updatedByName", "size", "typeLabel", "parentRef", "nameMarked", "role"):
            self.assertIn(k, x)
        self.assertEqual(x["location"], "My docs")
        self.assertEqual(x["typeLabel"], "PDF")
        self.assertEqual([c["token"] for c in r["chips"]], ["type:pdf", "size:<1mb"])
        self.assertEqual(r["options"]["sort"], "name")
        r = self.search(q="bill", type="pdf", modified="7d")
        self.assertEqual(sorted(c.get("param") for c in r["chips"]), ["modified", "type"])
        self.assertEqual(self.search(q="")["results"], [])
        self.assertFalse(self.search(q="")["searched"])

    def test_sort_group_and_pages(self):
        f = self.create("folder", "Many")
        for i in range(60):
            self.note(f"many {i:02d}", f["id"], text=f"bulk word {i}")
        r = self.search(q="many", match="name", sort="name", type="note")
        self.assertEqual((r["total"], r["pages"], len(r["results"])), (60, 2, 50))
        self.assertEqual(r["results"][0]["name"], "many 00.txt")
        r2 = self.search(q="many", match="name", sort="name", type="note", page=2)
        self.assertEqual([x["name"] for x in r2["results"]][-1], "many 59.txt")
        r = self.search(q="many", match="name", sort="name", dir="desc", type="note")
        self.assertEqual(r["results"][0]["name"], "many 59.txt")
        r = self.search(q="bill", sort="size", dir="desc")
        self.assertEqual([x["name"] for x in r["results"]], ["bill-2026-01.pdf", "bill-2026-02.pdf"])
        r = self.search(q="bill", sort="size", dir="asc")
        self.assertEqual([x["name"] for x in r["results"]], ["bill-2026-02.pdf", "bill-2026-01.pdf"])
        for s in ("relevance", "modified", "created", "type", "location"):
            self.assertTrue(self.search(q="bill", sort=s)["results"])
        r = self.search(q="car", group="location")
        self.assertEqual([x["group"] for x in r["results"]],
                         sorted([x["group"] for x in r["results"]], key=str.casefold))
        r = self.search(q="car", group="type")
        self.assertEqual([x["group"] for x in r["results"]], ["Folder", "Note", "Note", "Note", "Note"])
        r = self.search(q="bill OR car", group="type")
        groups = [x["group"] for x in r["results"]]
        self.assertEqual(groups, sorted(groups))
        r = self.search(q="bill", group="modified")
        self.assertEqual({x["group"] for x in r["results"]} <= {"Today", "This week", "Earlier"}, True)
        r = self.search(q="many", limit=10)
        self.assertEqual(r["total"], 10)
        self.assertTrue(r["more"])

    def test_csv_export(self):
        self.up("=HYPERLINK(1).txt", b"x")
        r = self.get("/api/search/export?" + urlencode({"q": "bill OR hyperlink", "match": "name"}))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-disposition"].startswith("attachment"))
        text = r.content.decode("utf-8")
        self.assertTrue(text.startswith("\ufeffName,Location,Type"))
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(rows[0], ["Name", "Location", "Type", "Modified (UTC)", "Created (UTC)", "Modified by", "Size (bytes)"])
        names = sorted(x[0] for x in rows[1:])
        self.assertEqual(names, ["'=HYPERLINK(1).txt", "bill-2026-01.pdf", "bill-2026-02.pdf"])
        self.assertNotIn("PDF one", text)
        r = self.get("/api/search/export?q=secret", MEERA)
        self.assertIn("Diary.txt", r.text)
        self.assertNotIn("Diary", self.get("/api/search/export?q=secret").text)


class SavedSearches(SearchBase):
    def test_save_pin_rename_delete(self):
        r = self.ok(self.post("/api/searches", {"name": "Bills", "q": "bill", "filters": {"type": ["pdf"]}, "pinned": True}), 201)
        sid = r["search"]["id"]
        self.assertEqual(r["searches"][0]["filters"], {"type": ["pdf"]})
        self.assertEqual(self.ok(self.get("/api/me"))["pinnedSearches"][0]["name"], "Bills")
        self.assertEqual(self.ok(self.get("/api/searches", MEERA))["searches"], [])
        self.assertEqual(self.patch(f"/api/searches/{sid}", {"pinned": False}, MEERA).status_code, 404)
        self.ok(self.patch(f"/api/searches/{sid}", {"pinned": False, "name": "PDF bills"}))
        self.assertEqual(self.ok(self.get("/api/me"))["pinnedSearches"], [])
        self.assertEqual(self.ok(self.get("/api/searches"))["searches"][0]["name"], "PDF bills")
        self.assertEqual(self.post("/api/searches", {"name": "", "q": "x", "filters": {}}).status_code, 422)
        self.assertEqual(self.post("/api/searches", {"name": "Empty", "q": "", "filters": {}}).status_code, 422)
        self.assertEqual(self.post("/api/searches", {"name": "x", "q": "x", "filters": []}).status_code, 422)
        self.assertEqual(self.delete(f"/api/searches/{sid}", MEERA).status_code, 404)
        self.ok(self.delete(f"/api/searches/{sid}"))
        self.assertEqual(self.ok(self.get("/api/searches"))["searches"], [])

    def test_recent_keeps_twenty(self):
        for i in range(25):
            self.ok(self.post("/api/searches/recent", {"q": f"word {i}", "filters": {}}))
            self.clock.advance(seconds=1)
        self.ok(self.post("/api/searches/recent", {"q": "word 3", "filters": {}}))
        rec = self.ok(self.get("/api/searches/recent"))["recent"]
        self.assertEqual(len(rec), 20)
        self.assertEqual(rec[0]["q"], "word 3")
        self.assertEqual(self.ok(self.get("/api/searches/recent", MEERA))["recent"], [])
        self.ok(self.delete("/api/searches/recent"))
        self.assertEqual(self.ok(self.get("/api/searches/recent"))["recent"], [])


class AccessEverywhere(SearchBase):
    """Every search, every mode, every filter, for every person: nothing in an answer they can't open."""
    QUERIES = [
        {"q": "car"}, {"q": "secret"}, {"q": "the"}, {"q": "*"}, {"q": "*.txt"}, {"q": "d*"}, {"q": "e", "match": "name"},
        {"q": ".", "nameMode": "regex"}, {"q": "a", "nameMode": "regex"}, {"q": "e", "contentMode": "regex"},
        {"q": "car", "nameMode": "regex", "contentMode": "regex"}, {"q": "content:/./"}, {"q": "name:/./"},
        {"q": "is:dup"}, {"q": "is:open"}, {"q": "is:done"}, {"q": "is:empty"}, {"q": "is:shared"}, {"q": "is:fav"},
        {"q": "type:note"}, {"q": "type:folder"}, {"q": "type:other"}, {"q": "size:>0b"}, {"q": "modified:>2000"},
        {"q": "created:>2000"}, {"q": "by:outside"}, {"q": "by:notme"}, {"q": "by:meera"}, {"q": "by:dev"},
        {"q": "owner:meera"}, {"q": "owner:dev"}, {"q": "shared:withme"}, {"q": "shared:everyone"},
        {"q": "shared:byme"}, {"q": "shared:not"}, {"q": "access:read"}, {"q": "access:edit"}, {"q": "path:*"},
        {"q": "in:trash"}, {"q": "car", "trash": "1"}, {"q": "x", "scope": "everyone"}, {"q": "r", "scope": "folders"},
        {"q": "car", "group": "location"}, {"q": "car OR secret OR cake OR router"}, {"q": '"secret car"'},
        {"q": "-car", "match": "name"}, {"modified": "lastyear"}, {"q": "is:dup", "sort": "size"},
        {"q": "tag:car"}, {"q": "-tag:car"}, {"tag": "car"}, {"q": "color:red"}, {"color": "red", "q": "car"},
    ]

    def test_scan_every_answer(self):
        for nid, h in ((self.diary["id"], MEERA), (self.budget["id"], KABIR), (self.recipes["id"], MEERA)):
            self.ok(self.put(f"/api/nodes/{nid}/tags", {"tags": ["car"], "color": "red"}, h))
        self.ok(self.delete(f"/api/nodes/{self.diary['id']}", MEERA))                # in Meera's Trash
        self.note("Kabir trash", text="car")
        for who in (KABIR, MEERA, DEV, ASHA):
            uid = who["X-Remote-User-Id"]
            with db.get_conn() as conn:
                user = {"id": uid, "disabled": False}
                for params in self.QUERIES:
                    with self.subTest(who=uid, params=params):
                        res = self.search(who, **params)
                        for item in res["results"]:
                            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (item["id"],)).fetchone()
                            self.assertIsNotNone(sharing.role_of(conn, user, row), (uid, item["name"]))
                        r = self.get("/api/search/export?" + urlencode(params), who)
                        exported = [x[0].lstrip("'") for x in list(csv.reader(io.StringIO(r.text.lstrip("\ufeff"))))[1:]]
                        if res["total"] <= 50:
                            self.assertEqual(sorted(exported), sorted(x["name"] for x in res["results"]))
            body = json.dumps([self.search(who, **p) for p in self.QUERIES])
            if uid != "u_meera":
                self.assertNotIn("my secret", body)
                self.assertNotIn(self.diary["id"], body)
            if uid in ("u_meera", "u_dev", "u_asha"):
                self.assertNotIn("insurance car", body)                               # only Kabir has House papers
                self.assertNotIn(self.budget["id"], body)


if __name__ == "__main__":
    unittest.main()
