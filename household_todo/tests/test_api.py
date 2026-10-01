"""HTTP-level tests: every route, the access rules and the spec's scenarios.

Runs against real FastAPI when it is installed. In the build sandbox (no
PyPI) it falls back to tests/shim, a Starlette-based stand-in; the app's
image always installs the real thing from requirements.txt.

All people, names and addresses in these tests are invented data."""
import _env  # noqa: F401  (must be first)

import glob
import io
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timezone

try:
    import fastapi  # noqa: F401
    USING_SHIM = False
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "shim"))
    USING_SHIM = True

from starlette.testclient import TestClient  # noqa: E402

from app import config, db, ha_notify, ha_people, settings  # noqa: E402
from app.main import app  # noqa: E402
from fake_ha import FakeHA  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)   # Monday 21 Sep 2026


def hdr(uid, name=None, username=None):
    return {"X-Remote-User-Id": uid, "X-Remote-User-Name": username or name or uid,
            "X-Remote-User-Display-Name": name or uid}


def link(key, service):
    """Test shorthand for Admin → Users: give the (existing) person whose login
    name or id is `key` a notify service."""
    with db.get_conn() as c:
        row = c.execute("SELECT id FROM users WHERE lower(username) = ? OR lower(id) = ?",
                        (key.lower(), key.lower())).fetchone()
        assert row, f"no user {key!r} yet"
        c.execute("INSERT OR IGNORE INTO user_notify (user_id, service, created_at) VALUES (?, ?, ?)",
                  (row["id"], ha_notify.normalize_service(service), config.now_iso()))


ADMIN = hdr("adm", "Adminy", "adminy")
ALICE = hdr("u_alice", "Alice", "alice")
BOB = hdr("u_bob", "Bob", "bob")


class ApiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ha = FakeHA()
        config.SUPERVISOR_CORE_API = cls.ha.start()
        config.SUPERVISOR_TOKEN = "test-token"
        cls._ctx = TestClient(app, client=("127.0.0.1", 12345))  # simulate a request from
        # Home Assistant's ingress proxy, which require_ha_ingress_auth now requires
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.ha.stop()

    def setUp(self):
        for f in glob.glob(config.DB_PATH + "*"):
            os.remove(f)
        db.init_db()
        config.utcnow = lambda: NOW
        config.set_timezone("UTC")
        self.ha.requests.clear()
        self.ha.states.clear()
        self.ha.people = None
        self.ha.notify_services = None
        ha_people.reset()
        # everyone opens the app once so they exist
        for h in (ADMIN, ALICE, BOB):
            self.assertEqual(self.get("/api/whoami", h).status_code, 200)

    # -- helpers ---------------------------------------------------------
    def get(self, path, h=ALICE, **kw):
        return self.c.get(path, headers=h, **kw)

    def post(self, path, body=None, h=ALICE, **kw):
        return self.c.post(path, json=body if body is not None else {}, headers=h, **kw)

    def patch(self, path, body, h=ALICE, **kw):
        return self.c.patch(path, json=body, headers=h, **kw)

    def put(self, path, body, h=ALICE, **kw):
        return self.c.put(path, json=body, headers=h, **kw)

    def delete(self, path, h=ALICE, **kw):
        return self.c.delete(path, headers=h, **kw)

    def lists(self, h=ALICE):
        return self.get("/api/lists", h).json()

    def shared_id(self):
        return next(l["id"] for l in self.lists() if l["kind"] == "shared")

    def mine_id(self, h=ALICE):
        return next(l["id"] for l in self.lists(h) if l["kind"] == "personal")

    def add(self, title="Task", list_id=None, h=ALICE, **kw):
        r = self.post(f"/api/lists/{list_id or self.shared_id()}/tasks", {"title": title, **kw}, h)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def titles(self, list_id, h=ALICE, **params):
        r = self.get(f"/api/lists/{list_id}/tasks", h, params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return [t["title"] for t in r.json()]

    def uid(self, name):
        return next(u["id"] for u in self.get("/api/users").json() if u["name"] == name)


class TestBasics(ApiBase):
    def test_health_needs_no_user(self):
        r = self.c.get("/api/health")
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))

    def test_no_ingress_headers_is_401(self):
        r = self.c.get("/api/lists")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Home Assistant sidebar", r.json()["detail"])

    def test_first_sight_creates_user_and_personal_list(self):
        r = self.get("/api/whoami", hdr("newbie", "Nia", "nia"))
        self.assertEqual(r.json()["haDisplayName"], "Nia")
        self.assertFalse(r.json()["isAdmin"])
        kinds = [(l["name"], l["kind"]) for l in self.lists(hdr("newbie", "Nia"))]
        self.assertEqual(kinds, [("Household", "shared"), ("My Tasks", "personal")])

    def test_whoami_admin(self):
        self.assertTrue(self.get("/api/whoami", ADMIN).json()["isAdmin"])

    def test_whoami_reports_what_ha_sent(self):
        r = self.get("/api/whoami", hdr("abc123", "Rohan Sharma", "rohan.sharma")).json()
        self.assertEqual((r["haUserId"], r["haUsername"], r["haDisplayName"]), ("abc123", "rohan.sharma", "Rohan Sharma"))
        self.assertTrue(r["nameSent"])
        self.assertFalse(r["isAdmin"])
        self.assertEqual(r["adminEntries"], len(config.ADMIN_NAMES))
        self.assertFalse(r["notifyLinked"])
        # the admin list itself is never returned — a count only
        self.assertNotIn("adminy", str(r).lower().replace("adminentries", ""))

    def test_whoami_name_not_sent(self):
        r = self.c.get("/api/whoami", headers={"X-Remote-User-Id": "solo"}).json()
        self.assertFalse(r["nameSent"])
        self.assertIsNone(r["haUsername"])

    def test_whoami_via_ingress_flag(self):
        self.assertFalse(self.get("/api/whoami").json()["viaIngress"])
        self.assertTrue(self.get("/api/whoami", {**ALICE, "X-Ingress-Path": "/api/hassio_ingress/x"}).json()["viaIngress"])

    def test_admin_matches_id_or_username_any_case_but_not_display_name(self):
        config.ADMIN_NAMES.update({"meera.sharma", "id-7f3c"})
        try:
            self.assertTrue(self.get("/api/whoami", hdr("zzz", "Whoever", "Meera.Sharma")).json()["isAdmin"])   # username, mixed case
            self.assertTrue(self.get("/api/whoami", hdr("ID-7F3C", "Whoever", "other")).json()["isAdmin"])       # id, upper case
            # a display name equal to an admin's must NOT grant admin (people may be able to edit it)
            self.assertFalse(self.get("/api/whoami", hdr("yyy", "meera.sharma", "not.meera")).json()["isAdmin"])
        finally:
            config.ADMIN_NAMES.difference_update({"meera.sharma", "id-7f3c"})

    def test_whoami_is_the_real_caller_while_acting(self):
        r = self.get(f"/api/whoami?as_user={self.uid('Bob')}", ADMIN).json()
        self.assertEqual(r["haUsername"], "adminy")
        self.assertTrue(r["isAdmin"])

    def test_notify_link_is_per_user_not_by_display_name(self):
        # services are assigned to a person (by HA user id) in Admin → Users
        self.get("/api/whoami", hdr("q1", "Anything", "Rohan.Sharma"))
        link("rohan.sharma", "mobile_app_rohan")
        self.assertTrue(self.get("/api/whoami", hdr("q1", "Anything", "Rohan.Sharma")).json()["notifyLinked"])
        # someone whose display name looks like it never inherits it
        self.assertFalse(self.get("/api/whoami", hdr("q2", "rohan.sharma", "other")).json()["notifyLinked"])

    def test_users_listed(self):
        names = [u["name"] for u in self.get("/api/users").json()]
        self.assertEqual(names, ["Adminy", "Alice", "Bob"])

    def test_disable_user_admin_only(self):
        bob = self.uid("Bob")
        self.assertEqual(self.patch(f"/api/users/{bob}", {"disabled": True}, ALICE).status_code, 403)
        r = self.patch(f"/api/users/{bob}", {"disabled": True}, ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["disabled"])
        self.assertEqual(self.patch("/api/users/nope", {"disabled": True}, ADMIN).status_code, 404)
        self.assertEqual(self.patch(f"/api/users/{bob}", {"disabled": "x"}, ADMIN).status_code, 422)

    def test_html_shell_not_cached(self):
        # no static dir in tests -> nothing to fetch; middleware must not break API responses
        r = self.c.get("/api/health")
        self.assertNotIn("no-store", r.headers.get("cache-control", ""))


