"""Who may see and do what (SPEC §3.2, §6): every role, turned-off people, admins, inherited shares (including
files added outside the app), Everyone, Shared with me, leave and hide, transfer. Anything you can't see is a
404; seeing but not being allowed is a 403."""
import _env  # noqa: F401

import os
import unittest


from base import ASHA, DEV, KABIR, MEERA, NOBODY, ApiBase, person_dir, write


class AccessMatrix(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def setUp(self):
        super().setUp()
        # Kabir owns Trip/ with a note and a checklist; Meera may manage it, Dev may edit, Asha (an admin) nothing
        self.folder = self.create("folder", "Trip")["id"]
        self.note_id = self.note("Plan", self.folder, text="Day one: the beach")["id"]
        self.list_id = self.create("checklist", "Packing", self.folder)["id"]
        self.assertEqual(self.ops(self.list_id, [{"op": "add", "text": "Socks"}, {"op": "add", "text": "Hat"}]).status_code, 200)
        self.ok(self.share(self.folder, MEERA, "manager"))
        self.ok(self.share(self.folder, DEV, "editor"))

    def key(self, h=KABIR, i=0):
        return self.open(self.list_id, h)["items"][i]["key"]

    def test_nobody_and_admin_see_nothing(self):
        self.get("/api/me", NOBODY)
        for h in (NOBODY, ASHA):
            with self.subTest(self.uid(h)):
                for path in (f"/api/docs/{self.note_id}", f"/api/nodes/{self.folder}", f"/api/list?node={self.folder}",
                             f"/api/nodes/{self.note_id}/shares", f"/api/docs/{self.note_id}/versions",
                             f"/api/nodes/{self.note_id}/file", f"/api/docs/{self.note_id}/etag",
                             f"/api/folders?node={self.folder}"):
                    self.assertEqual(self.get(path, h).status_code, 404, path)
                self.assertEqual(self.patch(f"/api/docs/{self.note_id}", {"etag": "x", "text": "y"}, h).status_code, 404)
                self.assertEqual(self.ops(self.list_id, [{"op": "untickAll"}], h).status_code, 404)
                self.assertEqual(self.post(f"/api/nodes/{self.note_id}/rename", {"name": "X"}, h).status_code, 404)
                self.assertEqual(self.delete(f"/api/nodes/{self.note_id}", h).status_code, 404)
                self.assertEqual(self.share(self.note_id, NOBODY, "viewer", h).status_code, 404)
                self.assertEqual(self.post(f"/api/nodes/{self.note_id}/copy", {}, h).status_code, 404)
                self.assertEqual(self.post(f"/api/nodes/{self.note_id}/favourite", {"value": True}, h).status_code, 404)
                self.assertEqual(self.get("/api/search?q=beach", h).json()["results"], [])
                self.assertEqual(self.get("/api/space/shared", h).json()["items"], [])

    def test_viewer(self):
        self.get("/api/me", NOBODY)
        self.ok(self.share(self.folder, NOBODY, "viewer"))
        v = NOBODY
        doc = self.open(self.note_id, v)
        self.assertEqual(doc["role"], "viewer")
        self.assertFalse(doc["canEdit"])
        self.assertEqual(doc["text"], "Day one: the beach")
        self.assertEqual(self.patch(f"/api/docs/{self.note_id}", {"etag": doc["etag"], "text": "mine"}, v).status_code, 403)
        self.assertEqual(self.post(f"/api/nodes/{self.note_id}/rename", {"name": "X"}, v).status_code, 403)
        self.assertEqual(self.delete(f"/api/nodes/{self.note_id}", v).status_code, 403)
        self.assertEqual(self.post("/api/docs", {"kind": "note", "name": "N", "parentId": self.folder}, v).status_code, 403)
        self.assertEqual(self.share(self.note_id, DEV, "viewer", v).status_code, 403)
        self.assertEqual(self.post(f"/api/docs/{self.note_id}/versions/1/restore", {}, v).status_code, 403)
        # viewers tick (on by default), but nothing else on a checklist
        self.assertEqual(self.ops(self.list_id, [{"op": "tick", "key": self.key(v)}], v).status_code, 200)
        self.assertEqual(self.ops(self.list_id, [{"op": "add", "text": "More"}], v).status_code, 403)
        self.assertEqual(self.ops(self.list_id, [{"op": "edit", "key": self.key(v), "text": "x"}], v).status_code, 403)
        # copy into their own My docs, download, favourite
        copy = self.ok(self.post(f"/api/nodes/{self.note_id}/copy", {}, v), 201)
        self.assertEqual(copy["role"], "owner")
        self.assertEqual(self.open(copy["id"], v)["text"], "Day one: the beach")
        r = self.get(f"/api/nodes/{self.note_id}/file", v)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"Day one: the beach")
        self.ok(self.post(f"/api/nodes/{self.note_id}/favourite", {"value": True}, v))
        self.assertEqual([i["id"] for i in self.get("/api/space/favourites", v).json()["items"]], [self.note_id])

    def test_viewers_tick_off(self):
        self.get("/api/me", NOBODY)
        self.ok(self.share(self.folder, NOBODY, "viewer", viewersTick=False))
        doc = self.open(self.list_id, NOBODY)
        self.assertFalse(doc["canTick"])
        self.assertEqual(self.ops(self.list_id, [{"op": "tick", "key": self.key()}], NOBODY).status_code, 403)
        self.assertEqual(self.ops(self.list_id, [{"op": "untickAll"}], NOBODY).status_code, 403)

    def test_editor(self):
        d = DEV
        doc = self.open(self.note_id, d)
        self.assertEqual(doc["role"], "editor")
        self.ok(self.patch(f"/api/docs/{self.note_id}", {"etag": doc["etag"], "text": "Edited by Dev"}, d))
        new = self.create("note", "Dev's idea", self.folder, d)
        self.assertEqual(new["ownerId"], self.uid(KABIR))                 # written into Kabir's folder
        self.assertEqual(new["createdBy"], self.uid(DEV))
        self.assertTrue(os.path.isfile(os.path.join(person_dir("Kabir Rao"), "Trip", "Dev's idea.txt")))
        self.ok(self.post(f"/api/nodes/{new['id']}/rename", {"name": "Dev's plan"}, d))
        sub = self.create("folder", "Days", self.folder, d)
        self.ok(self.post(f"/api/nodes/{new['id']}/move", {"parentId": sub["id"]}, d))      # within the shared tree
        self.assertEqual(self.post(f"/api/nodes/{new['id']}/move", {"parentId": None}, d).status_code, 403)
        self.assertEqual(self.post(f"/api/nodes/{self.folder}/move", {"parentId": None}, d).status_code, 403)
        self.assertEqual(self.share(self.note_id, NOBODY, "viewer", d).status_code, 403)
        self.ok(self.delete(f"/api/nodes/{new['id']}", d))
        self.assertEqual(self.delete(f"/api/nodes/{self.folder}", d).status_code, 403)    # carries shares
        self.assertEqual(self.post(f"/api/nodes/{self.note_id}/transfer", {"userId": self.uid(DEV)}, d).status_code, 403)

    def test_manager(self):
        self.get("/api/me", NOBODY)
        m = MEERA
        self.assertEqual(self.open(self.note_id, m)["role"], "manager")
        self.ok(self.share(self.note_id, NOBODY, "viewer", m))
        self.ok(self.share(self.note_id, NOBODY, "editor", m))
        self.assertEqual(self.share(self.note_id, NOBODY, "manager", m).status_code, 403)   # only the owner makes managers
        self.assertEqual(self.delete(f"/api/nodes/{self.folder}/shares/{self.uid(MEERA)}", DEV).status_code, 403)
        self.ok(self.delete(f"/api/nodes/{self.folder}/shares/{self.uid(DEV)}", m))         # managers remove people
        self.assertEqual(self.get(f"/api/docs/{self.note_id}", DEV).status_code, 404)
        self.ok(self.post(f"/api/nodes/{self.note_id}/move", {"parentId": None}, m))         # out of the shared tree
        self.assertEqual(self.get(f"/api/docs/{self.note_id}", m).status_code, 404)         # …and so out of reach
        self.assertEqual(self.post(f"/api/nodes/{self.list_id}/transfer", {"userId": self.uid(MEERA)}, m).status_code, 403)
        self.ok(self.delete(f"/api/nodes/{self.list_id}", m))

    def test_owner_only(self):
        self.get("/api/me", NOBODY)
        self.assertEqual(self.share(self.folder, KABIR, "viewer").status_code, 422)         # yourself
        self.ok(self.share(self.note_id, NOBODY, "manager"))
        self.assertEqual(self.share(self.note_id, "u_unknown", "viewer").status_code, 404)

    def test_turned_off_people_get_404(self):
        self.ok(self.patch(f"/api/admin/users/{self.uid(DEV)}", {"disabled": True}, ASHA))
        me = self.ok(self.get("/api/me", DEV))
        self.assertTrue(me["disabled"])
        self.assertIn("turned off", me["disabledMessage"])
        for path in (f"/api/docs/{self.note_id}", "/api/list", "/api/space/shared", "/api/search?q=beach",
                     f"/api/nodes/{self.folder}", "/api/people"):
            r = self.get(path, DEV)
            self.assertEqual(r.status_code, 404, path)
        self.assertEqual(self.get("/api/whoami", DEV).status_code, 200)                    # can still see why
        # their own folder stays, and stays shared
        mine = self.note("Dev's own", h=MEERA)
        self.ok(self.patch(f"/api/admin/users/{self.uid(MEERA)}", {"disabled": True}, ASHA))
        self.ok(self.patch(f"/api/admin/users/{self.uid(MEERA)}", {"disabled": False}, ASHA))
        self.assertEqual(self.open(mine["id"], MEERA)["text"], "")
        # sharing with a turned-off person is refused
        self.assertEqual(self.share(self.note_id, DEV, "viewer").status_code, 409)

    def test_owner_turned_off_keeps_sharing(self):
        self.ok(self.patch(f"/api/admin/users/{self.uid(KABIR)}", {"disabled": True}, ASHA))
        self.assertEqual(self.open(self.note_id, DEV)["text"], "Day one: the beach")


