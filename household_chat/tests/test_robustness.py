"""The 13 Oct 2026 review's robustness fixes: nothing left behind in /data, nightly jobs after a restart, held
notifications across a restart, retention that tells the pages, a restore that waits for running work, a file
that can't be sent twice, and files the browser asks about again."""
import _env  # noqa: F401

import concurrent.futures
import json
import os
import threading
import time
from datetime import datetime, timedelta
from unittest import mock

from base import ADMIN, NISHA, TARUN, ApiTestCase, sql

from app import chats, config, db, exporter, files, housekeeping, main, notifier
from app.common import backup_core
from app.live import hub
from app.main import app
from common_tests.ingress import ingress_client


def temp_files():
    return sorted(f for f in os.listdir(config.DATA_DIR) if f.startswith((".backup-", ".export-", ".restore-")))


class TempFileTests(ApiTestCase):
    """CI2: downloads and backups that fail leave nothing in /data; leftovers are swept at start-up."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA)
        self.hh = self.household()
        self.lenient = ingress_client(app, raise_server_exceptions=False)

    def test_failed_export_and_backup_leave_nothing(self):
        self.send(self.hh, "hello", NISHA)
        with mock.patch.object(exporter, "_write_zip", side_effect=OSError("disk full")):
            r = self.lenient.get(f"/api/conversations/{self.hh}/export", headers=self.headers(NISHA))
        self.assertEqual(r.status_code, 500)
        with mock.patch.object(backup_core, "write_zip", side_effect=OSError("disk full")):
            r = self.lenient.get("/api/admin-storage-download-db?includeFiles=1", headers=self.headers(ADMIN))
        self.assertEqual(r.status_code, 500)
        self.assertEqual(temp_files(), [])
        self.assertEqual(exporter._running, set())

    def test_a_file_that_vanishes_during_the_export(self):
        up = self.ok(self.upload(self.hh, "a.txt", b"abc", NISHA), 201)
        self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        real = __import__("zipfile").ZipFile.write

        def write(z, filename, arcname=None, *a, **kw):
            if arcname and arcname.startswith("files/"):
                raise FileNotFoundError(filename)
            return real(z, filename, arcname, *a, **kw)
        exporter.export_limit.reset()
        with mock.patch("zipfile.ZipFile.write", write):
            r = self.get(f"/api/conversations/{self.hh}/export", NISHA)
        exporter.export_limit.reset()
        self.assertEqual(r.status_code, 200)
        z = __import__("zipfile").ZipFile(__import__("io").BytesIO(r.content))
        self.assertIn("a.txt — no longer available", z.read("Household.html").decode())
        self.assertEqual([n for n in z.namelist() if n.startswith("files/")], [])

    def test_leftovers_are_swept(self):
        old = time.time() - 7 * 3600
        for name in (".backup-x.zip", ".export-y.zip", ".restore-z.db", ".backup-new.zip", "chat.db.keep"):
            with open(os.path.join(config.DATA_DIR, name), "w") as f:
                f.write("x")
            if name != ".backup-new.zip":
                os.utime(os.path.join(config.DATA_DIR, name), (old, old))
        self.assertEqual(main.remove_stale_temp(6 * 3600), 3)         # the hourly sweep: only old ones
        self.assertEqual(temp_files(), [".backup-new.zip"])
        self.assertEqual(main.remove_stale_temp(0), 1)                 # at start-up: all of them
        self.assertEqual(temp_files(), [])
        self.assertTrue(os.path.exists(os.path.join(config.DATA_DIR, "chat.db.keep")))

    def headers(self, user):
        from base import headers
        return headers(user)


class NightlyTests(ApiTestCase):
    """CI3: the nightly jobs run once a day, also when the app restarted (or was off) at 03:30."""

    def at(self, hh, mm, day=13):
        return mock.patch.object(config, "local_now", lambda: datetime(2026, 10, day, hh, mm, tzinfo=config.tz()))

    def test_runs_after_a_restart(self):
        ran = []
        with mock.patch.object(housekeeping, "nightly", lambda: ran.append(1)):
            with self.at(10, 0):                      # the very first start: waits for the next night
                self.assertFalse(housekeeping.nightly_due())
            with self.at(3, 0, 14):
                self.assertFalse(housekeeping.nightly_due())
            with self.at(3, 31, 14):
                self.assertTrue(housekeeping.nightly_due())
                self.assertFalse(housekeeping.nightly_due())          # once a day
            # the app was stopped over the night of the 15th and starts at 09:00: it runs then
            with self.at(9, 0, 15):
                self.assertTrue(housekeeping.nightly_due())
        self.assertEqual(len(ran), 2)
        self.assertEqual(json.loads(sql("SELECT value FROM app_settings WHERE key = ?", (housekeeping.NIGHTLY_KEY,))[0]["value"]),
                         "2026-10-15")


class HeldNotificationTests(ApiTestCase):
    """CI5: pushes held for quiet hours (or batching) survive a restart."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()
        self.notify_to(TARUN, "mobile_app_tarun")

    def test_saved_and_loaded(self):
        now = config.local_now()
        self.ok(self.put("/api/me/settings", {"quietStart": (now - timedelta(minutes=5)).strftime("%H:%M"),
                                              "quietEnd": (now + timedelta(minutes=5)).strftime("%H:%M")}, TARUN))
        self.send(self.hh, "@Tarun night", NISHA, mentions=[TARUN["id"]])
        notifier.flush()                                   # a tick: still quiet, and saved
        self.assertEqual(self.sent.items, [])
        notifier.reset()                                   # the app stops …
        self.assertEqual(notifier.load_held(), 1)          # … and starts again
        self.ok(self.put("/api/me/settings", {"quietStart": None, "quietEnd": None}, TARUN))
        notifier.flush()
        self.assertEqual(self.sent.items[-1]["message"], "Nisha: @Tarun night")
        notifier.reset()
        self.assertEqual(notifier.load_held(), 0)          # sent: not held any more
        # a batch's time is kept as wall-clock time
        self.ok(self.put("/api/me/settings", {"notifyLevel": "all"}, TARUN))
        self.send(self.hh, "one", NISHA)
        self.send(self.hh, "two", NISHA)
        notifier.save_held(True)
        saved = json.loads(sql("SELECT value FROM app_settings WHERE key = ?", (notifier.HELD_KEY,))[0]["value"])
        self.assertEqual(len(saved), 1)
        self.assertAlmostEqual(saved[0][3], time.time() + notifier.BATCH_SECONDS, delta=5)
        notifier.reset()
        notifier.load_held()
        notifier._held[(TARUN["id"], self.hh)]["until"] = time.monotonic() - 1
        notifier.flush()
        self.assertEqual(self.sent.items[-1]["message"], "Nisha: two")
        # a restore forgets them (they belong to the database that was replaced)
        self.send(self.hh, "three", NISHA)
        self.send(self.hh, "four", NISHA)
        notifier.forget_held()
        self.assertEqual((notifier._held, notifier.load_held()), ({}, 0))


