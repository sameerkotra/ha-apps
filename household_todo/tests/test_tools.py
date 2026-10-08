"""What Todo answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2, §6.4, §7.2).

- todo.tasks (today/week/overdue/all, a list by name), todo.lists, todo.schedule: as the person sees them;
  someone else's personal list is never seen; no notes;
- todo.items.add: only with the tap (`confirm`), only to a list the person can see;
- the switches (admin, person); the catalogue fits; Todo offers both kinds in its hello.

Monday 21 September 2026 (test_api.NOW). All people, lists and tasks here are invented."""
import _env  # noqa: F401

import json
from datetime import date, datetime, timedelta, timezone

from test_api import ADMIN, ALICE, BOB, NOW, ApiBase, link

from app import config, db, reminders, tools
from app.common import app_bus


def call(tool, uid="u_alice", confirm=None, **args):
    now = datetime.now(timezone.utc)
    data = {"tool": tool, "args": args, "requested_by": uid, "question": "q1"}
    if confirm is not None:
        data["confirm"] = confirm
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "household_todo",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(), "data": data}
    with db.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            conn.rollback()
            return n


class DeepLinkTests(ApiBase):
    def test_sub_paths_redirect_to_the_page(self):
        for path, where in (("/dashboard", "../#/dashboard"), ("/lists/abc", "../../#/lists/abc"),
                            ("/schedule", "../#/schedule")):
            r = self.c.get(path, headers=ALICE, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (307, where), path)
        self.assertEqual(self.c.get("/lists/a%20b", headers=ALICE, follow_redirects=False).status_code, 404)
        config.SIDEBAR_PAGE = "/local_household_todo"
        try:
            self.assertEqual(self.get("/api/whoami").json()["page"], "/local_household_todo")
        finally:
            config.SIDEBAR_PAGE = None


class ToolTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.add("Bins", due_date="2026-09-21")
        self.add("Pay water bill", due_date="2026-09-19", completion_required=True, notes="account 1234")
        self.add("Call plumber", due_date="2026-09-24")
        self.add("Paint fence")
        self.add("Bob's secret", list_id=self.mine_id(BOB), h=BOB, due_date="2026-09-21")
        self.old_page, config.SIDEBAR_PAGE = config.SIDEBAR_PAGE, "/a1b2c3d4_household_todo"

    def tearDown(self):
        config.SIDEBAR_PAGE = self.old_page
        super().tearDown()

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def titles(self, res):
        return [i["title"] for i in res["items"]]

    def test_tasks_by_when(self):
        res = call("todo.tasks", when="today")
        self.assertEqual(self.titles(res), ["Pay water bill", "Bins"])
        self.assertIn("Pay water bill (overdue 2 days)", res["text"])
        self.assertIn("Bins (today)", res["text"])
        self.assertEqual(self.titles(call("todo.tasks", when="week")), ["Pay water bill", "Bins", "Call plumber"])
        self.assertEqual(self.titles(call("todo.tasks", when="overdue")), ["Pay water bill"])
        self.assertEqual(len(call("todo.tasks", when="all")["items"]), 4)
        self.assertEqual(res["links"], [{"label": "Dashboard in Household Todo", "panel": "/a1b2c3d4_household_todo",
                                         "target": "/dashboard"}])
        blob = json.dumps(call("todo.tasks", when="all"))
        self.assertNotIn("account 1234", blob)                  # never notes
        self.assertNotIn("Bob's secret", blob)                  # never someone else's personal list
        self.assertNack(call("todo.tasks"), "invalid", "when")

    def test_tomorrow_the_schedule_and_whats_next(self):
        self.assertEqual(self.titles(call("todo.tasks", when="tomorrow")), [])
        res = call("todo.tasks", when="tomorrow")
        self.assertEqual(res["text"], "No tasks for tomorrow. Next due: Call plumber (Thu 24 Sep).")
        r = self.post("/api/schedule", {"name": "Dentist", "rule": "weeks:1:2", "anchor_date": "2026-09-15"})
        self.assertEqual(r.status_code, 201, r.text)       # Tuesdays: tomorrow (22nd), then the 29th
        res = call("todo.tasks", when="tomorrow")
        self.assertEqual(res["text"], "No tasks for tomorrow. Next due: Call plumber (Thu 24 Sep). "
                                      "On the schedule: tomorrow: Dentist.")
        self.assertEqual([(i["title"], i["due"], i["list"]) for i in res["items"]],
                         [("Dentist", "2026-09-22", "Schedule")])
        self.assertEqual(res["links"][1]["target"], "/schedule")
        week = call("todo.tasks", when="week")
        self.assertEqual(self.titles(week), ["Pay water bill", "Bins", "Call plumber", "Dentist"])
        self.assertTrue(week["text"].endswith("On the schedule: tomorrow: Dentist."))  # the 29th is after the week
        self.assertNotIn("Dentist", json.dumps(call("todo.tasks", when="today")["items"]))
        self.assertNotIn("Dentist", json.dumps(call("todo.tasks", when="all")))
        shared = next(l["name"] for l in self.lists() if l["kind"] == "shared")
        self.assertNotIn("Dentist", json.dumps(call("todo.tasks", when="week", list=shared)))

    def test_a_list_by_name(self):
        shared = next(l["name"] for l in self.lists() if l["kind"] == "shared")
        res = call("todo.tasks", when="all", list=shared.upper())
        self.assertEqual(len(res["items"]), 4)
        self.assertEqual(res["links"][0]["target"], f"/lists/{self.shared_id()}")
        bobs = next(l["name"] for l in self.lists(BOB) if l["kind"] == "personal")
        mine = next(l["name"] for l in self.lists() if l["kind"] == "personal")
        if bobs != mine:                                        # a name only Bob's list has looks like none
            self.assertNack(call("todo.tasks", when="all", list=bobs), "not_found", "list")
        self.assertNack(call("todo.tasks", when="all", list="Nope"), "not_found", "list")

    def test_lists(self):
        res = call("todo.lists")
        self.assertEqual(len(res["items"]), len(self.lists()))
        self.assertEqual(sum(i["open"] for i in res["items"]), 4)

    def test_schedule(self):
        r = self.post("/api/schedule", {"name": "Trash pickup", "rule": "weeks:1:1", "anchor_date": "2026-09-14"})
        self.assertEqual(r.status_code, 201, r.text)
        res = call("todo.schedule", days=8)
        self.assertEqual([(i["date"], i["what"]) for i in res["items"]],
                         [("2026-09-21", "Trash pickup"), ("2026-09-28", "Trash pickup")])
        self.assertIn("today: Trash pickup", res["text"])
        self.assertEqual(len(call("todo.schedule", days=1)["items"]), 1)
        self.assertNack(call("todo.schedule", days=15), "invalid", "days")

    def test_add_needs_the_tap(self):
        shared = next(l["name"] for l in self.lists() if l["kind"] == "shared")
        self.assertNack(call("todo.items.add", list=shared, text="Milk"), "not_allowed", "confirm")
        self.assertEqual(len(call("todo.tasks", when="all")["items"]), 4)
        res = call("todo.items.add", confirm=True, list=shared, text="Milk", due="2026-09-22")
        self.assertEqual(res["text"], f"Added “Milk” to {shared}, due tomorrow.")
        task = next(t for t in self.get(f"/api/lists/{self.shared_id()}/tasks").json() if t["title"] == "Milk")
        self.assertEqual((task["dueDate"], task["source"], task["createdBy"]), ("2026-09-22", "Assistant", "u_alice"))
        self.assertNack(call("todo.items.add", confirm=True, list="Nope", text="Eggs"), "not_found", "list")
        self.assertNack(call("todo.items.add", confirm=True, list=shared, text=""), "invalid", "text")

    def test_tasks_for_a_person(self):
        bins = next(t for t in self.get(f"/api/lists/{self.shared_id()}/tasks").json() if t["title"] == "Bins")
        r = self.patch(f"/api/tasks/{bins['id']}", {"assigned_to": "u_bob"})
        self.assertEqual(r.status_code, 200, r.text)
        res = call("todo.tasks", when="all", person="bob")
        self.assertEqual(self.titles(res), ["Bins"])
        self.assertIn("open for bob", res["text"])
        self.assertEqual(self.titles(call("todo.tasks", when="all", person="Bob", uid="u_bob")), ["Bins"])
        self.assertEqual(self.titles(call("todo.tasks", when="all", person="me", uid="u_bob")), ["Bins"])
        self.assertEqual(len(call("todo.tasks", when="all", person="nobody")["items"]), 3)
        self.assertEqual(call("todo.tasks", when="all", person="Zed")["items"], [])

    def test_tick_off_needs_the_tap_and_one_task(self):
        self.assertNack(call("todo.items.done", task="Bins"), "not_allowed", "confirm")
        res = call("todo.items.done", confirm=True, task="bins")
        shared = next(l["name"] for l in self.lists() if l["kind"] == "shared")
        self.assertEqual(res["text"], f"Ticked off “Bins” on {shared}.")
        self.assertEqual(res["links"][0]["target"], f"/lists/{self.shared_id()}")
        done = self.get(f"/api/lists/{self.shared_id()}/tasks", params={"show": "completed"}).json()
        self.assertEqual([(t["title"], t["completedBy"]) for t in done], [("Bins", "u_alice")])
        self.assertIn("No open task called “Bins”", call("todo.items.done", confirm=True, task="Bins")["text"])
        self.add("Call mum")
        res = call("todo.items.done", confirm=True, task="call")         # two tasks have "call" in them
        self.assertIn("2 open tasks match “call”", res["text"])
        self.assertIn("Nothing was changed", res["text"])
        self.assertEqual(len(call("todo.tasks", when="all")["items"]), 4)
        self.assertIn("No open task", call("todo.items.done", confirm=True, task="Bob's secret")["text"])  # not hers
        self.assertNack(call("todo.items.done", confirm=True, task="Bins", list="Nope"), "not_found", "list")

    def test_move_a_task_to_another_day(self):
        self.assertNack(call("todo.items.due", task="Paint fence", due="2026-09-26"), "not_allowed", "confirm")
        res = call("todo.items.due", confirm=True, task="Paint fence", due="2026-09-26")
        self.assertIn("“Paint fence” on", res["text"])
        self.assertIn("is now due Sat 26 Sep", res["text"])
        task = next(t for t in self.get(f"/api/lists/{self.shared_id()}/tasks").json() if t["title"] == "Paint fence")
        self.assertEqual(task["dueDate"], "2026-09-26")
        self.assertNack(call("todo.items.due", confirm=True, task="Paint fence", due="soon"), "invalid", "due")
        self.assertNack(call("todo.items.due", confirm=True, task="Paint fence"), "invalid", "due")

    def test_time_words(self):
        for said, want in (("17:00", "17:00"), ("5pm", "17:00"), ("5:30 PM", "17:30"), ("12am", "00:00"),
                           ("12 pm", "12:00"), ("9.15", "09:15"), ("25:00", None), ("13pm", None), ("soon", None)):
            self.assertEqual(tools.parse_time(said), want, said)

    def test_remind_me_at_a_time(self):
        now = config.now()                                       # 12:00 on Monday 21 September (test_api.NOW)
        self.assertEqual(now.strftime("%H:%M"), NOW.astimezone(now.tzinfo).strftime("%H:%M"))
        self.assertNack(call("todo.reminder.add", text="Call the plumber", time="5pm"), "not_allowed", "confirm")
        later = (now + timedelta(hours=5)).strftime("%H:%M")
        res = call("todo.reminder.add", confirm=True, text="Call the plumber", time=later)
        mine = next(l["name"] for l in self.lists() if l["kind"] == "personal")
        self.assertEqual(res["text"], f"I'll remind you today at {later}: “Call the plumber” (on {mine}).")
        task = next(t for t in self.get(f"/api/lists/{self.mine_id()}/tasks").json() if t["title"] == "Call the plumber")
        self.assertEqual((task["dueDate"], task["dueTime"], task["source"]), (now.date().isoformat(), later, "Assistant"))
        earlier = (now - timedelta(hours=2)).strftime("%H:%M")
        res = call("todo.reminder.add", confirm=True, text="Bins out", time=earlier)            # passed: tomorrow
        self.assertIn(f"tomorrow at {earlier}", res["text"])
        res = call("todo.reminder.add", confirm=True, text="Old", time=earlier, date=now.date().isoformat())
        self.assertIn("has already passed. Nothing was added.", res["text"])
        self.assertNack(call("todo.reminder.add", confirm=True, text="x", time="soon"), "invalid", "time")
        # at the time: one ping to her phone, once
        link("alice", "notify.mobile_app_alice")
        sent = []
        fake = lambda svc, title, msg, data=None: sent.append((svc, msg)) or True     # noqa: E731
        at = now + timedelta(hours=5)
        self.assertEqual(reminders.run_alarm_pass_blocking(now=at - timedelta(minutes=1), sender=fake), 0)
        self.assertEqual(reminders.run_alarm_pass_blocking(now=at, sender=fake), 1)
        self.assertEqual(sent, [("mobile_app_alice", f"⏰ Call the plumber ({later})")])
        self.assertEqual(reminders.run_alarm_pass_blocking(now=at + timedelta(minutes=1), sender=fake), 0)
        # done before its time, or the app was off for over an hour: dropped, not sent
        res = call("todo.reminder.add", confirm=True, text="Water plants", time=later, date="2026-09-23")
        call("todo.items.done", confirm=True, task="Water plants")
        tomorrow_ping = datetime.combine(date(2026, 9, 22), datetime.min.time(), tzinfo=now.tzinfo) + (at - now.replace(hour=0, minute=0, second=0, microsecond=0))
        self.assertEqual(reminders.run_alarm_pass_blocking(now=tomorrow_ping + timedelta(hours=3), sender=fake), 0)
        self.assertEqual(len(sent), 1)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_alarms WHERE sent_at IS NULL").fetchone()[0], 1)

    def test_switches(self):
        r = self.put("/api/admin/settings", {"assistant_answers": False}, ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNack(call("todo.lists"), "not_allowed", "off")
        self.put("/api/admin/settings", {"assistant_answers": True}, ADMIN)
        p = self.get("/api/prefs").json()
        self.assertTrue(p["assistant"] and p["assistantOk"])
        r = self.put("/api/prefs", {"assistantOk": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["assistantOk"])
        self.assertNack(call("todo.lists"), "not_allowed", "person_off")
        self.assertIn("text", call("todo.lists", uid="u_bob"))
        self.assertEqual(self.put("/api/prefs", {"assistantOk": "no"}).status_code, 422)
        self.assertNack(call("todo.lists", uid="u_nobody"), "not_allowed", "no_access")

    def test_catalogue(self):
        tools.tools.check()
        self.assertEqual([t["name"] for t in tools.tools.spec()],
                         ["todo.tasks", "todo.lists", "todo.schedule", "todo.items.add", "todo.items.done",
                          "todo.items.due", "todo.reminder.add"])
        for kind in ("assist.tools.list", "assist.tool.call", "todo.items.add", "todo.lists.list"):
            self.assertIn(kind, app_bus.default._handlers)
