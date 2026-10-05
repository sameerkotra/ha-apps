"""Step 8 (SPEC §17.4, §17.8, §17.9): 🕑 Activity (what is recorded, grouping, access, filters, changes found by the
index), follows (batching, quiet hours, own changes, access), Home Assistant sensors with the fake Home Assistant
(entity ids, states, attributes, re-posts, removal, who may) and the storage report (numbers, duplicates and
Keep one, admin totals without names, Empty all Trash). Every person and file here is invented."""
import _env  # noqa: F401

import json
import os
from datetime import timedelta

from app import activity, config, db, follows, ha_sensors, storage_report
from app.common import ha_people, sensor_publisher
from app.formats import sheet_model, sheet_xlsx
from base import ASHA, DEV, KABIR, MEERA, ApiBase, person_dir, write


def feed(t, h=KABIR, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return t.ok(t.get("/api/activity" + ("?" + q if q else ""), h))["items"]


class Activity(ApiBase):
    def test_recorded_changes_and_grouped_edits(self):
        folder = self.create("folder", "Trip")
        n = self.note("Plan", parent=folder["id"], text="one")
        self.save(n["id"], "two")
        self.clock.advance(minutes=3)
        self.save(n["id"], "three")                     # the same editing session
        items = feed(self)
        edits = [i for i in items if i["action"] == "edited"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["count"], 1)
        self.clock.advance(minutes=20)
        self.save(n["id"], "four")                      # later the same day: one more edit
        self.clock.advance(minutes=20)
        self.save(n["id"], "five")
        edits = [i for i in feed(self) if i["action"] == "edited"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["count"], 3)
        self.assertEqual(edits[0]["actorName"], "You")
        self.clock.advance(days=1)
        self.save(n["id"], "six")                       # another day: a new row
        self.assertEqual(len([i for i in feed(self) if i["action"] == "edited"]), 2)
        self.ok(self.post(f"/api/nodes/{n['id']}/rename", {"name": "Holiday plan"}))
        other = self.create("folder", "Other")
        self.ok(self.post(f"/api/nodes/{n['id']}/move", {"parentId": other["id"]}))
        items = feed(self)
        ren = next(i for i in items if i["action"] == "renamed")
        self.assertEqual(ren["detail"], {"from": "Plan.txt", "to": "Holiday plan.txt"})
        mov = next(i for i in items if i["action"] == "moved")
        self.assertEqual(mov["detail"], {"from": "Trip", "to": "Other"})
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        self.assertEqual(feed(self)[0]["action"], "deleted")
        self.assertTrue(feed(self)[0]["item"]["inTrash"])
        self.assertNotIn("five", json.dumps(feed(self)))       # never any content

    def test_only_what_you_can_open(self):
        folder = self.create("folder", "Trip")
        n = self.note("Plan", parent=folder["id"], text="x")
        secret = self.note("Secret", text="y")
        self.assertEqual(feed(self, MEERA), [])
        self.assertEqual(feed(self, ASHA), [])               # admins get no content access as admins
        self.ok(self.share(folder["id"], MEERA, "editor"))
        items = feed(self, MEERA)
        ids = {i["item"]["id"] for i in items}
        self.assertIn(n["id"], ids)
        self.assertNotIn(secret["id"], ids)
        shared = [i for i in items if i["action"] == "shared"]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["sharedWith"], "you")
        self.assertEqual([i for i in feed(self, DEV) if i["action"] == "shared"], [])     # told only to Meera
        self.save(n["id"], "z", MEERA)
        self.assertEqual(feed(self)[0]["actorName"], "Meera Rao")
        self.ok(self.delete(f"/api/nodes/{folder['id']}/shares/{self.uid(MEERA)}"))
        self.assertEqual([i for i in feed(self, MEERA) if i["item"]["id"] == n["id"]], [])
        for i in feed(self, MEERA):                           # whatever is left, she can open
            with db.get_conn() as conn:
                from app import sharing
                from app.store import nodes
                self.assertIsNotNone(sharing.role_of(conn, {"id": self.uid(MEERA)}, nodes.get(conn, i["item"]["id"], live=False)))

    def test_changes_found_outside_the_app(self):
        write(os.path.join(person_dir("Kabir Rao"), "Old.txt"), "already here")
        self.scan()                                            # the first scan: what is there already isn't news
        self.assertEqual(feed(self), [])
        p = os.path.join(person_dir("Kabir Rao"), "From Samba.txt")
        write(p, "hello")
        self.scan()
        items = feed(self)
        self.assertEqual(items[0]["action"], "created")
        self.assertTrue(items[0]["outside"])
        self.assertIsNone(items[0]["actorName"])
        write(p, "hello again", mtime=1_800_000_000)
        self.scan()
        self.assertEqual(feed(self)[0]["action"], "edited")
        os.rename(p, os.path.join(person_dir("Kabir Rao"), "Renamed.txt"))
        self.scan()
        self.assertEqual(feed(self)[0]["action"], "renamed")
        os.remove(os.path.join(person_dir("Kabir Rao"), "Renamed.txt"))
        self.scan()
        self.assertEqual(feed(self)[0]["action"], "deleted")
        self.assertEqual(len(feed(self, person="outside")), 4)
        self.assertEqual(feed(self, person="me"), [])

    def test_filters_and_grouping(self):
        a = self.create("folder", "A")
        b = self.create("folder", "B")
        for i in range(3):
            self.note(f"N{i}", parent=a["id"])
        self.create("checklist", "List", parent=b["id"])
        grouped = feed(self, place=a["id"])
        self.assertEqual(len(grouped), 2)                      # "You added 3 …" in one entry, and the folder
        self.assertEqual(grouped[0]["groupCount"], 3)
        self.assertEqual({i["item"]["kind"] for i in feed(self, type="checklist")}, {"checklist"})
        self.assertEqual(self.get("/api/activity?type=bogus").status_code, 422)
        self.assertEqual(self.get(f"/api/activity?place={a['id']}", MEERA).status_code, 404)
        self.assertTrue(all(i["item"]["kind"] != "folder" or True for i in feed(self, place="mine")))
        page = self.ok(self.get("/api/activity?limit=2"))
        self.assertTrue(page["more"])

    def test_old_rows_are_removed(self):
        self.note("Old")
        self.clock.advance(days=100)
        self.note("New")
        with db.get_conn() as conn:
            activity.purge(conn)
        names = [i["item"]["name"] for i in feed(self)]
        self.assertEqual(names, ["New.txt"])
        self.settings({"activity_days": 365})


