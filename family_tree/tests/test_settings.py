"""Admin → App settings: the settings API, its validation, that a change
applies at once (upload limit, trash purge), the one-time import from the old
app options, and the admin-only guards on the pages that moved into Admin."""
from base import ADMIN, ALICE, ApiTestCase, headers, image_bytes, reset_state, set_app_settings, sql
import _env

import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import timedelta
from unittest import mock

from fastapi.testclient import TestClient

from app import config, db, housekeeping, media, settings
from app.routers import export as export_router
from app.main import app

FEATURE_DEFAULTS = {"feature_reminders": True, "feature_milestones": True, "feature_sides": True,
                    "feature_photo_tagging": True, "feature_photo_fixes": True, "feature_inbox": True,
                    "feature_custom_fields": True, "feature_sources": True, "feature_contacts": True,
                    "feature_duplicates": True, "feature_quiz": True, "feature_printing": True, "feature_export": True,
                    "feature_map": False, "feature_kin_names": False, "feature_script_names": False,
                    "feature_tithi": False, "feature_ceremonies": False}
DEFAULTS = {"trash_days": 90, "max_upload_mb": 20, "media_path": _env.MEDIA_PATH, "relationship_language": "en", "name_order": "given_first",
            "default_phone_code": "+1", "tithi_rule": "aparahna",
            "map_tiles_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "nominatim_url": "https://nominatim.openstreetmap.org", **FEATURE_DEFAULTS}


def V(**changed):
    """The full settings values: the defaults with some changed."""
    return dict(DEFAULTS, **changed)


class SettingsApi(ApiTestCase):
    ALL_FEATURES = False                # a new install's switches
    def test_get_admin_only(self):
        body = self.ok(self.get("/api/admin/settings", user=ADMIN))
        self.assertEqual(body["values"], DEFAULTS)
        self.assertEqual(body["defaults"], DEFAULTS)
        self.assertEqual(body["meta"], {k: {"restartRequired": False} for k in tuple(DEFAULTS)})
        self.assertEqual(body["media"], {"path": _env.MEDIA_PATH, "online": True, "reason": None})
        self.assertNotIn("importedFromConfig", body)
        self.assertEqual(self.get("/api/admin/settings", user=ALICE).status_code, 403)

    def test_put_admin_only(self):
        r = self.put("/api/admin/settings", {"trash_days": 30}, user=ALICE)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(settings.get("trash_days"), 90)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_settings")[0]["n"], 0)

    def test_put_partial_keeps_other_keys(self):
        body = self.ok(self.put("/api/admin/settings", {"trash_days": 30}, user=ADMIN))
        self.assertEqual(body["values"], V(trash_days=30, max_upload_mb=20))
        body = self.ok(self.put("/api/admin/settings", {"max_upload_mb": 5}, user=ADMIN))
        self.assertEqual(body["values"], V(trash_days=30, max_upload_mb=5))
        self.assertEqual(self.ok(self.get("/api/admin/settings", user=ADMIN))["values"],
                         V(trash_days=30, max_upload_mb=5))
        rows = {r["key"]: r for r in sql("SELECT * FROM app_settings")}
        self.assertEqual(json.loads(rows["trash_days"]["value"]), 30)
        self.assertEqual(rows["trash_days"]["updated_by"], ADMIN["id"])
        self.assertTrue(rows["trash_days"]["updated_at"])

    def test_only_changed_keys_written(self):
        self.ok(self.put("/api/admin/settings", dict(DEFAULTS), user=ADMIN))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_settings")[0]["n"], 0)
        self.ok(self.put("/api/admin/settings", {"trash_days": 90, "max_upload_mb": 8}, user=ADMIN))
        self.assertEqual([r["key"] for r in sql("SELECT key FROM app_settings")], ["max_upload_mb"])
        self.ok(self.put("/api/admin/settings", {}, user=ADMIN))           # nothing to do is fine

    def test_bad_values_422(self):
        bad = [
            {"trash_days": 6}, {"trash_days": 3651}, {"trash_days": -1},
            {"max_upload_mb": 0}, {"max_upload_mb": 101},
            {"trash_days": "30"}, {"trash_days": 30.5}, {"trash_days": 30.0}, {"trash_days": True},
            {"trash_days": None}, {"trash_days": [30]}, {"max_upload_mb": {"mb": 5}},
            {"nope": 1}, {"_imported": ["trash_days"]}, {"trash_days": 30, "extra": 1},
        ]
        for body in bad:
            with self.subTest(body=body):
                r = self.put("/api/admin/settings", body, user=ADMIN)
                self.assertEqual(r.status_code, 422, r.text)
                self.assertIsInstance(r.json()["detail"], str)
        self.assertEqual(settings.all(), DEFAULTS)                         # nothing half-applied
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_settings")[0]["n"], 0)

    def test_readable_messages(self):
        r = self.put("/api/admin/settings", {"trash_days": 5}, user=ADMIN)
        self.assertEqual(r.json()["detail"], "Days in trash must be a whole number from 7 to 3650.")
        r = self.put("/api/admin/settings", {"max_upload_mb": "big"}, user=ADMIN)
        self.assertEqual(r.json()["detail"], "Largest upload (MB) must be a whole number from 1 to 100.")
        r = self.put("/api/admin/settings", {"colour": "red"}, user=ADMIN)
        self.assertEqual(r.json()["detail"], "Unknown setting: colour.")

    def test_nan_infinity_and_non_objects_422(self):
        h = {"Content-Type": "application/json"}
        for raw in ('{"trash_days": NaN}', '{"max_upload_mb": Infinity}', '[1, 2]', '"x"', '12'):
            with self.subTest(raw=raw):
                r = self.req("PUT", "/api/admin/settings", ADMIN, content=raw, headers=h)
                self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(settings.all(), DEFAULTS)

    def test_edges_accepted(self):
        for body in ({"trash_days": 7}, {"trash_days": 3650}, {"max_upload_mb": 1}, {"max_upload_mb": 100}):
            with self.subTest(body=body):
                self.assertEqual(self.ok(self.put("/api/admin/settings", body, user=ADMIN))["values"][next(iter(body))],
                                 next(iter(body.values())))

    def test_me_reports_live_values(self):
        me = self.ok(self.get("/api/me", user=ALICE))
        self.assertEqual((me["maxUploadMb"], me["trashDays"]), (20, 90))
        self.ok(self.put("/api/admin/settings", {"max_upload_mb": 3, "trash_days": 14}, user=ADMIN))
        me = self.ok(self.get("/api/me", user=ALICE))
        self.assertEqual((me["maxUploadMb"], me["trashDays"]), (3, 14))

    def test_invalid_stored_value_falls_back_to_default(self):
        now = config.now_iso()
        sql("INSERT INTO app_settings (key, value, updated_at) VALUES ('trash_days', '2', ?), "
            "('max_upload_mb', 'not json', ?)", (now, now))
        settings.invalidate()
        self.assertEqual(settings.all(), DEFAULTS)


class SettingsApplyAtOnce(ApiTestCase):
    """A change is used by the feature on the very next request — no restart."""
    ALL_FEATURES = False                # a new install's switches

    def upload(self, data, user=ALICE):
        return self.post("/api/media", files={"file": ("big.pdf", data, "application/pdf")}, user=user)

    def test_upload_limit_applies_immediately(self):
        data = b"%PDF-1.7\n" + b"0" * (int(1.5 * 1024 * 1024))
        self.ok(self.upload(data), 201)                                   # 1.5 MB is fine at 20 MB
        self.ok(self.put("/api/admin/settings", {"max_upload_mb": 1}, user=ADMIN))
        r = self.upload(data)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("1 MB", r.json()["detail"])
        self.assertIn("App settings", r.json()["detail"])
        self.ok(self.put("/api/admin/settings", {"max_upload_mb": 2}, user=ADMIN))
        self.ok(self.upload(data), 201)

    def test_body_size_guard_uses_the_setting(self):
        # the middleware refuses bodies over (limit + 1) MB before parsing them
        set_app_settings(max_upload_mb=1)
        r = self.post("/api/people", {"given_names": "x", "biography": "y" * (3 * 1024 * 1024)})
        self.assertEqual(r.status_code, 413)
        self.assertIn("1 MB", r.json()["detail"])
        set_app_settings(max_upload_mb=10)
        self.assertEqual(self.post("/api/people", {"given_names": "x", "biography": "y" * (3 * 1024 * 1024)}).status_code, 422)

    def test_purge_uses_new_trash_days(self):
        a = self.person("Ann")
        self.ok(self.delete(f"/api/people/{a}"))
        forty_days_ago = (config.utcnow() - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sql("UPDATE people SET deleted_at = ? WHERE id = ?", (forty_days_ago, a))
        self.assertEqual(housekeeping.purge_expired()["people"], 0)      # 90 days: kept
        self.assertEqual(self.ok(self.get("/api/trash", user=ADMIN))["trashDays"], 90)
        self.ok(self.put("/api/admin/settings", {"trash_days": 30}, user=ADMIN))
        self.assertEqual(self.ok(self.get("/api/trash", user=ADMIN))["trashDays"], 30)
        self.assertEqual(housekeeping.purge_expired()["people"], 1)      # 30 days: gone
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)


class NoLegacyOptions(ApiTestCase):
    """The app's Configuration tab holds only admin_users. Old values
    of the moved options in options.json are ignored (not carried over)."""
    ALL_FEATURES = False                # a new install's switches

    def options_file(self, data):
        fd, path = tempfile.mkstemp(suffix=".json", dir=_env.TMP)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        self.addCleanup(os.remove, path)
        return path

    def block(self, text, name):
        """The keys directly under a top-level YAML block (no yaml module needed)."""
        m = re.search(rf"^{name}:\n((?:[ #].*\n|\n)*)", text, re.M)
        self.assertIsNotNone(m, name)
        return re.findall(r"^  ([A-Za-z_]+):", m.group(1), re.M)

    def test_config_yaml_has_only_admin_users(self):
        with open(os.path.join(_env.ROOT, "config.yaml"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(self.block(text, "options"), ["admin_users"])
        self.assertEqual(self.block(text, "schema"), ["admin_users"])
        for gone in ("media_path", "trash_days", "max_upload_mb"):
            self.assertNotRegex(text, rf"^\s+{gone}:", gone)
        with open(os.path.join(_env.ROOT, "translations", "en.yaml"), encoding="utf-8") as f:
            self.assertEqual(self.block(f.read(), "configuration"), ["admin_users"])

    def test_old_values_in_options_json_are_ignored(self):
        # an upgraded install: the Supervisor may still have the old values in options.json
        reset_state(setup_media=False)
        nas = os.path.join(_env.SHARE_DIR, "nas", "family_tree")
        os.makedirs(os.path.dirname(nas))
        self.addCleanup(shutil.rmtree, os.path.dirname(nas), True)
        orig = config.OPTIONS_PATH
        config.OPTIONS_PATH = self.options_file({"admin_users": [], "media_path": nas, "trash_days": 30,
                                                 "max_upload_mb": 5})
        self.addCleanup(setattr, config, "OPTIONS_PATH", orig)

        async def idle():                 # the housekeeping loop's first round would outlive the client
            return None
        with mock.patch.object(housekeeping, "loop", idle):
            with TestClient(app, client=("127.0.0.1", 12345)) as c:
                body = c.get("/api/admin/settings", headers=headers(ADMIN)).json()
        self.assertEqual(body["values"], DEFAULTS)
        self.assertNotIn("importedFromConfig", body)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_settings")[0]["n"], 0)
        self.assertEqual(body["media"], {"path": _env.MEDIA_PATH, "online": True, "reason": None})
        self.assertFalse(os.path.exists(os.path.join(nas, media.MARKER)))       # the old folder isn't used
        self.assertEqual(config.read_options(config.OPTIONS_PATH)["trash_days"], 30)   # (it's there, just not read)

    def test_old_imported_row_is_harmless(self):
        sql("INSERT INTO app_settings (key, value, updated_at) VALUES ('_imported', '[\"trash_days\"]', 'x')")
        settings.invalidate()
        body = self.ok(self.get("/api/admin/settings", user=ADMIN))
        self.assertEqual(body["values"], DEFAULTS)
        self.assertEqual(self.ok(self.put("/api/admin/settings", {"trash_days": 40}, user=ADMIN))["values"]["trash_days"], 40)


class SettingsAndRestore(ApiTestCase):
    ALL_FEATURES = False                # a new install's switches
    def download(self):
        r = self.get("/api/admin-storage-download-db", user=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.content

    def restore(self, data):
        return self.ok(self.post("/api/admin-storage-import-db",
                                 files={"file": ("backup.zip", data, "application/zip")}, user=ADMIN))

    def test_backup_brings_its_settings(self):
        set_app_settings(trash_days=30)
        data = self.download()
        set_app_settings(trash_days=45)
        self.restore(data)
        self.assertEqual(settings.get("trash_days"), 30)                  # cache dropped after the swap

    def test_pre_040_backup_keeps_current_settings(self):
        data = self.download()
        # make it look like a backup from an older version: no app_settings table
        z = zipfile.ZipFile(io.BytesIO(data))
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(z.read("family.db"))
        c = sqlite3.connect(f.name)
        c.execute("DROP TABLE app_settings")
        c.commit()
        c.close()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as nz:
            with open(f.name, "rb") as fh:
                nz.writestr("family.db", fh.read())
            nz.writestr("backup.json", z.read("backup.json"))
        os.remove(f.name)
        set_app_settings(trash_days=45, max_upload_mb=9)
        self.restore(out.getvalue())
        self.assertEqual(settings.all(), V(trash_days=45, max_upload_mb=9))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_settings")[0]["n"], 2)


class AdminOnlyPages(ApiTestCase):
    """Users, Storage and Trash moved into Admin: their APIs are admin-only.
    Deleting (which moves things to the trash) and undo stay open to everyone."""
    ALL_FEATURES = False                # a new install's switches

    def test_moved_page_apis_403_for_non_admins(self):
        a = self.person("Ann")
        self.ok(self.delete(f"/api/people/{a}"))
        for method, path in (("GET", "/api/admin/settings"), ("GET", "/api/admin/users"), ("GET", "/api/admin/storage"),
                             ("GET", "/api/trash"), ("POST", f"/api/trash/people/{a}/restore"),
                             ("POST", f"/api/people/{a}/restore"), ("DELETE", "/api/trash")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.req(method, path, ALICE).status_code, 403)
        self.assertEqual(self.get(f"/api/people/{a}").status_code, 404)  # still in the trash
        self.ok(self.post(f"/api/trash/people/{a}/restore", user=ADMIN))

    def test_everyone_can_still_delete_and_undo(self):
        a = self.person("Ann", user=ALICE)
        fid = self.family(a, self.person("Ben", user=ALICE), user=ALICE)
        d = self.ok(self.delete(f"/api/families/{fid}", user=ALICE))
        self.ok(self.post(f"/api/history/{d['batchId']}/undo", user=ALICE))
        self.ok(self.get(f"/api/families/{fid}", user=ALICE))
        d = self.ok(self.delete(f"/api/people/{a}", user=ALICE))
        self.assertEqual(len(self.ok(self.get("/api/trash", user=ADMIN))["items"]), 1)
        self.ok(self.post(f"/api/history/{d['batchId']}/undo", user=ALICE))
        self.assertEqual(self.detail(a, user=ALICE)["name"], "Ann")
        pdf = b"%PDF-1.7\n..."
        m = self.ok(self.post("/api/media", files={"file": ("a.pdf", pdf, "application/pdf")}, user=ALICE), 201)["media"]
        d = self.ok(self.delete(f"/api/media/{m['id']}", user=ALICE))
        self.ok(self.post(f"/api/history/{d['batchId']}/undo", user=ALICE))
        self.assertEqual(self.ok(self.get("/api/trash", user=ADMIN))["items"], [])


class PhotoFolder(ApiTestCase):
    """media_path as an App setting: validation, the folder check, switching
    live (files are never moved), and everything that follows the new folder."""
    ALL_FEATURES = False                # a new install's switches

    def setUp(self):
        super().setUp()
        self.new = os.path.join(_env.SHARE_DIR, "moved", "family_tree")
        os.makedirs(os.path.dirname(self.new))
        self.addCleanup(shutil.rmtree, os.path.dirname(self.new), True)

    def check(self, path, user=ADMIN):
        return self.post("/api/admin/settings/check-media-path", {"path": path}, user=user)

    def switch(self, path, confirm=None, status=200):
        body = {"media_path": path}
        if confirm is not None:
            body["confirm"] = confirm
        return self.ok(self.put("/api/admin/settings", body, user=ADMIN), status)

    def upload_photo(self, pid=None):
        pid = pid or self.person("Ann")
        r = self.post(f"/api/people/{pid}/photo", files={"file": ("p.jpg", image_bytes(), "image/jpeg")})
        return self.ok(r, 201)["mediaId"]

    def marker(self, path):
        with open(os.path.join(path, media.MARKER)) as f:
            return f.read().strip()

    def store_id(self):
        with db.get_conn() as c:
            return db.get_setting(c, "media_store_id")

    # ---- validation ----
    def test_validation(self):
        for bad in ("share/x", "/share", "/share/", "/share/../etc", "/share/./x", "/share/a\nb", "", "  ", 5, None, ["/share/x"]):
            with self.subTest(bad=bad):
                r = self.put("/api/admin/settings", {"media_path": bad}, user=ADMIN)
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(self.check(bad).status_code, 422)
        config.ALLOW_ANY_MEDIA_PATH = False                  # as inside Home Assistant
        for bad in ("/data/photos", "/sharepoint/x", "/tmp/x"):
            with self.subTest(bad=bad):
                r = self.put("/api/admin/settings", {"media_path": bad}, user=ADMIN)
                self.assertEqual(r.status_code, 422, r.text)
                self.assertIn("inside /share", r.json()["detail"])
        self.assertEqual(settings.validate_one("media_path", "//share//nas/family_tree/"), "/share/nas/family_tree")
        self.assertEqual(settings.get("media_path"), _env.MEDIA_PATH)
        # a value already stored is kept (and reported offline), never swapped for another folder
        self.assertEqual(media.check()["online"], False)
        self.assertIn("inside /share", media.status()["reason"])

    def test_admin_only(self):
        self.assertEqual(self.check(self.new, user=ALICE).status_code, 403)
        self.assertEqual(self.put("/api/admin/settings", {"media_path": self.new}, user=ALICE).status_code, 403)
        self.assertEqual(settings.get("media_path"), _env.MEDIA_PATH)

    # ---- the folder check (no side effects) ----
    def test_check_verdicts(self):
        r = self.ok(self.check(_env.MEDIA_PATH))
        self.assertEqual((r["verdict"], r["ok"], r["marker"]), ("current", True, "this"))
        # empty tree: a missing folder (parent exists) will simply be created
        r = self.ok(self.check(self.new))
        self.assertEqual((r["verdict"], r["exists"], r["marker"], r["files"], r["ok"], r["needsConfirm"]),
                         ("new", False, "none", 0, True, False))
        self.assertFalse(os.path.exists(self.new))                               # nothing created
        r = self.ok(self.check(os.path.join(_env.SHARE_DIR, "nope", "x")))
        self.assertEqual((r["verdict"], r["parentExists"], r["ok"]), ("missing_parent", False, False))
        self.assertIn("mounted", r["message"])
        # with photos in the tree, a folder without this tree's marker needs a confirmation
        self.upload_photo()
        os.makedirs(self.new)
        r = self.ok(self.check(self.new))
        self.assertEqual((r["verdict"], r["exists"], r["marker"], r["needsConfirm"], r["dbMedia"]),
                         ("no_marker", True, "none", True, 1))
        self.assertIn(".family_tree_store", r["message"])
        self.assertIn("Your 1 photo or document stays in the old folder", r["message"])
        self.assertFalse(os.path.exists(os.path.join(self.new, media.MARKER)))
        # a copy of the whole folder is this tree's folder
        shutil.rmtree(self.new)
        shutil.copytree(_env.MEDIA_PATH, self.new)
        r = self.ok(self.check(self.new))
        self.assertEqual((r["verdict"], r["marker"], r["files"], r["ok"]), ("this", "this", 1, True))
        # another tree's folder is refused
        with open(os.path.join(self.new, media.MARKER), "w") as f:
            f.write("someone-else\n")
        r = self.ok(self.check(self.new))
        self.assertEqual((r["verdict"], r["marker"], r["refused"]), ("other", "other", True))
        self.assertIn("another Family Tree", r["message"])

    def test_check_read_only(self):
        os.makedirs(self.new)
        with mock.patch.object(media.os, "access", return_value=False):      # tests run as root
            r = self.ok(self.check(self.new))
        self.assertEqual((r["verdict"], r["writable"], r["ok"]), ("read_only", False, False))

    # ---- switching ----
    def test_empty_tree_switches_freely_and_sets_up_the_new_folder(self):
        old_files = sorted(os.listdir(_env.MEDIA_PATH))
        body = self.switch(self.new)
        self.assertEqual(body["values"]["media_path"], self.new)
        self.assertEqual(body["media"], {"path": self.new, "online": True, "reason": None})
        self.assertEqual(self.marker(self.new), self.store_id())                 # same store, new folder
        mid = self.upload_photo()
        self.assertTrue(os.path.isfile(os.path.join(self.new, "media", mid[:2], mid, "original")))
        self.assertFalse(os.path.exists(os.path.join(_env.MEDIA_PATH, "media")))  # old folder untouched
        self.assertEqual(sorted(os.listdir(_env.MEDIA_PATH)), old_files)
        s = self.ok(self.get("/api/admin/storage", user=ADMIN))
        self.assertEqual((s["path"], s["online"], s["files"]), (self.new, True, 1))

    def test_photos_need_confirm_and_stay_behind(self):
        mid = self.upload_photo()
        r = self.put("/api/admin/settings", {"media_path": self.new}, user=ADMIN)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("Copy the whole folder", r.json()["detail"])
        self.assertEqual(settings.get("media_path"), _env.MEDIA_PATH)             # unchanged
        self.assertEqual(self.put("/api/admin/settings", {"media_path": self.new, "confirm": "yes"}, user=ADMIN).status_code, 422)
        body = self.switch(self.new, confirm=True)
        self.assertEqual(body["values"]["media_path"], self.new)
        self.assertFalse(body["media"]["online"])
        self.assertIn(media.MARKER, body["media"]["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.new, media.MARKER)))   # never a fresh marker
        self.assertFalse(os.path.exists(self.new))                               # nor a folder
        me = self.ok(self.get("/api/me"))
        self.assertFalse(me["media"]["online"])                                  # the banner shows it
        self.assertEqual(self.get(f"/api/media/{mid}/file").status_code, 503)
        self.assertEqual(self.post(f"/api/people/{self.person('Ben')}/photo",
                                   files={"file": ("p.jpg", image_bytes(), "image/jpeg")}).status_code, 503)
        self.assertTrue(os.path.isfile(os.path.join(_env.MEDIA_PATH, "media", mid[:2], mid, "original")))
        # copying the whole folder there brings it back without another save
        shutil.copytree(_env.MEDIA_PATH, self.new)
        self.assertEqual(self.get(f"/api/media/{mid}/file").status_code, 503)    # until the next check…
        self.assertTrue(media.check()["online"])                                 # …(every 5 minutes, or any upload)
        self.assertEqual(self.get(f"/api/media/{mid}/file").status_code, 200)

    def test_copied_folder_switches_without_confirm(self):
        mid = self.upload_photo()
        shutil.copytree(_env.MEDIA_PATH, self.new)
        body = self.switch(self.new)
        self.assertTrue(body["media"]["online"])
        shutil.rmtree(_env.MEDIA_PATH)                                           # the old copy is no longer used
        self.assertEqual(self.get(f"/api/media/{mid}/file?size=256").status_code, 200)
        mid2 = self.upload_photo()
        self.assertTrue(os.path.isfile(os.path.join(self.new, "media", mid2[:2], mid2, "original")))
        self.assertFalse(os.path.exists(_env.MEDIA_PATH))
        # and back again: the old path has no folder any more → offline, and nothing is created there
        body = self.switch(_env.MEDIA_PATH, confirm=True)
        self.assertFalse(body["media"]["online"])
        self.assertFalse(os.path.exists(_env.MEDIA_PATH))

    def test_other_trees_folder_refused(self):
        os.makedirs(self.new)
        with open(os.path.join(self.new, media.MARKER), "w") as f:
            f.write("someone-else\n")
        for confirm in (None, True):
            r = self.put("/api/admin/settings", dict({"media_path": self.new}, **({"confirm": True} if confirm else {})),
                         user=ADMIN)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("another Family Tree", r.json()["detail"])
        self.assertEqual(settings.get("media_path"), _env.MEDIA_PATH)
        self.assertTrue(media.is_online())
        # a folder that gets another tree's marker later comes up offline, marked foreign
        ft2 = os.path.join(os.path.dirname(self.new), "ft2")
        self.switch(ft2)
        with open(os.path.join(ft2, media.MARKER), "w") as f:
            f.write("someone-else\n")
        st = media.check()
        self.assertFalse(st["online"])
        self.assertEqual(st["foreign_id"], "someone-else")
        self.assertIn("another Family Tree", st["reason"])

    def test_missing_parent_empty_tree(self):
        target = os.path.join(_env.SHARE_DIR, "unmounted", "family_tree")
        body = self.switch(target)
        self.assertFalse(body["media"]["online"])
        self.assertIn("doesn't exist", body["media"]["reason"])
        self.assertFalse(os.path.exists(os.path.dirname(target)))
        os.makedirs(os.path.dirname(target))                         # the mount appears…
        self.addCleanup(shutil.rmtree, os.path.dirname(target), True)
        self.assertFalse(media.check()["online"])                     # …a routine check never sets it up
        body = self.switch(target)                                    # saving again (no change) does nothing either
        self.assertFalse(body["media"]["online"])
        self.switch(_env.MEDIA_PATH)
        self.assertTrue(self.switch(target)["media"]["online"])       # choosing it again does

    def test_changed_elsewhere_is_not_online_until_checked(self):
        self.assertTrue(media.is_online())
        set_app_settings(media_path=self.new)                         # e.g. straight in the database
        self.assertFalse(media.is_online())                           # the last check was for the old folder
        self.assertEqual(self.ok(self.get("/api/admin/storage", user=ADMIN))["online"], False)
        self.assertFalse(os.path.exists(self.new))                    # routine checks never create it

    # ---- things that follow the folder ----
    def test_exports_and_backups_follow_the_new_folder(self):
        self.switch(self.new)
        self.assertEqual(export_router.exports_dir(), os.path.join(self.new, ".exports"))
        pid = self.person("Ann")
        mid = self.upload_photo(pid)
        r = self.get("/api/admin-storage-download-db?media=1", user=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertIn(f"media/{mid[:2]}/{mid}/original", z.namelist())
        self.assertEqual(os.listdir(os.path.join(self.new, ".exports")), [])      # scratch zip removed
        # restore with photos writes into the folder in use; the backup never moves it
        shutil.rmtree(os.path.join(self.new, "media"))
        r = self.ok(self.post("/api/admin-storage-import-db", files={"file": ("b.zip", r.content, "application/zip")},
                              user=ADMIN))
        self.assertEqual(r["mediaRestored"], 3)
        self.assertTrue(os.path.isfile(os.path.join(self.new, "media", mid[:2], mid, "original")))
        self.assertEqual(settings.get("media_path"), self.new)
        self.assertTrue(media.is_online())
        # orphans and the media check use it too
        os.makedirs(os.path.join(self.new, "media", "ab", "ab" + "0" * 30))
        c = self.ok(self.post("/api/admin/media/check", user=ADMIN))
        self.assertEqual(c["orphans"], ["ab" + "0" * 30])
        self.ok(self.post("/api/admin/media/orphans", {"ids": c["orphans"]}, user=ADMIN))
        self.assertTrue(os.path.isdir(os.path.join(self.new, "orphans", "ab" + "0" * 30)))

    def test_restore_keeps_this_installs_folder(self):
        set_app_settings(media_path="/share/somewhere/else")          # what the backup's install used
        data = self.ok_download()
        settings.update({"media_path": _env.MEDIA_PATH}, None)
        self.ok(self.post("/api/admin-storage-import-db", files={"file": ("b.zip", data, "application/zip")}, user=ADMIN))
        self.assertEqual(settings.get("media_path"), _env.MEDIA_PATH)
        self.assertTrue(media.is_online())

    def ok_download(self):
        r = self.get("/api/admin-storage-download-db", user=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.content

    def test_export_job_follows_the_new_folder(self):
        self.switch(self.new)
        self.person("Ann")
        job = self.ok(self.post("/api/export", {"format": "site", "options": {}}), 202)["jobId"]
        for _ in range(200):
            st = self.ok(self.get(f"/api/export/{job}"))
            if st["status"] != "running":
                break
            import time
            time.sleep
        self.assertEqual(st["status"], "done", st)
        self.assertTrue(any(f.startswith("export-") for f in os.listdir(os.path.join(self.new, ".exports"))))
        self.assertEqual(self.get(f"/api/export/{job}/file").status_code, 200)


if __name__ == "__main__":
    import unittest
    unittest.main()
