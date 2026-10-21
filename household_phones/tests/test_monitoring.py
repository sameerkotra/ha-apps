"""Not monitored by default (spec/SPEC.md §2, §5): an admin adds the phones to monitor; a device that isn't added is
left alone everywhere (requests, alerts, history, the summary, lost contact, Check now, the bus, the Assistant), and
its owner is told only that it isn't monitored. Plus the 1.0.0 → 1.1 migration (auto-watched devices, quiet times)."""
import json
import os
import shutil
import sqlite3
import tempfile
from unittest import mock

from base import ADMIN, ASHA, BOB, PhonesCase

from app import alerts, app_messages, config, db, ha_sensors, monitor, phones, tools
from app.common import app_bus as bus


class NotMonitoredByDefault(PhonesCase):
    def test_a_discovered_device_is_left_alone(self):
        pid = self.add(monitored=False)
        self.assertEqual(self.row(pid)["monitored"], 0)
        self.report(pid, value="50")                          # its reports aren't written
        self.assertIsNone(self.row(pid)["last_checkin"])
        for _ in range(4):
            self.advance(30)
            out = self.tick()
            self.assertEqual(out["requests"], [])
        self.assertEqual(self.ha.commands, [])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM history WHERE phone_id = ?", (pid,)).fetchone()[0], 0)
        self.assertEqual(self.sent, [])
        r = alerts.send("offline", pid, "x is offline")
        self.assertEqual(r["skipped"], "not monitored")
        self.assertEqual(self.get(f"/api/phones/{pid}").json()["admin"]["label"], "Not monitored")

    def test_add_to_monitoring_in_one_dialog(self):
        pid = self.add(monitored=False)
        avail = self.get("/api/admin/phones").json()["available"][0]
        self.assertEqual((avail["ownerId"], avail["owner"], avail["kindGuess"]), ("u-asha", "Asha", "Phone"))
        self.assertEqual(self.post(f"/api/admin/phones/{pid}/monitor", {"kind": "phone"}, ASHA).status_code, 403)
        r = self.post(f"/api/admin/phones/{pid}/monitor", {"ownerId": "u-asha", "name": "Asha's phone", "kind": "tablet",
                                                            "intervalMin": 10})
        self.assertEqual(r.status_code, 200, r.text)
        p = self.row(pid)
        self.assertEqual((p["monitored"], p["name"], p["kind"], p["interval_min"], p["owner_id"]),
                         (1, "Asha's phone", "tablet", 10, "u-asha"))
        self.assertEqual(self.post(f"/api/admin/phones/{pid}/monitor", {"kind": "phone"}).status_code, 409)
        texts = [h["text"] for h in self.get(f"/api/phones/{pid}").json()["admin"]["history"]]
        self.assertIn("Added to monitoring by Ann Admin.", texts)
        r = self.get("/api/admin/phones").json()
        self.assertEqual(([t["id"] for t in r["tablets"]], r["available"]), ([pid], []))

    def test_grace_after_adding(self):
        pid = self.add(monitored=False)
        self.advance(3 * 24 * 60)                             # found days ago, never reported
        self.post(f"/api/admin/phones/{pid}/monitor", {"kind": "phone"})
        self.advance(20)
        self.tick()
        self.assertEqual(self.row(pid)["status"], "late")     # asked, but not Offline straight away
        self.assertTrue(self.ha.commands)
        self.advance(8)                                       # interval + 12 minutes after it was added
        self.tick()
        self.assertEqual(self.row(pid)["status"], "offline")

    def test_owner_page_after_adding_and_forwarding_is_the_owners_choice(self):
        pid = self.add(monitored=False)
        r = self.get("/api/my", ASHA).json()
        self.assertEqual(r["phones"], [])
        self.assertEqual([set(u) for u in r["unmonitored"]], [{"id", "name", "replaces"}])     # no details
        res = self.get(f"/api/phones/{pid}", ASHA)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"], "Your phone isn't monitored — ask an admin.")
        self.assertEqual(self.put(f"/api/phones/{pid}/forwarding", {"on": True}, ASHA).status_code, 409)
        self.assertEqual(self.post(f"/api/phones/{pid}/pause", {"kind": "alerts", "hours": 1}, ASHA).status_code, 409)
        self.post(f"/api/admin/phones/{pid}/monitor", {"kind": "phone"})
        page = self.get(f"/api/phones/{pid}", ASHA).json()
        self.assertTrue(page["welcome"])                       # "Your phone is now monitored — set up forwarding…"
        self.assertTrue(page["checklist"])
        self.assertFalse(page["forwarding"]["on"])
        self.assertEqual(self.put(f"/api/phones/{pid}/forwarding", {"on": True}, ADMIN).status_code, 403)
        self.assertEqual(self.put(f"/api/phones/{pid}/forwarding", {"on": True}, ASHA).status_code, 200)
        self.put(f"/api/phones/{pid}/mine", {"welcomeSeen": True}, ASHA)
        self.assertFalse(self.get(f"/api/phones/{pid}", ASHA).json()["welcome"])
        self.assertEqual(self.get("/api/my", ASHA).json()["unmonitored"], [])

    def test_stop_monitoring_keeps_or_deletes_history(self):
        a, b = self.add("Phone A"), self.add("Phone B")
        for pid, name in ((a, "Phone A"), (b, "Phone B")):
            self.put(f"/api/phones/{pid}/forwarding", {"on": True}, ASHA)
            self.report(pid, name=name)
            self.post(f"/api/phones/{pid}/check", None, ADMIN)
        self.tick()
        self.assertEqual(self.post(f"/api/admin/phones/{a}/unmonitor", {}).status_code, 422)     # must say which
        self.assertEqual(self.post(f"/api/admin/phones/{a}/unmonitor", {"keepHistory": True}, ASHA).status_code, 403)
        r = self.post(f"/api/admin/phones/{a}/unmonitor", {"keepHistory": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(a, [x["id"] for x in r.json()["available"]])
        self.post(f"/api/admin/phones/{b}/unmonitor", {"keepHistory": False})
        with db.get_conn() as conn:
            count = lambda t, pid: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE phone_id = ?", (pid,)).fetchone()[0]  # noqa: E731
            self.assertGreater(count("history", a), 0)
            self.assertGreater(count("checks", a), 0)
            self.assertEqual((count("history", b), count("checks", b)), (0, 0))
        for pid in (a, b):
            self.assertEqual((self.row(pid)["monitored"], self.row(pid)["fwd_on"]), (0, 0))   # forwarding stops too
        self.assertIsNone(self.row(b)["last_checkin"])


class ExcludedEverywhere(PhonesCase):
    def setUp(self):
        super().setUp()
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "x",
                                                                    "service": "mobile_app_ann_spare"}]}])
        self.mon = [self.add(f"Mon {i}", user_id="u-asha") for i in range(2)]
        self.un = [self.add(f"Off {i}", user_id="u-bob", monitored=False) for i in range(3)]
        for i, pid in enumerate(self.mon):
            self.report(pid, name=f"Mon {i}")
        self.tick()

    def test_summary_and_lists(self):
        r = self.get("/api/admin/phones").json()
        self.assertEqual(r["summary"]["phones"], 2)
        self.assertEqual({p["id"] for p in r["phones"]}, set(self.mon))
        self.assertEqual({a["id"] for a in r["available"]}, set(self.un))
        self.advance(31 * 24 * 60)
        self.assertFalse({u["id"] for u in self.get("/api/admin/phones").json()["unused"]} & set(self.un))

    def test_lost_contact_counts_only_monitored(self):
        self.advance(18)
        self.report(self.mon[0], name="Mon 0")                  # 1 of 2 monitored silent; 3 unmonitored silent too
        self.tick()
        self.assertIsNone(self.get("/api/admin/phones").json()["lostContact"])
        self.assertEqual(self.ha.commands, ["mobile_app_mon_1"])

    def test_check_now_bus_and_assistant(self):
        off = self.un[0]
        for who in (ADMIN, BOB):
            self.assertEqual(self.post(f"/api/phones/{off}/check", None, who).status_code, 404)
        # even with forwarding left on in the database, Chat isn't told about it
        self.set_row(off, fwd_on=1, fwd_target="my_room")
        msg = bus.Message({"id": "01", "from": "household_chat", "to": "household_phones", "kind": "phones.forwarding_into",
                           "data": {"requested_by": "u-bob", "my_room": True}})
        with db.get_conn() as conn:
            self.assertEqual(app_messages.forwarding_into(msg, conn)["phones"], [])
            self.assertEqual({p["id"] for p in tools.visible(conn, {"id": "u-ann", "is_admin": True})}, set(self.mon))
        call = bus.Message({"id": "02", "from": "household_assistant", "to": "household_phones", "kind": "assist.tool.call",
                            "data": {"tool": "phones.status", "args": {"person": "Bob"}, "requested_by": "u-ann"}})
        with db.get_conn() as conn:
            self.assertIn("No phone you can see", tools.tools.on_call(call, conn)["text"])
        self.setting(ha_sensors=True)
        eids = [e for e, _, _ in ha_sensors.items()]
        self.assertEqual(len(eids), 3)                           # two phones and the count
        self.assertNotIn("off", " ".join(eids))
        self.assertEqual(self.get("/api/my", BOB).json()["phones"], [])
        self.assertEqual(len(self.get("/api/my", BOB).json()["unmonitored"]), 3)


