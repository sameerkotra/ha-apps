"""Sub-path links into a page of the app (common/python/deeplinks.py): the redirect to the page's own "#/route",
only for the routes an app names, never anything else. Shared: run by the repository's tests and copied into
every app that uses it."""
import os
import sys
import unittest

try:                                            # in an app: its own copy
    from app.common import deeplinks
except ImportError:                             # in the repository: common/ itself
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from common.python import deeplinks  # noqa: E402

from fastapi import FastAPI
from fastapi.testclient import TestClient


class DeepLinkTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        deeplinks.add(app, {"today": 0, "group": 1, "month": 2})
        self.c = TestClient(app, follow_redirects=False)

    def test_redirects_to_the_pages_own_route(self):
        for path, where in (("/today", "../#/today"), ("/group/abc_1", "../../#/group/abc_1"),
                            ("/month/2026-09/food", "../../../#/month/2026-09/food")):
            r = self.c.get(path)
            self.assertEqual((r.status_code, r.headers["location"]), (307, where), path)
            self.assertEqual(r.headers["cache-control"], "no-store")

    def test_nothing_else(self):
        for path in ("/other", "/group", "/group/a/b", "/group/a b", "/today/x"):
            self.assertEqual(self.c.get(path).status_code, 404, path)
        with self.assertRaises(ValueError):
            deeplinks.add(FastAPI(), {"../x": 0})
        with self.assertRaises(ValueError):
            deeplinks.add(FastAPI(), {"x": 3})


if __name__ == "__main__":
    unittest.main()
