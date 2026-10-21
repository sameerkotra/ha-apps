# Shared file: edit common/tests/test_shared_copies.py and run tools/sync_common.py; don't edit this copy. sha256=a9a3d198003ff9a8afc2cdd5b2180e1a8de656ce3b3d2483b70592455e10ce3b
"""Every shared file in this app is an unedited copy of the repository's common/.

Shared copies start with a header line naming their source in common/ and the
SHA-256 of the rest of the file (written by tools/sync_common.py). This test
recomputes the hash, so it catches an edited copy even when the app folder is
tested on its own. To change a shared file, edit it in common/ and run
`python tools/sync_common.py` from the repository root.
"""
import hashlib
import os
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MARK = b"Shared file: edit common/"
EXTS = (".py", ".js", ".css")


def shared_copies():
    for dirpath, dirnames, filenames in os.walk(APP_DIR):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules", ".git")]
        for name in filenames:
            if name.endswith(EXTS):
                path = os.path.join(dirpath, name)
                with open(path, "rb") as f:
                    data = f.read()
                first, _, body = data.partition(b"\n")
                if MARK in first:
                    yield os.path.relpath(path, APP_DIR), first, body


class SharedCopiesTest(unittest.TestCase):
    def test_copies_are_unedited(self):
        found = 0
        for rel, first, body in shared_copies():
            found += 1
            with self.subTest(rel):
                self.assertIn(b"sha256=", first, "header without a hash")
                want = first.split(b"sha256=", 1)[1][:64].decode()
                self.assertEqual(hashlib.sha256(body).hexdigest(), want,
                                 f"{rel} was edited: change common/ and run tools/sync_common.py instead")
                self.assertNotIn(b"\r\n", body, f"{rel} has CRLF line endings")
        self.assertGreater(found, 0, "no shared copies found")

    def test_package_markers(self):
        for rel, _first, _body in shared_copies():
            if rel.endswith(".py"):
                folder = os.path.join(APP_DIR, os.path.dirname(rel))
                with self.subTest(rel):
                    self.assertTrue(os.path.exists(os.path.join(folder, "__init__.py")))


if __name__ == "__main__":
    unittest.main()