class InheritedShares(ApiBase):
    def test_folder_share_reaches_everything_inside_now_and_later(self):
        top = self.create("folder", "House")["id"]
        sub = self.create("folder", "Bills", top)["id"]
        deep = self.note("Gas", sub, text="meter 1234")
        self.ok(self.share(top, MEERA, "viewer"))
        self.assertEqual(self.open(deep["id"], MEERA)["role"], "viewer")
        later = self.note("Water", sub)                                       # made after the share
        self.assertEqual(self.open(later["id"], MEERA)["role"], "viewer")
        # a file added outside the app (Samba): found by the scan, inherited too
        write(os.path.join(person_dir("Kabir Rao"), "House", "Bills", "Scan.txt"), "outside text")
        listing = self.ok(self.get(f"/api/list?node={sub}", MEERA))
        added = [i for i in listing["items"] if i["name"] == "Scan.txt"]
        self.assertEqual(len(added), 1)
        self.assertTrue(added[0]["outside"])
        self.assertEqual(self.open(added[0]["id"], MEERA)["text"], "outside text")
        # Shared with me shows only the top of the shared tree, with its owner
        items = self.ok(self.get("/api/space/shared", MEERA))["items"]
        self.assertEqual([(i["name"], i["ownerName"]) for i in items], [("House", "Kabir Rao")])

    def test_deeper_share_adds_but_never_takes_away(self):
        top = self.create("folder", "House")["id"]
        sub = self.create("folder", "Bills", top)["id"]
        n = self.note("Gas", sub)
        self.ok(self.share(top, MEERA, "editor"))
        self.ok(self.share(sub, MEERA, "viewer"))
        self.assertEqual(self.open(n["id"], MEERA)["role"], "editor")
        self.ok(self.share(top, MEERA, "viewer"))
        self.ok(self.share(sub, MEERA, "editor"))
        self.assertEqual(self.open(n["id"], MEERA)["role"], "editor")
        self.assertEqual(self.open(self.create("note", "Top note", top)["id"], MEERA)["role"], "viewer")
        info = self.ok(self.get(f"/api/nodes/{n['id']}/shares", MEERA))
        self.assertEqual({(x["from"], x["role"]) for x in info["inherited"]}, {("House", "viewer"), ("Bills", "editor")})

    def test_crumbs_stop_at_what_you_can_see(self):
        top = self.create("folder", "House")["id"]
        sub = self.create("folder", "Bills", top)["id"]
        self.ok(self.share(sub, MEERA, "viewer"))
        crumbs = self.ok(self.get(f"/api/list?node={sub}", MEERA))["crumbs"]
        self.assertEqual([c["name"] for c in crumbs], ["Shared with me", "Bills"])
        mine = self.ok(self.get(f"/api/list?node={sub}"))["crumbs"]
        self.assertEqual([c["name"] for c in mine], ["My docs", "House", "Bills"])


