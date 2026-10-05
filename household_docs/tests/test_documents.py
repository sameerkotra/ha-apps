"""Notes and checklists (SPEC §5.2, §7): saving, conflicts in the app and from outside it (409), versions
(the 5-minute rule, other editors, outside changes, conflicts, restore), checklist operations from two people,
byte-order marks and line endings, size limits and quotas."""
import _env  # noqa: F401

import os
import time
import unittest

from base import DEV, KABIR, MEERA, ApiBase, read, write
from app.formats import checklist_md, text as text_fmt


class Notes(ApiBase):
    def test_create_open_save(self):
        n = self.note("Shopping")
        self.assertEqual(n["name"], "Shopping.txt")
        self.assertTrue(os.path.isfile(self.real(n["id"])))
        doc = self.open(n["id"])
        self.assertEqual(doc["text"], "")
        out = self.save(n["id"], "Milk\nBread")
        self.assertEqual(read(self.real(n["id"])), "Milk\nBread")
        self.assertEqual(self.open(n["id"])["etag"], out["etag"])
        self.assertEqual(self.open(n["id"])["text"], "Milk\nBread")

    def test_new_note_with_text_and_name_clash(self):
        a = self.create("note", "Idea", text="first")
        b = self.create("note", "Idea")
        self.assertEqual(b["name"], "Idea (2).txt")
        self.assertEqual(self.open(a["id"])["text"], "first")

    def test_stale_etag_in_the_app_is_409_with_their_text(self):
        n = self.note("Plan", text="v1")
        self.ok(self.share(n["id"], MEERA, "editor"))
        mine = self.open(n["id"])
        theirs = self.open(n["id"], MEERA)
        self.ok(self.patch(f"/api/docs/{n['id']}", {"etag": theirs["etag"], "text": "Meera's v2"}, MEERA))
        r = self.patch(f"/api/docs/{n['id']}", {"etag": mine["etag"], "text": "Kabir's v2"})
        self.assertEqual(r.status_code, 409)
        d = r.json()["detail"]
        self.assertEqual(d["text"], "Meera's v2")
        self.assertEqual(d["by"], "Meera Rao")
        self.assertFalse(d["outside"])
        # Keep mine: saved, and theirs becomes a version
        self.ok(self.patch(f"/api/docs/{n['id']}", {"etag": d["etag"], "text": "Kabir's v2", "keepMine": True}))
        self.assertEqual(self.open(n["id"])["text"], "Kabir's v2")
        vs = self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]
        texts = [self.ok(self.get(f"/api/docs/{n['id']}/versions/{v['n']}"))["text"] for v in vs]
        self.assertIn("Meera's v2", texts)

    def test_change_outside_the_app_is_409_and_kept_in_history(self):
        n = self.note("Plan", text="from the app")
        doc = self.open(n["id"])
        time.sleep(0.01)
        write(self.real(n["id"]), "edited in the File editor")
        r = self.patch(f"/api/docs/{n['id']}", {"etag": doc["etag"], "text": "my change"})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()["detail"]["outside"])
        self.assertEqual(r.json()["detail"]["text"], "edited in the File editor")
        # opening shows the disk version (stat on open), saving after that keeps the outside text as a version
        doc = self.open(n["id"])
        self.assertEqual(doc["text"], "edited in the File editor")
        self.save(n["id"], "my change", etag=doc["etag"])
        vs = self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]
        outside = [v for v in vs if v["outside"]]
        self.assertEqual(len(outside), 1)
        self.assertEqual(self.ok(self.get(f"/api/docs/{n['id']}/versions/{outside[0]['n']}"))["text"],
                         "edited in the File editor")

    def test_bom_and_line_endings_are_kept(self):
        n = self.note("Windows")
        path = self.real(n["id"])
        write(path, b"\xef\xbb\xbfone\r\ntwo\r\n")
        doc = self.open(n["id"])
        self.assertEqual(doc["text"], "one\ntwo\n")
        self.save(n["id"], "one\ntwo\nthree\n", etag=doc["etag"])
        self.assertEqual(read(path, binary=True), b"\xef\xbb\xbfone\r\ntwo\r\nthree\r\n")

    def test_not_text_and_too_big(self):
        n = self.note("Binary")
        write(self.real(n["id"]), b"\xff\xfe\x00bad")
        self.assertEqual(self.get(f"/api/docs/{n['id']}").status_code, 415)
        big = self.note("Big")
        write(self.real(big["id"]), "x" * (5 * 1024 * 1024 + 10))
        self.assertEqual(self.get(f"/api/docs/{big['id']}").status_code, 413)
        self.settings({"max_doc_mb": 6})
        self.assertEqual(self.get(f"/api/docs/{big['id']}").status_code, 200)
        small = self.note("Small")
        doc = self.open(small["id"])
        self.settings({"max_doc_mb": 1})
        r = self.patch(f"/api/docs/{small['id']}", {"etag": doc["etag"], "text": "y" * (1024 * 1024 + 1)})
        self.assertEqual(r.status_code, 413)

    def test_quota(self):
        self.settings({"quota_gb": 1})
        n = self.note("Fits", text="small")
        self.assertEqual(self.open(n["id"])["text"], "small")
        from app import db
        with db.get_conn() as conn:          # pretend the folder is nearly full
            conn.execute("UPDATE nodes SET size = ? WHERE id = ?", (1024 ** 3, n["id"]))
        other = self.note("More")
        doc = self.open(other["id"])
        r = self.patch(f"/api/docs/{other['id']}", {"etag": doc["etag"], "text": "anything"})
        self.assertEqual(r.status_code, 507)

    def test_editing_indicator_and_etag_poll(self):
        n = self.note("Plan")
        self.ok(self.share(n["id"], MEERA, "editor"))
        self.save(n["id"], "x", MEERA)
        st = os.stat(self.real(n["id"]))
        from datetime import datetime, timezone
        self.clock.t = datetime.fromtimestamp(st.st_mtime, timezone.utc)
        e = self.ok(self.get(f"/api/docs/{n['id']}/etag"))
        self.assertEqual(e["editing"], "Meera Rao")
        self.assertEqual(e["etag"], self.open(n["id"])["etag"])
        self.assertIsNone(self.ok(self.get(f"/api/docs/{n['id']}/etag", MEERA))["editing"])

    def test_rename_keeps_the_extension(self):
        n = self.note("Plan")
        self.ok(self.post(f"/api/nodes/{n['id']}/rename", {"name": "Holiday plan"}))
        self.assertEqual(self.row(n["id"])["name"], "Holiday plan.txt")
        self.ok(self.post(f"/api/nodes/{n['id']}/rename", {"name": "Holiday plan.txt"}))
        self.assertEqual(self.row(n["id"])["name"], "Holiday plan.txt")
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/rename", {"name": "a:b"}).status_code, 422)
        self.note("Taken")
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/rename", {"name": "taken"}).status_code, 409)


