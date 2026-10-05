"""Connect and tidy (SPEC §17.17–§17.21): templates; filing rules (arrivals by upload, scan and the index, name
patterns, renames, first match wins, dry run, Undo from Activity, read-only mode); clean-up rules (Trash and
Archive, favourites and pins skipped, read-only mode, once a day); Kids' space (every route walked as a child,
the content checks, parents' view access); what changed since you last looked (dots, the open document's
changes, is:new, Mark all as seen)."""
import os
import time
from datetime import datetime, timedelta, timezone

from base import ASHA, DEV, KABIR, MEERA, SHARE, ApiBase, hdr, person_dir, write

from app import config, db, filing, kids
from app.main import app

KID = hdr("u_kid", "Ravi Rao", "ravi")


def grid(name="Sheet1", cells=None, **kw):
    t = {"name": name, "kind": "grid", "cells": cells or {}, "cols": {}, "freeze": {"r": 0, "c": 0}, "totals": None,
         "cond": [], "charts": []}
    t.update(kw)
    return t


class TidyBase(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV, KID)

    def setUp(self):
        super().setUp()
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET is_child = 1, child_since = ? WHERE id = 'u_kid'", (config.now_iso(),))

    def upload(self, ref, name, data=b"%PDF-1.4 hello", h=KABIR, code=201):
        r = self.c.post(f"/api/nodes/{ref}/upload?name={name}", content=data, headers=h)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def node(self, nid):
        return self.ok(self.get(f"/api/nodes/{nid}"))

    def children(self, ref, h=KABIR):
        return {i["name"]: i for i in self.ok(self.get(f"/api/list?node={ref}", h))["items"]}

    def sheet(self, name, cells, h=KABIR, parent=None):
        it = self.create("sheet", name, parent, h)
        etag = self.open(it["id"], h)["etag"]
        self.ok(self.patch(f"/api/docs/{it['id']}", {"etag": etag, "sheet": {"tabs": [grid("Sheet1", cells)]}}, h))
        return it


# =====================================================================
# Templates (§17.17)
# =====================================================================
class TemplateTests(TidyBase):
    def test_built_in_templates(self):
        d = self.ok(self.get("/api/templates"))
        self.assertEqual([t["name"] for t in d["builtin"]], ["Meeting notes", "Home maintenance log", "Monthly budget",
                                                             "Emergency contacts", "Babysitter info", "Trip plan"])
        self.assertFalse(any("grocer" in t["name"].lower() or "packing" in t["name"].lower() for t in d["builtin"]))
        m = self.ok(self.post("/api/templates/use", {"ref": "builtin:meeting", "name": "Monday"}), 201)
        self.assertEqual((m["name"], m["kind"]), ("Monday.md", "markdown"))
        self.assertIn("## Decisions", self.open(m["id"])["text"])
        b = self.ok(self.post("/api/templates/use", {"ref": "builtin:budget", "name": "October"}), 201)
        self.assertEqual((b["name"], b["kind"]), ("October.xlsx", "sheet"))
        s = self.open(b["id"])
        self.assertTrue(s["canEdit"])                       # the app's own .xlsx
        tab = s["sheet"]["tabs"][0]
        self.assertEqual(tab["cells"]["D2"]["v"], "=B2-C2")
        self.assertEqual(tab["cells"]["A2"]["v"], "Rent or mortgage")
        self.assertEqual(tab["totals"], {"B": "SUM", "C": "SUM", "D": "SUM"})
        log = self.ok(self.post("/api/templates/use", {"ref": "builtin:maintenance", "name": "House log"}), 201)
        cells = self.open(log["id"])["sheet"]["tabs"][0]["cells"]
        self.assertEqual([cells[c]["v"] for c in ("A1", "B1", "C1", "D1")], ["Date", "What", "Who", "Cost"])
        for key in ("contacts", "babysitter", "trip"):
            self.ok(self.post("/api/templates/use", {"ref": f"builtin:{key}", "name": key}), 201)
        f = self.create("folder", "Trips")
        t = self.ok(self.post("/api/templates/use", {"ref": "builtin:trip", "name": "Goa", "parentId": f["id"]}), 201)
        self.assertEqual(t["parentId"], f["id"])
        self.assertEqual(self.post("/api/templates/use", {"ref": "builtin:nope", "name": "x"}).status_code, 404)
        self.assertEqual(self.post("/api/templates/use", {"ref": "builtin:trip", "name": "x", "parentId": f["id"]}, MEERA).status_code, 404)

    def test_save_as_template_yours_and_the_households(self):
        c = self.create("checklist", "Before we leave")
        self.ok(self.ops(c["id"], [{"op": "add", "text": "Windows shut"}, {"op": "add", "text": "Iron off"}]))
        t = self.ok(self.post(f"/api/docs/{c['id']}/save-template", {"name": "Leaving"}), 201)
        self.assertEqual(t, {"ref": "mine:Leaving.md", "name": "Leaving"})
        self.assertTrue(os.path.isfile(os.path.join(person_dir("Kabir Rao"), ".templates", "Leaving.md")))
        d = self.ok(self.get("/api/templates"))
        self.assertEqual([(x["name"], x["kind"]) for x in d["mine"]], [("Leaving", "checklist")])
        self.assertEqual(self.ok(self.get("/api/templates", MEERA))["mine"], [])          # yours are yours
        new = self.ok(self.post("/api/templates/use", {"ref": "mine:Leaving.md", "name": "Trip"}), 201)
        self.assertEqual(new["kind"], "checklist")
        self.assertEqual([i["text"] for i in self.open(new["id"])["items"]], ["Windows shut", "Iron off"])
        # the hidden folder is never listed, indexed or found
        self.scan()
        self.assertNotIn(".templates", {i["name"] for i in self.ok(self.get("/api/space/mine"))["items"]})
        self.assertEqual(self.ok(self.get("/api/search?q=Leaving"))["total"], 0)
        # the household's: admins only
        self.assertEqual(self.post(f"/api/docs/{c['id']}/save-template", {"name": "All", "household": True}).status_code, 403)
        self.ok(self.share(c["id"], ASHA, "viewer"))
        h = self.ok(self.post(f"/api/docs/{c['id']}/save-template", {"name": "House leaving", "household": True}, ASHA), 201)
        self.assertEqual(h["ref"], "household:House leaving.md")
        self.assertEqual([x["name"] for x in self.ok(self.get("/api/templates", MEERA))["household"]], ["House leaving"])
        self.assertEqual(self.delete("/api/templates/household:House%20leaving.md", MEERA).status_code, 403)
        self.ok(self.delete("/api/templates/mine:Leaving.md"))
        self.assertEqual(self.ok(self.get("/api/templates"))["mine"], [])
        for ref in ("mine:../Kabir Rao/x.md", "mine:.hidden", "household:", "nope:x"):
            self.assertEqual(self.post("/api/templates/use", {"ref": ref, "name": "x"}).status_code, 404, ref)
        self.ok(self.post("/api/admin/docs-folder/read-only", {"on": True}, ASHA))
        self.assertEqual(self.post(f"/api/docs/{c['id']}/save-template", {"name": "Again"}).status_code, 423)
        self.assertEqual(self.post("/api/templates/use", {"ref": "builtin:trip", "name": "x"}).status_code, 423)


