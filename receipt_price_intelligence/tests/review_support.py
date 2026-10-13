"""Shared set-up for the tests of the 13 Oct 2026 review fixes: a fresh database per test, two people (Pat, an
administrator, and Sam), one home, and a test client that comes in through Home Assistant's ingress."""
import importlib.util
import os
import sys
import tempfile
import unittest
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "fastapi", "httpx", "PIL"))
INGRESS = ("172.30.32.2", 50000)
PAT = {"X-Remote-User-Id": "u_pat", "X-Remote-User-Name": "pat", "X-Remote-User-Display-Name": "Pat"}
SAM = {"X-Remote-User-Id": "u_sam", "X-Remote-User-Name": "sam", "X-Remote-User-Display-Name": "Sam"}


class DbCase(unittest.TestCase):
    """A fresh database with Pat (admin), Sam and the home h1."""

    def setUp(self):
        from app import app_settings, auth
        from app.db import dispose_engine, get_db_session, init_models
        self.dir = tempfile.mkdtemp()
        self.old_env = {k: os.environ.get(k) for k in ("DATABASE_PATH", "IMAGE_PATH", "ADMIN_USERS")}
        os.environ["DATABASE_PATH"] = os.path.join(self.dir, "t.db")
        os.environ["IMAGE_PATH"] = os.path.join(self.dir, "img")
        os.environ["ADMIN_USERS"] = "pat"
        self.old_admins = (list(auth.ADMIN_USERS), set(auth._ADMIN_FOLDED))
        auth.ADMIN_USERS[:] = auth.load_admin_users()
        auth._ADMIN_FOLDED.clear()
        auth._ADMIN_FOLDED.update(n.casefold() for n in auth.ADMIN_USERS)
        auth.invalidate_user_cache()
        dispose_engine()
        app_settings.configure(os.environ["DATABASE_PATH"])
        init_models()
        self.app_settings = app_settings
        from app.db.models import Home, User
        with get_db_session()() as db:
            db.add(User(id="u_pat", display_name="Pat", role="ADMIN"))
            db.add(User(id="u_sam", display_name="Sam"))
            db.flush()
            db.add(Home(id="h1", name="Maple Street", created_by_user_id="u_pat"))
            db.commit()
        from app.services import notifier
        notifier.forget_saved()

    def tearDown(self):
        from app import auth
        from app.db import dispose_engine
        dispose_engine()
        auth.ADMIN_USERS[:] = self.old_admins[0]
        auth._ADMIN_FOLDED.clear()
        auth._ADMIN_FOLDED.update(self.old_admins[1])
        auth.invalidate_user_cache()
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.old_env["DATABASE_PATH"]:
            self.app_settings.configure(self.old_env["DATABASE_PATH"])

    def client(self, host=INGRESS):
        from fastapi.testclient import TestClient
        from app import main
        return TestClient(main.app, client=host)

    def session(self):
        from app.db import get_db_session
        return get_db_session()()

    @staticmethod
    def png(size=(40, 30), color=(200, 10, 10)):
        from PIL import Image
        out = BytesIO()
        Image.new("RGB", size, color).save(out, "PNG")
        return out.getvalue()

    def draft(self, status="NEEDS_REVIEW", data=None, user="u_pat"):
        """A draft in the home, with ``data`` as its reading."""
        import json
        from app.services import drafts as drafts_service
        with self.session() as db:
            d = drafts_service.create_draft(db, user, home_id="h1")
            d.status = status
            d.raw_llm_output = json.dumps(data if data is not None else {"store_name": "Corner Shop", "grand_total": 4.5})
            db.commit()
            return d.id