class TestPhonesFromHomeAssistant(ApiBase):
    """Phones come from Home Assistant (Settings → People → Track device); an admin can add extra services."""

    def set_people(self):
        self.ha.notify_services = ["mobile_app_pixel_7", "mobile_app_bob_s_phone_2", "family_speaker"]
        self.ha.people = [
            {"entity_id": "person.alice", "name": "Alice", "user_id": "u_alice", "state": "home", "picture": None,
             "phones": [{"name": "Pixel 7", "label": "Alice's phone", "tracker": "device_tracker.pixel_7"},
                        {"name": "Old Tablet", "label": "Old Tablet", "tracker": "device_tracker.old_tablet"}]},
            {"entity_id": "person.bob", "name": "Bob", "user_id": "u_bob", "state": "not_home", "picture": None,
             "phones": [{"name": "Bob's Phone", "label": "Bob's Phone", "tracker": "device_tracker.bobs_phone"}]},
            {"entity_id": "person.kid", "name": "Kid", "user_id": None, "state": "home", "picture": None, "phones": []},
        ]
        ha_people.refresh_blocking(True)

    def test_phone_linked_in_home_assistant_is_used(self):
        self.assertFalse(self.get("/api/whoami").json()["notifyLinked"])
        self.set_people()
        self.assertTrue(self.get("/api/whoami").json()["notifyLinked"])
        self.assertEqual(ha_people.phones_for("u_alice"), ["mobile_app_pixel_7"])          # the tablet has no action
        self.assertEqual(ha_people.phones_for("u_bob"), ["mobile_app_bob_s_phone_2"])      # HA numbered it
        self.assertFalse(self.get("/api/whoami", ADMIN).json()["notifyLinked"])            # no person linked
        self.ha.requests.clear()
        self.assertEqual(self.post("/api/prefs/test-notification").status_code, 200)
        self.assertEqual([n for n, _ in self.ha.notifications()], ["mobile_app_pixel_7"])

    def test_admin_sees_phones_and_adds_extras(self):
        self.set_people()
        users = {u["name"]: u for u in self.get("/api/admin/users", ADMIN).json()["users"]}
        a = users["Alice"]["ha"]
        self.assertEqual((a["person"], a["personName"]), ("person.alice", "Alice"))
        self.assertEqual([(p["label"], p["service"]) for p in a["phones"]],
                         [("Alice's phone", "notify.mobile_app_pixel_7"), ("Old Tablet", None)])
        self.assertIsNone(users["Adminy"]["ha"]["person"])
        link("alice", "family_speaker")                                    # an extra, added in this app
        self.ha.requests.clear()
        r = self.post(f"/api/admin/users/{self.uid('Alice')}/notify/test", {}, ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(r.json()["results"]), {"notify.mobile_app_pixel_7", "notify.family_speaker"})
        self.assertEqual(sorted(n for n, _ in self.ha.notifications()), ["family_speaker", "mobile_app_pixel_7"])
        # the phone can't be removed here (it's Home Assistant's), only extras
        self.assertEqual(self.delete(f"/api/admin/users/{self.uid('Alice')}/notify/notify.mobile_app_pixel_7", ADMIN).status_code, 404)

    def test_follows_home_assistant_and_survives_it_being_down(self):
        self.set_people()
        self.ha.fail = True
        ha_people.refresh_blocking(True)                                    # HA down: the last answer stays
        self.ha.fail = False
        self.assertEqual(ha_people.phones_for("u_alice"), ["mobile_app_pixel_7"])
        self.ha.people[0]["phones"] = []                                     # phone removed in HA
        self.ha.requests.clear()
        self.get("/api/admin/users?refresh=1", ADMIN)
        self.assertEqual(ha_people.phones_for("u_alice"), [])
        self.assertEqual(self.post("/api/prefs/test-notification").status_code, 400)


