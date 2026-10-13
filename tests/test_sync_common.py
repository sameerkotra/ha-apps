"""tools/sync_common.py on a small made-up repository: a stamped copy in a folder the manifest doesn't list
fails --check and stops a sync before it writes anything. Standard library only."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sync_common  # noqa: E402


class SyncCommonTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sync-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.write("common/python/x.py", "X = 1\n")
        self.manifest = {"apps": {"listed": {"dest": {"python": "app/common"}, "files": {"python": ["x.py"]}}}}
        with open(os.path.join(self.root, "common", "manifest.json"), "w") as f:
            json.dump(self.manifest, f)
        patcher = mock.patch.multiple(sync_common, ROOT=self.root, COMMON=os.path.join(self.root, "common"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def test_in_sync(self):
        written, apps = sync_common.sync(self.manifest)
        self.assertEqual((written, apps), (["listed/app/common/x.py"], ["listed"]))
        self.assertEqual(sync_common.check(self.manifest), [])

    def test_copy_in_an_unlisted_folder(self):
        sync_common.sync(self.manifest)
        stamped = sync_common.expected_copy("common/python/x.py").decode()
        self.write("unlisted_app/app/common/x.py", stamped)            # e.g. household_ai_docker before it was listed
        self.write("unlisted_app/app/own.py", "# not a shared copy\n")
        problems = sync_common.check(self.manifest)
        self.assertEqual(len(problems), 1)
        self.assertIn("unlisted_app/app/common/x.py", problems[0])
        self.assertIn("doesn't list (unlisted_app)", problems[0])
        # a sync refuses before writing anything
        with open(os.path.join(self.root, "common/python/x.py"), "w") as f:
            f.write("X = 2\n")
        with self.assertRaises(SystemExit):
            sync_common.sync(self.manifest)
        with open(os.path.join(self.root, "listed/app/common/x.py")) as f:
            self.assertIn("X = 1", f.read())

    def test_stale_copy_in_a_listed_folder_is_removed(self):
        sync_common.sync(self.manifest)
        self.write("listed/app/common/old.py", sync_common.expected_copy("common/python/x.py").decode())
        self.assertIn("no longer listed", sync_common.check(self.manifest)[0])
        written, _ = sync_common.sync(self.manifest)
        self.assertEqual(written, ["listed/app/common/old.py (removed: no longer in the manifest)"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "listed/app/common/old.py")))


if __name__ == "__main__":
    unittest.main()