class EveryoneShares(ApiBase):
    def test_everyone_can_view_and_hide_but_not_leave(self):
        n = self.note("Wifi", text="Ask at the desk")
        self.ok(self.share(n["id"], "*", "viewer"))
        self.get("/api/me", NOBODY)                         # someone new, later
        self.assertEqual(self.open(n["id"], NOBODY)["role"], "viewer")
        items = self.ok(self.get("/api/space/everyone", MEERA))["items"]
        self.assertEqual([i["name"] for i in items], ["Wifi.txt"])
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/leave", {}, MEERA).status_code, 409)
        self.ok(self.post(f"/api/nodes/{n['id']}/hide", {"value": True}, MEERA))
        self.assertEqual(self.ok(self.get("/api/space/everyone", MEERA))["items"], [])
        self.assertEqual(len(self.ok(self.get("/api/space/everyone?hidden=1", MEERA))["items"]), 1)
        self.assertEqual(self.open(n["id"], MEERA)["role"], "viewer")         # hidden, not gone
        mine = self.ok(self.get("/api/space/everyone"))["items"]
        self.assertEqual([i["ownerId"] for i in mine], [self.uid(KABIR)])

    def test_everyone_editor_and_the_switch(self):
        n = self.note("Rota")
        self.assertEqual(self.share(n["id"], "*", "manager").status_code, 422)
        self.ok(self.share(n["id"], "*", "editor"))
        self.assertTrue(self.open(n["id"], MEERA)["canEdit"])
        self.settings({"everyone_shares": False})
        self.assertEqual(self.share(self.note("Other")["id"], "*", "viewer").status_code, 403)

    def test_everyone_default_role_is_reported(self):
        n = self.note("Rota")
        self.assertEqual(self.ok(self.get(f"/api/nodes/{n['id']}/shares"))["everyoneDefaultRole"], "viewer")
        self.settings({"everyone_default_role": "editor"})
        self.assertEqual(self.ok(self.get(f"/api/nodes/{n['id']}/shares"))["everyoneDefaultRole"], "editor")


