"""Who may change a group, each person's own default group, limits, disabled people, and the new group features
(rename, take someone out, change a settle-up payment) — the 13 Oct 2026 review (SP1, SP3, SP4, SP5, SPI7).
Every person here is invented."""
import _env  # noqa: F401  (must be first)

import unittest

from app import main
from test_app import ADMIN, ALICE, Base, hdr

BEN = hdr("u_ben", "Ben", "ben")
XAVIER = hdr("u_xavier", "Xavier", "xavier")      # a Home Assistant login linked to Xavier, who is in no group


class Linked(Base):
    """Alice's login is Ann (a member of House), Ben's is Ben, Xavier's is Xavier (not a member)."""

    def setUp(self):
        super().setUp()
        with main.get_conn() as conn:
            for uid, ha in (("a", "u_alice"), ("b", "u_ben"), ("x", "u_xavier")):
                conn.execute("UPDATE users SET ha_user_id = ? WHERE id = ?", (ha, uid))
            conn.commit()

    def group(self, who=ALICE, **q):
        r = self.c.get(f"/api/groups/{self.gid}", params=q, headers=who)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class OpenModel(Linked):
    def test_everyone_sees_and_adds_to_every_group(self):
        """The documented open model (DOCS "Who can see what"): a non-member can open any group and add to it."""
        self.assertEqual(self.c.get(f"/api/groups/{self.gid}", headers=XAVIER).status_code, 200)
        r = self.c.post(f"/api/groups/{self.gid}/expenses", headers=XAVIER,
                        json={"description": "Milk", "amount": 3, "paidBy": "a"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.c.get(f"/api/groups/{self.gid}/export.csv", headers=XAVIER).status_code, 200)
        docs = (main.Path(main.__file__).parent.parent / "DOCS.md").read_text(encoding="utf-8")
        self.assertTrue("sees every group" in docs and "Money is private" not in docs)


class MembersOnly(Linked):
    def test_only_members_and_admins_add_members(self):
        r = self.c.post(f"/api/groups/{self.gid}/members", json={"userId": "x"}, headers=XAVIER)
        self.assertEqual(r.status_code, 403)
        r = self.c.post(f"/api/groups/{self.gid}/members", json={"userId": "x"}, headers=hdr("u_nobody", "Nobody"))
        self.assertEqual(r.status_code, 403)                     # a login linked to nobody
        self.assertNotIn("x", self.group()["memberIds"])
        r = self.c.post(f"/api/groups/{self.gid}/members", json={"userId": "x"}, headers=ALICE)   # Ann is a member
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("x", r.json()["memberIds"])
        with main.get_conn() as conn:
            conn.execute("DELETE FROM group_members WHERE user_id = 'x'")
            conn.commit()
        r = self.c.post(f"/api/groups/{self.gid}/members", json={"userId": "x"}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)

    def test_rename(self):
        self.assertEqual(self.c.put(f"/api/groups/{self.gid}", json={"name": "Flat"}, headers=XAVIER).status_code, 403)
        r = self.c.put(f"/api/groups/{self.gid}", json={"name": "  The   Flat "}, headers=BEN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["name"], "The Flat")
        self.assertEqual(self.c.put(f"/api/groups/{self.gid}", json={"name": "  "}, headers=BEN).status_code, 400)
        self.assertEqual(self.c.put(f"/api/groups/{self.gid}", json={"name": "x" * 81}, headers=BEN).status_code, 422)
        self.assertEqual(self.c.put("/api/groups/nope", json={"name": "x"}, headers=ADMIN).status_code, 404)
        ev = self.c.get("/api/events", headers=ALICE).json()[0]
        self.assertEqual((ev["type"], ev["message"]), ("group_renamed", "Ben renamed group 'House' to 'The Flat'"))

    def test_take_out_only_at_zero_balance(self):
        self.expense(amount=30, paid_by="a")                      # Ann +20, Ben -10, Cat -10
        r = self.c.delete(f"/api/groups/{self.gid}/members/c", headers=ALICE)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Cat owes $10.00", r.json()["detail"])
        self.assertEqual(self.c.delete(f"/api/groups/{self.gid}/members/c", headers=XAVIER).status_code, 403)
        r = self.c.post(f"/api/groups/{self.gid}/payments", headers=ALICE,
                        json={"fromUserId": "c", "toUserId": "a", "amount": 10})
        self.assertEqual(r.status_code, 201)
        r = self.c.delete(f"/api/groups/{self.gid}/members/c", headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        g = r.json()
        self.assertEqual(g["memberIds"], ["a", "b"])
        self.assertEqual(len(g["expenses"]), 2)                   # their entries stay
        self.assertEqual({n["userId"]: n["amount"] for n in g["balances"]["net"]}, {"a": 10.0, "b": -10.0, "c": 0.0})
        self.assertEqual(self.c.delete(f"/api/groups/{self.gid}/members/c", headers=ALICE).status_code, 404)
        self.c.delete(f"/api/groups/{self.gid}/members/b", headers=ADMIN)     # Ben owes: refused
        r = self.c.post("/api/groups", json={"name": "Solo", "memberIds": ["a"]}, headers=ALICE)
        self.assertEqual(self.c.delete(f"/api/groups/{r.json()['id']}/members/a", headers=ALICE).status_code, 400)


class GroupsListStanding(Linked):
    """The Groups list says where the viewer stands in each group, and when each group last changed."""

    def test_my_balance_and_last_activity(self):
        self.expense(amount=30, paid_by="a")                      # Ann +20, Ben -10, Cat -10
        trip = self.c.post("/api/groups", json={"name": "Trip", "memberIds": ["b", "c"]}, headers=ALICE).json()["id"]
        by = lambda who: {g["id"]: g for g in self.c.get("/api/groups", headers=who).json()}
        ann, ben, xav = by(ALICE), by(BEN), by(XAVIER)
        self.assertEqual(ann[self.gid]["myBalance"], 20.0)
        self.assertIsNone(ann[trip]["myBalance"])                  # Ann has no part in Trip
        self.assertEqual(ben[self.gid]["myBalance"], -10.0)
        self.assertEqual(ben[trip]["myBalance"], 0.0)              # a member, settled
        self.assertIsNone(xav[self.gid]["myBalance"])
        self.assertIsNone(by(hdr("u_nobody", "Nobody"))[self.gid]["myBalance"])   # linked to no one
        r = self.c.post(f"/api/groups/{self.gid}/payments", headers=BEN, json={"fromUserId": "b", "toUserId": "a", "amount": 10})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(by(BEN)[self.gid]["myBalance"], 0.0)
        # the newest activity-log entry, else when it was created
        ann = by(ALICE)
        self.assertGreaterEqual(ann[self.gid]["lastActivity"], ann[trip]["createdAt"])
        self.assertGreaterEqual(ann[trip]["lastActivity"], ann[trip]["createdAt"])


class DefaultGroupIsYourOwn(Linked):
    def test_each_login_has_its_own_default(self):
        other = self.c.post("/api/groups", json={"name": "Trip", "memberIds": ["a", "b"]}, headers=ALICE).json()["id"]
        r = self.c.put(f"/api/groups/{self.gid}/default", json={"isDefault": True}, headers=ALICE)
        self.assertEqual((r.status_code, r.json()["isDefault"]), (200, True))
        self.c.put(f"/api/groups/{other}/default", json={"isDefault": True}, headers=BEN)

        def defaults(who):
            return [g["name"] for g in self.c.get("/api/groups", headers=who).json() if g["isDefault"]]
        self.assertEqual(defaults(ALICE), ["House"])
        self.assertEqual(defaults(BEN), ["Trip"])
        self.assertEqual(defaults(XAVIER), [])
        self.assertFalse(self.group(BEN)["isDefault"])
        # unmarking your default: no default for you, nothing changes for anyone else
        self.c.put(f"/api/groups/{self.gid}/default", json={"isDefault": False}, headers=ALICE)
        self.assertEqual((defaults(ALICE), defaults(BEN)), ([], ["Trip"]))
        # a personal choice, not a household change: nothing in the activity log
        self.assertFalse([e for e in self.c.get("/api/events", headers=ALICE).json() if "default" in e["type"]])

    def test_the_old_household_default_stays_until_someone_chooses(self):
        with main.get_conn() as conn:                             # set by an older version
            conn.execute("UPDATE groups SET is_default = 1 WHERE id = ?", (self.gid,))
            conn.commit()
        self.assertTrue(self.group(XAVIER)["isDefault"])
        self.c.put(f"/api/groups/{self.gid}/default", json={"isDefault": False}, headers=XAVIER)
        self.assertFalse(self.group(XAVIER)["isDefault"])
        self.assertTrue(self.group(BEN)["isDefault"])
        with main.get_conn() as conn:                             # a deleted default: no default (not an error)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM groups WHERE id = ?", (self.gid,))
            conn.commit()
        self.assertEqual(self.c.get("/api/groups", headers=BEN).json(), [])


class Sizes(Linked):
    def test_oversized_names_and_lists_refused(self):
        r = self.c.post("/api/groups", json={"name": "n" * 81, "memberIds": ["a"]}, headers=ALICE)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.post("/api/groups", json={"name": "n" * 80, "memberIds": ["a"]},
                                     headers=ALICE).status_code, 201)
        r = self.c.post("/api/groups", json={"name": "Big", "memberIds": [f"id{i}" for i in range(51)]}, headers=ALICE)
        self.assertEqual(r.status_code, 422)
        r = self.c.post("/api/groups", json={"name": "Big", "memberIds": ["a" * 65]}, headers=ALICE)
        self.assertEqual(r.status_code, 422)
        self.expense(participants=[f"id{i}" for i in range(51)], status=422)
        self.assertEqual(self.c.post(f"/api/groups/{self.gid}/members", json={"userId": "u" * 65},
                                     headers=ALICE).status_code, 422)

    def test_request_body_cap_counts_chunked_bodies(self):
        def chunks():
            for _ in range(20):
                yield b" " * 65536                                # 1.25 MB with no Content-Length
        r = self.c.post(f"/api/groups/{self.gid}/expenses", headers={**ALICE, "Content-Type": "application/json"},
                        content=chunks())
        self.assertEqual(r.status_code, 413)
        r = self.c.post(f"/api/groups/{self.gid}/expenses", headers={**ALICE, "Content-Type": "application/json"},
                        content=b" " * (2 << 20))
        self.assertEqual(r.status_code, 413)


class Limits(Linked):
    def test_entries_per_group(self):
        main.update_settings({"max_entries_per_group": 2}, "test")
        self.expense()
        self.expense()
        r = self.c.post(f"/api/groups/{self.gid}/expenses", json={"description": "x", "amount": 1, "paidBy": "a"},
                        headers=ALICE)
        self.assertEqual(r.status_code, 400)
        self.assertIn("already has 2 entries", r.json()["detail"])
        r = self.c.post(f"/api/groups/{self.gid}/payments", json={"fromUserId": "b", "toUserId": "a", "amount": 1},
                        headers=ALICE)
        self.assertEqual(r.status_code, 400)
        main.update_settings({"max_entries_per_group": 0}, "test")     # 0 = no limit
        self.expense()

    def test_groups_per_person(self):
        main.update_settings({"max_groups_per_person": 2}, "test")     # Ann is in House already
        self.assertEqual(self.c.post("/api/groups", json={"name": "G2", "memberIds": ["a", "x"]},
                                     headers=ALICE).status_code, 201)
        r = self.c.post("/api/groups", json={"name": "G3", "memberIds": ["x", "a"]}, headers=ALICE)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Ann is already in 2 groups", r.json()["detail"])
        g4 = self.c.post("/api/groups", json={"name": "G4", "memberIds": ["b"]}, headers=BEN).json()["id"]
        r = self.c.post(f"/api/groups/{g4}/members", json={"userId": "a"}, headers=BEN)
        self.assertEqual(r.status_code, 400)

    def test_changes_per_minute_per_login(self):
        main.update_settings({"writes_per_minute": 3}, "test")
        main.WRITES.clear()                                       # (setUp's group counted for Alice)
        for _ in range(3):
            self.expense()
        r = self.c.post(f"/api/groups/{self.gid}/expenses", json={"description": "x", "amount": 1, "paidBy": "a"},
                        headers=ALICE)
        self.assertEqual(r.status_code, 429)
        self.assertIn("Too many changes", r.json()["detail"])
        self.assertTrue(r.headers["retry-after"].isdigit())
        self.assertEqual(self.c.get(f"/api/groups/{self.gid}", headers=ALICE).status_code, 200)   # reading: fine
        r = self.c.post(f"/api/groups/{self.gid}/expenses", json={"description": "x", "amount": 1, "paidBy": "a"},
                        headers=BEN)                              # someone else: their own count
        self.assertEqual(r.status_code, 201)

    def test_limiter_window(self):
        t = [0.0]
        lim = main.WriteLimiter(lambda: 2, clock=lambda: t[0])
        self.assertIsNone(lim.check("u"))
        self.assertIsNone(lim.check("u"))
        self.assertEqual(lim.check("u"), 61)
        t[0] = 59.5
        self.assertIsNotNone(lim.check("u"))
        t[0] = 60.5
        self.assertIsNone(lim.check("u"))
        self.assertIsNone(main.WriteLimiter(lambda: 0).check("u"))    # 0 = no limit


class DisabledPeople(Linked):
    def disable(self, uid):
        with main.get_conn() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (uid,))
            conn.commit()

    def test_new_entries_leave_disabled_people_out(self):
        e = self.expense(amount=30)                               # Ann, Ben, Cat
        self.disable("c")
        self.expense(paid_by="c", status=400)
        self.expense(participants=["a", "c"], status=400)
        self.expense(splitType="custom", splits=[{"userId": "a", "amount": 20}, {"userId": "c", "amount": 10}],
                     status=400)
        e2 = self.expense(amount=10)                              # nobody picked: everyone enabled
        self.assertEqual(sorted(s["userId"] for s in e2["splits"]), ["a", "b"])
        r = self.c.post(f"/api/groups/{self.gid}/payments", json={"fromUserId": "c", "toUserId": "a", "amount": 5},
                        headers=ALICE)
        self.assertEqual(r.status_code, 400)
        # editing an entry they're already in keeps them possible
        r = self.c.put(f"/api/expenses/{e['id']}", headers=ALICE, json={
            "description": "Groceries", "amount": 33, "paidBy": "a", "participants": ["a", "b", "c"]})
        self.assertEqual(r.status_code, 200, r.text)
        e3 = self.expense(amount=8, participants=["a", "b"])
        r = self.c.put(f"/api/expenses/{e3['id']}", headers=ALICE, json={
            "description": "Groceries", "amount": 8, "paidBy": "a", "participants": ["a", "c"]})
        self.assertEqual(r.status_code, 400)                      # but not adding them to another one


