"""Shared pytest fixtures: a fresh database + app per test.

Run from the app folder (finance/):   python3 -m pytest -q
Nothing here talks to a real Ollama: tests that exercise the parsing pipeline
monkeypatch the pipeline's extract_vision (see helpers below).
"""
import os
import sqlite3
import sys
import tempfile
import time
import warnings

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
warnings.filterwarnings("ignore", category=DeprecationWarning)

HEADERS = {
    "x-ingress-path": "/hassio_ingress/test",
    "x-remote-user-id": "tester",
    "x-remote-user-name": "Tester",
}


class Env:
    """One isolated app instance. `client` is a TestClient that has run the app's
    startup (so the background worker exists); `db()` opens the same database."""

    def __init__(self, tmp):
        self.dir = tmp
        self.db_path = os.path.join(tmp, "finance.db")
        self.pending = os.path.join(tmp, "pending")
        self.headers = dict(HEADERS)

    def db(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def insert(self, table, **cols):
        with self.db() as c:
            return c.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                tuple(cols.values()),
            ).lastrowid

    def account(self, name="Checking", type_="checking", user="tester"):
        return self.insert("accounts", user_id=user, name=name, type=type_)

    def txn(self, account_id, date, amount, description, user="tester", **extra):
        return self.insert("transactions", user_id=user, account_id=account_id, date=date,
                           amount=amount, description=description, **extra)

    def pdf(self, lines, name=None):
        """A small real PDF (text layer included) inside PENDING_DIR/tester."""
        from reportlab.pdfgen import canvas
        d = os.path.join(self.pending, "tester")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name or f"t-{os.urandom(4).hex()}.pdf")
        c = canvas.Canvas(path, pagesize=(612, 792))
        y = 750
        for line in lines:
            c.drawString(50, y, line)
            y -= 15
        c.save()
        return path

    def get(self, url, **kw):
        return self.client.get(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def post(self, url, **kw):
        return self.client.post(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def wait_status(self, table, row_id, timeout=10.0):
        """Poll until a background job has moved the row out of 'processing'."""
        deadline = time.time() + timeout
        while True:
            with self.db() as c:
                row = c.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
            if row["status"] != "processing" or time.time() > deadline:
                return row
            time.sleep(0.05)


def _fresh_app(tmp, extra_env):
    os.environ.update({
        "DB_PATH": os.path.join(tmp, "finance.db"),
        "PENDING_DIR": os.path.join(tmp, "pending"),
        "ADMIN_USERS": "tester",
        "OLLAMA_URL": "http://ollama.invalid:11434",
        "OLLAMA_MODEL": "test-model",
        "TRUSTED_CLIENT_IPS": "testclient",
        **extra_env,
    })
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.main import app
    return app


@pytest.fixture
def make_env(monkeypatch):
    """Factory so a test can pick extra env vars before the app is imported."""
    from fastapi.testclient import TestClient
    opened = []

    def factory(client_host="testclient", tmp=None, features=True, **extra_env):
        """tmp: reuse an earlier env's directory (same database) — i.e. an app restart.
        features: switch Utilities and Tolls on (a new install starts with them off)."""
        reused = tmp is not None
        tmp = tmp or tempfile.mkdtemp()
        for k in list(extra_env):
            monkeypatch.setenv(k, extra_env[k])
        app = _fresh_app(tmp, extra_env)
        env = Env(tmp)
        env.app = app
        cm = TestClient(app, client=(client_host, 50000), follow_redirects=False)
        env.client = cm.__enter__()
        opened.append(cm)
        if features and not reused:
            with env.db() as c:
                c.executemany("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, '1')",
                              [("feature_utilities",), ("feature_tolls",)])
        return env

    yield factory
    for cm in opened:
        cm.__exit__(None, None, None)


@pytest.fixture
def env(make_env):
    return make_env()


@pytest.fixture
def fake_vision(monkeypatch):
    """Replace the statement pipeline's vision call with canned transactions.
    Usage: fake_vision([("2026-08-05", -20.0, "COFFEE")])."""
    def setter(rows):
        from app.parser import pipeline as pl
        from app.parser.vision import ParsedTransaction, VisionResult
        txns = [ParsedTransaction(d, a, desc) for d, a, desc in rows]
        monkeypatch.setattr(pl, "extract_vision",
                            lambda *a, **k: VisionResult(transactions=txns, raw_response="{}"))
    return setter


@pytest.fixture(autouse=True)
def _no_llm_categorization(monkeypatch):
    """Keep categorization's LLM fallback from trying the network in every test."""
    import urllib.request

    def refuse(*a, **k):
        raise OSError("network disabled in tests")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