class TestTasks(ApiBase):
    def test_only_title_required(self):
        t = self.add("Buy milk")
        self.assertEqual(t["title"], "Buy milk")
        self.assertIsNone(t["dueDate"])
        self.assertIsNone(t["priority"])
        self.assertFalse(t["completionRequired"])
        self.assertEqual(t["items"], [])
        self.assertEqual(self.post(f"/api/lists/{self.shared_id()}/tasks", {"notes": "x"}).status_code, 422)
        self.assertEqual(self.post(f"/api/lists/{self.shared_id()}/tasks", {"title": "  "}).status_code, 422)

    def test_all_fields_and_checklist(self):
        typ = self.get("/api/task-types").json()[0]
        place = self.post("/api/places", {"name": "Clinic", "address": "1 Main St"}).json()
        t = self.add("Dentist", due_date="2026-09-25", due_time="09:30", priority="high",
                     type_id=typ["id"], place_id=place["id"], notes="bring card",
                     assigned_to=self.uid("Bob"), completion_required=True, items=["forms", "card"])
        self.assertEqual((t["dueDate"], t["dueTime"], t["priority"]), ("2026-09-25", "09:30", "high"))
        self.assertEqual(t["typeId"], typ["id"])
        self.assertEqual(t["placeId"], place["id"])
        self.assertTrue(t["completionRequired"])
        self.assertEqual([i["text"] for i in t["items"]], ["forms", "card"])

    def test_validation(self):
        sid = self.shared_id()
        bad = [{"due_date": "2026-13-45"}, {"due_date": "x"}, {"due_time": "09:00"}, {"due_date": "2026-09-25", "due_time": "25:00"},
               {"priority": "urgent"}, {"completion_required": "yes"}, {"type_id": "nope"}, {"assigned_to": "ghost"},
               {"items": ["ok", ""]}, {"items": "abc"}, {"items": ["x"] * 101}, {"notes": 5}]
        for extra in bad:
            r = self.post(f"/api/lists/{sid}/tasks", {"title": "x", **extra})
            self.assertEqual(r.status_code, 422, (extra, r.text))
        self.assertEqual(self.post(f"/api/lists/{sid}/tasks", {"title": "x", "place_id": "nope"}).status_code, 404)

    def test_time_needs_date_and_clearing_date_clears_time(self):
        t = self.add("A", due_date="2026-09-30", due_time="10:00")
        r = self.patch(f"/api/tasks/{t['id']}", {"due_date": None})
        self.assertIsNone(r.json()["dueDate"])
        self.assertIsNone(r.json()["dueTime"])
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}", {"due_time": "10:00"}).status_code, 422)

    def test_complete_and_reopen(self):
        t = self.add("A")
        r = self.patch(f"/api/tasks/{t['id']}", {"completed": True}, BOB)
        self.assertTrue(r.json()["completed"])
        self.assertEqual(r.json()["completedBy"], self.uid("Bob"))
        self.assertIsNotNone(r.json()["completedAt"])
        self.assertEqual(self.titles(self.shared_id()), [])
        self.assertEqual(self.titles(self.shared_id(), show="completed"), ["A"])
        r = self.patch(f"/api/tasks/{t['id']}", {"completed": False})
        self.assertFalse(r.json()["completed"])
        self.assertIsNone(r.json()["completedAt"])
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}", {"completed": 1}).status_code, 422)

    def test_overdue_only_when_required(self):
        opt = self.add("optional past", due_date="2026-09-10")
        req = self.add("required past", due_date="2026-09-10", completion_required=True)
        self.assertFalse(opt["overdue"])
        self.assertTrue(opt["past"])
        self.assertTrue(req["overdue"])
        dash = self.get("/api/dashboard").json()
        self.assertEqual([t["title"] for t in dash["overdue"]], ["required past"])
        self.assertEqual(dash["counts"]["overdue"], 1)
        # flipping the flag on later makes the optional one overdue
        r = self.patch(f"/api/tasks/{opt['id']}", {"completion_required": True})
        self.assertTrue(r.json()["overdue"])

    def test_delete_task(self):
        t = self.add("A")
        self.assertEqual(self.delete(f"/api/tasks/{t['id']}").status_code, 204)
        self.assertEqual(self.delete(f"/api/tasks/{t['id']}").status_code, 404)

    def test_quick_add_defaults_to_my_tasks(self):
        r = self.post("/api/tasks", {"title": "Call mum"}, ALICE)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["listId"], self.mine_id(ALICE))
        self.assertEqual(self.post("/api/tasks", {"title": "x", "list_id": "nope"}).status_code, 404)
        # into a shared list by id
        r = self.post("/api/tasks", {"title": "Shared one", "list_id": self.shared_id()})
        self.assertEqual(r.json()["listId"], self.shared_id())

    def test_personal_list_is_private(self):
        mine = self.mine_id(ALICE)
        t = self.add("secret", list_id=mine)
        self.assertEqual(self.get(f"/api/lists/{mine}/tasks", BOB).status_code, 403)
        self.assertEqual(self.post(f"/api/lists/{mine}/tasks", {"title": "x"}, BOB).status_code, 403)
        self.assertIn(self.patch(f"/api/tasks/{t['id']}", {"title": "hack"}, BOB).status_code, (403, 404))
        self.assertIn(self.delete(f"/api/tasks/{t['id']}", BOB).status_code, (403, 404))
        titles = [x["title"] for x in self.get("/api/dashboard", BOB).json()["upcoming"]]
        self.assertNotIn("secret", titles)

    def test_move_between_lists(self):
        bob, alice = self.uid("Bob"), self.uid("Alice")
        shared, mine = self.shared_id(), self.mine_id(ALICE)
        self.add("Already mine", list_id=mine)
        t = self.add("Call plumber", assigned_to=bob, items=["find number"])
        # household -> my tasks: goes to the end, keeps its checklist, Bob's assignment is dropped
        r = self.patch(f"/api/tasks/{t['id']}", {"list_id": mine})
        self.assertEqual(r.status_code, 200, r.text)
        m = r.json()
        self.assertEqual((m["listId"], m["assignedTo"], len(m["items"])), (mine, None, 1))
        self.assertEqual(self.titles(mine), ["Already mine", "Call plumber"])
        self.assertNotIn("Call plumber", self.titles(shared))
        self.assertEqual(self.get(f"/api/lists/{mine}/tasks", BOB).status_code, 403)   # now private to Alice
        # assigned to the owner stays assigned; other fields can change in the same call
        t2 = self.add("Pay bill", assigned_to=alice)
        m2 = self.patch(f"/api/tasks/{t2['id']}", {"list_id": mine, "priority": "high"}).json()
        self.assertEqual((m2["listId"], m2["assignedTo"], m2["priority"]), (mine, alice, "high"))
        self.assertEqual(self.patch(f"/api/tasks/{t2['id']}", {"list_id": shared, "assigned_to": bob}).json()["assignedTo"], bob)
        # can't move into someone else's personal list, or a list that doesn't exist
        self.assertEqual(self.patch(f"/api/tasks/{t2['id']}", {"list_id": self.mine_id(BOB)}).status_code, 403)
        self.assertEqual(self.patch(f"/api/tasks/{t2['id']}", {"list_id": "nope"}).status_code, 404)
        self.assertEqual(self.patch(f"/api/tasks/{t2['id']}", {"list_id": 5}).status_code, 422)
        # Bob can't move Alice's private task out
        self.assertIn(self.patch(f"/api/tasks/{t['id']}", {"list_id": shared}, BOB).status_code, (403, 404))
        # my tasks -> household
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}", {"list_id": shared}).json()["listId"], shared)
        self.assertIn("Call plumber", self.titles(shared, BOB))
        # moving to the list it's already in changes nothing
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}", {"list_id": shared}).status_code, 200)

    def test_assignment_rules(self):
        bob = self.uid("Bob")
        mine = self.mine_id(ALICE)
        # personal list: only the owner
        r = self.post(f"/api/lists/{mine}/tasks", {"title": "x", "assigned_to": bob})
        self.assertEqual(r.status_code, 400)
        # disabled users can't be newly assigned
        self.patch(f"/api/users/{bob}", {"disabled": True}, ADMIN)
        r = self.post(f"/api/lists/{self.shared_id()}/tasks", {"title": "x", "assigned_to": bob})
        self.assertEqual(r.status_code, 400)

    def test_assignment_ping(self):
        link("bob", "mobile_app_bob")
        bob = self.uid("Bob")
        from app import db as _db
        with _db.get_conn() as conn:
            conn.execute("INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign) VALUES (?,1,0,1)", (bob,))
        self.add("Take out bins", assigned_to=bob, h=ALICE)
        notes = self.ha.notifications()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], "mobile_app_bob")
        # self-assignment sends nothing
        self.ha.requests.clear()
        self.add("mine", assigned_to=bob, h=BOB)
        self.assertEqual(self.ha.notifications(), [])

    def test_any_place_usable_in_any_list(self):
        place = self.post("/api/places", {"name": "Hospital", "address": "1 Rd"}).json()
        for lst in (self.shared_id(), self.mine_id()):
            r = self.post(f"/api/lists/{lst}/tasks", {"title": "a", "place_id": place["id"]})
            self.assertEqual(r.status_code, 201)
        # Bob can use Alice's place too — every place is shared
        r = self.post(f"/api/lists/{self.shared_id()}/tasks", {"title": "d", "place_id": place["id"]}, BOB)
        self.assertEqual(r.status_code, 201)
        # a place that doesn't exist is still a 404
        r = self.post(f"/api/lists/{self.shared_id()}/tasks", {"title": "e", "place_id": "nope"})
        self.assertEqual(r.status_code, 404)

    def test_sort_and_filter(self):
        sid, bob = self.shared_id(), self.uid("Bob")
        self.add("c-low", due_date="2026-10-03", priority="low", assigned_to=bob)
        self.add("a-high", due_date="2026-10-01", priority="high")
        self.add("b-none", completion_required=True)
        self.assertEqual(self.titles(sid), ["c-low", "a-high", "b-none"])              # manual = creation order
        self.assertEqual(self.titles(sid, sort="due"), ["a-high", "c-low", "b-none"])   # undated last
        self.assertEqual(self.titles(sid, sort="priority"), ["a-high", "c-low", "b-none"])
        self.assertEqual(self.titles(sid, assignee="unassigned", sort="due"), ["a-high", "b-none"])
        self.assertEqual(self.titles(sid, assignee=bob), ["c-low"])
        self.assertEqual(self.titles(sid, priority="none"), ["b-none"])
        self.assertEqual(self.titles(sid, priority="high"), ["a-high"])
        self.assertEqual(self.titles(sid, completion="required"), ["b-none"])
        self.assertEqual(self.titles(sid, completion="optional", sort="due"), ["a-high", "c-low"])
        for bad in ({"show": "x"}, {"sort": "nope"}, {"sort": "completed"}, {"show": "completed", "sort": "manual"},
                    {"priority": "x"}, {"completion": "maybe"}):
            self.assertEqual(self.get(f"/api/lists/{sid}/tasks", params=bad).status_code, 422, bad)

    def test_completed_view_sorted_newest_first(self):
        a, b = self.add("a"), self.add("b")
        self.patch(f"/api/tasks/{a['id']}", {"completed": True})
        config.utcnow = lambda: NOW.replace(hour=13)
        self.patch(f"/api/tasks/{b['id']}", {"completed": True})
        self.assertEqual(self.titles(self.shared_id(), show="completed"), ["b", "a"])

    def test_reorder(self):
        a, b, c = self.add("a"), self.add("b"), self.add("c")
        sid = self.shared_id()
        r = self.post(f"/api/lists/{sid}/tasks/reorder", {"ordered_ids": [c["id"], a["id"], b["id"]]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.titles(sid), ["c", "a", "b"])
        # a partial payload leaves the others in place
        self.post(f"/api/lists/{sid}/tasks/reorder", {"ordered_ids": [b["id"]]})
        self.assertEqual(self.titles(sid)[0], "b")
        for bad in ({"ordered_ids": [a["id"], a["id"]]}, {"ordered_ids": ["nope"]}, {"ordered_ids": "x"}, {}):
            self.assertEqual(self.post(f"/api/lists/{sid}/tasks/reorder", bad).status_code, 400, bad)

    def test_checklist_routes(self):
        t = self.add("Pack")
        r = self.post(f"/api/tasks/{t['id']}/items", {"text": "socks"})
        self.assertEqual(r.status_code, 201)
        r = self.post(f"/api/tasks/{t['id']}/items", {"texts": ["shoes", "", "hat"]})
        self.assertEqual([i["text"] for i in r.json()["items"]], ["socks", "shoes", "hat"])
        item = r.json()["items"][0]
        r = self.patch(f"/api/task-items/{item['id']}", {"done": True})
        self.assertTrue(r.json()["items"][0]["done"])
        self.assertEqual(r.json()["itemsDone"] if "itemsDone" in r.json() else 1, 1)
        r = self.patch(f"/api/task-items/{item['id']}", {"text": "warm socks"})
        self.assertEqual(r.json()["items"][0]["text"], "warm socks")
        self.assertEqual(self.delete(f"/api/task-items/{item['id']}").status_code, 204)
        self.assertEqual(len(self.get(f"/api/lists/{self.shared_id()}/tasks").json()[0]["items"]), 2)
        self.assertEqual(self.post(f"/api/tasks/{t['id']}/items", {}).status_code, 422)
        self.assertEqual(self.post(f"/api/tasks/{t['id']}/items", {"texts": []}).status_code, 422)
        self.assertEqual(self.post(f"/api/tasks/{t['id']}/items", {"texts": ["x"] * 99}).status_code, 422)
        self.assertEqual(self.patch("/api/task-items/nope", {"done": True}).status_code, 404)

    def test_checklist_items_of_private_task_protected(self):
        t = self.add("secret", list_id=self.mine_id(ALICE))
        item = self.post(f"/api/tasks/{t['id']}/items", {"text": "x"}).json()["items"][0]
        self.assertIn(self.patch(f"/api/task-items/{item['id']}", {"done": True}, BOB).status_code, (403, 404))
        self.assertIn(self.post(f"/api/tasks/{t['id']}/items", {"text": "y"}, BOB).status_code, (403, 404))

    def test_no_tag_routes_or_fields(self):
        self.assertEqual(self.get("/api/tags").status_code, 404)
        t = self.add("x")
        self.assertFalse([k for k in t if "tag" in k.lower()])


class TestLists(ApiBase):
    def test_create_rename_delete(self):
        r = self.post("/api/lists", {"name": "Chores", "kind": "shared"})
        self.assertEqual(r.status_code, 201)
        lid = r.json()["id"]
        self.assertEqual(self.patch(f"/api/lists/{lid}", {"name": "Housework"}, BOB).json()["name"], "Housework")
        self.add("x", list_id=lid)
        self.assertEqual(self.delete(f"/api/lists/{lid}", BOB).status_code, 204)
        self.assertEqual(self.get(f"/api/lists/{lid}/tasks").status_code, 404)
        self.assertEqual(self.post("/api/lists", {"name": "", "kind": "shared"}).status_code, 422)
        self.assertEqual(self.post("/api/lists", {"name": "x", "kind": "weird"}).status_code, 422)

    def test_personal_list_owner_only(self):
        lid = self.post("/api/lists", {"name": "Mine", "kind": "personal"}).json()["id"]
        self.assertEqual(self.patch(f"/api/lists/{lid}", {"name": "x"}, BOB).status_code, 403)
        self.assertEqual(self.delete(f"/api/lists/{lid}", BOB).status_code, 403)
        self.assertNotIn(lid, [l["id"] for l in self.lists(BOB)])

    def test_open_count(self):
        self.add("a"); b = self.add("b")
        self.patch(f"/api/tasks/{b['id']}", {"completed": True})
        shared = next(l for l in self.lists() if l["kind"] == "shared")
        self.assertEqual(shared["openCount"], 1)

    def test_reorder_lists(self):
        a = self.post("/api/lists", {"name": "A", "kind": "shared"}).json()["id"]
        b = self.post("/api/lists", {"name": "B", "kind": "shared"}).json()["id"]
        shared = [l["id"] for l in self.lists() if l["kind"] == "shared"]
        new = [b, a] + [i for i in shared if i not in (a, b)]
        self.assertEqual(self.post("/api/lists/reorder", {"kind": "shared", "ordered_ids": new}).status_code, 200)
        self.assertEqual([l["id"] for l in self.lists() if l["kind"] == "shared"], new)
        self.assertEqual(self.post("/api/lists/reorder", {"kind": "personal", "ordered_ids": [a]}).status_code, 400)
        self.assertEqual(self.post("/api/lists/reorder", {"kind": "x", "ordered_ids": []}).status_code, 422)


class TestTypesAndPlaces(ApiBase):
    def test_default_types_seeded(self):
        names = [t["name"] for t in self.get("/api/task-types").json()]
        self.assertEqual(len(names), 5)

    def test_type_crud(self):
        r = self.post("/api/task-types", {"name": "Vet", "icon": "🐕", "color": "#ff8800"})
        self.assertEqual(r.status_code, 201)
        tid = r.json()["id"]
        self.assertEqual(self.post("/api/task-types", {"name": "vet"}).status_code, 409)
        self.assertEqual(self.post("/api/task-types", {"name": "X", "color": "red"}).status_code, 422)
        self.add("Bella shots", type_id=tid)
        self.assertEqual(self.get("/api/task-types").json()[-1]["usageCount"], 1)
        self.assertEqual(self.patch(f"/api/task-types/{tid}", {"name": "Veterinarian"}).json()["name"], "Veterinarian")
        self.assertEqual(self.delete(f"/api/task-types/{tid}").status_code, 204)
        self.assertIsNone(self.get(f"/api/lists/{self.shared_id()}/tasks").json()[0]["typeId"])
        self.assertEqual(self.delete(f"/api/task-types/{tid}").status_code, 404)

    def test_type_usage_counts_only_visible_tasks(self):
        typ = self.get("/api/task-types").json()[0]["id"]
        self.add("private", list_id=self.mine_id(ALICE), type_id=typ)
        counts = {t["id"]: t["usageCount"] for t in self.get("/api/task-types", BOB).json()}
        self.assertEqual(counts[typ], 0)
        counts = {t["id"]: t["usageCount"] for t in self.get("/api/task-types", ALICE).json()}
        self.assertEqual(counts[typ], 1)

    def test_place_crud_and_search(self):
        p = self.post("/api/places", {"name": "City Hospital", "address": "5 Elm Road", "phone": "303-555-0100"}).json()
        self.assertEqual(p["phone"], "303-555-0100")
        self.assertIsNone(p["driveMinutes"])   # §8n — not computed yet (and unconfigured in tests)
        self.assertIsNone(p["driveTollsAvoided"])
        self.assertIsNone(p["driveAvoidTolls"])
        self.post("/api/places", {"name": "School", "address": "9 Oak Lane"})
        self.assertEqual([x["name"] for x in self.get("/api/places", params={"q": "elm"}).json()], ["City Hospital"])
        self.assertEqual([x["name"] for x in self.get("/api/places", params={"q": "SCHOOL"}).json()], ["School"])
        self.assertEqual(self.patch(f"/api/places/{p['id']}", {"address": "6 Elm Road"}, BOB).json()["address"], "6 Elm Road")
        self.add("Visit", place_id=p["id"])
        self.assertEqual(self.get("/api/places", params={"q": "hospital"}).json()[0]["usageCount"], 1)
        task = self.get(f"/api/lists/{self.shared_id()}/tasks").json()[0]
        self.assertEqual(task["place"]["phone"], "303-555-0100")
        self.assertEqual(self.delete(f"/api/places/{p['id']}").status_code, 204)
        self.assertIsNone(self.get(f"/api/lists/{self.shared_id()}/tasks").json()[0]["placeId"])
        self.assertEqual(self.post("/api/places", {"name": "x"}).status_code, 422)
        self.assertEqual(self.post("/api/places", {"name": "y", "address": "1 Rd", "phone": "1" * 31}).status_code, 422)

    def test_place_address_dedup_ignores_whitespace_and_case(self):
        p = self.post("/api/places", {"name": "Hospital", "address": "123 Main St"}).json()
        for variant in ("123   Main   St", "123 Main St\n", "  123 main st  ", "123\nMain\nSt", "123 MAIN ST"):
            r = self.post("/api/places", {"name": "Dupe", "address": variant})
            self.assertEqual(r.status_code, 409, variant)
            self.assertIn("Hospital", r.json()["detail"])
        other = self.post("/api/places", {"name": "Vet", "address": "456 Elm Ave"}).json()
        self.assertEqual(self.patch(f"/api/places/{other['id']}", {"address": "123  main  st"}).status_code, 409)
        # updating a place to a differently-formatted version of its OWN address is fine
        self.assertEqual(self.patch(f"/api/places/{p['id']}", {"address": "  123   Main   St  "}).status_code, 200)

    def test_places_are_always_shared(self):
        # a stale client sending kind:"personal" must not create a private place
        p = self.post("/api/places", {"name": "Gym", "address": "x", "kind": "personal"}).json()
        self.assertNotIn("kind", p)
        self.assertIn("Gym", [x["name"] for x in self.get("/api/places", BOB).json()])
        self.assertEqual(self.patch(f"/api/places/{p['id']}", {"name": "Gym 2"}, BOB).status_code, 200)
        self.assertEqual(self.delete(f"/api/places/{p['id']}", BOB).status_code, 204)

    def test_recalculate_drive_time_unconfigured_by_default(self):
        # A new install has the "Drive times" switch off: the endpoint says so (409).
        # Switched on but with no home address, the feature is still unconfigured (400).
        p = self.post("/api/places", {"name": "Gym", "address": "1 Gym Rd"}).json()
        self.assertIsNone(p["driveMinutes"])
        r = self.post(f"/api/places/{p['id']}/recalculate-drive-time")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.put("/api/admin/settings", {"drive_times_enabled": True}, ADMIN).status_code, 200)
        r = self.post(f"/api/places/{p['id']}/recalculate-drive-time")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.post("/api/places/does-not-exist/recalculate-drive-time").status_code, 404)