class EditPayment(Linked):
    def test_change_a_payment(self):
        self.expense(amount=30)
        r = self.c.post(f"/api/groups/{self.gid}/payments", json={"fromUserId": "b", "toUserId": "a", "amount": 10},
                        headers=ALICE)
        pay = next(e for e in r.json()["expenses"] if e["splitType"] == "payment")
        self.assertEqual(self.c.put(f"/api/expenses/{pay['id']}", headers=ALICE, json={
            "description": "x", "amount": 5, "paidBy": "b"}).status_code, 400)   # not through the expense route
        r = self.c.put(f"/api/payments/{pay['id']}", headers=BEN,
                       json={"fromUserId": "c", "toUserId": "a", "amount": 7.5})
        self.assertEqual(r.status_code, 200, r.text)
        g = r.json()
        p = next(e for e in g["expenses"] if e["id"] == pay["id"])
        self.assertEqual((p["paidBy"], p["splits"][0]["userId"], p["amount"], p["description"]), ("c", "a", 7.5, "Cat paid Ann"))
        self.assertEqual({n["userId"]: n["amount"] for n in g["balances"]["net"]}, {"a": 12.5, "b": -10.0, "c": -2.5})
        ev = self.c.get("/api/events", headers=ALICE).json()[0]
        self.assertEqual(ev["type"], "payment_edited")
        self.assertEqual(ev["message"], "Ben edited a settle-up payment (Ben → Ann, $10.00) in House: amount $10.00 → "
                                        "$7.50; paid by Ben → Cat")
        for body in ({"fromUserId": "a", "toUserId": "a", "amount": 1}, {"fromUserId": "x", "toUserId": "a", "amount": 1}):
            self.assertEqual(self.c.put(f"/api/payments/{pay['id']}", headers=BEN, json=body).status_code, 400)
        expense_id = next(e["id"] for e in g["expenses"] if e["splitType"] != "payment")
        self.assertEqual(self.c.put(f"/api/payments/{expense_id}", headers=BEN,
                                    json={"fromUserId": "c", "toUserId": "a", "amount": 1}).status_code, 404)