class Versions(ApiBase):
    def test_five_minute_rule_other_editors_and_restore(self):
        n = self.note("Diary", text="one")
        self.ok(self.share(n["id"], MEERA, "editor"))
        self.save(n["id"], "two")                     # keeps "one"
        self.save(n["id"], "three")                   # same editor within 5 minutes: no new version
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]), 1)
        self.clock.advance(minutes=6)
        self.save(n["id"], "four")                    # keeps "three"
        self.save(n["id"], "five", MEERA)             # another editor: keeps "four"
        vs = self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]
        texts = [self.ok(self.get(f"/api/docs/{n['id']}/versions/{v['n']}"))["text"] for v in vs]
        self.assertEqual(texts, ["four", "three", "one"])
        self.assertEqual([v["who"] for v in vs], ["Kabir Rao", "Kabir Rao", "Kabir Rao"])
        # restore "one": written back as the current text; "five" becomes a version
        one = vs[-1]["n"]
        self.ok(self.post(f"/api/docs/{n['id']}/versions/{one}/restore", {}))
        self.assertEqual(self.open(n["id"])["text"], "one")
        vs = self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]
        self.assertEqual(self.ok(self.get(f"/api/docs/{n['id']}/versions/{vs[0]['n']}"))["text"], "five")
        self.assertEqual(vs[0]["who"], "Meera Rao")
        self.assertEqual(self.post(f"/api/docs/{n['id']}/versions/99/restore", {}).status_code, 404)

    def test_versions_kept_and_files_on_disk(self):
        self.settings({"versions_kept": 5})
        n = self.note("Log", text="0")
        for i in range(1, 9):
            self.clock.advance(minutes=6)
            self.save(n["id"], str(i))
        vs = self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]
        self.assertEqual(len(vs), 5)
        vdir = os.path.join(_env.os.environ["SHARE_DIR"], "household_docs", ".versions", n["id"])
        self.assertEqual(sorted(os.listdir(vdir)), sorted(f"{v['n']}.txt" for v in vs))

    def test_versions_follow_renames_and_moves(self):
        n = self.note("Diary", text="one")
        self.save(n["id"], "two")
        f = self.create("folder", "Old")
        self.ok(self.post(f"/api/nodes/{n['id']}/rename", {"name": "Journal"}))
        self.ok(self.post(f"/api/nodes/{n['id']}/move", {"parentId": f["id"]}))
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]), 1)

    def test_outside_change_then_scan_then_save(self):
        n = self.note("Plan", text="app text")
        time.sleep(0.01)
        write(self.real(n["id"]), "outside text")
        self.scan()
        self.assertIsNone(self.row(n["id"])["updated_by"])          # changed outside the app
        self.save(n["id"], "back in the app")
        vs = self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]
        self.assertTrue(vs[0]["outside"])
        self.assertEqual(self.ok(self.get(f"/api/docs/{n['id']}/versions/{vs[0]['n']}"))["text"], "outside text")