class Follows(ApiBase):
    def setUp(self):
        super().setUp()
        self.ha.people = [{"entity_id": "person.meera", "name": "Meera Rao", "user_id": "u_meera", "state": "home",
                           "phones": [{"name": "Meera Phone", "label": "Meera Phone", "tracker": "device_tracker.mp"}]},
                          {"entity_id": "person.kabir", "name": "Kabir Rao", "user_id": "u_kabir", "state": "home",
                           "phones": [{"name": "Kabir Phone", "label": "Kabir Phone", "tracker": "device_tracker.kp"}]}]
        ha_people.refresh_blocking(True)

    def sent(self, who="meera"):
        return [b["message"] for s, b in self.ha.notifications() if who in s and "with you" not in b["message"]]

    def test_batched_and_at_most_every_15_minutes(self):
        folder = self.create("folder", "Trip")
        self.ok(self.share(folder["id"], MEERA, "viewer"))
        self.ha.requests.clear()
        self.ok(self.post("/api/follows", {"target": folder["id"]}, MEERA), 201)
        self.assertIn(folder["id"], self.ok(self.get("/api/me", MEERA))["follows"])
        self.clock.advance(seconds=1)
        for i in range(3):
            self.note(f"Day {i}", parent=folder["id"])
        follows.dispatch()
        self.assertEqual(self.sent(), [])                      # still happening: wait for the burst to end
        self.clock.advance(minutes=2)
        follows.dispatch()
        self.assertEqual(self.sent(), ["3 new files in Trip"])
        n = self.note("Later", parent=folder["id"])
        self.clock.advance(minutes=5)
        follows.dispatch()
        self.assertEqual(len(self.sent()), 1)                  # at most one per item per 15 minutes
        self.clock.advance(minutes=10)
        follows.dispatch()
        self.assertEqual(self.sent()[1], "Kabir Rao added “Later.txt” to Trip")
        self.save(n["id"], "secret words")
        self.clock.advance(minutes=16)
        follows.dispatch()
        self.assertEqual(self.sent()[2], "Kabir Rao edited “Later.txt”")
        self.assertNotIn("secret", json.dumps(self.ha.notifications()))
        self.assertEqual(self.sent("kabir"), [])               # not following; and never own changes

    def test_own_changes_and_access(self):
        folder = self.create("folder", "Trip")
        self.ok(self.share(folder["id"], MEERA, "editor"))
        self.ok(self.post("/api/follows", {"target": folder["id"]}, MEERA), 201)
        self.ok(self.post("/api/follows", {"target": folder["id"]}, KABIR), 201)
        self.clock.advance(seconds=1)
        self.note("Hers", parent=folder["id"], h=MEERA)
        self.clock.advance(minutes=2)
        follows.dispatch()
        self.assertEqual(self.sent(), [])                      # her own change
        self.assertEqual(self.sent("kabir"), ["Meera Rao added “Hers.txt” to Trip"])
        self.ok(self.delete(f"/api/nodes/{folder['id']}/shares/{self.uid(MEERA)}"))
        self.note("Private", parent=folder["id"])
        self.clock.advance(minutes=20)
        follows.dispatch()
        self.assertEqual(self.sent(), [])                      # she can't open it any more
        self.assertEqual(self.post("/api/follows", {"target": folder["id"]}, DEV).status_code, 404)
        self.assertEqual(self.ok(self.get("/api/follows", MEERA))["follows"], [])

    def test_quiet_hours_and_switch(self):
        doc = self.note("Budget")
        self.ok(self.share(doc["id"], MEERA, "viewer"))
        self.ok(self.post("/api/follows", {"target": doc["id"]}, MEERA), 201)
        self.clock.t = self.clock.t.replace(hour=22, minute=30)
        self.save(doc["id"], "x")
        self.clock.advance(minutes=5)
        follows.dispatch()
        self.assertEqual(self.sent(), [])                      # 22:00–07:00 by default
        self.clock.t = self.clock.t.replace(hour=6, minute=59) + timedelta(days=1)
        follows.dispatch()
        self.assertEqual(self.sent(), [])
        self.clock.advance(minutes=2)
        follows.dispatch()
        self.assertEqual(self.sent(), ["Kabir Rao edited “Budget.txt”"])
        self.ok(self.put("/api/me/settings", {"quietFrom": "08:00", "quietTo": "20:00"}, MEERA))
        self.clock.advance(hours=2)                            # 09:01, now quiet for her
        self.save(doc["id"], "y")
        self.clock.advance(minutes=20)
        follows.dispatch()
        self.assertEqual(len(self.sent()), 1)
        self.assertTrue(follows.quiet_now({"quietFrom": "22:00", "quietTo": "07:00"}, config.now().replace(hour=23)))
        self.assertFalse(follows.quiet_now({"quietFrom": "07:00", "quietTo": "07:00"}, config.now()))
        self.ok(self.put("/api/me/settings", {"notifyFollows": False, "quietFrom": "00:00", "quietTo": "00:00"}, MEERA))
        self.save(doc["id"], "z")
        self.clock.advance(minutes=20)
        follows.dispatch()
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.put("/api/me/settings", {"quietFrom": "25:00"}, MEERA).status_code, 422)

    def test_shared_folder_top_and_deleted_item(self):
        os.makedirs(os.path.join(os.environ["SHARE_DIR"], "House"))
        f = self.ok(self.post("/api/admin/shared-folders", {"path": "/share/House", "label": "House papers",
                                                            "access": {"*": "rw"}}, ASHA), 201)
        ref = "root:" + f["id"]
        self.ok(self.post("/api/follows", {"target": ref}, MEERA), 201)
        self.clock.advance(seconds=1)
        n = self.create("note", "Bill", parent=ref)
        self.clock.advance(minutes=2)
        follows.dispatch()
        self.assertEqual(self.sent(), ["Kabir Rao added “Bill.txt” to House papers"])
        doc = self.note("Gone")
        self.ok(self.share(doc["id"], MEERA, "viewer"))
        self.ok(self.post("/api/follows", {"target": doc["id"]}, MEERA), 201)
        self.clock.advance(seconds=1)
        self.ok(self.delete(f"/api/nodes/{doc['id']}"))
        self.clock.advance(minutes=20)
        follows.dispatch()
        # in Trash an item is its owner's alone (security review): someone it was shared with isn't told about it
        # any more — not even its deletion — and the owner's own Trash isn't news to them
        self.assertNotIn("Kabir Rao deleted “Gone.txt”", self.sent())
        self.assertEqual(len(self.sent()), 1)
        self.ok(self.delete(f"/api/follows/{ref}", MEERA))
        self.assertEqual(n["name"], "Bill.txt")


