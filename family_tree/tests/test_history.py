"""Change history, undo/redo and conflicts (§7), and the trash."""
from base import ADMIN, ALICE, BOB, ApiTestCase, sql

import unittest
from datetime import timedelta

from app import config, housekeeping


class History(ApiTestCase):
    def history(self, query="", user=None):
        return self.ok(self.get(f"/api/history{query}", user=user))

    def test_every_write_returns_a_batch_in_history(self):
        batches = []

        def take(resp, status=200):
            body = self.ok(resp, status)
            self.assertIn("batchId", body)
            batches.append(body["batchId"])
            return body

        a = take(self.post("/api/people", {"given_names": "Ann", "gender": "female"}), 201)["person"]["id"]
        take(self.patch(f"/api/people/{a}", {"surname": "Smith"}))
        body = take(self.post(f"/api/people/{a}/add-parent", {"person": {"given_names": "Pa", "gender": "male"}}), 201)
        pa = body["newPersonId"]
        body = take(self.post(f"/api/people/{a}/add-partner", {"person": {"given_names": "Ben"}}), 201)
        fid = body["familyId"]
        kid = take(self.post(f"/api/people/{a}/add-child", {"person": {"given_names": "Kid"}}), 201)["newPersonId"]
        take(self.patch(f"/api/families/{fid}", {"kind": "partners"}))
        kid2 = take(self.post(f"/api/families/{fid}/children", {"person": {"given_names": "Kid2"}}), 201)["newPersonId"]
        take(self.patch(f"/api/families/{fid}/children/{kid2}", {"position": 0}))
        take(self.delete(f"/api/families/{fid}/children/{kid2}"))
        ev = take(self.post("/api/events", {"personId": a, "type": "occupation", "title": "Nurse"}), 201)["event"]["id"]
        take(self.patch(f"/api/events/{ev}", {"place": "Leeds"}))
        take(self.delete(f"/api/events/{ev}"))
        other = take(self.post("/api/people", {"given_names": "Zed"}), 201)["person"]["id"]
        take(self.put(f"/api/families/{fid}/partners/2", {"personId": other}))
        take(self.post("/api/families", {"partner1Id": pa}), 201)
        take(self.post("/api/families/quick", {"partners": [{"person": {"given_names": "Q"}}]}), 201)
        take(self.delete(f"/api/people/{kid}"))
        take(self.post(f"/api/people/{kid}/restore", user=ADMIN))
        take(self.delete(f"/api/people/{kid}"))
        take(self.post(f"/api/trash/people/{kid}/restore", user=ADMIN))
        take(self.delete(f"/api/families/{fid}"))
        take(self.post(f"/api/families/{fid}/restore", user=ADMIN))
        h = self.history("?page_size=200")
        self.assertEqual(h["total"], len(batches))
        self.assertEqual({i["id"] for i in h["items"]}, set(batches))
        self.assertEqual(len(set(batches)), len(batches))
        # newest first
        self.assertEqual(h["items"][0]["id"], batches[-1])
        self.assertEqual(h["items"][-1]["id"], batches[0])

    def test_labels_and_people(self):
        a = self.person("Ann", "Smith")
        h = self.history()
        self.assertEqual(h["items"][0]["label"], "Added Ann Smith")
        self.assertEqual(h["items"][0]["people"], [{"id": a, "name": "Ann Smith"}])
        self.assertEqual(h["items"][0]["userId"], ALICE["id"])
        self.assertEqual(h["items"][0]["userName"], ALICE["display"])
        self.assertGreaterEqual(h["items"][0]["changes"], 1)

    def test_filters_and_pagination(self):
        a = self.person("Ann", user=ALICE)
        b = self.person("Bea", user=BOB)
        self.ok(self.patch(f"/api/people/{a}", {"surname": "X"}, user=BOB))
        self.ok(self.post(f"/api/people/{a}/add-child", {"existingId": b}, user=ALICE), 201)
        allh = self.history()
        self.assertEqual(allh["total"], 4)
        by_a = self.history(f"?personId={a}")
        self.assertEqual(by_a["total"], 3)
        self.assertEqual([i["label"] for i in by_a["items"]], ["Added Bea as a child of Ann X", "Edited Ann", "Added Ann"])
        by_b = self.history(f"?personId={b}")
        self.assertEqual(by_b["total"], 2)
        by_bob = self.history(f"?userId={BOB['id']}")
        self.assertEqual(by_bob["total"], 2)
        self.assertTrue(all(i["userId"] == BOB["id"] for i in by_bob["items"]))
        both = self.history(f"?userId={BOB['id']}&personId={a}")
        self.assertEqual(both["total"], 1)
        self.assertEqual({u["id"] for u in allh["users"]}, {ALICE["id"], BOB["id"]})
        p1 = self.history("?page_size=3")
        p2 = self.history("?page_size=3&page=2")
        self.assertEqual((p1["total"], len(p1["items"]), p1["page"], p1["pageSize"]), (4, 3, 1, 3))
        self.assertEqual(len(p2["items"]), 1)
        self.assertEqual([i["id"] for i in p1["items"] + p2["items"]], [i["id"] for i in allh["items"]])
        self.assertEqual(self.history("?page=5")["items"], [])

    def test_batch_detail_field_diffs(self):
        a = self.person("Ann", "Smith", born=1950)
        bid = self.ok(self.patch(f"/api/people/{a}", {"surname": "Jones", "birth": {"date": {"y": 1951}, "place": "York"}}))["batchId"]
        d = self.ok(self.get(f"/api/history/{bid}"))
        self.assertEqual(d["id"], bid)
        self.assertEqual(d["label"], "Edited Ann Smith")
        by_entity = {c["entity"]: c for c in d["changes"]}
        self.assertEqual(by_entity["person"]["op"], "update")
        self.assertEqual(by_entity["person"]["fields"], [{"field": "surname", "before": "Smith", "after": "Jones"}])
        ev_fields = {f["field"]: (f["before"], f["after"]) for f in by_entity["event"]["fields"]}
        self.assertEqual(ev_fields, {"date_text": ("1950", "1951"), "place": (None, "York")})
        # hidden bookkeeping columns never show
        for c in d["changes"]:
            for f in c["fields"]:
                self.assertNotIn(f["field"], ("updated_at", "sort_key", "date_y", "id"))
        self.assertEqual(self.get("/api/history/nope").status_code, 404)

    def test_undo_and_redo_edit(self):
        a = self.person("Ann", "Smith")
        bid = self.ok(self.patch(f"/api/people/{a}", {"surname": "Jones"}))["batchId"]
        u = self.ok(self.post(f"/api/history/{bid}/undo"))
        self.assertEqual(self.detail(a)["surname"], "Smith")
        h = self.history()
        self.assertEqual(h["items"][0]["id"], u["batchId"])
        self.assertEqual(h["items"][0]["label"], "Undo: Edited Ann Smith")
        self.assertEqual(h["items"][0]["undoOf"], bid)
        orig = next(i for i in h["items"] if i["id"] == bid)
        self.assertEqual(orig["undoneBy"], u["batchId"])
        # undo the undo = redo
        r = self.ok(self.post(f"/api/history/{u['batchId']}/undo"))
        self.assertEqual(self.detail(a)["surname"], "Jones")
        # and again (undo the redo)
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual(self.detail(a)["surname"], "Smith")

    def test_undo_create_and_redo(self):
        bid = self.ok(self.post("/api/people", {"given_names": "Ann", "birth": {"date": {"y": 1950}}}), 201)["batchId"]
        a = self.ok(self.get("/api/people"))["items"][0]["id"]
        u = self.ok(self.post(f"/api/history/{bid}/undo"))
        self.assertEqual(self.get(f"/api/people/{a}").status_code, 404)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM events")[0]["n"], 0)
        self.ok(self.post(f"/api/history/{u['batchId']}/undo"))
        d = self.detail(a)
        self.assertEqual(d["birth"]["dateDisplay"], "1950")

    def test_undo_delete_restores(self):
        a = self.person("Ann")
        bid = self.ok(self.delete(f"/api/people/{a}"))["batchId"]
        self.assertEqual(self.get(f"/api/people/{a}").status_code, 404)
        self.ok(self.post(f"/api/history/{bid}/undo"))
        self.assertEqual(self.detail(a)["name"], "Ann")

    def test_undo_conflict_when_row_edited_later(self):
        a = self.person("Ann", "Smith")
        b1 = self.ok(self.patch(f"/api/people/{a}", {"surname": "Jones"}))["batchId"]
        self.ok(self.patch(f"/api/people/{a}", {"nickname": "Annie"}))
        r = self.post(f"/api/history/{b1}/undo")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("undo the later change first", r.json()["detail"])
        self.assertEqual(self.detail(a)["surname"], "Jones")
        # the failed undo left no trace
        self.assertEqual(self.history()["total"], 3)

    def test_undo_conflict_when_later_batch_depends_on_created_row(self):
        b1 = self.ok(self.post("/api/people", {"given_names": "Ann"}), 201)
        a, bid = b1["person"]["id"], b1["batchId"]
        self.ok(self.post("/api/events", {"personId": a, "type": "occupation", "title": "Nurse"}), 201)
        r = self.post(f"/api/history/{bid}/undo")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.detail(a)["events"][0]["title"], "Nurse")

    def test_undo_conflict_child_added_to_created_family(self):
        a = self.person("Ann")
        body = self.ok(self.post(f"/api/people/{a}/add-partner", {"person": {"given_names": "Ben"}}), 201)
        self.ok(self.post(f"/api/families/{body['familyId']}/children", {"person": {"given_names": "Kid"}}), 201)
        r = self.post(f"/api/history/{body['batchId']}/undo")
        self.assertEqual(r.status_code, 409, r.text)

    def test_undo_twice_conflicts(self):
        a = self.person("Ann")
        bid = self.ok(self.patch(f"/api/people/{a}", {"surname": "X"}))["batchId"]
        self.ok(self.post(f"/api/history/{bid}/undo"))
        r = self.post(f"/api/history/{bid}/undo")
        self.assertEqual(r.status_code, 409, r.text)

    def test_undo_unknown_404(self):
        self.assertEqual(self.post("/api/history/nope/undo").status_code, 404)

    def test_undo_add_child_batch(self):
        a = self.person("Ann")
        body = self.ok(self.post(f"/api/people/{a}/add-child", {"person": {"given_names": "Kid", "birth": {"date": {"y": 2000}}}}), 201)
        self.ok(self.post(f"/api/history/{body['batchId']}/undo"))
        self.assertEqual(self.detail(a)["families"], [])
        self.assertEqual(self.get(f"/api/people/{body['newPersonId']}").status_code, 404)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM family_children")[0]["n"], 0)

    def test_undo_by_another_user_is_recorded_as_them(self):
        a = self.person("Ann", user=ALICE)
        bid = self.ok(self.patch(f"/api/people/{a}", {"surname": "X"}, user=ALICE))["batchId"]
        u = self.ok(self.post(f"/api/history/{bid}/undo", user=BOB))
        item = self.history()["items"][0]
        self.assertEqual((item["id"], item["userId"]), (u["batchId"], BOB["id"]))

    def test_undo_refuses_unknown_tables_and_columns_in_snapshots(self):
        # history rows are data (a restored backup can carry any): they never become SQL names
        a = self.person("Ann")
        bid = self.ok(self.patch(f"/api/people/{a}", {"surname": "Smith"}))["batchId"]
        ch = sql("SELECT id, before FROM changes WHERE batch_id = ?", (bid,))[0]
        evil = ch["before"][:-1] + ', "given_names = 1, deleted_at": "x"}'
        sql("UPDATE changes SET before = ? WHERE id = ?", (evil, ch["id"]))
        self.assertEqual(self.post(f"/api/history/{bid}/undo").status_code, 409)
        sql("UPDATE changes SET before = NULL, entity = 'users' WHERE id = ?", (ch["id"],))
        self.assertEqual(self.post(f"/api/history/{bid}/undo").status_code, 409)
        self.assertEqual(self.detail(a)["surname"], "Smith")