class Checklists(ApiBase):
    PEOPLE = (KABIR, MEERA, DEV)

    def make(self):
        c = self.create("checklist", "Party")
        self.assertEqual(c["name"], "Party.md")
        self.assertEqual(c["kind"], "checklist")
        r = self.ops(c["id"], [{"op": "add", "text": "Cake"}, {"op": "add", "text": "Balloons"},
                               {"op": "add", "text": "Candles", "indent": 1}, {"op": "add", "text": "Music"}])
        self.assertEqual(r.status_code, 200, r.text)
        return c["id"], {i["text"]: i["key"] for i in r.json()["items"]}

    def test_file_format(self):
        cid, keys = self.make()
        self.ok(self.ops(cid, [{"op": "tick", "key": keys["Cake"]}]))
        self.assertEqual(read(self.real(cid)), "- [x] Cake\n- [ ] Balloons\n  - [ ] Candles\n- [ ] Music\n")

    def test_two_people_tick_different_items_without_conflict(self):
        cid, keys = self.make()
        self.ok(self.share(cid, MEERA, "editor"))
        self.ok(self.ops(cid, [{"op": "tick", "key": keys["Cake"]}]))
        out = self.ok(self.ops(cid, [{"op": "tick", "key": keys["Music"]}], MEERA))
        done = {i["text"]: (i["done"], i["doneByName"]) for i in out["items"]}
        self.assertEqual(done["Cake"], (True, "Kabir Rao"))
        self.assertEqual(done["Music"], (True, "Meera Rao"))
        self.assertEqual(done["Balloons"], (False, None))

    def test_edited_item_by_someone_else_is_409_for_that_item_only(self):
        cid, keys = self.make()
        self.ok(self.share(cid, MEERA, "editor"))
        self.ok(self.ops(cid, [{"op": "edit", "key": keys["Cake"], "text": "Chocolate cake"}], MEERA))
        r = self.ops(cid, [{"op": "tick", "key": keys["Cake"]}, {"op": "tick", "key": keys["Balloons"]}])
        self.assertEqual(r.status_code, 409)
        d = r.json()["detail"]
        self.assertEqual(len(d["conflicts"]), 1)
        self.assertTrue(d["conflicts"][0]["gone"])
        done = {i["text"]: i["done"] for i in d["items"]}
        self.assertTrue(done["Balloons"])                 # the other op was applied
        self.assertFalse(done["Chocolate cake"])

    def test_all_operations(self):
        cid, keys = self.make()
        out = self.ok(self.ops(cid, [{"op": "move", "key": keys["Music"], "before": keys["Cake"]}]))
        self.assertEqual([i["text"] for i in out["items"]], ["Music", "Cake", "Balloons", "Candles"])
        out = self.ok(self.ops(cid, [{"op": "indent", "key": keys["Candles"], "level": 0}]))
        self.assertEqual(out["items"][3]["level"], 0)
        out = self.ok(self.ops(cid, [{"op": "delete", "key": keys["Balloons"]}]))
        self.assertEqual([i["text"] for i in out["items"]], ["Music", "Cake", "Candles"])
        out = self.ok(self.ops(cid, [{"op": "add", "text": "Napkins", "after": keys["Music"]}]))
        self.assertEqual([i["text"] for i in out["items"]], ["Music", "Napkins", "Cake", "Candles"])
        keys = {i["text"]: i["key"] for i in out["items"]}
        out = self.ok(self.ops(cid, [{"op": "tick", "key": keys["Cake"]}, {"op": "tick", "key": keys["Music"]}]))
        out = self.ok(self.ops(cid, [{"op": "untickAll"}]))
        self.assertFalse(any(i["done"] for i in out["items"]))
        self.assertEqual(self.ops(cid, [{"op": "edit", "key": keys["Cake"], "text": ""}]).status_code, 409)
        self.assertEqual(self.ops(cid, [{"op": "nope"}]).status_code, 422)
        self.assertEqual(self.ops(cid, [{"op": "indent", "key": keys["Cake"], "level": 3}]).status_code, 409)

    def test_duplicate_items_are_told_apart(self):
        c = self.create("checklist", "Shop")
        out = self.ok(self.ops(c["id"], [{"op": "add", "text": "Milk"}, {"op": "add", "text": "Milk"}]))
        k2 = out["items"][1]["key"]
        out = self.ok(self.ops(c["id"], [{"op": "tick", "key": k2}]))
        self.assertEqual([i["done"] for i in out["items"]], [False, True])

    def test_ticks_made_outside_and_kind_changes(self):
        cid, _keys = self.make()
        write(self.real(cid), "- [x] Cake\n- [ ] Balloons\n")
        doc = self.open(cid)
        self.assertEqual([(i["text"], i["done"], i["doneBy"]) for i in doc["items"]],
                         [("Cake", True, None), ("Balloons", False, None)])
        write(self.real(cid), "# Party\n\n- [ ] Cake\n")              # not a pure task list any more
        doc = self.open(cid)
        self.assertEqual(doc["kind"], "markdown")
        self.assertIn("# Party", doc["text"])
        self.assertEqual(self.ops(cid, [{"op": "untickAll"}]).status_code, 422)
        write(self.real(cid), "- [ ] Cake\n")
        self.scan()
        self.assertEqual(self.open(cid)["kind"], "checklist")

    def test_checklist_versions(self):
        cid, keys = self.make()
        self.ok(self.share(cid, MEERA, "editor"))
        self.ok(self.ops(cid, [{"op": "tick", "key": keys["Cake"]}], MEERA))
        vs = self.ok(self.get(f"/api/docs/{cid}/versions"))["versions"]
        self.assertEqual(len(vs), 1)
        self.assertNotIn("[x]", self.ok(self.get(f"/api/docs/{cid}/versions/{vs[0]['n']}"))["text"])


class Formats(unittest.TestCase):
    def test_checklist_parse_and_detect(self):
        self.assertTrue(checklist_md.is_checklist("- [ ] a\n\n  - [x] b\n"))
        self.assertFalse(checklist_md.is_checklist("- [ ] a\nplain\n"))
        self.assertFalse(checklist_md.is_checklist(""))
        self.assertEqual(checklist_md.md_kind("", "checklist"), "checklist")
        self.assertEqual(checklist_md.md_kind("", None), "markdown")
        items = checklist_md.parse("- [X] A\n    - [ ] deep\n")
        self.assertEqual([(i.text, i.done, i.level) for i in items], [("A", True, 0), ("deep", False, 1)])
        self.assertEqual(checklist_md.serialise(items), "- [x] A\n  - [ ] deep\n")

    def test_text_round_trips(self):
        for raw in (b"a\nb", b"a\r\nb\r\n", b"\xef\xbb\xbfa\rb", "é\n".encode()):
            text, meta = text_fmt.decode(raw)
            self.assertEqual(text_fmt.encode(text, meta), raw)
        with self.assertRaises(text_fmt.NotText):
            text_fmt.decode(b"\xff\xfe")


if __name__ == "__main__":
    unittest.main()