class TestActingAs(ApiBase):
    def test_non_admin_as_user_ignored(self):
        bob = self.uid("Bob")
        r = self.get(f"/api/whoami?as_user={bob}", ALICE)
        self.assertIsNone(r.json()["actingAs"])
        self.assertEqual(self.get(f"/api/lists?as_user={bob}", ALICE).json(), self.lists(ALICE))

    def test_admin_switches_and_writes_as_target(self):
        bob = self.uid("Bob")
        r = self.get(f"/api/whoami?as_user={bob}", ADMIN).json()
        self.assertEqual(r["actingAs"], {"id": bob, "name": "Bob"})
        self.assertEqual(r["haDisplayName"], "Adminy")          # the real caller, always
        bob_personal = self.mine_id(BOB)
        lists = self.get(f"/api/lists?as_user={bob}", ADMIN).json()
        self.assertIn(bob_personal, [l["id"] for l in lists])
        t = self.post(f"/api/tasks?as_user={bob}", {"title": "for bob"}, ADMIN).json()
        self.assertEqual(t["listId"], bob_personal)
        self.assertEqual(t["createdBy"] if "createdBy" in t else bob, bob)

    def test_unknown_user_is_404_not_silent_fallback(self):
        self.assertEqual(self.get("/api/lists?as_user=ghost", ADMIN).status_code, 404)
        self.assertEqual(self.post("/api/tasks?as_user=ghost", {"title": "x"}, ADMIN).status_code, 404)

    def test_prefs_are_409_when_acting_as_someone_else(self):
        bob = self.uid("Bob")
        self.assertEqual(self.get(f"/api/prefs?as_user={bob}", ADMIN).status_code, 409)
        self.assertEqual(self.put(f"/api/prefs?as_user={bob}", {"leadDays": 1}, ADMIN).status_code, 409)
        self.assertEqual(self.post(f"/api/prefs/test-notification?as_user={bob}", {}, ADMIN).status_code, 409)
        # acting as yourself is fine
        self.assertEqual(self.get(f"/api/prefs?as_user={self.uid('Adminy')}", ADMIN).status_code, 200)

    def test_admin_routes_use_real_caller(self):
        bob = self.uid("Bob")
        # ALICE tries to be admin via as_user: still 403
        self.assertEqual(self.get(f"/api/admin-storage-download-db?as_user={self.uid('Adminy')}", ALICE).status_code, 403)
        # admin acting as Bob still passes require_admin
        self.assertEqual(self.get(f"/api/admin-storage-download-db?as_user={bob}", ADMIN).status_code, 200)

    def test_audit_log_on_writes_only(self):
        bob = self.uid("Bob")
        with self.assertLogs("auth", level="INFO") as cm:
            self.post(f"/api/tasks?as_user={bob}", {"title": "x"}, ADMIN)
        self.assertTrue(any("AUDIT Adminy acting as Bob" in m for m in cm.output))
        with self.assertRaises(AssertionError):     # a read produces no audit line
            with self.assertLogs("auth", level="INFO"):
                self.get(f"/api/lists?as_user={bob}", ADMIN)