class LeaveHideTransfer(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def test_leave(self):
        n = self.note("Gift ideas")
        self.ok(self.share(n["id"], MEERA, "editor"))
        self.ok(self.post(f"/api/nodes/{n['id']}/leave", {}, MEERA))
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)
        self.assertEqual(self.ok(self.get("/api/space/shared", MEERA))["items"], [])

    def test_hide_direct_share(self):
        n = self.note("Gift ideas")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        self.ok(self.post(f"/api/nodes/{n['id']}/hide", {"value": True}, MEERA))
        self.assertEqual(self.ok(self.get("/api/space/shared", MEERA))["items"], [])
        self.ok(self.post(f"/api/nodes/{n['id']}/hide", {"value": False}, MEERA))
        self.assertEqual(len(self.ok(self.get("/api/space/shared", MEERA))["items"]), 1)

    def test_transfer_moves_the_item_and_keeps_everything(self):
        top = self.create("folder", "Car")["id"]
        n = self.note("Service", top, text="oil change")
        lst = self.create("checklist", "Before the trip", top)
        self.assertEqual(self.ops(lst["id"], [{"op": "add", "text": "Tyres"}]).status_code, 200)
        self.ok(self.share(top, DEV, "viewer"))
        self.ok(self.post(f"/api/nodes/{top}/favourite", {"value": True}))
        self.save(n["id"], "oil change and filters")          # a version
        self.ok(self.post(f"/api/nodes/{top}/transfer", {"userId": self.uid(MEERA)}))
        self.assertTrue(os.path.isfile(os.path.join(person_dir("Meera Rao"), "Car", "Service.txt")))
        self.assertFalse(os.path.exists(os.path.join(person_dir("Kabir Rao"), "Car")))
        doc = self.open(n["id"], MEERA)
        self.assertEqual(doc["role"], "owner")
        self.assertEqual(self.open(n["id"])["role"], "editor")           # the previous owner keeps Can edit
        self.assertEqual(self.open(n["id"], DEV)["role"], "viewer")      # shares move with it
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{n['id']}/versions", MEERA))["versions"]), 1)
        self.assertEqual(self.open(lst["id"], MEERA)["items"][0]["text"], "Tyres")
        self.assertEqual(self.post(f"/api/nodes/{top}/transfer", {"userId": self.uid(DEV)}).status_code, 403)

    def test_transfer_name_clash_and_refusals(self):
        self.create("folder", "Car", h=MEERA)
        top = self.create("folder", "Car")["id"]
        self.ok(self.post(f"/api/nodes/{top}/transfer", {"userId": self.uid(MEERA)}))
        self.assertTrue(os.path.isdir(os.path.join(person_dir("Meera Rao"), "Car (2)")))
        other = self.note("x")
        self.assertEqual(self.post(f"/api/nodes/{other['id']}/transfer", {"userId": self.uid(KABIR)}).status_code, 422)
        self.assertEqual(self.post(f"/api/nodes/{other['id']}/transfer", {"userId": "u_none"}).status_code, 404)

    def test_move_between_people_is_refused(self):
        n = self.note("x")
        theirs = self.create("folder", "Theirs", h=MEERA)
        self.ok(self.share(theirs["id"], KABIR, "editor", h=MEERA))
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/move", {"parentId": theirs["id"]}).status_code, 422)