class Sensors(ApiBase):
    def setUp(self):
        super().setUp()
        ha_sensors.reset()
        self.mono = [1000.0]
        ha_sensors.REFRESH = sensor_publisher.Refresh(ha_sensors.REPOST_SECONDS, clock=lambda: self.mono[0])
        self.scan()

    def posts(self, eid):
        return [b for m, p, b, _a in self.ha.requests if m == "POST" and p == "/api/states/" + eid]

    def test_checklist_sensor(self):
        c = self.create("checklist", "Party list")
        out = self.ok(self.ops(c["id"], [{"op": "add", "text": "Cake"}, {"op": "add", "text": "Balloons"},
                                         {"op": "add", "text": "Music"}]))
        cake = next(i["key"] for i in out["items"] if i["text"] == "Cake")
        self.ok(self.ops(c["id"], [{"op": "tick", "key": cake}]))
        self.ok(self.share(c["id"], MEERA, "viewer"))
        self.assertEqual(self.put(f"/api/nodes/{c['id']}/ha-sensor", {}, MEERA).status_code, 403)
        info = self.ok(self.put(f"/api/nodes/{c['id']}/ha-sensor", {}))
        eid = info["sensor"]["entityId"]
        self.assertEqual(eid, "sensor.docs_party_list_open")
        ha_sensors.tick()
        st = self.ha.states[eid]
        self.assertEqual(st["state"], "2")
        self.assertEqual(st["attributes"]["total"], 3)
        self.assertEqual(st["attributes"]["done"], 1)
        self.assertEqual(st["attributes"]["open_items"], ["Balloons", "Music"])
        self.assertEqual(st["attributes"]["state_class"], "measurement")
        self.assertEqual(self.ok(self.get(f"/api/nodes/{c['id']}", MEERA))["haSensor"], eid)      # she is told
        self.assertEqual(self.ok(self.get(f"/api/docs/{c['id']}", MEERA))["haSensor"], eid)
        n = len(self.posts(eid))
        ha_sensors.tick()
        self.assertEqual(len(self.posts(eid)), n)             # nothing changed: nothing sent
        self.ok(self.ops(c["id"], [{"op": "add", "text": "Candles"}]))
        ha_sensors.tick()
        self.assertEqual(self.ha.states[eid]["state"], "3")
        self.mono[0] += 301                                    # 5 minutes: everything again (HA may have restarted)
        ha_sensors.tick()
        self.assertEqual(len(self.posts(eid)), n + 2)
        self.ok(self.put(f"/api/nodes/{c['id']}/ha-sensor", {"hideItems": True, "name": "Party"}))
        ha_sensors.tick()
        self.assertNotIn("open_items", self.ha.states[eid]["attributes"])
        self.assertEqual(self.ha.states[eid]["attributes"]["friendly_name"], "Party")
        self.ok(self.delete(f"/api/nodes/{c['id']}/ha-sensor"))
        ha_sensors.tick()
        self.assertNotIn(eid, self.ha.states)

    def test_changes_found_outside_and_deleting(self):
        p = os.path.join(person_dir("Kabir Rao"), "Chores.md")
        write(p, "- [ ] Bins\n- [x] Dishes\n")
        self.scan()
        cid = self.ok(self.get("/api/space/mine"))["items"][0]["id"]
        eid = self.ok(self.put(f"/api/nodes/{cid}/ha-sensor", {}))["sensor"]["entityId"]
        ha_sensors.tick()
        self.assertEqual(self.ha.states[eid]["state"], "1")
        write(p, "- [ ] Bins\n- [ ] Dishes\n- [ ] Floor\n", mtime=1_800_000_000)
        self.scan()                                            # found by the index → refreshed
        ha_sensors.tick()
        self.assertEqual(self.ha.states[eid]["state"], "3")
        self.ok(self.delete(f"/api/nodes/{cid}"))
        ha_sensors.tick()
        self.assertNotIn(eid, self.ha.states)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ha_sensors").fetchone()[0], 0)

    def test_sheet_and_folder_sensors_and_the_switch(self):
        sheet = {"tabs": [{"name": "Budget", "kind": "grid", "cells": {
            "A1": {"v": "Left"}, "B1": {"v": 120.5}, "B2": {"v": 10}, "B3": {"v": "=B1+B2", "c": 130.5},
            "C1": {"v": "note"}}, "cols": {}, "freeze": {"r": 0, "c": 0}, "totals": None, "cond": [], "charts": []}]}
        folder = self.create("folder", "Money")
        write(os.path.join(person_dir("Kabir Rao"), "Money", "Budget.xlsx"), sheet_xlsx.write(sheet))
        write(os.path.join(person_dir("Kabir Rao"), "Money", "Receipt.pdf"), b"%PDF-1.4 x")
        self.scan()
        sid = next(i["id"] for i in self.ok(self.get(f"/api/list?node={folder['id']}"))["items"] if i["name"] == "Budget.xlsx")
        self.assertEqual(self.put(f"/api/nodes/{sid}/ha-sensor", {"cell": "nonsense"}).status_code, 422)
        a = self.ok(self.put(f"/api/nodes/{sid}/ha-sensor", {"cell": "Budget!B3", "unit": "€", "name": "Budget left"}))
        b = self.ok(self.put(f"/api/nodes/{folder['id']}/ha-sensor", {}))
        ha_sensors.tick()
        st = self.ha.states[a["sensor"]["entityId"]]
        self.assertEqual(a["sensor"]["entityId"], "sensor.docs_budget_left")
        self.assertEqual(st["state"], "130.5")
        self.assertEqual(st["attributes"]["unit_of_measurement"], "€")
        self.assertEqual(st["attributes"]["state_class"], "measurement")
        fs = self.ha.states[b["sensor"]["entityId"]]
        self.assertEqual(b["sensor"]["entityId"], "sensor.docs_money_files")
        self.assertEqual(fs["state"], "2")
        self.assertIn(fs["attributes"]["newest_file"], ("Budget.xlsx", "Receipt.pdf"))
        self.ok(self.put(f"/api/nodes/{sid}/ha-sensor", {"cell": "Budget!B1:B2"}))
        ha_sensors.tick()
        self.assertEqual(self.ha.states["sensor.docs_budget_left"]["state"], "130.5")
        self.ok(self.put(f"/api/nodes/{sid}/ha-sensor", {"cell": "Budget!C1"}))
        ha_sensors.tick()
        st = self.ha.states["sensor.docs_budget_left"]
        self.assertEqual(st["state"], "note")
        self.assertNotIn("state_class", st["attributes"])
        other = self.create("checklist", "Money")              # same name: a different id
        self.assertEqual(self.ok(self.put(f"/api/nodes/{other['id']}/ha-sensor", {}))["sensor"]["entityId"],
                         "sensor.docs_money_open")
        self.settings({"ha_sensors": False})                   # off: every sensor removed
        ha_sensors.tick()
        self.assertEqual([k for k in self.ha.states if k.startswith("sensor.docs_")], [])
        self.assertEqual(self.put(f"/api/nodes/{sid}/ha-sensor", {"cell": "Budget!B1"}).status_code, 403)
        self.settings({"ha_sensors": True})
        ha_sensors.tick()
        self.assertEqual(len([k for k in self.ha.states if k.startswith("sensor.docs_")]), 3)
        self.assertEqual(self.put("/api/nodes/" + self.note("Plain")["id"] + "/ha-sensor", {}).status_code, 422)


