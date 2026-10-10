"""Files on tasks (SPEC §17): attaching tickets to any task in the files folder, who may see them, what ticking
off / unticking / deleting does (30-day undo), the folder not being connected, the reminder's *Open ticket*
button and its deep link, and what the Household Assistant says. Monday 21 September 2026 (test_api.NOW)."""
import _env  # noqa: F401  (must be first)

import io
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from test_api import ADMIN, ALICE, BOB, NOW, ApiBase, link
from test_tools import call

from app import config, db, housekeeping, maint_files, reminders, task_files

PDF = b"%PDF-1.4 ticket"


class TaskFilesBase(ApiBase):
    def setUp(self):
        super().setUp()
        maint_files.reset()
        self.share = tempfile.mkdtemp(prefix="share_")
        config.SHARE_ROOT = self.share
        self.folder = os.path.join(self.share, "household", "files")
        os.makedirs(os.path.dirname(self.folder))

    def tearDown(self):
        shutil.rmtree(self.share, ignore_errors=True)
        config.utcnow = lambda: NOW
        super().tearDown()

    def set_folder(self):
        r = self.put("/api/admin/settings", {"maintenance_files_path": self.folder}, ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["maintenanceFiles"]["online"])

    def attach(self, task, name="ticket.pdf", data=PDF, h=ALICE):
        return self.c.post(f"/api/tasks/{task['id']}/files", files={"file": (name, io.BytesIO(data))}, headers=h)

    def files(self, task, h=ALICE):
        r = self.get(f"/api/tasks/{task['id']}/files", h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def names(self, task):
        return [f["name"] for f in self.files(task)["files"]]

    def task_json(self, task, h=ALICE):
        lid = task["listId"]
        both = self.get(f"/api/lists/{lid}/tasks", h).json() + self.get(f"/api/lists/{lid}/tasks", h, params={"show": "completed"}).json()
        return next(t for t in both if t["id"] == task["id"])

    def tick(self, task, done=True, h=ALICE):
        r = self.patch(f"/api/tasks/{task['id']}", {"completed": done}, h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def path(self, *parts):
        return os.path.join(self.folder, *parts)

    def task_dir(self, task):
        return self.path("Tasks", f"{task['title']} ({task['id'][:8]})")

    def at(self, days):
        """Move the clock `days` from NOW."""
        when = NOW + timedelta(days=days)
        config.utcnow = lambda: when


class TestAttaching(TaskFilesBase):
    def test_attach_list_open_rename_remove(self):
        t = self.add("Concert", due_date="2026-09-26", due_time="19:30")
        self.assertEqual(self.attach(t).status_code, 503)                       # no files folder yet
        d = self.files(t)
        self.assertEqual((d["files"], d["folder"]["configured"], d["maxFiles"]), ([], False, 10))
        self.set_folder()                                                        # Maintenance stays off
        f = self.attach(t).json()
        self.assertEqual((f["name"], f["size"], f["keepAfterDone"]), ("ticket.pdf", len(PDF), False))
        self.assertTrue(os.path.isfile(os.path.join(self.task_dir(t), "ticket.pdf")))
        self.assertEqual(self.attach(t).json()["name"], "ticket (2).pdf")
        self.assertEqual(self.task_json(t)["fileCount"], 2)
        # open: inline, sandboxed; ?download=1 saves it
        r = self.get(f"/api/tasks/{t['id']}/files/{f['id']}")
        self.assertEqual((r.status_code, r.content, r.headers["content-security-policy"]), (200, PDF, "sandbox"))
        self.assertTrue(r.headers["content-disposition"].startswith("inline"))
        r = self.get(f"/api/tasks/{t['id']}/files/{f['id']}?download=1")
        self.assertTrue(r.headers["content-disposition"].startswith("attachment"))
        # rename: in the folder too, the extension stays
        r = self.patch(f"/api/tasks/{t['id']}/files/{f['id']}", {"name": "Concert ticket"})
        self.assertEqual(r.json()["name"], "Concert ticket.pdf")
        self.assertTrue(os.path.isfile(os.path.join(self.task_dir(t), "Concert ticket.pdf")))
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}/files/{f['id']}", {"name": "ticket (2).pdf"}).json()["name"],
                         "ticket (2) (2).pdf")                                   # taken: a free name
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}/files/{f['id']}", {"name": " "}).status_code, 422)
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}/files/{f['id']}", {"size": 1}).status_code, 422)
        # Keep after done
        self.assertTrue(self.patch(f"/api/tasks/{t['id']}/files/{f['id']}", {"keepAfterDone": True}).json()["keepAfterDone"])
        self.assertEqual(self.patch(f"/api/tasks/{t['id']}/files/{f['id']}", {"keepAfterDone": "yes"}).status_code, 422)
        # remove: to _deleted
        self.assertEqual(self.delete(f"/api/tasks/{t['id']}/files/{f['id']}").status_code, 204)
        self.assertEqual(self.names(t), ["ticket (2).pdf"])
        self.assertTrue(os.path.isfile(self.path("_deleted", "2026-09-21", "Tasks", f"Concert ({t['id'][:8]})", "ticket (2) (2).pdf")))
        self.assertEqual(self.get(f"/api/tasks/{t['id']}/files/{f['id']}").status_code, 404)

    def test_rules(self):
        self.set_folder()
        t = self.add("Flight")
        for i in range(task_files.MAX_FILES):
            self.assertEqual(self.attach(t, f"p{i}.pdf").status_code, 201)
        r = self.attach(t, "one-too-many.pdf")
        self.assertEqual(r.status_code, 422)
        self.assertIn("at most 10 files", r.json()["detail"])
        self.assertEqual(self.attach(self.add("x"), name="empty.txt", data=b"").status_code, 422)
        big = b"x" * (maint_files.MAX_BYTES + 1)
        self.assertEqual(self.attach(self.add("y"), name="big.bin", data=big).status_code, 413)
        done = self.add("Done already")
        self.tick(done)
        self.assertEqual(self.attach(done).status_code, 409)
        self.assertEqual(self.attach({"id": "nope"}).status_code, 404)
        # "Tasks" is never a maintenance item's folder
        with db.get_conn() as conn:
            self.assertEqual(maint_files.item_folder_name(conn, {"id": "i1", "name": "Tasks"}), "Tasks (2)")

    def test_only_people_who_see_the_task(self):
        self.set_folder()
        mine = self.add("Doctor", list_id=self.mine_id())                        # Alice's personal list
        f = self.attach(mine).json()
        self.assertEqual(self.attach(mine, h=BOB).status_code, 403)
        self.assertEqual(self.get(f"/api/tasks/{mine['id']}/files", BOB).status_code, 403)
        self.assertEqual(self.get(f"/api/tasks/{mine['id']}/files/{f['id']}", BOB).status_code, 403)
        self.assertEqual(self.patch(f"/api/tasks/{mine['id']}/files/{f['id']}", {"name": "x"}, BOB).status_code, 403)
        self.assertEqual(self.delete(f"/api/tasks/{mine['id']}/files/{f['id']}", BOB).status_code, 403)
        # nor through Maintenance's own file route
        self.assertEqual(self.put("/api/admin/maintenance", {"enabled": True}, ADMIN).status_code, 200)
        self.assertEqual(self.get(f"/api/maintenance/files/{f['id']}", BOB).status_code, 403)
        self.assertEqual(self.get(f"/api/maintenance/files/{f['id']}").status_code, 200)
        # a file id from another task isn't this task's
        other = self.add("Shared one")
        self.assertEqual(self.get(f"/api/tasks/{other['id']}/files/{f['id']}").status_code, 404)
        # on the shared list, Bob sees and adds
        g = self.attach(other, h=BOB)
        self.assertEqual(g.status_code, 201)
        self.assertEqual(self.names(other), ["ticket.pdf"])

    def test_maintenance_jobs_keep_their_files_by_default(self):
        self.set_folder()
        self.assertEqual(self.put("/api/admin/maintenance", {"enabled": True}, ADMIN).status_code, 200)
        lid = self.get("/api/maintenance").json()["listId"]
        job = self.add("Fix tap", list_id=lid)
        f = self.attach(job, "receipt.pdf").json()
        self.assertTrue(f["keepAfterDone"])
        self.assertTrue(os.path.isfile(self.path("Jobs", f"Fix tap ({job['id'][:8]})", "receipt.pdf")))
        old = self.c.post(f"/api/maintenance/files?task_id={job['id']}", files={"file": ("photo.jpg", io.BytesIO(b"x"))},
                          headers=ALICE).json()
        self.tick(job)
        self.assertEqual(self.names(job), ["receipt.pdf", "photo.jpg"])         # both kept
        self.assertEqual(old["name"], "photo.jpg")