class SharingNotifications(ApiBase):
    def setUp(self):
        super().setUp()
        self.ha.people = [{"entity_id": "person.meera", "name": "Meera Rao", "user_id": "u_meera", "state": "home",
                           "phones": [{"name": "Meera Phone", "label": "Meera Phone", "tracker": "device_tracker.mp"}]}]
        from app.common import ha_people
        ha_people.refresh_blocking(True)

    def test_new_share_notifies_once_without_content(self):
        n = self.note("Trip 2026", text="secret plans")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        sent = self.ha.notifications()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "mobile_app_meera_phone")
        self.assertEqual(sent[0][1]["message"], "Kabir Rao shared “Trip 2026.txt” with you")
        self.assertNotIn("secret", str(sent[0][1]))
        self.ok(self.share(n["id"], MEERA, "editor"))                 # a change of role: no second message
        self.assertEqual(len(self.ha.notifications()), 1)

    def test_switched_off_and_everyone(self):
        self.ok(self.put("/api/me/settings", {"notifyShares": False}, MEERA))
        n = self.note("A")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        self.ok(self.share(n["id"], "*", "viewer"))
        self.assertEqual(self.ha.notifications(), [])

    def test_transfer_tells_the_new_owner(self):
        n = self.note("Car")
        self.ok(self.post(f"/api/nodes/{n['id']}/transfer", {"userId": "u_meera"}))
        self.assertEqual(self.ha.notifications()[0][1]["message"], "Kabir Rao gave you “Car.txt”")


if __name__ == "__main__":
    unittest.main()

