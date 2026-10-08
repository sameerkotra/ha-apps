"""View links (SPEC §6.6): "Anyone in the household with the link can view" — made and turned off by an owner or a
manager only; opening one gives Can view (marked as by the link); people who can open it already keep their role;
turning it off (or a new address) ends what it gave and leaves shares made by hand; children, admin shared folders,
Trash and a maker who can no longer share get nothing. The /view/<token> address opens the app's #/view route."""
import _env  # noqa: F401

from base import ASHA, DEV, KABIR, MEERA, NOBODY, ApiBase
from app import config, db


class ViewLinks(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV, NOBODY)

    def setUp(self):
        super().setUp()
        self.folder = self.create("folder", "Recipes")["id"]
        self.note_id = self.note("Dal", self.folder, text="Lentils, turmeric")["id"]
        self.ok(self.share(self.note_id, MEERA, "manager"))

    def link(self, node=None, h=KABIR, new=False, code=200):
        r = self.post(f"/api/nodes/{node or self.note_id}/link", {"new": new}, h)
        self.assertEqual(r.status_code, code, r.text)
        return r.json().get("link")

    def visit(self, token, h, code=200):
        r = self.get(f"/api/view-links/{token}", h)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def test_open_gives_can_view(self):
        self.assertIsNone(self.get(f"/api/nodes/{self.note_id}/shares").json()["link"])
        link = self.link()
        self.assertRegex(link["token"], r"^[A-Za-z0-9_-]{22}$")
        self.assertEqual(self.link()["token"], link["token"])                 # asking again keeps the same link
        self.assertEqual(self.get(f"/api/docs/{self.note_id}", DEV).status_code, 404)
        seen = self.visit(link["token"], DEV)
        self.assertEqual((seen["id"], seen["kind"]), (self.note_id, "note"))
        self.assertTrue(seen["name"].startswith("Dal"))
        doc = self.open(self.note_id, DEV)
        self.assertEqual((doc["role"], doc["canEdit"], doc["text"]), ("viewer", False, "Lentils, turmeric"))
        self.assertIn(self.note_id, [i["id"] for i in self.get("/api/space/shared", DEV).json()["items"]])
        shares = self.get(f"/api/nodes/{self.note_id}/shares").json()
        dev = next(s for s in shares["shares"] if s["userId"] == self.uid(DEV))
        self.assertEqual((dev["role"], dev["viaLink"]), ("viewer", True))
        self.assertEqual(shares["link"]["opened"], 1)
        # the manager sees and manages the link; a viewer sees none and can't make one
        self.assertEqual(self.get(f"/api/nodes/{self.note_id}/shares", MEERA).json()["link"]["token"], link["token"])
        self.assertIsNone(self.get(f"/api/nodes/{self.note_id}/shares", DEV).json()["link"])
        self.link(h=DEV, code=403)
        self.assertEqual(self.delete(f"/api/nodes/{self.note_id}/link", DEV).status_code, 403)

    def test_people_who_can_open_it_keep_their_role(self):
        token = self.link()["token"]
        self.visit(token, MEERA)
        self.visit(token, KABIR)
        roles = {s["userId"]: s["role"] for s in self.get(f"/api/nodes/{self.note_id}/shares").json()["shares"]}
        self.assertEqual(roles, {self.uid(MEERA): "manager"})

    def test_turning_off_and_new_address(self):
        token = self.link()["token"]
        self.visit(token, DEV)
        self.visit(token, NOBODY)
        self.ok(self.share(self.note_id, NOBODY, "editor"))           # changed by hand: an ordinary share now
        r = self.delete(f"/api/nodes/{self.note_id}/link")
        self.assertEqual(r.json()["removed"], 1)
        self.assertEqual(self.get(f"/api/docs/{self.note_id}", DEV).status_code, 404)
        self.assertEqual(self.open(self.note_id, NOBODY)["role"], "editor")
        self.assertIn("doesn't work any more", self.visit(token, DEV, code=404)["detail"])
        self.assertEqual(self.delete(f"/api/nodes/{self.note_id}/link").status_code, 404)
        first = self.link()["token"]
        second = self.link(new=True)["token"]
        self.assertNotEqual(first, second)
        self.visit(first, DEV, code=404)
        self.visit(second, DEV)

    def test_who_gets_nothing(self):
        token = self.link()["token"]
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET is_child = 1, child_since = ? WHERE id = ?", (config.now_iso(), self.uid(DEV)))
        self.assertIn("Kids' space", self.visit(token, DEV, code=403)["detail"])      # a child's account
        self.visit("not-a-token", NOBODY, code=404)
        meera_link = self.link(h=MEERA, new=True)["token"]
        self.ok(self.delete(f"/api/nodes/{self.note_id}/shares/{self.uid(MEERA)}"))
        self.visit(meera_link, NOBODY, code=404)                       # its maker can't share it any more
        self.assertEqual(self.delete(f"/api/nodes/{self.note_id}").status_code, 200)
        self.visit(meera_link, NOBODY, code=404)                       # in the Trash

    def test_deep_link_address(self):
        r = self.c.get("/view/AbC_12-x", headers=KABIR, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (307, "../#/view/AbC_12-x"))