# =====================================================================
# Filing rules (§17.18)
# =====================================================================
class FilingTests(TidyBase):
    def setUp(self):
        super().setUp()
        self.inbox = self.create("folder", "Inbox")
        self.bills = self.create("folder", "Bills")
        self.electric = self.create("folder", "Electric", self.bills["id"])

    def rule(self, body, ref=None, h=KABIR, code=201):
        r = self.post(f"/api/rules/{ref or self.inbox['id']}/filing", body, h)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def test_upload_is_renamed_and_moved_with_undo_from_activity(self):
        d = self.rule({"pattern": "Power-bill*.pdf", "rename": "{yyyy}-{mm} Electric.pdf", "moveTo": self.electric["id"]})
        self.assertEqual(d["filing"][0]["moveToName"], "Electric")
        up = self.upload(self.inbox["id"], "power-BILL-oct.pdf")              # the pattern ignores case
        self.assertEqual((up["name"], up["parentId"]), ("2026-10 Electric.pdf", self.electric["id"]))
        self.assertTrue(os.path.isfile(os.path.join(person_dir("Kabir Rao"), "Bills", "Electric", "2026-10 Electric.pdf")))
        up2 = self.upload(self.inbox["id"], "Power-bill-nov.pdf")
        self.assertEqual(up2["name"], "2026-10 Electric (2).pdf")              # taken: " (2)"
        other = self.upload(self.inbox["id"], "Water.pdf")                     # no rule matches: stays
        self.assertEqual(other["parentId"], self.inbox["id"])
        feed = self.ok(self.get("/api/activity"))["items"]
        filed = [e for e in feed if e["action"] == "filed"]
        self.assertEqual(len(filed), 2)
        self.assertEqual((filed[-1]["detail"]["from"], filed[-1]["detail"]["to"], filed[-1]["detail"]["toFolder"]),
                         ("power-BILL-oct.pdf", "2026-10 Electric.pdf", "Electric"))
        self.assertTrue(filed[-1]["undo"])
        self.assertFalse([e for e in feed if e["action"] in ("moved", "renamed")])    # one "filed" row, not three
        # Undo puts it back where it arrived, under its old name
        self.ok(self.post(f"/api/filing/{filed[-1]['undo']}/undo", {}))
        n = self.node(up["id"])
        self.assertEqual((n["name"], n["parentId"]), ("power-BILL-oct.pdf", self.inbox["id"]))
        self.assertEqual(self.post(f"/api/filing/{filed[-1]['undo']}/undo", {}).status_code, 409)
        feed = self.ok(self.get("/api/activity"))["items"]
        self.assertFalse([e for e in feed if e["action"] == "filed" and e["detail"]["log"] == filed[-1]["undo"]][0]["undo"])
        # 7 days: then it can't be undone
        self.clock.advance(days=8)
        self.assertEqual(self.post(f"/api/filing/{filed[0]['undo']}/undo", {}).status_code, 409)
        self.assertEqual(self.post("/api/filing/nope/undo", {}).status_code, 404)

    def test_undo_needs_edit_rights_and_viewers_see_no_undo(self):
        self.rule({"pattern": "*.pdf", "moveTo": self.electric["id"]})
        self.ok(self.share(self.bills["id"], MEERA, "viewer"))
        up = self.upload(self.inbox["id"], "a.pdf")
        log = [e for e in self.ok(self.get("/api/activity"))["items"] if e["action"] == "filed"][0]["undo"]
        mine = [e for e in self.ok(self.get("/api/activity", MEERA))["items"] if e["action"] == "filed"]
        self.assertEqual((len(mine), mine[0]["undo"]), (1, None))
        self.assertEqual(self.post(f"/api/filing/{log}/undo", {}, MEERA).status_code, 403)
        self.assertEqual(self.post(f"/api/filing/{log}/undo", {}, DEV).status_code, 404)
        self.assertEqual(self.node(up["id"])["parentId"], self.electric["id"])

    def test_placeholders_n_first_match_wins_type_and_size(self):
        self.rule({"pattern": "IMG_*", "type": "image", "rename": "Photo {date} {n}"})
        self.rule({"pattern": "*", "type": "pdf", "minKb": 1, "rename": "Big {name}"})
        self.rule({"pattern": "*", "type": "pdf", "rename": "Small {name} {dd}"})
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        a = self.upload(self.inbox["id"], "IMG_0001.png", png)
        b = self.upload(self.inbox["id"], "IMG_0002.png", png)
        self.assertEqual((a["name"], b["name"]), ("Photo 2026-10-05 1.png", "Photo 2026-10-05 2.png"))
        big = self.upload(self.inbox["id"], "scan.pdf", b"%PDF-1.4 " + b"x" * 2000)
        small = self.upload(self.inbox["id"], "tiny.pdf", b"%PDF-1.4 x")
        self.assertEqual((big["name"], small["name"]), ("Big scan.pdf", "Small tiny 05.pdf"))
        txt = self.upload(self.inbox["id"], "IMG_note.txt", b"just text")            # an image rule, not an image
        self.assertEqual(txt["name"], "IMG_note.txt")

    def test_files_found_by_the_index(self):
        self.rule({"pattern": "*.pdf", "moveTo": self.electric["id"], "rename": "{name} (filed)"})
        self.scan()                                                              # the folder is known
        write(os.path.join(person_dir("Kabir Rao"), "Inbox", "From samba.pdf"), b"%PDF-1.4 z")
        self.scan()
        self.assertEqual(filing.run_queue(), 1)
        names = self.children(self.electric["id"])
        self.assertIn("From samba (filed).pdf", names)
        self.assertNotIn("From samba.pdf", self.children(self.inbox["id"]))
        # a document someone makes in the app is no arrival
        n = self.note("Draft", self.inbox["id"])
        self.assertEqual(filing.run_queue(), 0)
        self.assertEqual(self.node(n["id"])["parentId"], self.inbox["id"])

    def test_read_only_mode_holds_arrivals(self):
        self.rule({"pattern": "*.pdf", "moveTo": self.electric["id"]})
        self.scan()
        self.ok(self.post("/api/admin/docs-folder/read-only", {"on": True}, ASHA))
        self.upload(self.inbox["id"], "x.pdf", code=423)
        write(os.path.join(person_dir("Kabir Rao"), "Inbox", "outside.pdf"), b"%PDF-1.4 z")
        self.scan()
        self.assertEqual(filing.run_queue(), 0)
        self.assertIn("outside.pdf", self.children(self.inbox["id"]))
        self.assertEqual(self.post(f"/api/rules/{self.inbox['id']}/filing", {"pattern": "*", "rename": "x"}).status_code, 423)
        self.ok(self.post("/api/admin/docs-folder/read-only", {"on": False}, ASHA))
        self.assertEqual(filing.run_queue(), 1)
        self.assertIn("outside.pdf", self.children(self.electric["id"]))

    def test_dry_run_changes_nothing(self):
        for n in ("Power-bill-1.pdf", "Power-bill-2.pdf", "other.pdf"):
            self.upload(self.inbox["id"], n)
        out = self.ok(self.post(f"/api/rules/{self.inbox['id']}/filing/dry-run",
                                {"pattern": "power-bill*", "rename": "{yyyy} {n}", "moveTo": self.electric["id"]}))
        self.assertEqual(out["count"], 2)
        self.assertEqual([(m["name"], m["newName"], m["to"]) for m in out["matches"]],
                         [("Power-bill-1.pdf", "2026 1.pdf", "Electric"), ("Power-bill-2.pdf", "2026 2.pdf", "Electric")])
        self.assertEqual(set(self.children(self.inbox["id"])), {"Power-bill-1.pdf", "Power-bill-2.pdf", "other.pdf"})
        self.assertEqual(self.ok(self.get(f"/api/rules/{self.inbox['id']}"))["filing"], [])

    def test_rules_rights_and_checks(self):
        ref = self.inbox["id"]
        self.ok(self.share(self.inbox["id"], MEERA, "viewer"))
        self.assertEqual(self.post(f"/api/rules/{ref}/filing", {"pattern": "*", "rename": "x"}, MEERA).status_code, 403)
        self.assertFalse(self.ok(self.get(f"/api/rules/{ref}", MEERA))["canEdit"])
        self.assertEqual(self.get(f"/api/rules/{ref}", DEV).status_code, 404)
        self.assertEqual(self.post("/api/rules/mine/filing", {"pattern": "*", "rename": "x"}).status_code, 404)
        root = "root:" + self.root_id()
        self.assertEqual(self.post(f"/api/rules/{root}/filing", {"pattern": "*", "rename": "x"}).status_code, 422)
        meera_folder = self.create("folder", "Hers", None, MEERA)
        for body in ({"pattern": "", "rename": "x"}, {"pattern": "a/b", "rename": "x"}, {"pattern": "*"},
                     {"pattern": "*", "rename": "{when}"}, {"pattern": "*", "rename": "bad:name"},
                     {"pattern": "*", "type": "movie", "rename": "x"}, {"pattern": "*", "minKb": -1, "rename": "x"},
                     {"pattern": "*", "moveTo": meera_folder["id"]}, {"pattern": "*", "x": 1}):
            self.assertIn(self.post(f"/api/rules/{ref}/filing", body).status_code, (404, 422), body)
        d = self.rule({"pattern": "a*", "rename": "A {n}"})
        d = self.rule({"pattern": "b*", "rename": "B {n}"})
        ids = [r["id"] for r in d["filing"]]
        d = self.ok(self.post(f"/api/rules/{ref}/filing/order", {"ids": list(reversed(ids))}))
        self.assertEqual([r["pattern"] for r in d["filing"]], ["b*", "a*"])
        self.assertEqual(self.post(f"/api/rules/{ref}/filing/order", {"ids": ids[:1]}).status_code, 409)
        d = self.ok(self.put(f"/api/rules/{ref}/filing/{ids[0]}", {"pattern": "c*", "rename": "C"}))
        self.assertEqual([r["pattern"] for r in d["filing"]], ["b*", "c*"])
        d = self.ok(self.delete(f"/api/rules/{ref}/filing/{ids[1]}"))
        self.assertEqual([r["pattern"] for r in d["filing"]], ["c*"])
        # the folder carries the count (its head and its row)
        self.assertEqual({i["name"]: i for i in self.ok(self.get("/api/space/mine"))["items"]}["Inbox"]["filingRules"], 1)

    def test_rule_acts_as_its_maker_and_stops_when_they_lose_rights(self):
        self.ok(self.share(self.inbox["id"], MEERA, "editor"))
        self.rule({"pattern": "*.pdf", "rename": "M {name}"}, h=MEERA)
        a = self.upload(self.inbox["id"], "one.pdf")
        self.assertEqual(a["name"], "M one.pdf")
        filed = [e for e in self.ok(self.get("/api/activity"))["items"] if e["action"] == "filed"][0]
        self.assertEqual(filed["actorName"], "Meera Rao")
        self.ok(self.delete(f"/api/nodes/{self.inbox['id']}/shares/u_meera"))
        b = self.upload(self.inbox["id"], "two.pdf")
        self.assertEqual(b["name"], "two.pdf")

    def test_hourly_tidying_of_old_records(self):
        from app import app_messages
        self.rule({"pattern": "*.pdf", "moveTo": self.electric["id"]})
        self.upload(self.inbox["id"], "a.pdf")
        with db.get_conn() as conn:
            conn.execute("INSERT INTO bus_requests (id, user_id, kind, ref, state, created_at, updated_at) "
                         "VALUES ('r1', 'u_kabir', 'chats', 'chats:r1', 'done', ?, ?)", ("2026-01-01T00:00:00+00:00",) * 2)
        self.clock.advance(days=31)
        self.ok(self.delete(f"/api/nodes/{self.bills['id']}"))
        with db.get_conn() as conn:
            filing.prune(conn)
            app_messages.prune(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM filing_log").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bus_requests").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM filing_rules").fetchone()[0], 1)   # its folder is in Trash
        self.ok(self.delete(f"/api/nodes/{self.inbox['id']}"))
        self.ok(self.post("/api/trash/empty", {}))
        with db.get_conn() as conn:
            filing.prune(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM filing_rules").fetchone()[0], 0)    # gone for good

    def test_shared_folder_top_rules(self):
        d = os.path.join(SHARE, "House")
        os.makedirs(os.path.join(d, "Bills"))
        rid = self.ok(self.post("/api/admin/shared-folders", {"path": "/share/House", "label": "House",
                                                              "access": {"u_kabir": "rw", "u_meera": "ro"}}, ASHA), 201)["id"]
        self.scan()
        ref = "root:" + rid
        bills = self.children(ref)["Bills"]
        self.rule({"pattern": "*.pdf", "moveTo": bills["id"]}, ref=ref)
        self.assertEqual(self.post(f"/api/rules/{ref}/filing", {"pattern": "*", "rename": "x"}, MEERA).status_code, 403)
        up = self.upload(ref, "bill.pdf")
        self.assertEqual(up["parentId"], bills["id"])
        self.assertEqual(self.ok(self.get(f"/api/list?node={ref}"))["folder"]["filingRules"], 1)


# =====================================================================
# Clean-up rules (§17.19)
# =====================================================================
class CleanupTests(TidyBase):
    def age(self, nid, days):
        t = (config.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
        with db.get_conn() as conn:
            conn.execute("UPDATE nodes SET mtime = ? WHERE id = ?", (t, nid))

    def test_trash_after_n_days_skipping_favourites_pins_and_folders(self):
        inbox = self.create("folder", "Inbox")
        old = self.note("Old", inbox["id"])
        new = self.note("New", inbox["id"])
        fav = self.note("Fav", inbox["id"])
        pin = self.note("Pin", inbox["id"])
        sub = self.create("folder", "Keep me", inbox["id"])
        self.ok(self.share(inbox["id"], MEERA, "viewer"))
        self.ok(self.post(f"/api/nodes/{fav['id']}/favourite", {"value": True}, MEERA))    # anyone's favourite
        self.ok(self.post(f"/api/nodes/{pin['id']}/pin", {"value": True}))
        for n in (old, fav, pin, sub):
            self.age(n["id"], 40)
        self.age(new["id"], 10)
        d = self.ok(self.put(f"/api/rules/{inbox['id']}/cleanup", {"action": "trash", "days": 30}))
        self.assertEqual(d["cleanup"]["text"], "Items here move to Trash 30 days after their last change.")
        self.assertEqual(self.ok(self.get(f"/api/list?node={inbox['id']}"))["folder"]["cleanup"], d["cleanup"]["text"])
        stats = filing.run_cleanup()
        self.assertEqual((stats["trashed"], stats["skipped"]), (1, 2))
        self.assertEqual(set(self.children(inbox["id"])), {"New.txt", "Fav.txt", "Pin.txt", "Keep me"})
        self.assertEqual([t["name"] for t in self.ok(self.get("/api/space/trash"))["items"]], ["Old.txt"])
        # once a day
        self.age(new["id"], 40)
        self.assertEqual(filing.run_cleanup()["rules"], 0)
        self.clock.advance(days=1)
        self.age(new["id"], 40)
        self.assertEqual(filing.run_cleanup()["trashed"], 1)

    def test_archive_and_read_only_mode(self):
        inbox = self.create("folder", "Inbox")
        a = self.note("A", inbox["id"])
        self.age(a["id"], 100)
        self.ok(self.put(f"/api/rules/{inbox['id']}/cleanup", {"action": "archive", "days": 90}))
        self.ok(self.post("/api/admin/docs-folder/read-only", {"on": True}, ASHA))
        self.assertEqual(filing.run_cleanup(force=True)["rules"], 0)
        self.assertIn("A.txt", self.children(inbox["id"]))
        self.ok(self.post("/api/admin/docs-folder/read-only", {"on": False}, ASHA))
        self.assertEqual(filing.run_cleanup()["archived"], 1)
        kids_ = self.children(inbox["id"])
        self.assertEqual(set(kids_), {"Archive"})
        self.assertIn("A.txt", self.children(kids_["Archive"]["id"]))
        # nothing in a folder without a rule is touched
        other = self.note("Elsewhere")
        self.age(other["id"], 1000)
        filing.run_cleanup(force=True)
        self.assertIn("Elsewhere.txt", {i["name"] for i in self.ok(self.get("/api/space/mine"))["items"]})

    def test_rights_and_checks(self):
        inbox = self.create("folder", "Inbox")
        self.ok(self.share(inbox["id"], MEERA, "viewer"))
        self.assertEqual(self.put(f"/api/rules/{inbox['id']}/cleanup", {"action": "trash", "days": 3}, MEERA).status_code, 403)
        for body in ({"action": "burn", "days": 3}, {"action": "trash", "days": 0}, {"action": "trash", "days": "3"}, {}):
            self.assertEqual(self.put(f"/api/rules/{inbox['id']}/cleanup", body).status_code, 422, body)
        self.ok(self.put(f"/api/rules/{inbox['id']}/cleanup", {"action": "trash", "days": 3}))
        self.assertIsNone(self.ok(self.delete(f"/api/rules/{inbox['id']}/cleanup"))["cleanup"])


# =====================================================================
# Kids' space (§17.20)
# =====================================================================
def api_routes():
    from fastapi.routing import APIRoute
    out = []

    def walk(routes):
        for r in routes:
            if hasattr(r, "original_router"):
                walk(r.original_router.routes)
            elif isinstance(r, APIRoute):
                out.append(r)
    walk(app.routes)
    return out


class KidsTests(TidyBase):
    def fill(self, path, nid):
        return (path.replace("{node_id}", nid).replace("{ref}", nid).replace("{target}", "u_kabir").replace("{n}", "1")
                .replace("{trash_id}", "x").replace("{sid}", "x").replace("{kind}", "zip").replace("{token}", "x")
                .replace("{job_id}", "x").replace("{space}", "mine").replace("{request_id}", "x").replace("{rule_id}", "x")
                .replace("{log_id}", "x"))

    def test_every_route_is_decided_and_the_blocked_ones_refuse_a_child(self):
        n = self.note("Mine", h=KID, text="hi")
        seen = set()
        for r in api_routes():
            if not r.path.startswith("/api/") or r.path.startswith("/api/admin/") or r.path == "/api/health":
                continue
            for m in r.methods - {"HEAD", "OPTIONS"}:
                key = (m, r.path)
                seen.add(key)
                classes = [kids.blocked(m, r.path), key in kids.CHECKED, key in kids.ALLOWED]
                self.assertEqual(sum(classes), 1, f"{key}: decide in app/kids.py whether children may use it")
                if kids.blocked(m, r.path):
                    res = self.c.request(m, self.fill(r.path, n["id"]), headers=KID, json={})
                    self.assertEqual(res.status_code, 403, f"{key}: {res.text}")
                    self.assertEqual(res.json()["detail"], kids.MESSAGE)
        self.assertTrue(len(seen) > 100)
        self.assertFalse((kids.ALLOWED | kids.CHECKED) - seen, "routes listed in kids.py that don't exist")
        # a grown-up on the same blocked routes isn't refused for being a child
        for path in ("/api/nodes/{node_id}/shares", "/api/ai"):
            self.assertNotEqual(self.c.get(self.fill(path, self.note("x")["id"]), headers=KABIR).status_code, 403)

    def test_content_checks(self):
        # sheets: not made, opened, saved, converted or restored; notes and checklists are fine
        self.assertEqual(self.post("/api/docs", {"kind": "sheet", "name": "S"}, KID).status_code, 403)
        self.create("note", "N", h=KID)
        self.create("checklist", "C", h=KID)
        s = self.sheet("Budget", {"A1": {"v": 1}})
        self.ok(self.share(s["id"], KID, "editor"))
        self.assertEqual(self.get(f"/api/docs/{s['id']}", KID).status_code, 403)
        self.assertEqual(self.patch(f"/api/docs/{s['id']}", {"etag": "x", "sheet": {"tabs": [grid()]}}, KID).status_code, 403)
        self.assertEqual(self.post(f"/api/docs/{s['id']}/convert", {}, KID).status_code, 403)
        self.assertEqual(self.post(f"/api/docs/{s['id']}/versions/1/restore", {}, KID).status_code, 403)
        self.assertEqual(self.post("/api/templates/use", {"ref": "builtin:budget", "name": "B"}, KID).status_code, 403)
        self.assertEqual(self.post(f"/api/docs/{s['id']}/save-template", {"name": "T"}, KID).status_code, 403)
        self.assertNotIn("sheet", {t["kind"] for t in self.ok(self.get("/api/templates", KID))["builtin"]})
        n = self.create("note", "Plain", h=KID)
        self.ok(self.post(f"/api/docs/{n['id']}/convert", {}, KID))                 # Plain → Markdown is fine
        # no regular expressions
        for q in ("/api/search?q=x&nameMode=regex", "/api/search?q=name:/x/", "/api/search?q=x&contentMode=regex",
                  "/api/search/export?q=x&nameMode=regex"):
            self.assertEqual(self.get(q, KID).status_code, 403, q)
        self.assertFalse(self.ok(self.get("/api/me", KID))["app"]["regexSearch"])
        # no Everyone, no AI, no sharing
        ev = self.note("For everyone", text="hi")
        self.ok(self.share(ev["id"], "*", "viewer"))
        self.assertEqual(self.ok(self.get("/api/space/everyone", KID))["items"], [])
        self.assertEqual(self.get(f"/api/docs/{ev['id']}", KID).status_code, 404)
        self.assertEqual(self.ok(self.get("/api/search?q=everyone", KID))["total"], 0)
        me = self.ok(self.get("/api/me", KID))
        self.assertEqual((me["isChild"], me["ai"]["on"], me["app"]["everyoneShares"]), (True, False, False))
        shared = self.note("For Ravi", text="hi")
        self.ok(self.share(shared["id"], KID, "editor"))
        d = self.open(shared["id"], KID)
        self.assertEqual((d["role"], d["canShare"]), ("editor", False))
        self.assertEqual(self.ok(self.get(f"/api/nodes/{n['id']}", KID))["canShare"], False)

    def test_shared_folders_only_kids_folders(self):
        for name in ("House", "Kids"):
            os.makedirs(os.path.join(SHARE, name))
            write(os.path.join(SHARE, name, "readme.txt"), "hello " + name)
        house = self.ok(self.post("/api/admin/shared-folders", {"path": "/share/House", "label": "House", "access": {"*": "ro"}}, ASHA), 201)["id"]
        kidsf = self.ok(self.post("/api/admin/shared-folders", {"path": "/share/Kids", "label": "Kids", "access": {"*": "rw"},
                                                                "kids": True}, ASHA), 201)["id"]
        self.scan()
        self.assertEqual([f["label"] for f in self.ok(self.get("/api/shared-folders", KID))["folders"]], ["Kids"])
        self.assertEqual([f["label"] for f in self.ok(self.get("/api/shared-folders", KABIR))["folders"]], ["House", "Kids"])
        self.assertEqual(self.get(f"/api/list?node=root:{house}", KID).status_code, 404)
        self.ok(self.get(f"/api/list?node=root:{kidsf}", KID))
        self.assertEqual(self.ok(self.get("/api/search?q=hello", KID))["total"], 1)
        self.assertEqual(self.ok(self.get("/api/search?q=hello"))["total"], 2)
        self.assertTrue([f for f in self.ok(self.get("/api/admin/shared-folders", ASHA))["folders"] if f["label"] == "Kids"][0]["kids"])
        self.ok(self.patch(f"/api/admin/shared-folders/{kidsf}", {"kids": False}, ASHA))
        self.assertEqual(self.ok(self.get("/api/shared-folders", KID))["folders"], [])

    def test_parents_view_access(self):
        n = self.note("Homework", h=KID, text="sums")
        self.assertEqual(self.get(f"/api/docs/{n['id']}").status_code, 404)
        self.assertEqual(self.patch("/api/admin/users/u_kabir", {"parents": ["u_meera"]}, ASHA).status_code, 409)   # not a child
        self.assertEqual(self.patch("/api/admin/users/u_kid", {"parents": ["u_kid"]}, ASHA).status_code, 422)
        self.assertEqual(self.patch("/api/admin/users/u_kid", {"parents": ["u_nope"]}, ASHA).status_code, 404)
        p = self.ok(self.patch("/api/admin/users/u_kid", {"parents": ["u_kabir"]}, ASHA))
        self.assertEqual((p["isChild"], p["parents"]), (True, ["u_kabir"]))
        me = self.ok(self.get("/api/me"))
        self.assertEqual([k["name"] for k in me["kidsView"]], ["Ravi Rao"])
        d = self.open(n["id"])
        self.assertEqual((d["role"], d["canEdit"], d["text"]), ("viewer", False, "sums"))
        self.assertEqual(self.patch(f"/api/docs/{n['id']}", {"etag": d["etag"], "text": "x"}).status_code, 403)
        listing = self.ok(self.get(f"/api/list?node=root:{me['kidsView'][0]['rootId']}"))
        self.assertEqual(([i["name"] for i in listing["items"]], listing["role"], listing["crumbs"][0]["space"]),
                         (["Homework.txt"], "viewer", "kids"))
        self.assertEqual(listing["folder"]["name"], "Ravi Rao's docs")
        self.assertEqual(self.ok(self.get("/api/search?q=Homework"))["total"], 1)
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)
        self.assertEqual(self.get(f"/api/list?node=root:{me['kidsView'][0]['rootId']}", MEERA).status_code, 404)
        # not a child any more: the parents' access goes
        self.ok(self.patch("/api/admin/users/u_kid", {"isChild": False}, ASHA))
        self.assertEqual(self.get(f"/api/docs/{n['id']}").status_code, 404)
        self.assertEqual(self.ok(self.get("/api/me"))["kidsView"], [])


# =====================================================================
# What changed since you last looked (§17.21)
# =====================================================================
class ChangedTests(TidyBase):
    def setUp(self):
        super().setUp()
        # the clock an hour behind the files' real modified times, so saves made now are "after" a look
        self.clock.t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
        with db.get_conn() as conn:                          # everyone started looking now (on that clock)
            conn.execute("UPDATE users SET seen_from = ?", (config.now_iso(),))

    def later(self):
        self.clock.advance(minutes=10)
        time.sleep(1.05)                                     # file times have second precision in the index

    def test_note_dots_changed_lines_and_is_new(self):
        n = self.note("Plan", text="one\ntwo\nthree")
        self.ok(self.share(n["id"], MEERA, "editor"))
        shared = lambda: {i["id"]: i for i in self.ok(self.get("/api/space/shared", MEERA))["items"]}   # noqa: E731
        self.assertTrue(shared()[n["id"]]["changed"])                  # never opened, made after Meera's start
        self.assertIsNone(self.open(n["id"], MEERA)["changedSince"])     # a first look has nothing to compare
        self.assertFalse(shared()[n["id"]].get("changed"))
        self.later()
        self.save(n["id"], "one\nTWO\nthree\nfour")                   # Kabir changes it
        self.assertTrue(shared()[n["id"]]["changed"])
        self.assertFalse({i["id"]: i for i in self.ok(self.get("/api/space/mine"))["items"]}[n["id"]].get("changed"))
        res = self.ok(self.get("/api/search?q=is:new", MEERA))["results"]
        self.assertEqual([r["id"] for r in res], [n["id"]])
        self.assertTrue(res[0]["changed"])
        self.assertEqual(self.ok(self.get("/api/search?q=is:new"))["total"], 0)
        d = self.open(n["id"], MEERA)
        self.assertEqual(d["changedSince"]["by"], ["Kabir Rao"])
        self.assertEqual(d["changedSince"]["before"], "one\ntwo\nthree")   # the version Meera last saw (History)
        self.assertIsNone(self.open(n["id"], MEERA)["changedSince"])       # seen now
        self.assertFalse(shared()[n["id"]].get("changed"))
        # your own saves are never new to you
        self.later()
        self.save(n["id"], "mine", MEERA)
        self.assertFalse(shared()[n["id"]].get("changed"))
        self.assertTrue({i["id"]: i for i in self.ok(self.get("/api/space/mine"))["items"]}[n["id"]]["changed"])

    def test_checklist_items_and_sheet_cells(self):
        c = self.create("checklist", "Shop")
        self.ok(self.ops(c["id"], [{"op": "add", "text": "Milk"}, {"op": "add", "text": "Bread"}]))
        s = self.sheet("Budget", {"A1": {"v": 1}, "B2": {"v": "x"}})
        for it in (c, s):
            self.ok(self.share(it["id"], MEERA, "editor"))
            self.open(it["id"], MEERA)
        self.later()
        items = self.open(c["id"])["items"]
        self.ok(self.ops(c["id"], [{"op": "tick", "key": items[1]["key"]}, {"op": "add", "text": "Eggs"}]))
        etag = self.open(s["id"])["etag"]
        self.ok(self.patch(f"/api/docs/{s['id']}", {"etag": etag, "cells": [{"tab": "Sheet1", "ref": "A1", "was": {"v": 1}, "now": {"v": 2}},
                                                                             {"tab": "Sheet1", "ref": "C3", "was": None, "now": {"v": 5}}]}))
        d = self.open(c["id"], MEERA)
        texts = {i["key"]: i["text"] for i in d["items"]}
        self.assertEqual(sorted(texts[k] for k in d["changedSince"]["items"]), ["Bread", "Eggs"])
        d = self.open(s["id"], MEERA)
        self.assertEqual(d["changedSince"]["cells"], {"Sheet1": ["A1", "C3"]})

    def test_mark_all_as_seen(self):
        f = self.create("folder", "Trip")
        a = self.note("A", f["id"], text="a")
        sub = self.create("folder", "Sub", f["id"])
        b = self.note("B", sub["id"], text="b")
        self.ok(self.share(f["id"], MEERA, "viewer"))
        changed = lambda ref: {i["name"]: bool(i.get("changed")) for i in self.ok(self.get(f"/api/list?node={ref}", MEERA))["items"]}   # noqa: E731
        self.assertEqual(changed(f["id"]), {"A.txt": True, "Sub": False})
        self.assertTrue(changed(sub["id"])["B.txt"])
        self.assertEqual(self.ok(self.post(f"/api/nodes/{f['id']}/seen", {}, MEERA))["items"], 2)
        self.assertEqual(changed(f["id"]), {"A.txt": False, "Sub": False})
        self.assertFalse(changed(sub["id"])["B.txt"])
        self.assertEqual(self.post(f"/api/nodes/{f['id']}/seen", {}, DEV).status_code, 404)
        self.ok(self.post("/api/nodes/mine/seen", {}))
        self.assertEqual((a["name"], b["name"]), ("A.txt", "B.txt"))