class UndoAgainstLaterChanges(ApiTestCase):
    """Undo must answer 409 (never 500) when putting a row back is impossible
    because of something done later."""

    def setUp(self):
        super().setUp()
        from common_tests.ingress import ingress_client
        from app.main import app
        self.client = ingress_client(app, raise_server_exceptions=False)

    def assert_conflict(self, bid):
        r = self.post(f"/api/history/{bid}/undo")
        self.assertEqual(r.status_code, 409, r.text)

    def test_undo_birth_removal_after_new_birth_entered(self):
        # "remove birth date" → "enter a birth date again" → undo the removal
        a = self.person("Ann", born=1950)
        bid = self.ok(self.patch(f"/api/people/{a}", {"birth": None}))["batchId"]
        self.ok(self.patch(f"/api/people/{a}", {"birth": {"date": {"y": 1951}}}))
        self.assert_conflict(bid)
        self.assertEqual(self.detail(a)["birth"]["dateDisplay"], "1951")

    def test_undo_marriage_removal_after_new_marriage_entered(self):
        a = self.person("Ann")
        fid = self.ok(self.post(f"/api/people/{a}/add-partner", {"person": {"given_names": "Ben"},
                                                                  "marriage": {"date": {"y": 1990}}}), 201)["familyId"]
        bid = self.ok(self.patch(f"/api/families/{fid}", {"marriage": None}))["batchId"]
        self.ok(self.patch(f"/api/families/{fid}", {"marriage": {"date": {"y": 1991}}}))
        self.assert_conflict(bid)

    def test_undo_event_delete_after_person_purged(self):
        a = self.person("Ann")
        ev = self.ok(self.post("/api/events", {"personId": a, "type": "residence", "place": "York"}), 201)["event"]["id"]
        bid = self.ok(self.delete(f"/api/events/{ev}"))["batchId"]
        self.ok(self.delete(f"/api/people/{a}"))
        self.ok(self.delete("/api/trash", user=ADMIN))
        self.assert_conflict(bid)

    def test_undo_child_unlink_after_child_purged(self):
        a = self.person("Ann")
        body = self.ok(self.post(f"/api/people/{a}/add-child", {"person": {"given_names": "Kid"}}), 201)
        bid = self.ok(self.delete(f"/api/families/{body['familyId']}/children/{body['newPersonId']}"))["batchId"]
        self.ok(self.delete(f"/api/people/{body['newPersonId']}"))
        self.ok(self.delete("/api/trash", user=ADMIN))
        self.assert_conflict(bid)