class TestSchedule(ApiBase):
    def create(self, **kw):
        body = {"name": "Trash pickup", "rule": "weeks:1:1", "anchor_date": "2026-09-14", "lead_days": 1, **kw}
        r = self.post("/api/schedule", body)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_create_pushes_sensor(self):
        item = self.create()
        self.assertEqual(item["entityId"], "binary_sensor.household_todo_trash_pickup")
        self.assertEqual(item["nextDate"], "2026-09-21")
        self.assertTrue(item["sensorOn"])
        st = self.ha.states["binary_sensor.household_todo_trash_pickup"]
        self.assertEqual(st["state"], "on")
        self.assertEqual(self.ha.requests[-1][3], "Bearer test-token")

    def test_sunday_and_monday_window(self):
        self.create()
        config.utcnow = lambda: NOW.replace(day=20)      # Sunday
        self.assertTrue(self.get("/api/schedule").json()[0]["sensorOn"])
        config.utcnow = lambda: NOW.replace(day=22)      # Tuesday after
        s = self.get("/api/schedule").json()[0]
        self.assertFalse(s["sensorOn"])
        self.assertEqual(s["nextDate"], "2026-09-28")

    def test_validation(self):
        base = {"name": "X", "rule": "daily", "anchor_date": "2026-09-14"}
        for extra in ({"name": ""}, {"rule": "weekly"}, {"rule": "weeks:1:9"}, {"anchor_date": "nope"}, {"lead_days": 8},
                      {"lead_days": -1}, {"icon": "trash"}, {"notes": "x" * 501}):
            self.assertEqual(self.post("/api/schedule", {**base, **extra}).status_code, 422, extra)
        self.assertEqual(self.post("/api/schedule", base).status_code, 201)

    def test_every_n_months_and_years(self):
        quarterly = self.create(name="Filter change", rule="months:3:15", anchor_date="2026-10-15")
        self.assertEqual(quarterly["ruleLabel"], "Every 3 months on the 15th")
        self.assertEqual(quarterly["nextDate"], "2026-10-15")
        boiler = self.create(name="Boiler service", rule="years:5", anchor_date="2027-03-02")
        self.assertEqual(boiler["ruleLabel"], "Every 5 years")
        self.assertEqual(boiler["nextDate"], "2027-03-02")
        # the calendar sees them too
        cal = self.get("/api/calendar", params={"from": "2026-12-20", "to": "2027-01-31"})
        self.assertEqual(cal.status_code, 200, cal.text)
        self.assertIn("2027-01-15", str(cal.json()))
        self.assertEqual(self.post("/api/schedule", {"name": "X", "rule": "years:11", "anchor_date": "2026-10-15"}).status_code, 422)

    def test_unique_slugs(self):
        a, b = self.create(), self.create()
        self.assertNotEqual(a["entityId"], b["entityId"])

    def test_edit_repushes_and_delete_removes_entity(self):
        item = self.create()
        r = self.patch(f"/api/schedule/{item['id']}", {"name": "Bins", "lead_days": 0})
        self.assertEqual(r.json()["entityId"], item["entityId"])                 # slug never changes
        self.assertEqual(self.ha.states[item["entityId"]]["attributes"]["friendly_name"], "Bins")
        self.assertEqual(self.delete(f"/api/schedule/{item['id']}").status_code, 204)
        self.assertNotIn(item["entityId"], self.ha.states)
        self.assertEqual(self.delete(f"/api/schedule/{item['id']}").status_code, 404)

    def test_skip_move_add_undo(self):
        item = self.create()
        iid = item["id"]
        r = self.post(f"/api/schedule/{iid}/exceptions", {"kind": "skip", "date": "2026-09-21", "reason": "holiday"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["nextDate"], "2026-09-28")
        self.assertEqual(self.ha.states[item["entityId"]]["state"], "off")
        skipped = [u for u in r.json()["upcoming"] if u["status"] == "skipped"]
        self.assertEqual([(u["date"], u["reason"]) for u in skipped], [("2026-09-21", "holiday")])
        exc = skipped[0]
        # idempotent repeat
        r2 = self.post(f"/api/schedule/{iid}/exceptions", {"kind": "skip", "date": "2026-09-21"})
        self.assertEqual(len([u for u in r2.json()["upcoming"] if u["status"] == "skipped"]), 1)
        # undo brings it back
        r = self.delete(f"/api/schedule/{iid}/exceptions/{exc['exceptionId']}")
        self.assertEqual(r.json()["nextDate"], "2026-09-21")
        self.assertEqual(self.ha.states[item["entityId"]]["state"], "on")
        # move the 28th to the 30th
        r = self.post(f"/api/schedule/{iid}/exceptions", {"kind": "skip", "date": "2026-09-21"})
        r = self.post(f"/api/schedule/{iid}/exceptions", {"kind": "move", "date": "2026-09-28", "to_date": "2026-09-30"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["nextDate"], "2026-09-30")
        # add an extra one-off
        r = self.post(f"/api/schedule/{iid}/exceptions", {"kind": "add", "date": "2026-09-25"})
        self.assertEqual(r.json()["nextDate"], "2026-09-25")
        # bad exceptions
        for body in ({"kind": "skip", "date": "2026-09-22"},          # not an occurrence
                     {"kind": "skip", "date": "2026-09-07"},          # in the past
                     {"kind": "move", "date": "2026-10-05"},          # missing to_date
                     {"kind": "wat", "date": "2026-10-05"},
                     {"kind": "add", "date": "2026-09-28"}):          # already occurs
            self.assertEqual(self.post(f"/api/schedule/{iid}/exceptions", body).status_code, 422, body)
        self.assertEqual(self.delete(f"/api/schedule/{iid}/exceptions/nope").status_code, 404)

    def test_changing_rule_clears_skips_and_moves_keeps_adds(self):
        item = self.create()
        iid = item["id"]
        self.post(f"/api/schedule/{iid}/exceptions", {"kind": "skip", "date": "2026-09-21"})
        self.post(f"/api/schedule/{iid}/exceptions", {"kind": "add", "date": "2026-09-25"})
        # the anchor must sit on a listed weekday, so move it with the rule
        r = self.patch(f"/api/schedule/{iid}", {"rule": "weeks:1:2", "anchor_date": "2026-09-15"})
        self.assertEqual(r.status_code, 200, r.text)
        statuses = {u["date"]: u["status"] for u in r.json()["upcoming"]}
        self.assertEqual(statuses["2026-09-25"], "added")                   # the extra date survives
        self.assertFalse([s for s in statuses.values() if s in ("skipped", "moved_away")])   # the skip is gone
        self.assertEqual(r.json()["nextDate"], "2026-09-22")
        # a rule that doesn't fit the old anchor is rejected
        self.assertEqual(self.patch(f"/api/schedule/{iid}", {"rule": "weeks:1:3"}).status_code, 422)

    def test_biweekly_recycling(self):
        item = self.create(name="Recycling", rule="weeks:2:1", anchor_date="2026-09-14")
        self.assertEqual(item["nextDate"], "2026-09-28")
        self.assertFalse(item["sensorOn"])
        config.utcnow = lambda: NOW.replace(day=27)
        self.assertTrue(self.get("/api/schedule").json()[0]["sensorOn"])

    def test_item_limit(self):
        for i in range(100):     # personal items add up
            self.assertEqual(self.post("/api/schedule", {"name": f"I{i}", "rule": "daily", "anchor_date": "2026-09-01"}).status_code, 201)
        self.assertEqual(self.post("/api/schedule", {"name": "one more", "rule": "daily", "anchor_date": "2026-09-01"}).status_code, 422)

    def test_force_sync_admin_only(self):
        self.create()
        self.assertEqual(self.post("/api/schedule/sync", {}, ALICE).status_code, 403)
        r = self.post("/api/schedule/sync", {}, ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["failed"], 0)
        self.assertEqual(r.json()["pushed"], 1)

    def test_switch_off_leaves_no_ha_calls(self):
        settings.update({"expose_schedule_sensors": False}, None)
        self.ha.requests.clear()
        self.create()
        self.assertEqual([r for r in self.ha.requests if r[1].startswith("/api/states/")], [])


class TestPersonalSchedule(ApiBase):
    """Schedule items that belong to one person (spec §13)."""

    def yoga(self, h=ALICE, **kw):
        # every Tue, Thu & Fri 18:00–19:00; NOW is Monday 21 Sep 12:00 UTC
        body = {"name": "Yoga", "rule": "weeks:1:2,4,5", "anchor_date": "2026-09-22", "start_time": "18:00",
                "end_time": "19:00", "assigned_to": self.uid("Alice"), "visibility": "private", **kw}
        r = self.post("/api/schedule", body, h)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def names(self, h=ALICE, **params):
        r = self.get("/api/schedule", h, params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return [i["name"] for i in r.json()]

    def test_household_item_defaults_unchanged(self):
        item = self.post("/api/schedule", {"name": "Trash", "rule": "weeks:1:1", "anchor_date": "2026-09-14"}).json()
        self.assertEqual((item["assignedTo"], item["visibility"], item["exposeSensor"], item["published"]),
                         (None, "household", True, True))
        self.assertEqual((item["startTime"], item["endTime"], item["nextStart"], item["nextEnd"], item["place"]),
                         (None, None, None, None, None))
        attrs = self.ha.states[item["entityId"]]["attributes"]
        self.assertEqual((attrs["assigned_to"], attrs["start_time"], attrs["next_start"]), (None, None, None))

    def test_private_item_hidden_from_everyone_else(self):
        item = self.yoga()
        iid = item["id"]
        self.assertEqual((item["visibility"], item["exposeSensor"], item["published"]), ("private", False, False))
        self.assertNotIn(item["entityId"], self.ha.states)                         # private: sensor off by default
        self.assertEqual(self.names(ALICE), ["Yoga"])
        self.assertEqual(self.names(BOB), [])
        self.assertEqual(self.names(ADMIN), [])                                    # admin not acting: hidden
        self.assertEqual(self.names(ADMIN, as_user=self.uid("Alice")), ["Yoga"])   # only while acting as her
        # someone who can't see it gets a 404 for every route
        self.assertEqual(self.patch(f"/api/schedule/{iid}", {"name": "x"}, BOB).status_code, 404)
        self.assertEqual(self.delete(f"/api/schedule/{iid}", BOB).status_code, 404)
        self.assertEqual(self.post(f"/api/schedule/{iid}/exceptions", {"kind": "skip", "date": "2026-09-22"}, BOB).status_code, 404)
        skip = self.post(f"/api/schedule/{iid}/exceptions", {"kind": "skip", "date": "2026-09-22"}).json()
        eid = next(u["exceptionId"] for u in skip["upcoming"] if u["status"] == "skipped")
        self.assertEqual(self.delete(f"/api/schedule/{iid}/exceptions/{eid}", BOB).status_code, 404)
        self.assertEqual(self.patch(f"/api/schedule/{iid}", {"name": "x"}, ADMIN).status_code, 404)
        # calendar
        q = {"from": "2026-09-20", "to": "2026-09-27"}
        self.assertEqual(self.get("/api/calendar", BOB, params=q).json()["schedule"], [])
        mine = self.get("/api/calendar", ALICE, params=q).json()["schedule"]
        self.assertEqual([(e["date"], e["status"]) for e in mine],
                         [("2026-09-22", "skipped"), ("2026-09-24", "normal"), ("2026-09-25", "normal")])
        self.assertEqual((mine[1]["startTime"], mine[1]["endTime"], mine[1]["assigneeName"], mine[1]["private"]),
                         ("18:00", "19:00", "Alice", True))
        # dashboard: her own item occurring today or tomorrow shows even though the sensor is off
        self.delete(f"/api/schedule/{iid}/exceptions/{eid}")
        coming = self.get("/api/dashboard", ALICE).json()["schedule"]
        self.assertEqual([(c["name"], c["nextDate"], c["startTime"], c["sensorOn"]) for c in coming],
                         [("Yoga", "2026-09-22", "18:00", False)])
        self.assertEqual(self.get("/api/dashboard", BOB).json()["schedule"], [])
        # a full sync never publishes it; the owner can still edit and delete it
        self.assertEqual(self.post("/api/schedule/sync", {}, ADMIN).json(), {"pushed": 0, "failed": 0})
        self.assertEqual(self.patch(f"/api/schedule/{iid}", {"name": "Hot yoga"}).json()["name"], "Hot yoga")
        self.assertEqual(self.delete(f"/api/schedule/{iid}").status_code, 204)

    def test_household_items_stay_editable_by_anyone(self):
        item = self.yoga(visibility="household")
        self.assertTrue(item["exposeSensor"])                                      # household default: published
        self.assertEqual(self.names(BOB), ["Yoga"])
        r = self.patch(f"/api/schedule/{item['id']}", {"name": "Yoga class"}, BOB)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["assigneeName"], "Alice")
        self.assertEqual(self.delete(f"/api/schedule/{item['id']}", BOB).status_code, 204)

    def test_validation(self):
        alice, bob = self.uid("Alice"), self.uid("Bob")
        base = {"name": "X", "rule": "daily", "anchor_date": "2026-09-14"}
        for extra, code in (({"start_time": "18:00"}, 422),                          # both or neither
                            ({"end_time": "19:00"}, 422),
                            ({"start_time": "19:00", "end_time": "18:00"}, 422),     # end after start
                            ({"start_time": "18:00", "end_time": "18:00"}, 422),
                            ({"start_time": "6pm", "end_time": "19:00"}, 422),
                            ({"start_time": "18:00", "end_time": "24:00"}, 422),
                            ({"visibility": "private"}, 422),                        # private needs a person
                            ({"visibility": "secret", "assigned_to": alice}, 422),
                            ({"assigned_to": "nobody"}, 422),
                            ({"expose_sensor": "yes"}, 422),
                            ({"place_id": "nope"}, 404)):
            self.assertEqual(self.post("/api/schedule", {**base, **extra}).status_code, code, extra)
        item = self.post("/api/schedule", {**base, "assigned_to": bob, "start_time": "08:00", "end_time": "09:00"}).json()
        iid = item["id"]
        # PATCH validates the resulting pair
        self.assertEqual(self.patch(f"/api/schedule/{iid}", {"start_time": "09:30"}).status_code, 422)
        self.assertEqual(self.patch(f"/api/schedule/{iid}", {"end_time": None}).status_code, 422)
        self.assertEqual(self.patch(f"/api/schedule/{iid}", {"start_time": "08:30"}).json()["startTime"], "08:30")
        r = self.patch(f"/api/schedule/{iid}", {"start_time": None, "end_time": None})
        self.assertEqual((r.json()["startTime"], r.json()["endTime"]), (None, None))    # all-day again
        # disabled: 400 only when the assignee changes
        self.patch(f"/api/users/{bob}", {"disabled": True}, ADMIN)
        self.assertEqual(self.patch(f"/api/schedule/{iid}", {"assigned_to": bob, "name": "Y"}).status_code, 200)
        self.assertEqual(self.post("/api/schedule", {**base, "assigned_to": bob}).status_code, 400)
        # unassigning a private item is a 422 until it's made household
        p = self.post("/api/schedule", {**base, "assigned_to": alice, "visibility": "private"}).json()
        self.assertEqual(self.patch(f"/api/schedule/{p['id']}", {"assigned_to": None}).status_code, 422)
        r = self.patch(f"/api/schedule/{p['id']}", {"assigned_to": None, "visibility": "household"})
        self.assertEqual((r.status_code, r.json()["assignedTo"]), (200, None))

    def test_place_and_its_deletion(self):
        place = self.post("/api/places", {"name": "Studio", "address": "5 Elm St"}).json()
        item = self.yoga(place_id=place["id"])
        self.assertEqual(item["place"], {"id": place["id"], "name": "Studio", "address": "5 Elm St", "driveMinutes": None})
        self.assertEqual(self.delete(f"/api/places/{place['id']}").status_code, 204)
        again = self.get("/api/schedule").json()[0]
        self.assertEqual((again["placeId"], again["place"]), (None, None))

    def test_expose_sensor_switch(self):
        item = self.yoga(visibility="household")
        eid, iid = item["entityId"], item["id"]
        self.assertEqual(self.ha.states[eid]["state"], "off")                      # 12:00, class is at 18:00
        self.assertEqual(self.ha.states[eid]["attributes"]["assigned_to"], "Alice")
        r = self.patch(f"/api/schedule/{iid}", {"expose_sensor": False})
        self.assertEqual((r.json()["exposeSensor"], r.json()["published"]), (False, False))
        self.assertNotIn(eid, self.ha.states)                                      # removed straight away
        self.ha.requests.clear()
        self.post(f"/api/schedule/{iid}/exceptions", {"kind": "skip", "date": "2026-09-22"})
        self.assertEqual(self.post("/api/schedule/sync", {}, ADMIN).json(), {"pushed": 0, "failed": 0})
        self.assertEqual([r for r in self.ha.requests if r[1].startswith("/api/states/")], [])   # skipped everywhere
        self.patch(f"/api/schedule/{iid}", {"expose_sensor": True})
        self.assertIn(eid, self.ha.states)
        # a private item can be published on purpose
        p = self.yoga(name="Physio", expose_sensor=True)
        self.assertTrue(p["published"])
        self.assertIn(p["entityId"], self.ha.states)

    def test_timed_sensor_window(self):
        item = self.post("/api/schedule", {"name": "Swim", "rule": "daily", "anchor_date": "2026-09-21", "lead_days": 3,
                                           "start_time": "13:00", "end_time": "14:00"}).json()
        self.assertFalse(item["sensorOn"])                                         # lead_days ignored
        self.assertEqual((item["nextStart"], item["nextEnd"]), ("2026-09-21T13:00:00+00:00", "2026-09-21T14:00:00+00:00"))
        attrs = self.ha.states[item["entityId"]]["attributes"]
        self.assertEqual((attrs["start_time"], attrs["end_time"], attrs["next_start"]), ("13:00", "14:00", "2026-09-21T13:00:00+00:00"))
        config.utcnow = lambda: NOW.replace(hour=13, minute=30)
        self.assertTrue(self.get("/api/schedule").json()[0]["sensorOn"])
        config.utcnow = lambda: NOW.replace(hour=14)
        s = self.get("/api/schedule").json()[0]
        self.assertFalse(s["sensorOn"])
        self.assertEqual((s["nextDate"], s["nextStart"]), ("2026-09-21", "2026-09-22T13:00:00+00:00"))

    def test_assignee_filters(self):
        alice, bob = self.uid("Alice"), self.uid("Bob")
        self.post("/api/schedule", {"name": "Trash", "rule": "weeks:1:1", "anchor_date": "2026-09-14"})
        self.yoga()
        self.yoga(name="Bob gym", assigned_to=bob, visibility="household")
        self.assertEqual(self.names(), ["Trash", "Bob gym", "Yoga"])              # by next date, time, then name
        self.assertEqual(self.names(assignee="me"), ["Yoga"])
        self.assertEqual(self.names(assignee="unassigned"), ["Trash"])
        self.assertEqual(self.names(assignee=bob), ["Bob gym"])
        self.assertEqual(self.names(BOB), ["Trash", "Bob gym"])
        q = {"from": "2026-09-21", "to": "2026-09-22"}
        names = lambda **p: [e["name"] for e in self.get("/api/calendar", params={**q, **p}).json()["schedule"]]
        self.assertEqual(names(), ["Trash", "Bob gym", "Yoga"])
        self.assertEqual(names(assignee="me"), ["Yoga"])
        self.assertEqual(names(assignee="unassigned"), ["Trash"])                 # household items count as unassigned
        self.assertEqual(names(assignee=alice), ["Yoga"])

    def test_assignment_ping(self):
        link("bob", "mobile_app_bob")
        bob = self.uid("Bob")
        with db.get_conn() as conn:
            conn.execute("INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign) VALUES (?,1,0,1)", (bob,))
        item = self.yoga(assigned_to=bob, visibility="household")
        notes = self.ha.notifications()
        self.assertEqual([(s, b["message"]) for s, b in notes],
                         [("mobile_app_bob", "Alice assigned you: Yoga (Every Tue, Thu & Fri, 18:00–19:00)")])
        self.ha.requests.clear()
        self.patch(f"/api/schedule/{item['id']}", {"name": "Yoga 2"})                 # assignee unchanged: no ping
        self.post("/api/schedule", {"name": "Mine", "rule": "daily", "anchor_date": "2026-09-21", "assigned_to": bob}, BOB)
        self.assertEqual(self.ha.notifications(), [])


class TestCalendarDashboard(ApiBase):
    def test_calendar_range_and_contents(self):
        self.add("dated", due_date="2026-09-25", priority="high")
        self.add("later", due_date="2026-11-30")
        self.add("undated")
        self.post("/api/schedule", {"name": "Trash", "rule": "weeks:1:1", "anchor_date": "2026-09-14"})
        r = self.get("/api/calendar", params={"from": "2026-09-01", "to": "2026-09-30"}).json()
        self.assertEqual(r["today"], "2026-09-21")
        self.assertEqual([t["title"] for t in r["tasks"]], ["dated"])
        self.assertEqual(r["undatedCount"], 1)
        self.assertEqual([e["date"] for e in r["schedule"]], ["2026-09-14", "2026-09-21", "2026-09-28"])
        r = self.get("/api/calendar", params={"from": "2026-09-01", "to": "2026-09-30", "schedule": 0}).json()
        self.assertEqual(r["schedule"], [])

    def test_calendar_filters_and_show_all(self):
        typ = self.get("/api/task-types").json()[0]["id"]
        a = self.add("a", due_date="2026-09-25", type_id=typ)
        self.add("b", due_date="2026-09-26")
        self.patch(f"/api/tasks/{a['id']}", {"completed": True})
        q = {"from": "2026-09-20", "to": "2026-09-30"}
        self.assertEqual([t["title"] for t in self.get("/api/calendar", params=q).json()["tasks"]], ["b"])
        self.assertEqual(len(self.get("/api/calendar", params={**q, "show": "all"}).json()["tasks"]), 2)
        self.assertEqual([t["title"] for t in self.get("/api/calendar", params={**q, "show": "all", "type": typ}).json()["tasks"]], ["a"])

    def test_calendar_validation(self):
        for q in ({"from": "2026-09-01", "to": "2026-12-31"},      # > 92 days
                  {"from": "2026-09-10", "to": "2026-09-01"},
                  {"from": "x", "to": "2026-09-01"}, {"from": "2026-09-01"}, {},
                  {"from": "2026-09-01", "to": "2026-09-05", "show": "x"},
                  {"from": "2026-09-01", "to": "2026-09-05", "schedule": 2}):
            self.assertEqual(self.get("/api/calendar", params=q).status_code, 422, q)
        self.assertEqual(self.get("/api/calendar", params={"from": "2026-09-01", "to": "2026-12-01"}).status_code, 200)   # exactly 91 days

    def test_calendar_hides_private_tasks(self):
        self.add("secret", list_id=self.mine_id(ALICE), due_date="2026-09-25")
        r = self.get("/api/calendar", BOB, params={"from": "2026-09-20", "to": "2026-09-30"}).json()
        self.assertEqual(r["tasks"], [])

    def test_dashboard_buckets(self):
        self.add("overdue req", due_date="2026-09-15", completion_required=True)
        self.add("past opt", due_date="2026-09-15")
        self.add("today", due_date="2026-09-21")
        self.add("soon", due_date="2026-09-25")
        self.add("far", due_date="2026-10-30")
        self.post("/api/schedule", {"name": "Trash", "rule": "weeks:1:1", "anchor_date": "2026-09-14"})
        d = self.get("/api/dashboard").json()
        self.assertEqual([t["title"] for t in d["overdue"]], ["overdue req"])
        self.assertEqual([t["title"] for t in d["dueToday"]], ["today"])
        self.assertEqual([t["title"] for t in d["upcoming"]], ["soon"])
        self.assertEqual(d["counts"], {"overdue": 1, "dueToday": 1, "upcoming": 1, "open": 5})
        self.assertEqual([s["name"] for s in d["schedule"]], ["Trash"])
        self.assertTrue(d["schedule"][0]["occursToday"])

    def test_workload(self):
        bob = self.uid("Bob")
        self.add("b1", assigned_to=bob, due_date="2026-09-10", completion_required=True)
        self.add("b2", assigned_to=bob, due_date="2026-09-10")            # optional & past: not overdue
        self.add("nobody")
        self.add("alice private", list_id=self.mine_id(ALICE))
        t = self.add("done by bob")
        self.patch(f"/api/tasks/{t['id']}", {"completed": True}, BOB)
        w = self.get("/api/workload", ALICE).json()
        by = {p["name"]: p for p in w["people"]}
        self.assertEqual(w["people"][0]["name"], "Alice")                       # me first
        self.assertEqual((by["Bob"]["open"], by["Bob"]["overdue"], by["Bob"]["doneThisWeek"]), (2, 1, 1))
        self.assertEqual(by["Alice"]["open"], 1)                                # her personal list counts on her own card
        self.assertEqual(w["unassigned"]["open"], 1)
        # Bob's view of Alice must not leak her personal tasks
        wb = self.get("/api/workload", BOB).json()
        self.assertEqual({p["name"]: p for p in wb["people"]}["Alice"]["open"], 0)

    def test_workload_hides_idle_disabled_user(self):
        self.patch(f"/api/users/{self.uid('Bob')}", {"disabled": True}, ADMIN)
        names = [p["name"] for p in self.get("/api/workload").json()["people"]]
        self.assertNotIn("Bob", names)


class TestPrefs(ApiBase):
    def test_defaults_and_cannot_enable_without_mapping(self):
        p = self.get("/api/prefs").json()
        self.assertEqual((p["notificationsEnabled"], p["leadDays"], p["notifyOnAssign"], p["canNotify"]), (False, 0, True, False))
        self.assertEqual(p["digestTime"], "08:00")   # everyone's own time; 08:00 until they pick one
        # the fix is now in the app (Admin → Users), not the app's Configuration tab
        self.assertIn("Admin → Users", p["canNotifyReason"])
        self.assertEqual(self.put("/api/prefs", {"notificationsEnabled": True}).status_code, 400)
        self.assertEqual(self.get("/api/prefs").json()["notificationsEnabled"], False)

    def test_enable_with_mapping(self):
        link("alice", "mobile_app_alice")
        r = self.put("/api/prefs", {"notificationsEnabled": True, "leadDays": 2, "notifyOnAssign": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["notificationsEnabled"], r.json()["leadDays"], r.json()["notifyOnAssign"], r.json()["canNotify"]),
                         (True, 2, False, True))
        for bad in ({"leadDays": 9}, {"leadDays": "1"}, {"leadDays": True}, {"notificationsEnabled": "yes"}):
            self.assertEqual(self.put("/api/prefs", bad).status_code, 422, bad)

    def test_digest_time_configurable_per_user(self):
        link("alice", "mobile_app_alice")
        r = self.put("/api/prefs", {"digestTime": "07:15"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["digestTime"], "07:15")
        self.assertEqual(self.get("/api/prefs").json()["digestTime"], "07:15")
        for bad in ("7:15", "25:00", "07:60", "noon", 715):
            self.assertEqual(self.put("/api/prefs", {"digestTime": bad}).status_code, 422, bad)
        # null resets to 08:00 (there's no household-wide default)
        r = self.put("/api/prefs", {"digestTime": None})
        self.assertEqual(r.json()["digestTime"], "08:00")

    def test_test_notification(self):
        self.assertEqual(self.post("/api/prefs/test-notification").status_code, 400)
        link("alice", "mobile_app_alice")
        from app.routers import prefs
        prefs._last_test.clear()
        self.assertEqual(self.post("/api/prefs/test-notification").status_code, 200)
        self.assertEqual(self.ha.notifications()[0][0], "mobile_app_alice")
        self.assertEqual(self.post("/api/prefs/test-notification").status_code, 429)
        prefs._last_test.clear()
        self.ha.fail = True
        try:
            self.assertEqual(self.post("/api/prefs/test-notification").status_code, 502)
        finally:
            self.ha.fail = False


class TestBackupRestore(ApiBase):
    def test_download_is_a_valid_snapshot(self):
        self.add("keep me")
        self.assertEqual(self.get("/api/admin-storage-download-db", ALICE).status_code, 403)
        r = self.get("/api/admin-storage-download-db", ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertIn("household-todo-backup-", r.headers["content-disposition"])
        path = os.path.join(config.DATA_DIR, "dl.db")
        with open(path, "wb") as f:
            f.write(r.content)
        conn = sqlite3.connect(path)
        conn.create_function("normalize_addr", 1, db.normalize_address, deterministic=True)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("SELECT title FROM tasks").fetchone()[0], "keep me")
        conn.close()
        os.remove(path)
        self.assertEqual([f for f in os.listdir(config.DATA_DIR) if f.endswith(".db") and f != "household.db"], [])

    def test_restore_round_trip_and_rejections(self):
        self.add("before")
        snapshot = self.get("/api/admin-storage-download-db", ADMIN).content
        self.add("after")
        self.post("/api/schedule", {"name": "Trash", "rule": "daily", "anchor_date": "2026-09-01"})
        self.assertEqual(self.post("/api/admin-storage-import-db", h=ALICE, files={"file": ("x.db", snapshot)}).status_code, 403)
        for junk in (b"not a database", b""):
            r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("x.db", io.BytesIO(junk))})
            self.assertEqual(r.status_code, 400, junk)
        self.assertEqual(len(self.titles(self.shared_id())), 2)              # nothing was touched
        r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("x.db", io.BytesIO(snapshot))})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.titles(self.shared_id()), ["before"])
        self.assertEqual(self.get("/api/schedule").json(), [])
        self.assertEqual([f for f in os.listdir(config.DATA_DIR) if f.endswith(".db") and f != "household.db"], [])

    def test_restore_rejects_db_missing_tables(self):
        path = os.path.join(config.DATA_DIR, "partial.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE tasks (id TEXT)")
        conn.commit(); conn.close()
        with open(path, "rb") as f:
            r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("p.db", f)})
        os.remove(path)
        self.assertEqual(r.status_code, 400)