class EditLog(Linked):
    def test_payer_split_and_date_changes_are_written_out(self):
        e = self.expense(amount=30, date="2026-01-05")
        r = self.c.put(f"/api/expenses/{e['id']}", headers=BEN, json={
            "description": "Big shop", "amount": 60, "paidBy": "b", "splitType": "custom", "date": "2026-01-06",
            "splits": [{"userId": "a", "amount": 30}, {"userId": "c", "amount": 30}]})
        self.assertEqual(r.status_code, 200, r.text)
        ev = self.c.get("/api/events", headers=ALICE).json()[0]
        self.assertEqual(ev["message"], "Ben edited 'Groceries' ($30.00) in House: 'Groceries' → 'Big shop'; amount "
                                        "$30.00 → $60.00; paid by Ann → Ben; split equally between Ann, Ben, Cat → by "
                                        "amount: Ann $30.00, Cat $30.00; date 2026-01-05 → 2026-01-06")
        r = self.c.put(f"/api/expenses/{e['id']}", headers=BEN, json={
            "description": "Big shop", "amount": 60, "paidBy": "b", "splitType": "percent", "date": "2026-01-06",
            "splits": [{"userId": "a", "percent": 50}, {"userId": "c", "percent": 50}]})
        self.assertIn("split by amount: Ann $30.00, Cat $30.00 → by percentage: Ann 50%, Cat 50%",
                      self.c.get("/api/events", headers=ALICE).json()[0]["message"])
        self.c.put(f"/api/expenses/{e['id']}", headers=BEN, json={
            "description": "Big shop", "amount": 60, "paidBy": "b", "splitType": "percent", "date": "2026-01-06",
            "splits": [{"userId": "a", "percent": 50}, {"userId": "c", "percent": 50}]})
        self.assertTrue(self.c.get("/api/events", headers=ALICE).json()[0]["message"].endswith(": nothing changed"))


if __name__ == "__main__":
    unittest.main()