class RetentionTests(ApiTestCase):
    """CI6: messages deleted by age leave replies saying so, open pages are told, and kept files still count."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def test_retention(self):
        up = self.ok(self.upload(self.hh, "old.pdf", b"%PDF-1 old", NISHA), 201)
        old = self.send(self.hh, "old", NISHA, attachmentIds=[up["id"]])
        reply = self.send(self.hh, "new reply", TARUN, replyTo=old["id"])
        sql("UPDATE messages SET created_at = '2000-01-01T00:00:00.000Z' WHERE id = ?", (old["id"],))
        self.ok(self.put("/api/admin/settings", {"message_retention_days": 30}))
        got = []
        real = hub.publish
        with mock.patch.object(hub, "publish", lambda uids, name, data, eid=None: got.append((name, data)) or real(uids, name, data, eid)):
            self.assertEqual(housekeeping.retention(), 1)
        self.assertIn(("message_removed", {"conversationId": self.hh, "messageIds": [old["id"]]}), got)
        msgs = {m["id"]: m for m in self.ok(self.get(f"/api/conversations/{self.hh}/messages", TARUN))["messages"]}
        self.assertEqual(msgs[reply["id"]]["replyTo"], {"gone": True})
        # the file stays (Keep the files is on by default) and is still counted
        kept = sql("SELECT rel_path, size FROM kept_files")
        self.assertEqual(len(kept), 1)
        self.assertTrue(os.path.isfile(os.path.join(config.SHARE_DIR, kept[0]["rel_path"])))
        st = self.ok(self.get("/api/admin/storage"))
        self.assertEqual((st["keptFiles"], st["keptBytes"]), (1, len(b"%PDF-1 old")))
        self.assertEqual(st["filesBytes"], len(b"%PDF-1 old"))
        self.assertEqual(sql("SELECT bytes FROM file_usage")[0]["bytes"], len(b"%PDF-1 old"))
        # once it's gone from the folder, it's forgotten
        os.remove(os.path.join(config.SHARE_DIR, kept[0]["rel_path"]))
        files.check_missing()
        self.assertEqual((sql("SELECT COUNT(*) AS n FROM kept_files")[0]["n"], sql("SELECT bytes FROM file_usage")[0]["bytes"]), (0, 0))


class RestoreGateTests(ApiTestCase):
    """CI7: a restore waits for the connections that are open (requests, background jobs) and holds new ones
    until the new database is in place — no sleep and hope."""

    def test_maintenance_waits_for_open_connections_and_holds_new_ones(self):
        opened, release = threading.Event(), threading.Event()
        log = []

        def long_job():
            with db.get_conn() as conn:
                opened.set()
                conn.execute("SELECT 1")
                release.wait(5)
                log.append("job done")

        def late_job():
            with db.get_conn() as conn:
                conn.execute("SELECT 1")
                log.append("late job")
        t = threading.Thread(target=long_job)
        t.start()
        opened.wait(5)
        threading.Timer(0.3, release.set).start()
        with db.maintenance(timeout=5):
            log.append("swap")
            late = threading.Thread(target=late_job)
            late.start()
            time.sleep(0.2)
            with db.get_conn() as conn:                 # the restore's own connections go ahead
                conn.execute("SELECT 1")
            log.append("swapped")
        late.join(5)
        t.join(5)
        self.assertEqual(log, ["job done", "swap", "swapped", "late job"])

    def test_nested_connections_dont_wait_for_themselves(self):
        with db.get_conn():
            with db.get_conn() as inner:
                inner.execute("SELECT 1")
        self.assertEqual(getattr(db._inside, "depth", 0), 0)

    def test_restore_still_works(self):
        self.enable(ADMIN, NISHA)
        hh = self.household()
        self.send(hh, "before the backup", NISHA)
        backup = self.get("/api/admin-storage-download-db").content
        self.send(hh, "after the backup", NISHA)
        self.ok(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=backup))
        bodies = [m["body"] for m in self.ok(self.get(f"/api/conversations/{hh}/messages", NISHA))["messages"]]
        self.assertIn("before the backup", bodies)
        self.assertNotIn("after the backup", bodies)
        self.assertEqual(temp_files(), [])

    def test_restore_busy_when_work_doesnt_finish(self):
        self.enable(ADMIN)
        backup = self.get("/api/admin-storage-download-db").content
        from app.routers import admin as admin_router
        opened, release = threading.Event(), threading.Event()

        def stuck():
            with db.get_conn():
                opened.set()
                release.wait(10)
        t = threading.Thread(target=stuck)
        t.start()
        opened.wait(5)
        try:
            with mock.patch.object(admin_router, "RESTORE_WAIT", 0.2):
                r = self.req("POST", "/api/admin-storage-import-db", ADMIN, content=backup)
        finally:
            release.set()
            t.join(5)
        self.assertEqual(r.status_code, 503)
        self.assertFalse(db.RESTORING.is_set())
        self.assertEqual(temp_files(), [])
        self.ok(self.get("/api/me"))


class SendOnceTests(ApiTestCase):
    """CI8: an upload sent twice at the same moment ends up in one message only."""

    def test_two_sends_of_one_file(self):
        self.enable(ADMIN, NISHA)
        hh = self.household()
        up = self.ok(self.upload(hh, "a.txt", b"a", NISHA), 201)
        real = chats.resolve_expiry

        def slow(conv, user, expires_in):                  # between the check and the update
            time.sleep(0.2)
            return real(conv, user, expires_in)
        with mock.patch.object(chats, "resolve_expiry", slow):
            with concurrent.futures.ThreadPoolExecutor(2) as ex:
                rs = list(ex.map(lambda i: self.post(f"/api/conversations/{hh}/messages",
                                                     {"body": f"take {i}", "attachmentIds": [up["id"]]}, NISHA), range(2)))
        self.assertEqual(sorted(r.status_code for r in rs), [201, 422])
        winner = [r.json() for r in rs if r.status_code == 201][0]
        self.assertEqual(sql("SELECT message_id FROM attachments WHERE id = ?", (up["id"],))[0]["message_id"], winner["id"])
        bodies = [m["body"] for m in self.ok(self.get(f"/api/conversations/{hh}/messages", NISHA))["messages"]]
        self.assertEqual(len([b for b in bodies if b.startswith("take")]), 1)


class FileServingTests(ApiTestCase):
    """CI10: a failed upload leaves no file whatever the error; chat files are asked about again (no-cache)
    and answered "304 Not Modified" while they're the same."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def test_any_error_removes_the_upload(self):
        lenient = ingress_client(app, raise_server_exceptions=False)
        from base import headers
        calls_ = []
        real = files.check_pending

        def boom(conn, user_id, incoming):
            calls_.append(1)
            if len(calls_) == 2:                           # in record(), before the row is saved
                raise RuntimeError("database trouble")
            return real(conn, user_id, incoming)
        with mock.patch.object(files, "check_pending", boom):
            r = lenient.post(f"/api/conversations/{self.hh}/uploads?name=a.txt", headers=headers(NISHA), content=b"abc")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(sql("SELECT id FROM attachments"), [])
        self.assertEqual([f for _, _, fs in os.walk(config.SHARE_DIR) for f in fs if f.startswith("a")], [])

    def test_no_cache_and_not_modified(self):
        up = self.ok(self.upload(self.hh, "a.pdf", b"%PDF-1 a", NISHA), 201)
        self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        r = self.get(f"/api/files/{up['id']}", TARUN)
        self.assertEqual(r.headers["cache-control"], "private, no-cache")
        tag = r.headers["etag"]
        again = self.get(f"/api/files/{up['id']}", TARUN, headers={"If-None-Match": tag})
        self.assertEqual((again.status_code, again.content), (304, b""))
        # taken away from someone: they get a 404, never their cached copy
        self.ok(self.post(f"/api/conversations/{self.hh}/leave", user=TARUN))
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN, headers={"If-None-Match": tag}).status_code, 404)
        # changed: the whole file again
        path = os.path.join(config.SHARE_DIR, sql("SELECT rel_path FROM attachments")[0]["rel_path"])
        with open(path, "ab") as f:
            f.write(b" more")
        r = self.get(f"/api/files/{up['id']}", NISHA, headers={"If-None-Match": tag})
        self.assertEqual((r.status_code, r.content), (200, b"%PDF-1 a more"))