class TestDoneAndUndo(TaskFilesBase):
    def setUp(self):
        super().setUp()
        self.set_folder()
        self.t = self.add("Theatre", due_date="2026-09-25")
        self.ticket = self.attach(self.t).json()
        self.kept = self.attach(self.t, "seating plan.pdf").json()
        self.patch(f"/api/tasks/{self.t['id']}/files/{self.kept['id']}", {"keepAfterDone": True})
        self.where = os.path.join(self.task_dir(self.t), "ticket.pdf")
        self.gone = self.path("_deleted", "2026-09-21", "Tasks", f"Theatre ({self.t['id'][:8]})", "ticket.pdf")

    def test_ticking_off_moves_files_and_unticking_brings_them_back(self):
        self.tick(self.t)
        self.assertFalse(os.path.exists(self.where))
        self.assertTrue(os.path.isfile(self.gone))
        self.assertEqual(self.names(self.t), ["seating plan.pdf"])               # Keep stays with the done task
        self.assertEqual(self.task_json(self.t)["fileCount"], 1)
        self.assertEqual(self.get(f"/api/tasks/{self.t['id']}/files/{self.ticket['id']}").status_code, 404)
        self.at(29)                                                              # within 30 days
        self.tick(self.t, False)
        self.assertTrue(os.path.isfile(self.where))
        self.assertFalse(os.path.exists(self.gone))
        self.assertEqual(self.names(self.t), ["ticket.pdf", "seating plan.pdf"])
        self.assertEqual(self.get(f"/api/tasks/{self.t['id']}/files/{self.ticket['id']}").content, PDF)

    def test_after_30_days_they_are_gone(self):
        self.tick(self.t)
        self.at(32)
        housekeeping.run_pass_blocking()                                        # forgets the row, empties _deleted
        self.assertFalse(os.path.exists(self.gone))
        self.tick(self.t, False)
        self.assertEqual(self.names(self.t), ["seating plan.pdf"])

    def test_a_name_taken_meanwhile(self):
        self.tick(self.t)
        self.tick(self.t, False)
        self.tick(self.t)
        self.assertTrue(os.path.isfile(self.gone))
        self.tick(self.t, False)
        self.assertEqual(self.names(self.t), ["ticket.pdf", "seating plan.pdf"])
        # ticked off, then a new ticket.pdf added in the same folder by hand: the one coming back gets a free name
        self.tick(self.t)
        with open(self.where, "wb") as fh:
            fh.write(b"someone else's")
        self.tick(self.t, False)
        self.assertEqual(sorted(self.names(self.t)), ["seating plan.pdf", "ticket (2).pdf"])

    def test_keep_turned_off_on_a_done_task(self):
        self.tick(self.t)
        r = self.patch(f"/api/tasks/{self.t['id']}/files/{self.kept['id']}", {"keepAfterDone": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.names(self.t), [])

    def test_deleting_the_task_moves_every_file(self):
        self.assertEqual(self.delete(f"/api/tasks/{self.t['id']}").status_code, 204)
        moved = self.path("_deleted", "2026-09-21", "Tasks", f"Theatre ({self.t['id'][:8]})")
        self.assertEqual(sorted(os.listdir(moved)), ["seating plan.pdf", "ticket.pdf"])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM maint_files").fetchone()[0], 0)

    def test_deleting_a_list_moves_its_tasks_files(self):
        r = self.post("/api/lists", {"name": "Trip", "kind": "shared"})
        self.assertEqual(r.status_code, 201, r.text)
        trip = self.add("Train", list_id=r.json()["id"])
        self.attach(trip, "train.pdf")
        self.assertEqual(self.delete(f"/api/lists/{trip['listId']}").status_code, 204)
        self.assertTrue(os.path.isfile(self.path("_deleted", "2026-09-21", "Tasks", f"Train ({trip['id'][:8]})", "train.pdf")))

    def test_the_assistant_ticking_off(self):
        res = call("todo.items.done", confirm=True, task="Theatre")
        self.assertIn("Ticked off", res["text"])
        task_files.flush()                                                      # the app does this a moment later
        self.assertTrue(os.path.isfile(self.gone))

    def test_purged_done_task_keeps_its_kept_files(self):
        self.tick(self.t)
        self.at(config.COMPLETED_RETENTION_DAYS + 2)
        housekeeping.run_pass_blocking()
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM maint_files").fetchone()[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.task_dir(self.t), "seating plan.pdf")))


class TestNotConnected(TaskFilesBase):
    def test_moves_wait_for_the_folder(self):
        self.set_folder()
        t = self.add("Gig")
        f = self.attach(t).json()
        where = os.path.join(self.task_dir(t), "ticket.pdf")
        os.remove(self.path(maint_files.MARKER))                                # the NAS went away
        maint_files.check()
        self.assertEqual(self.attach(t).status_code, 503)
        self.assertEqual(self.get(f"/api/tasks/{t['id']}/files/{f['id']}").status_code, 503)
        self.assertEqual(self.files(t)["folder"]["online"], False)
        self.tick(t)                                                            # ticking off still works
        self.assertTrue(os.path.isfile(where))
        self.assertEqual(self.names(t), [])
        self.tick(t, False)                                                     # unticked before it moved
        self.tick(t)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT pending FROM maint_files").fetchone()[0], "remove")
        housekeeping.run_pass_blocking()                                        # a row waiting isn't forgotten
        self.assertTrue(self.post("/api/admin/maintenance/files/check", {"useThisFolder": True}, ADMIN).json()["online"])
        task_files.flush()                                                      # with the 5-minute check
        self.assertFalse(os.path.exists(where))
        self.assertTrue(os.path.isfile(self.path("_deleted", "2026-09-21", "Tasks", f"Gig ({t['id'][:8]})", "ticket.pdf")))
        # unticked while away: back once it's connected
        os.remove(self.path(maint_files.MARKER))
        maint_files.check()
        self.tick(t, False)
        self.assertEqual(self.names(t), ["ticket.pdf"])
        self.post("/api/admin/maintenance/files/check", {"useThisFolder": True}, ADMIN)
        task_files.flush()
        self.assertTrue(os.path.isfile(where))


class TestTicketReminder(TaskFilesBase):
    def setUp(self):
        super().setUp()
        self.set_folder()
        link("alice", "mobile_app_alice")
        with db.get_conn() as c:
            c.execute("INSERT INTO user_reminder_offsets (id, user_id, minutes_before, created_at) VALUES ('o1', 'u_alice', 60, ?)",
                      (config.now_iso(),))
        self.old_page, config.SIDEBAR_PAGE = config.SIDEBAR_PAGE, "/a1b2c3d4_household_todo"
        self.sent = []

    def tearDown(self):
        config.SIDEBAR_PAGE = self.old_page
        super().tearDown()

    def fake(self, service, title, message, data=None):
        self.sent.append(data or {})
        return True

    def remind(self):
        config.utcnow = lambda: datetime(2026, 9, 21, 18, 45, tzinfo=timezone.utc)
        return reminders.run_task_reminder_pass_blocking(sender=self.fake)

    def test_open_ticket_with_exactly_one_pdf(self):
        t = self.add("Concert", list_id=self.mine_id(), due_date="2026-09-21", due_time="19:30")
        f = self.attach(t).json()
        self.attach(t, "map.png", b"not really a png")
        self.assertEqual(self.remind(), 1)
        self.assertEqual(self.sent[-1]["actions"], [{"action": "URI", "title": "Open ticket",
                                                    "uri": f"/a1b2c3d4_household_todo/ticket/{t['id']}/{f['id']}"}])
        # the link answers with the page's #/ticket/<task>/<file>
        r = self.c.get(f"/ticket/{t['id']}/{f['id']}", headers=ALICE, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (307, f"../../../#/ticket/{t['id']}/{f['id']}"))

    def test_no_button_with_two_pdfs_or_none(self):
        t = self.add("Two", list_id=self.mine_id(), due_date="2026-09-21", due_time="19:30")
        self.attach(t)
        self.attach(t, "return.pdf")
        u = self.add("None", list_id=self.mine_id(), due_date="2026-09-21", due_time="19:30")
        self.assertEqual(self.remind(), 2)
        self.assertEqual([d.get("actions") for d in self.sent], [None, None])
        self.assertIsNotNone(u)


class TestAssistant(TaskFilesBase):
    def test_says_how_many_files(self):
        self.set_folder()
        t = self.add("Flight", due_date="2026-09-21")
        self.attach(t)
        self.attach(t, "boarding pass.pdf")
        res = call("todo.tasks", when="today")
        self.assertIn("Flight (today, 2 files attached)", res["text"])
        self.assertEqual(res["items"][0]["files"], 2)


class TestMigration(ApiBase):
    def test_older_job_files_stay_when_done(self):
        with db.get_conn() as c:
            c.execute("INSERT INTO maint_files (id, task_id, rel_path, name, size, created_at) "
                      "VALUES ('f1', 't1', 'Jobs/x/a.pdf', 'a.pdf', 1, ?)", (config.now_iso(),))
            c.execute("INSERT INTO maint_files (id, item_id, rel_path, name, size, created_at) "
                      "VALUES ('f2', 'i1', 'Item/b.pdf', 'b.pdf', 1, ?)", (config.now_iso(),))
            fresh = [tuple(r)[1:] for r in c.execute("PRAGMA table_info(maint_files)")]
            for col in ("removed_at", "removed_rel", "pending", "keep_after_done"):
                c.execute(f"ALTER TABLE maint_files DROP COLUMN {col}")
        db.init_db()
        with db.get_conn() as c:
            self.assertEqual([tuple(r)[1:] for r in c.execute("PRAGMA table_info(maint_files)")], fresh)
            self.assertEqual(dict(c.execute("SELECT id, keep_after_done FROM maint_files").fetchall()), {"f1": 1, "f2": 0})
        db.init_db()   # idempotent
