"""The shared ingress identity and admin rules (common/python/auth_core.py), tested in every app that uses it."""
import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI, HTTPException, Request
from starlette.testclient import TestClient

from app.common import auth_core


class _Conn:
    def __init__(self, host=None, headers=None):
        self.client = type("C", (), {"host": host})() if host is not None else None
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


class SourceAddress(unittest.TestCase):
    def test_default_set_is_the_proxy_and_loopback(self):
        self.assertEqual(auth_core.INGRESS_HOSTS, {"172.30.32.2", "127.0.0.1", "::1"})
        for host in ("172.30.32.2", "127.0.0.1", "::1"):
            self.assertTrue(auth_core.from_ingress(_Conn(host)), host)
        for host in ("10.0.0.5", "testclient", ""):
            self.assertFalse(auth_core.from_ingress(_Conn(host)), host)
        self.assertFalse(auth_core.from_ingress(_Conn(None)))          # unknown peer: never trusted

    def test_an_app_may_allow_only_the_proxy(self):
        only = {auth_core.INGRESS_PROXY}
        self.assertTrue(auth_core.from_ingress(_Conn("172.30.32.2"), only))
        self.assertFalse(auth_core.from_ingress(_Conn("127.0.0.1"), only))
        self.assertEqual(auth_core.client_host(_Conn(None), ""), "")

    def test_middleware_refuses_everyone_else_with_403(self):
        app = FastAPI()

        @app.middleware("http")
        async def guard(request: Request, call_next):
            blocked = auth_core.refuse_outsiders(request)
            if blocked is not None:
                return blocked
            return await call_next(request)

        @app.get("/x")
        def x(request: Request):
            i = auth_core.identity(request)
            return {"id": i.user_id, "name": i.username, "display": i.display_name, "path": i.ingress_path}

        ok = TestClient(app, client=("172.30.32.2", 1)).get(
            "/x", headers={"X-Remote-User-Id": "u1", "X-Remote-User-Name": "jane", "X-Ingress-Path": "/p"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json(), {"id": "u1", "name": "jane", "display": None, "path": "/p"})
        bad = TestClient(app, client=("10.0.0.5", 1)).get("/x", headers={"X-Remote-User-Id": "u1"})
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(bad.json(), {"detail": "Forbidden — access only via Home Assistant"})

    def test_forwarded_headers_never_make_an_outsider_the_proxy(self):
        """The check uses the TCP peer only (uvicorn runs with proxy headers off: tools/check_build.py), so
        an outsider claiming to be the ingress proxy in X-Forwarded-For / Forwarded / X-Real-IP is refused."""
        app = FastAPI()

        @app.middleware("http")
        async def guard(request: Request, call_next):
            blocked = auth_core.refuse_outsiders(request)
            if blocked is not None:
                return blocked
            return await call_next(request)

        @app.get("/x")
        def x():
            return {"ok": True}

        outsider = TestClient(app, client=("10.0.0.5", 1))
        for headers in ({"X-Forwarded-For": "172.30.32.2"}, {"X-Forwarded-For": "172.30.32.2, 127.0.0.1"},
                        {"Forwarded": "for=172.30.32.2"}, {"X-Real-IP": "172.30.32.2"},
                        {"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"}):
            with self.subTest(headers=headers):
                r = outsider.get("/x", headers={**headers, "X-Remote-User-Id": "u1"})
                self.assertEqual(r.status_code, 403)
        self.assertFalse(auth_core.from_ingress(_Conn("10.0.0.5", {"X-Forwarded-For": "172.30.32.2"})))

    def test_no_user_id_is_401(self):
        with self.assertRaises(HTTPException) as e:
            auth_core.require_user_id("", "Some App")
        self.assertEqual(e.exception.status_code, 401)
        self.assertEqual(e.exception.detail, "No Home Assistant user identified. Open Some App from its panel "
                                             "in the Home Assistant sidebar.")
        auth_core.require_user_id("u1", "Some App")


class AdminList(unittest.TestCase):
    def test_entries_are_trimmed_and_lowercased(self):
        self.assertEqual(auth_core.admin_names([" Jane.Doe ", "", "  ", "ID-7F"]), {"jane.doe", "id-7f"})
        self.assertEqual(auth_core.admin_names(['"Jane"', "'bob' "]), {'"jane"', "'bob'"})
        self.assertEqual(auth_core.admin_names(['"Jane"', "'bob' ", '""'], strip_quotes=True), {"jane", "bob"})
        self.assertEqual(auth_core.admin_entries([" pat ", "'Other'", ""], strip_quotes=True), ["pat", "Other"])
        self.assertEqual(auth_core.admin_names(["Straße"], fold=str.casefold), {"strasse"})

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"X_TEST_ADMINS": " Ann , ,bob"}):
            self.assertEqual(auth_core.env_admins("X_TEST_ADMINS"), {"ann", "bob"})
        with mock.patch.dict(os.environ, {"X_TEST_ADMINS": ""}):
            self.assertEqual(auth_core.env_admins("X_TEST_ADMINS"), set())
        os.environ.pop("X_TEST_ADMINS", None)
        self.assertEqual(auth_core.env_admins("X_TEST_ADMINS"), set())

    def test_read_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "options.json")
            self.assertEqual(auth_core.read_options(path), {})
            with open(path, "w") as f:
                json.dump({"admin_users": ["a"]}, f)
            self.assertEqual(auth_core.read_options(path), {"admin_users": ["a"]})
            with open(path, "w") as f:
                f.write("[1, 2]")
            self.assertEqual(auth_core.read_options(path), {})
            with open(path, "w") as f:
                f.write("{not json")
            log = mock.Mock()
            self.assertEqual(auth_core.read_options(path, log=log), {})
            log.warning.assert_called_once_with("Could not read %s; using defaults", path)
            with self.assertRaises(ValueError):          # an app may let other errors through
                auth_core.read_options(path, errors=(OSError,))


class Matching(unittest.TestCase):
    ADMINS = {"jane.doe", "id-7f3c"}

    def test_by_id_or_login_name_any_case(self):
        self.assertTrue(auth_core.is_admin("ID-7F3C", None, self.ADMINS))
        self.assertTrue(auth_core.is_admin("u1", " Jane.Doe ", self.ADMINS))
        self.assertFalse(auth_core.is_admin("u1", "jane", self.ADMINS))
        self.assertFalse(auth_core.is_admin(None, None, self.ADMINS))
        self.assertFalse(auth_core.is_admin("", "", set()))

    def test_display_names_never_count(self):
        self.assertFalse(auth_core.is_admin("u1", None, {"jane doe"}))
        self.assertTrue(auth_core.display_name_listed(" Jane Doe", {"jane doe"}))
        self.assertTrue(auth_core.display_name_only("u1", "jd", "Jane Doe", {"jane doe"}))
        self.assertFalse(auth_core.display_name_only("u1", "jane doe", "Jane Doe", {"jane doe"}))
        self.assertFalse(auth_core.display_name_listed(None, {"jane doe"}))

    def test_no_admin(self):
        self.assertTrue(auth_core.no_admin(set()))
        self.assertFalse(auth_core.no_admin({"a"}))

    def test_require_admin_flag(self):
        auth_core.require_admin_flag(True, "nope")
        with self.assertRaises(HTTPException) as e:
            auth_core.require_admin_flag(False, "nope")
        self.assertEqual((e.exception.status_code, e.exception.detail), (403, "nope"))


if __name__ == "__main__":
    unittest.main()