def old_schema() -> str:
    """The 1.0.0 schema, from today's: `watched` and the phone's quiet times instead of the new columns."""
    s = db.SCHEMA
    start = s.index("    monitored INTEGER")
    end = s.index("    interval_min INTEGER")
    s = s[:start] + "    watched INTEGER NOT NULL DEFAULT 1,\n" + s[end:]
    start = s.index("    dnm_windows TEXT")
    end = s.index("    share_place INTEGER")
    s = s[:start] + "    quiet_start TEXT NOT NULL DEFAULT '',\n    quiet_end TEXT NOT NULL DEFAULT '',\n" + s[end:]
    return s


class Migration(PhonesCase):
    def migrate(self, rows, children=()):
        folder = tempfile.mkdtemp(prefix="phones_mig_")
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "phones.db")
        conn = sqlite3.connect(path)
        conn.executescript(old_schema())
        cols = {r[1] for r in conn.execute("PRAGMA table_info(phones)")}
        self.assertIn("watched", cols)
        self.assertNotIn("monitored", cols)
        for uid in children:
            conn.execute("INSERT INTO users (id, name, is_child) VALUES (?, ?, 1)", (uid, uid))
        for i, r in enumerate(rows):
            r = {"id": f"p{i}", "device_id": f"d{i}", "first_seen": 1.0, **r}
            conn.execute(f"INSERT INTO phones ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", tuple(r.values()))
        conn.commit()
        conn.close()
        with mock.patch.object(config, "DB_PATH", path):
            db.init_db()
            with db.get_conn() as c:
                out = {r["id"]: r for r in c.execute("SELECT * FROM phones").fetchall()}
            db.init_db()                                         # a second start changes nothing
            with db.get_conn() as c:
                again = {r["id"]: dict(r) for r in c.execute("SELECT * FROM phones").fetchall()}
        db.init_db()
        self.assertEqual({k: dict(v) for k, v in out.items()}, again)
        return out

    def test_auto_watched_become_not_monitored(self):
        out = self.migrate([
            {"watched": 1, "kind": "phone"},                                      # watched only by the guess
            {"watched": 1, "kind": "phone", "kind_confirmed": 1, "status": "online"},   # confirmed by an admin
            {"watched": 1, "kind": "phone", "name": "Asha's phone"},              # edited by an admin
            {"watched": 1, "kind": "phone", "owner_by_admin": 1},
            {"watched": 1, "kind": "phone", "interval_min": 5},
            {"watched": 0, "kind": "tablet", "kind_confirmed": 1},                # an admin chose not to watch it
            {"watched": 1, "kind": "ignored", "kind_confirmed": 1},
        ])
        self.assertEqual([out[f"p{i}"]["monitored"] for i in range(7)], [0, 1, 1, 1, 1, 0, 0])
        self.assertEqual((out["p0"]["status"], out["p0"]["status_reason"]), ("unknown", "not_monitored"))
        self.assertEqual(out["p1"]["status"], "online")

    def test_quiet_times_become_do_not_monitor_hours(self):
        out = self.migrate([
            {"watched": 1, "kind_confirmed": 1, "quiet_start": "23:00", "quiet_end": "06:00"},
            {"watched": 1, "kind_confirmed": 1},
            {"watched": 1, "kind_confirmed": 1, "quiet_start": "22:00", "quiet_end": "07:00", "owner_id": "u-kid"},
        ], children=("u-kid",))
        self.assertEqual(json.loads(out["p0"]["dnm_windows"]),
                         [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "23:00", "end": "06:00", "pauseForwarding": False}])
        self.assertIsNone(out["p1"]["dnm_windows"])
        self.assertIsNone(out["p2"]["dnm_windows"])               # children can't have do-not-monitor hours
        self.assertEqual({out[k]["quiet_start"] for k in out}, {""})


if __name__ == "__main__":
    import unittest
    unittest.main()
