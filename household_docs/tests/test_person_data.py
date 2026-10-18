"""One person's data (PERSON_DATA_SPEC §7, SPEC §18): Admin → People → ⋯ Data. Export → delete → import gives the
same back; import into another person; Add skips what's there, Replace needs the name; delete keeps or forgets
them, with Move to Trash / Give to someone / Delete for good; what they made elsewhere, others' data and Kids'
space links; a newer or tampered file refused; admins only. Every person here is invented."""
import _env  # noqa: F401

import io
import json
import os
import unittest
import zipfile

from app import db, people_data
from app.common import person_data as pd
from base import ASHA, DEV, DOCS, KABIR, MEERA, SHARE, ApiBase, person_dir, read, write

HOUSE = os.path.join(SHARE, "Documents", "House")
P = "/api/admin/people"


def walk_files(top):
    out = {}
    for dirpath, dirnames, filenames in os.walk(top):
        for f in filenames:
            full = os.path.join(dirpath, f)
            out[os.path.relpath(full, top)] = read(full, binary=True)
    return out


class PersonData(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def setUp(self):
        super().setUp()
        write(os.path.join(HOUSE, "manual.txt"), "the house manual")
        self.ok(self.post("/api/admin/shared-folders", {"path": "/share/Documents/House", "label": "House papers",
                                                         "access": {"u_kabir": "rw", "u_meera": "rw"}}, ASHA), 201)

    # -- helpers -------------------------------------------------------------------------------------------------
    def fill_kabir(self):
        """Kabir's folder: notes, a folder, a checklist, a plain file put there over the network, versions, a tag,
        a favourite, a saved search; a note he made in the shared folder; Meera shares her note with him; Dev is a
        child whose docs Kabir may view."""
        self.settings({"versions_kept": 5})
        a = self.note("Plans", text="one")
        self.clock.advance(minutes=6)
        self.save(a["id"], "two")
        self.clock.advance(minutes=6)
        self.save(a["id"], "three")
        f = self.create("folder", "Bills")
        c = self.create("checklist", "Jobs", f["id"])
        write(os.path.join(person_dir("Kabir Rao"), "Bills", "power.bin"), b"\x00\x01binary")
        self.scan()
        self.ok(self.put(f"/api/nodes/{a['id']}/tags", {"tags": ["home"], "color": "blue"}))
        self.ok(self.post(f"/api/nodes/{a['id']}/favourite", {"value": True}))
        self.ok(self.post("/api/searches", {"name": "Bills", "q": "power", "filters": {}}), 201)
        write(os.path.join(person_dir("Kabir Rao"), ".templates", "Weekly.txt"), "template text")
        house = self.ok(self.get("/api/shared-folders"))["folders"][0]["rootId"]
        made = self.note("Kabir's house note", parent="root:" + house, text="made by kabir")
        mine = self.note("Meera's own", h=MEERA, text="meera words")
        self.ok(self.share(mine["id"], KABIR, "editor", h=MEERA))
        self.ok(self.patch("/api/admin/users/u_dev", {"isChild": True, "parents": ["u_kabir"]}, ASHA))
        return {"plans": a, "bills": f, "jobs": c, "made": made, "meeras": mine, "house": house}

    def summary(self, uid="u_kabir", h=ASHA):
        return self.ok(self.get(f"{P}/{uid}/data", h))

    def counts(self, uid="u_kabir"):
        return {c["key"]: c["count"] for c in self.summary(uid)["counts"]}

    def export(self, uid="u_kabir", options=None):
        r = self.post(f"{P}/{uid}/data/export", {"options": options or {}}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertIn("household-docs-kabir-rao-", r.headers["content-disposition"])
        return r.content

    def upload(self, data, h=ASHA, code=200):
        r = self.c.post(f"{P}/data/import", files={"file": ("export.zip", data, "application/zip")}, headers=h)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def do_import(self, data, uid="u_kabir", mode="add", confirm=None, options=None, code=200):
        up = self.upload(data)
        r = self.post(f"{P}/{uid}/data/import/{up['token']}", {"mode": mode, "confirm": confirm, "options": options or {}},
                      ASHA)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def delete_data(self, uid="u_kabir", confirm="Kabir Rao", forget=False, folder="delete", code=200):
        r = self.post(f"{P}/{uid}/data/delete", {"confirm": confirm, "forget": forget, "choices": {"folder": folder}},
                      ASHA)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def mine(self, h=KABIR):
        return self.ok(self.get("/api/list", h))["items"]

    # -- tests ---------------------------------------------------------------------------------------------------
    def test_export_delete_import_gives_the_same_back(self):
        x = self.fill_kabir()
        before = self.counts()
        self.assertEqual((before["files"], before["folders"], before["versions"], before["templates"],
                          before["searches"], before["kids"], before["elsewhere"]), (3, 1, 2, 1, 1, 1, 1))
        on_disk = walk_files(person_dir("Kabir Rao"))
        data = self.export()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            man = json.loads(z.read("manifest.json"))
            self.assertEqual(man["schema"], people_data.SCHEMA)
            self.assertEqual(man["app"], "household_docs")
            self.assertIn("files/Plans.txt", names)
            self.assertIn("files/Bills/power.bin", names)
            self.assertIn("files/.templates/Weekly.txt", names)
            self.assertIn("data/elsewhere.json", names)
            self.assertEqual(len([n for n in names if n.startswith("files/.versions/")]), 2)
            self.assertNotIn("meera words", z.read("data/shares.json").decode())
            self.assertEqual(json.loads(z.read("data/elsewhere.json"))[0]["path"], "Kabir's house note.txt")
            for n in names:                                    # no keys, no other person's content
                self.assertNotIn(b"test-token", z.read(n))
        out = self.delete_data()
        self.assertIn("Deleted 3 files", out["message"])
        after = self.counts()
        self.assertEqual({k: after[k] for k in ("files", "folders", "versions", "templates", "searches", "kids")},
                         {"files": 0, "folders": 0, "versions": 0, "templates": 0, "searches": 0, "kids": 0})
        self.assertEqual(os.listdir(person_dir("Kabir Rao")), [])
        res = self.do_import(data)
        self.assertIn("Imported 3 files", res["message"])
        back = self.counts()
        for k in ("files", "folders", "versions", "templates", "searches"):
            self.assertEqual(back[k], before[k], k)
        self.assertEqual(walk_files(person_dir("Kabir Rao")), on_disk)          # the same files, at the same paths
        items = {i["name"]: i for i in self.mine()}
        plans = items["Plans.txt"]
        self.assertNotEqual(plans["id"], x["plans"]["id"])                     # new ids
        self.assertEqual(plans["tags"], ["home"])
        self.assertEqual(plans["color"], "blue")
        self.assertTrue(plans["favourite"])
        self.assertEqual(self.open(plans["id"])["text"], "three")
        vs = self.ok(self.get(f"/api/docs/{plans['id']}/versions"))["versions"]
        self.assertEqual(len(vs), 2)
        self.assertEqual(self.ok(self.get(f"/api/docs/{plans['id']}/versions/{vs[-1]['n']}"))["text"], "one")
        self.assertEqual([s["name"] for s in self.ok(self.get("/api/searches"))["searches"]], ["Bills"])
        found = self.ok(self.get("/api/search?q=three"))["results"]                # indexed again
        self.assertEqual([r["name"] for r in found], ["Plans.txt"])

    def test_import_into_another_person(self):
        self.fill_kabir()
        data = self.export()
        res = self.do_import(data, uid="u_meera")
        self.assertIn("into “Imported 2026-10-05”", res["message"])
        top = {i["name"]: i for i in self.mine(MEERA)}
        self.assertIn("Meera's own.txt", top)                                 # what she had is untouched
        imp = top["Imported 2026-10-05"]
        inside = {i["name"]: i for i in self.ok(self.get(f"/api/list?node={imp['id']}", MEERA))["items"]}
        self.assertEqual(set(inside), {"Plans.txt", "Bills"})
        self.assertEqual(inside["Plans.txt"]["ownerId"], "u_meera")
        self.assertEqual(self.open(inside["Plans.txt"]["id"], MEERA)["text"], "three")
        self.assertEqual(self.get(f"/api/docs/{inside['Plans.txt']['id']}", KABIR).status_code, 404)  # not his
        row = self.row(inside["Plans.txt"]["id"])
        self.assertEqual(row["created_by"], "u_meera")                        # Kabir's own edits → hers now
        self.assertEqual(walk_files(os.path.join(person_dir("Meera Rao"), "Imported 2026-10-05")),
                         {k: v for k, v in walk_files(person_dir("Kabir Rao")).items() if not k.startswith(".")})
        self.assertEqual(self.counts("u_kabir")["files"], 3)                    # the source person keeps theirs

    def test_add_skips_duplicates_and_replace_needs_the_name(self):
        x = self.fill_kabir()
        data = self.export()
        res = self.do_import(data)                                              # the same again: all there already
        self.assertIn("Imported 0 files", res["message"])
        self.assertIn("6 already there were skipped", res["message"])   # 3 files, the folder, the template, the search
        self.assertNotIn("Imported", [i["name"] for i in self.mine()][0])
        self.assertEqual(self.counts()["files"], 3)
        self.save(x["plans"]["id"], "changed since")                           # a changed one comes in beside
        res = self.do_import(data)
        self.assertIn("Imported 1 file, 2 old versions into “Imported 2026-10-05”", res["message"])
        # Replace: the name, typed
        up = self.upload(data)
        r = self.post(f"{P}/u_kabir/data/import/{up['token']}", {"mode": "replace", "confirm": "Meera Rao"}, ASHA)
        self.assertEqual(r.status_code, 409)
        res = self.ok(self.post(f"{P}/u_kabir/data/import/{up['token']}", {"mode": "replace", "confirm": " kabir  rao "},
                                ASHA))
        self.assertIn("Imported 3 files", res["message"])
        self.assertEqual(sorted(i["name"] for i in self.mine()), ["Bills", "Plans.txt"])
        trashed = self.ok(self.get("/api/space/trash"))["items"]
        self.assertEqual(sorted(t["name"] for t in trashed), ["Bills", "Imported 2026-10-05", "Plans.txt"])
        # the token is gone after an import
        r = self.post(f"{P}/u_kabir/data/import/{up['token']}", {"mode": "add"}, ASHA)
        self.assertEqual(r.status_code, 404)

    def test_trash_comes_back_in_trash(self):
        n = self.note("Old", text="old words")
        self.note("Kept", text="kept")
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        data = self.export(options={"trash": True})
        self.delete_data()
        res = self.do_import(data)
        self.assertIn("1 item in Trash", res["message"])
        t = self.ok(self.get("/api/space/trash"))["items"]
        self.assertEqual([x["name"] for x in t], ["Old.txt"])
        self.ok(self.post(f"/api/trash/{t[0]['id']}/restore", {}))
        names = sorted(i["name"] for i in self.mine())
        self.assertEqual(names, ["Kept.txt", "Old.txt"])
        old = next(i for i in self.mine() if i["name"] == "Old.txt")
        self.assertEqual(self.open(old["id"])["text"], "old words")

    def test_delete_keep_and_forget(self):
        x = self.fill_kabir()
        meera_before = walk_files(person_dir("Meera Rao"))
        out = self.delete_data()
        self.assertIn("stays, empty", out["message"])
        with db.get_conn() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE id = 'u_kabir'").fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM kid_parents").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shares WHERE user_id = 'u_kabir'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM root_access WHERE user_id = 'u_kabir'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM saved_searches WHERE user_id = 'u_kabir'").fetchone()[0], 0)
        self.assertFalse(os.path.exists(os.path.join(DOCS, ".versions", x["plans"]["id"])))
        self.assertEqual(self.ok(self.get("/api/me"))["folder"], "Kabir Rao")
        # shared things keep working for everyone else
        self.assertEqual(self.open(x["made"]["id"], MEERA)["text"], "made by kabir")
        self.assertEqual(self.open(x["meeras"]["id"], MEERA)["text"], "meera words")
        self.assertEqual(self.get(f"/api/docs/{x['meeras']['id']}", KABIR).status_code, 404)   # his share went
        self.assertEqual(walk_files(person_dir("Meera Rao")), meera_before)      # others' data untouched
        # forget: the row, the root and the folder go; what he made elsewhere shows "Former member"
        self.delete_data(forget=True)
        with db.get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE id = 'u_kabir'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM roots WHERE user_id = 'u_kabir'").fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nodes WHERE created_by = 'u_kabir' "
                                          "OR updated_by = 'u_kabir'").fetchone()[0], 0)
        self.assertFalse(os.path.exists(person_dir("Kabir Rao")))
        house = self.ok(self.get(f"/api/list?node=root:{x['house']}", MEERA))["items"]
        made = next(i for i in house if i["id"] == x["made"]["id"])
        self.assertEqual(made["updatedByName"], "Former member")
        self.assertEqual(self.open(x["made"]["id"], MEERA)["text"], "made by kabir")
        self.assertEqual(walk_files(person_dir("Meera Rao")), meera_before)

    def test_move_to_trash_is_the_old_delete(self):
        self.note("One")
        self.create("folder", "Two")
        self.assertEqual(self.delete_data(confirm="wrong", folder="trash", code=409)["detail"], "Type Kabir Rao to confirm.")
        self.assertEqual(self.delete_data(folder="trash", forget=True, code=422)["detail"],
                         "To forget them, choose Give to someone or Delete for good.")
        out = self.delete_data(folder="trash")
        self.assertEqual(out["moved"], 2)
        self.assertEqual(os.listdir(person_dir("Kabir Rao")), [])
        self.assertEqual(len(self.ok(self.get("/api/space/trash"))["items"]), 2)
        summary = self.summary()
        self.assertEqual(summary["deleteChoices"][0]["default"], "trash")
        values = [o["value"] for o in summary["deleteChoices"][0]["options"]]
        self.assertEqual(values[0], "trash")
        self.assertEqual(values[-1], "delete")
        self.assertIn("give:u_meera", values)
        self.assertNotIn("give:u_kabir", values)

    def test_give_to_someone(self):
        x = self.fill_kabir()
        out = self.delete_data(folder="give:u_meera")
        self.assertIn("Gave 2 items to Meera Rao", out["message"])
        top = {i["name"]: i for i in self.mine(MEERA)}
        given = top["From Kabir Rao"]
        inside = {i["name"]: i for i in self.ok(self.get(f"/api/list?node={given['id']}", MEERA))["items"]}
        self.assertEqual(inside["Plans.txt"]["id"], x["plans"]["id"])          # the same item, with its history
        self.assertEqual(inside["Plans.txt"]["ownerId"], "u_meera")
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{x['plans']['id']}/versions", MEERA))["versions"]), 2)
        self.assertEqual(read(os.path.join(person_dir("Meera Rao"), "From Kabir Rao", "Bills", "power.bin"), True),
                         b"\x00\x01binary")
        self.assertEqual(os.listdir(person_dir("Kabir Rao")), [])               # templates went too
        self.assertEqual(self.mine(), [])

    def test_refused_files(self):
        self.note("One", text="1")
        data = self.export()
        temp = lambda: {n for n in os.listdir(_env.os.environ["DATA_DIR"]) if n.startswith(people_data.TEMP_PREFIX)}
        before = temp()
        # a newer schema
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            man = json.loads(z.read("manifest.json"))
            members = {n: z.read(n) for n in z.namelist()}
        newer = dict(man, schema=people_data.SCHEMA + 1)
        self.assertIn("newer version", self.upload(self.rezip(members, newer), code=422)["detail"])
        # tampered: one member changed, the manifest not
        changed = dict(members)
        changed["files/One.txt"] = b"2"
        self.assertIn("was changed", self.upload(self.rezip(changed, man), code=422)["detail"])
        # another app's file
        self.assertIn("not this app", self.upload(self.rezip(members, dict(man, app="calorie_tracker")), code=422)["detail"])
        # not a zip
        self.assertIn("not a .zip", self.upload(b"hello", code=422)["detail"])
        # a row the app's own rules refuse: the table and row number
        bad = dict(members)
        rows = json.loads(members["data/node_tags.json"] or b"[]") + [{"node_id": "x", "tag": "a,b"}]
        bad["data/node_tags.json"] = json.dumps(rows).encode()
        man2 = dict(man, sha256=dict(man["sha256"], **{"data/node_tags.json": pd._sha(bad["data/node_tags.json"])}))
        self.assertIn("node_tags, row 1", self.upload(self.rezip(bad, man2), code=422)["detail"])
        # nothing came in
        self.assertEqual([i["name"] for i in self.mine()], ["One.txt"])
        self.assertEqual(temp() - before, set())                              # refused uploads aren't kept

    def rezip(self, members, manifest):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("manifest.json", json.dumps(manifest))
            for n, b in members.items():
                if n != "manifest.json":
                    z.writestr(n, b)
        return buf.getvalue()

    def test_admins_only(self):
        self.note("One")
        data = self.export()
        token = self.upload(data)["token"]
        for h in (KABIR, MEERA):
            self.assertEqual(self.get(f"{P}/u_kabir/data", h).status_code, 403)
            self.assertEqual(self.post(f"{P}/u_kabir/data/export", {}, h).status_code, 403)
            self.assertEqual(self.get(f"{P}/data/jobs/x", h).status_code, 403)
            self.assertEqual(self.get(f"{P}/data/jobs/x/file", h).status_code, 403)
            r = self.c.post(f"{P}/data/import", files={"file": ("e.zip", data, "application/zip")}, headers=h)
            self.assertEqual(r.status_code, 403)
            self.assertEqual(self.post(f"{P}/u_kabir/data/import/{token}", {"mode": "add"}, h).status_code, 403)
            self.assertEqual(self.post(f"{P}/u_kabir/data/delete", {"confirm": "Kabir Rao"}, h).status_code, 403)
        self.assertEqual([i["name"] for i in self.mine()], ["One.txt"])

    def test_big_export_runs_as_a_job_and_busy_is_423(self):
        self.note("One", text="1")
        old = people_data.SMALL_FILES
        people_data.SMALL_FILES = 0
        try:
            r = self.ok(self.post(f"{P}/u_kabir/data/export", {}, ASHA))
        finally:
            people_data.SMALL_FILES = old
        import time
        for _ in range(100):
            st = self.ok(self.get(f"{P}/data/jobs/{r['job']}", ASHA))
            if st["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(st["state"], "done", st)
        self.assertEqual(self.get(f"{P}/data/jobs/{r['job']}", MEERA).status_code, 403)
        f = self.get(f"{P}/data/jobs/{r['job']}/file", ASHA)
        self.assertEqual(f.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(f.content)) as z:
            self.assertIn("files/One.txt", z.namelist())
        with pd.one_at_a_time(people_data._LOCK):
            self.assertEqual(self.delete_data(code=423)["detail"], "Another import or delete is running — try again in a minute.")
        self.assertEqual([i["name"] for i in self.mine()], ["One.txt"])

    def test_sensors_on_their_items_go(self):
        from app import ha_sensors
        ha_sensors.reset()
        c = self.create("checklist", "Party list")
        self.ok(self.ops(c["id"], [{"op": "add", "text": "Cake"}]))
        eid = self.ok(self.put(f"/api/nodes/{c['id']}/ha-sensor", {}))["sensor"]["entityId"]
        ha_sensors.tick()
        self.assertIn(eid, self.ha.states)
        self.assertEqual(self.counts()["files"], 1)
        self.delete_data()
        ha_sensors.tick()
        self.assertNotIn(eid, self.ha.states)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ha_sensors").fetchone()[0], 0)

    def test_page_loads_the_dialogs(self):
        index = read(os.path.join(os.path.dirname(people_data.__file__), "static", "index.html"))
        self.assertIn('common/person-data.js?v=', index)
        self.assertIn('common/person-data.css?v=', index)
        self.assertLess(index.index("common/ui.js"), index.index("common/person-data.js"))
        admin = read(os.path.join(os.path.dirname(people_data.__file__), "static", "admin.js"))
        self.assertIn("PeopleData.menu(", admin)
        self.assertIn("PeopleData.importButton(", admin)


if __name__ == "__main__":
    unittest.main()