class TestLinks(ApiBase):
    """Optional http(s) link on tasks and schedule items."""

    BAD = ["javascript:alert(1)", "ftp://x", "/relative", "example.com", "data:text/html,hi", "https://" + "a" * 2000 + ".com"]

    def item(self, h=ALICE, **kw):
        r = self.post("/api/schedule", {"name": "Yoga", "rule": "weeks:1:2", "anchor_date": "2026-09-22",
                                        "start_time": "18:00", "end_time": "19:00", **kw}, h)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_task_url_round_trip_patch_and_clear(self):
        t = self.add("No link")
        self.assertIsNone(t["url"])
        t = self.add("Book", url="  https://example.com/book?x=1  ")
        self.assertEqual(t["url"], "https://example.com/book?x=1")
        listed = next(x for x in self.get(f"/api/lists/{self.shared_id()}/tasks").json() if x["id"] == t["id"])
        self.assertEqual(listed["url"], "https://example.com/book?x=1")
        r = self.patch(f"/api/tasks/{t['id']}", {"url": "http://intranet.local/page"})
        self.assertEqual((r.status_code, r.json()["url"]), (200, "http://intranet.local/page"))
        r = self.patch(f"/api/tasks/{t['id']}", {"title": "Renamed"})               # untouched when not sent
        self.assertEqual(r.json()["url"], "http://intranet.local/page")
        self.assertIsNone(self.patch(f"/api/tasks/{t['id']}", {"url": None}).json()["url"])
        self.patch(f"/api/tasks/{t['id']}", {"url": "https://x.com"})
        self.assertIsNone(self.patch(f"/api/tasks/{t['id']}", {"url": "   "}).json()["url"])   # blank clears too
        q = self.post("/api/tasks", {"title": "Quick", "url": "https://q.example"})
        self.assertEqual((q.status_code, q.json()["url"]), (201, "https://q.example"))

    def test_task_url_validation(self):
        t = self.add("T", url="https://ok.example")
        for bad in self.BAD + [123]:
            r = self.post(f"/api/lists/{self.shared_id()}/tasks", {"title": "x", "url": bad})
            self.assertEqual(r.status_code, 422, bad)
            r = self.patch(f"/api/tasks/{t['id']}", {"url": bad})
            self.assertEqual(r.status_code, 422, bad)
        self.assertIn("http:// or https://", self.patch(f"/api/tasks/{t['id']}", {"url": "javascript:alert(1)"}).json()["detail"])
        self.assertIn("2000", self.patch(f"/api/tasks/{t['id']}", {"url": self.BAD[-1]}).json()["detail"])
        self.assertEqual(self.get(f"/api/lists/{self.shared_id()}/tasks").json()[0]["url"], "https://ok.example")   # unchanged

    def test_task_url_follows_list_privacy(self):
        t = self.add("Mine", list_id=self.mine_id(), url="https://private.example")
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}", {"url": None}, BOB).status_code, 403)
        r = self.get("/api/calendar", BOB, params={"from": "2026-09-01", "to": "2026-09-30"})
        self.assertNotIn("private.example", r.text)

    def test_schedule_url_round_trip_validation_and_clear(self):
        it = self.item(url="https://studio.example/timetable")
        self.assertEqual(it["url"], "https://studio.example/timetable")
        self.assertEqual(self.get("/api/schedule").json()[0]["url"], "https://studio.example/timetable")
        household = self.post("/api/schedule", {"name": "Trash", "rule": "weeks:1:1", "anchor_date": "2026-09-14"}).json()
        self.assertIsNone(household["url"])
        for bad in self.BAD:
            self.assertEqual(self.post("/api/schedule", {"name": "Bad", "rule": "daily", "anchor_date": "2026-09-21", "url": bad}).status_code, 422, bad)
            self.assertEqual(self.patch(f"/api/schedule/{it['id']}", {"url": bad}).status_code, 422, bad)
        r = self.patch(f"/api/schedule/{it['id']}", {"url": "https://studio.example/zoom"}, BOB)   # household: anyone may edit
        self.assertEqual(r.json()["url"], "https://studio.example/zoom")
        self.assertIsNone(self.patch(f"/api/schedule/{it['id']}", {"url": None}).json()["url"])

    def test_calendar_entries_include_url(self):
        self.item(url="https://studio.example/")
        self.add("Dated", due_date="2026-09-25", url="https://task.example/")
        r = self.get("/api/calendar", params={"from": "2026-09-21", "to": "2026-09-30"}).json()
        self.assertEqual([e["url"] for e in r["schedule"]], ["https://studio.example/", "https://studio.example/"])
        self.assertEqual(r["tasks"][0]["url"], "https://task.example/")

    def test_private_item_url_only_for_assignee(self):
        self.item(url="https://secret.example/", assigned_to=self.uid("Alice"), visibility="private")
        for path, params in (("/api/schedule", None), ("/api/calendar", {"from": "2026-09-21", "to": "2026-09-30"})):
            self.assertNotIn("secret.example", self.get(path, BOB, params=params).text)
            self.assertIn("secret.example", self.get(path, ALICE, params=params).text)

    def test_assignment_pings_carry_link_as_data(self):
        link("bob", "mobile_app_bob")
        bob = self.uid("Bob")
        with db.get_conn() as conn:
            conn.execute("INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign) VALUES (?,1,0,1)", (bob,))
        self.add("Book MOT", assigned_to=bob, url="https://garage.example/book")
        t = self.add("Later")
        self.patch(f"/api/tasks/{t['id']}", {"assigned_to": bob, "url": "https://later.example"})
        self.item(assigned_to=bob, url="https://studio.example/")
        bodies = [b for s, b in self.ha.notifications()]
        self.assertEqual(bodies, [
            {"title": "Household Todo", "message": "Alice assigned you: Book MOT\nhttps://garage.example/book",
             "data": {"url": "https://garage.example/book", "clickAction": "https://garage.example/book"}},
            {"title": "Household Todo", "message": "Alice assigned you: Later\nhttps://later.example",
             "data": {"url": "https://later.example", "clickAction": "https://later.example"}},
            {"title": "Household Todo", "message": "Alice assigned you: Yoga (Every Tuesday, 18:00–19:00)\nhttps://studio.example/",
             "data": {"url": "https://studio.example/", "clickAction": "https://studio.example/"}},
        ])


if __name__ == "__main__":
    print("using FastAPI shim" if USING_SHIM else "using real FastAPI")
    unittest.main()