class Trash(ApiTestCase):
    """Deleting is done as Alice (anyone may delete); the trash itself
    (list, restore, empty) is admin-only, so those calls are made as ADMIN."""

    def trash(self):
        return self.ok(self.get("/api/trash", user=ADMIN))["items"]

    def test_soft_delete_person_hides_and_lists_in_trash(self):
        a = self.person("Ann", "Smith")
        b = self.person("Bob", "Smith")
        fid = self.family(a, None, [b])
        self.ok(self.delete(f"/api/people/{a}"))
        self.assertEqual(self.get(f"/api/people/{a}").status_code, 404)
        self.assertEqual([p["id"] for p in self.ok(self.get("/api/people"))["items"]], [b])
        self.assertEqual(self.get(f"/api/tree?focus={a}").status_code, 404)
        self.assertEqual(self.detail(b)["parents"], [])
        self.assertEqual(self.ok(self.get(f"/api/families/{fid}"))["partners"], [])
        t = self.trash()
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["entity"], t[0]["id"], t[0]["name"]), ("people", a, "Ann Smith"))
        # a second delete of the same person is a 404
        self.assertEqual(self.delete(f"/api/people/{a}").status_code, 404)

    def test_restore_person_both_routes(self):
        a = self.person("Ann")
        self.ok(self.delete(f"/api/people/{a}"))
        self.ok(self.post(f"/api/trash/people/{a}/restore", user=ADMIN))
        self.assertEqual(self.detail(a)["name"], "Ann")
        self.assertEqual(self.trash(), [])
        self.assertEqual(self.post(f"/api/trash/people/{a}/restore", user=ADMIN).status_code, 409)
        self.ok(self.delete(f"/api/people/{a}"))
        self.ok(self.post(f"/api/people/{a}/restore", user=ADMIN))
        self.assertEqual(self.detail(a)["name"], "Ann")
        self.assertEqual(self.post(f"/api/people/{a}/restore", user=ADMIN).status_code, 409)
        self.assertEqual(self.post("/api/people/nope/restore", user=ADMIN).status_code, 404)
        self.assertEqual(self.post("/api/trash/users/x/restore", user=ADMIN).status_code, 404)
        self.assertEqual(self.post("/api/trash/people/nope/restore", user=ADMIN).status_code, 404)

    def test_soft_delete_family_and_restore(self):
        a = self.person("Ann", gender="female")
        b = self.person("Ben", gender="male")
        k = self.person("Kid")
        fid = self.family(a, b, [k])
        self.ok(self.delete(f"/api/families/{fid}"))
        self.assertEqual(self.get(f"/api/families/{fid}").status_code, 404)
        self.assertEqual(self.detail(k)["parents"], [])
        self.assertEqual(self.detail(a)["families"], [])
        t = self.trash()
        self.assertEqual((t[0]["entity"], t[0]["id"], t[0]["name"]), ("families", fid, "Family of Ben & Ann"))
        self.ok(self.post(f"/api/trash/families/{fid}/restore", user=ADMIN))
        self.assertEqual(len(self.detail(k)["parents"]), 2)
        self.ok(self.delete(f"/api/families/{fid}"))
        self.ok(self.post(f"/api/families/{fid}/restore", user=ADMIN))
        self.assertEqual(len(self.detail(k)["parents"]), 2)
        self.assertEqual(self.post(f"/api/families/{fid}/restore", user=ADMIN).status_code, 409)

    def test_empty_trash_admin_only(self):
        a = self.person("Ann")
        self.ok(self.delete(f"/api/people/{a}"))
        self.assertEqual(self.delete("/api/trash", user=ALICE).status_code, 403)
        self.assertEqual(len(self.trash()), 1)
        purged = self.ok(self.delete("/api/trash", user=ADMIN))["purged"]
        self.assertEqual(purged, {"people": 1, "families": 0, "media": 0})
        self.assertEqual(self.trash(), [])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)

    def test_purge_keeps_family_with_other_partner(self):
        dad = self.person("Dad", gender="male")
        mum = self.person("Mum", gender="female")
        kid = self.person("Kid")
        fid = self.family(dad, mum, [kid])
        solo = self.person("Solo")
        kid2 = self.person("Kid2")
        fid2 = self.family(solo, None, [kid2])
        self.ok(self.delete(f"/api/people/{dad}"))
        self.ok(self.delete(f"/api/people/{solo}"))
        purged = self.ok(self.delete("/api/trash", user=ADMIN))["purged"]
        self.assertEqual(purged, {"people": 2, "families": 1, "media": 0})
        fam = self.ok(self.get(f"/api/families/{fid}"))
        self.assertEqual([p["id"] for p in fam["partners"]], [mum])
        self.assertEqual([c["id"] for c in fam["children"]], [kid])
        self.assertEqual([p["id"] for p in self.detail(kid)["parents"]], [mum])
        self.assertEqual(self.get(f"/api/families/{fid2}").status_code, 404)
        self.assertEqual(self.detail(kid2)["parents"], [])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM family_children WHERE family_id = ?", (fid2,))[0]["n"], 0)

    def test_purge_deleted_family_and_deleted_child(self):
        a = self.person("Ann")
        k = self.person("Kid")
        fid = self.family(a, None, [k])
        self.ok(self.post("/api/events", {"familyId": fid, "type": "residence", "place": "Leeds"}), 201)
        k2 = self.person("Kid2")
        self.ok(self.post(f"/api/families/{fid}/children", {"existingId": k2}), 201)
        self.ok(self.delete(f"/api/people/{k2}"))
        self.ok(self.delete(f"/api/families/{fid}"))
        purged = self.ok(self.delete("/api/trash", user=ADMIN))["purged"]
        self.assertEqual(purged, {"people": 1, "families": 1, "media": 0})
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM events")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM family_children")[0]["n"], 0)
        self.assertEqual(self.detail(a)["families"], [])

    def test_undo_after_purge_is_a_conflict(self):
        a = self.person("Ann")
        bid = self.ok(self.delete(f"/api/people/{a}"))["batchId"]
        self.ok(self.delete("/api/trash", user=ADMIN))
        r = self.post(f"/api/history/{bid}/undo")
        self.assertEqual(r.status_code, 409, r.text)
        h = self.ok(self.get("/api/history"))
        self.assertEqual(h["items"][0]["people"], [{"id": a, "name": "a deleted person"}])

    def test_purge_older_than_days(self):
        old = self.person("Old")
        new = self.person("New")
        f_old = self.family(self.person("Keeper"), None)
        self.ok(self.delete(f"/api/people/{old}"))
        self.ok(self.delete(f"/api/people/{new}"))
        self.ok(self.delete(f"/api/families/{f_old}"))
        long_ago = (config.utcnow() - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sql("UPDATE people SET deleted_at = ? WHERE id = ?", (long_ago, old))
        sql("UPDATE families SET deleted_at = ? WHERE id = ?", (long_ago, f_old))
        self.assertEqual(housekeeping.purge(older_than_days=200), {"people": 0, "families": 0, "media": 0})
        self.assertEqual(housekeeping.purge(older_than_days=30), {"people": 1, "families": 1, "media": 0})
        left = {(i["entity"], i["id"]) for i in self.trash()}
        self.assertEqual(left, {("people", new)})
        self.assertEqual(self.ok(self.get("/api/trash", user=ADMIN))["trashDays"], 90)



class LoopsThroughTrash(ApiTestCase):
    """Links made while someone is in the trash can't see them; restoring them
    (or undoing their delete) must not make anyone their own ancestor.
    Restoring is admin-only; undo stays open to everyone."""

    def chain(self):
        a = self.person("Ann")
        b = self.ok(self.post(f"/api/people/{a}/add-child", {"person": {"given_names": "Ben"}}), 201)["newPersonId"]
        c = self.ok(self.post(f"/api/people/{b}/add-child", {"person": {"given_names": "Cal"}}), 201)["newPersonId"]
        delete = self.ok(self.delete(f"/api/people/{b}"))
        # with Ben in the trash, Cal and Ann look unrelated: Cal becomes Ann's parent
        self.ok(self.post(f"/api/people/{a}/add-parent", {"existingId": c}), 201)
        return a, b, c, delete["batchId"]

    def test_restore_person_that_would_close_a_loop_422(self):
        a, b, c, _ = self.chain()
        for path in (f"/api/people/{b}/restore", f"/api/trash/people/{b}/restore"):
            r = self.post(path, user=ADMIN)
            self.assertEqual(r.status_code, 422, r.text)
            self.assertIn("own ancestor", r.json()["detail"])
        self.assertEqual(self.get(f"/api/people/{b}").status_code, 404)      # still in the trash
        self.assertEqual(self.detail(a)["warnings"], [])

    def test_undo_delete_that_would_close_a_loop_409(self):
        a, b, c, delete_batch = self.chain()
        r = self.post(f"/api/history/{delete_batch}/undo")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("own ancestor", r.json()["detail"])
        self.assertEqual(self.get(f"/api/people/{b}").status_code, 404)

    def test_restore_family_that_would_close_a_loop_422(self):
        a = self.person("Ann")
        b = self.person("Ben")
        fid = self.family(a, None, [b])
        self.ok(self.delete(f"/api/families/{fid}"))
        self.family(b, None, [a])                  # Ben is now Ann's parent
        for path in (f"/api/families/{fid}/restore", f"/api/trash/families/{fid}/restore"):
            self.assertEqual(self.post(path, user=ADMIN).status_code, 422)
        self.assertEqual(self.get(f"/api/families/{fid}").status_code, 404)

    def test_restore_without_a_loop_still_works(self):
        a = self.person("Ann")
        b = self.ok(self.post(f"/api/people/{a}/add-child", {"person": {"given_names": "Ben"}}), 201)["newPersonId"]
        d = self.ok(self.delete(f"/api/people/{b}"))
        self.ok(self.post(f"/api/history/{d['batchId']}/undo"))
        self.assertEqual(len(self.detail(b)["parents"]), 1)


if __name__ == "__main__":
    unittest.main()