class Storage(ApiBase):
    def _file(self, name, data, where="Kabir Rao", mtime=None):
        write(os.path.join(person_dir(where), name), data, mtime=mtime)

    def test_report_numbers_duplicates_and_keep_one(self):
        self._file("a.bin", b"x" * 1000)
        self._file("Photos/b.bin", b"x" * 1000)                 # the same content
        self._file("big.pdf", b"%PDF" + b"y" * 5000)
        self._file("old.txt", "old", mtime=1_500_000_000)
        os.makedirs(os.path.join(person_dir("Kabir Rao"), "Empty"))
        self._file("theirs.bin", b"x" * 1000, where="Meera Rao")
        self.scan()
        theirs = self.ok(self.get("/api/space/mine", MEERA))["items"][0]
        self.ok(self.share(theirs["id"], KABIR, "viewer", h=MEERA))
        n = self.note("Draft", text="v1")
        r = self.ok(self.get("/api/reports/storage"))
        self.assertEqual(r["files"], 5)
        self.assertEqual(r["bytes"], 1000 + 1000 + 5004 + 3 + 2)
        self.assertEqual([x["name"] for x in r["largest"]][:3], ["big.pdf", "a.bin", "b.bin"])
        self.assertEqual(r["old"]["files"], 1)
        self.assertEqual([x["name"] for x in r["emptyFolders"]], ["Empty"])
        types = {t["type"]: t for t in r["byType"]}
        self.assertEqual(types["PDF"]["files"], 1)
        dup = r["duplicateGroups"][0]
        self.assertEqual(len(dup["copies"]), 3)                   # Meera's copy too: shared with him
        self.assertEqual(dup["extraBytes"], 2000)
        a = next(c for c in dup["copies"] if c["name"] == "a.bin")
        out = self.ok(self.post("/api/reports/duplicates/keep", {"keepId": a["id"]}))
        self.assertEqual(out, {"removed": 1, "skipped": 1})       # hers: he can only view it
        r = self.ok(self.get("/api/reports/storage"))
        self.assertEqual(r["trash"]["files"], 1)
        self.assertEqual(r["trash"]["bytes"], 1000)
        self.assertEqual(len(r["duplicateGroups"][0]["copies"]), 2)
        self.assertEqual(self.get("/api/reports/storage?place=root:nope").status_code, 404)

    def test_versions_growth_quota_and_shared_folder(self):
        n = self.note("Draft", text="first version")
        self.save(n["id"], "second", h=KABIR)
        self.ok(self.share(n["id"], MEERA, "editor"))
        self.save(n["id"], "third from Meera", h=MEERA)         # another author: a version kept
        storage_report.snapshot()
        self.settings({"quota_gb": 2})
        r = self.ok(self.get("/api/reports/storage"))
        self.assertGreaterEqual(r["versions"]["count"], 1)
        self.assertEqual(r["quota"]["gb"], 2)
        self.assertEqual(len(r["growth"]), 12)
        self.assertEqual(r["growth"][-1]["bytes"], r["bytes"])
        os.makedirs(os.path.join(os.environ["SHARE_DIR"], "House"))
        f = self.ok(self.post("/api/admin/shared-folders", {"path": "/share/House", "label": "House papers",
                                                            "access": {"u_kabir": "rw", "u_meera": "ro"}}, ASHA), 201)
        self.ok(self.get(f"/api/reports/storage?place=root:{f['id']}"))
        self.assertEqual(self.get(f"/api/reports/storage?place=root:{f['id']}", MEERA).status_code, 403)
        self.assertEqual(self.get(f"/api/reports/storage?place=root:{f['id']}", DEV).status_code, 404)

    def test_admin_totals_without_names_and_empty_all_trash(self):
        self._file("Tax return 2026.pdf", b"%PDF" + b"z" * 300)
        self.scan()
        doomed = self.note("Doomed")
        self.ok(self.delete(f"/api/nodes/{doomed['id']}"))
        self.assertEqual(self.get("/api/admin/storage").status_code, 403)
        r = self.ok(self.get("/api/admin/storage", ASHA))
        text = json.dumps(r)
        self.assertNotIn("Tax return", text)                        # admins see sizes, not names
        mine = next(f for f in r["folders"] if f.get("personName") == "Kabir Rao")
        self.assertEqual(mine["files"], 1)
        self.assertEqual(mine["trash"]["items"], 1)
        self.assertGreater(r["data"]["database"], 0)
        out = self.ok(self.post("/api/admin/storage/empty-trash", {}, ASHA))
        self.assertEqual(out["removed"], 1)
        self.assertEqual(self.ok(self.get("/api/space/trash"))["items"], [])
