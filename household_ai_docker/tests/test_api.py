"""The admin page's API (SPEC.md §7): status, models (download with progress, cancel, delete, keep), callers,
keys, settings, log, backups — admins only, through ingress only."""
import _env  # noqa: F401  (must be first)

import time
import unittest

import harness
from app import config, models, ollama, server, settings
from common_tests.ingress import ingress_client
from starlette.testclient import TestClient

A = harness.ADMIN


def wait_for(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = harness.get()
        cls.c = cls.h.api

    def setUp(self):
        self.h.reset()

    def get(self, path):
        r = self.c.get(path, headers=A)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_no_login_but_no_cross_site_writes(self):
        """On a Docker host the page has no login: any address may open it, without Home Assistant's headers."""
        anyone = TestClient(self.h.api.app, client=("192.168.1.50", 5555))
        self.assertEqual(anyone.get("api/health").status_code, 200)
        self.assertEqual(anyone.get("api/status").status_code, 200)
        me = anyone.get("api/me").json()
        self.assertTrue(me["user"]["isAdmin"] and me["standalone"])
        self.assertEqual(anyone.get("api/whoami").status_code, 404)
        r = anyone.put("api/server", json={"on": True}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        page = anyone.get("/").text
        self.assertNotIn("whoami.js", page)

    def test_status(self):
        st = self.get("api/status")
        self.assertEqual(st["server"]["state"], "ready")
        self.assertEqual(st["server"]["version"], "0.40.0")
        self.assertGreaterEqual(st["machine"]["cores"], 1)
        self.assertIn("free", st["machine"]["memory"])
        self.assertEqual(st["network"], {"known": True, "port": 11434, "requireKey": False})   # docker -p 11434
        self.assertEqual(set(st["queue"]), {"running", "waiting"})

    def test_models_listing(self):
        m = self.get("api/models")
        self.assertEqual([d["name"] for d in m["downloaded"]], ["qwen2.5:3b", "qwen2.5vl:3b"])
        self.assertEqual({d["name"]: d["kind"] for d in m["downloaded"]}, {"qwen2.5:3b": "text",
                                                                           "qwen2.5vl:3b": "vision"})
        self.assertEqual(m["kept"], "qwen2.5:3b")
        rec = {r["name"]: r["downloaded"] for r in m["recommended"]}
        self.assertTrue(rec["qwen2.5:3b"])
        self.assertFalse(rec["qwen2.5vl:7b"])

    def test_download_with_progress_then_delete(self):
        r = self.c.post("api/models/pull", json={"name": "llama3.2:3b"}, headers=A)
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["id"]
        done = wait_for(lambda: (lambda v: v if v["done"] else None)(self.get(f"api/models/pull/{pid}")))
        self.assertEqual((done["status"], done["error"], done["percent"]), ("Downloaded", None, 100.0))
        self.assertIn("llama3.2:3b", [d["name"] for d in self.get("api/models")["downloaded"]])
        # Downloaded models can be used through the gateway at once.
        r = self.h.gw().post("/api/generate", json={"model": "llama3.2:3b", "prompt": "x", "stream": False})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.delete("api/models/llama3.2:3b", headers=A).status_code, 200)
        self.assertNotIn("llama3.2:3b", [d["name"] for d in self.get("api/models")["downloaded"]])
        self.assertEqual(self.c.delete("api/models/llama3.2:3b", headers=A).status_code, 400)

    def test_download_errors_in_plain_words(self):
        pid = self.c.post("api/models/pull", json={"name": "missing-model:1b"}, headers=A).json()["id"]
        done = wait_for(lambda: (lambda v: v if v["done"] else None)(self.get(f"api/models/pull/{pid}")))
        self.assertEqual(done["error"], "Ollama's library has no model by that name.")
        r = self.c.post("api/models/pull", json={"name": "bad name!"}, headers=A)
        self.assertEqual(r.status_code, 400)

    def test_cancel_a_download(self):
        import os
        step = os.path.join(config.MODELS_DIR, "fake_pull_step")
        with open(step, "w") as f:
            f.write("1")                                # one second per download step
        try:
            pid = self.c.post("api/models/pull", json={"name": "slow:1b"}, headers=A).json()["id"]
            self.assertEqual(self.c.delete(f"api/models/pull/{pid}", headers=A).status_code, 200)
            done = wait_for(lambda: (lambda v: v if v["done"] else None)(self.get(f"api/models/pull/{pid}")))
            self.assertTrue(done["cancelled"])
        finally:
            os.remove(step)

    def test_not_enough_disk(self):
        with self.assertRaises(ValueError) as cm:
            models.check_disk(5 * models.GB, 6 * models.GB)
        self.assertIn("needs about 5.0 GB", str(cm.exception))
        models.check_disk(5 * models.GB, 8 * models.GB)
        self.assertEqual(models.manifest_url("qwen2.5:3b"),
                         f"{config.REGISTRY_URL}/v2/library/qwen2.5/manifests/3b")
        self.assertEqual(models.manifest_url("someone/model"),
                         f"{config.REGISTRY_URL}/v2/someone/model/manifests/latest")

    def test_keep_loaded(self):
        r = self.c.put("api/models/keep", json={"name": "qwen2.5vl:3b"}, headers=A)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get("api/models")["kept"], "qwen2.5vl:3b")
        self.c.put("api/models/keep", json={"name": "none"}, headers=A)
        self.assertIsNone(self.get("api/models")["kept"])
        self.assertEqual(self.c.put("api/models/keep", json={"name": "a b"}, headers=A).status_code, 422)
        # a kept model missing after a restore is listed
        self.c.put("api/models/keep", json={"name": "llama3.2:3b"}, headers=A)
        self.assertEqual(self.get("api/models")["missing"], ["llama3.2:3b"])

    def test_callers_and_answer_first(self):
        self.h.gw("127.0.0.3").post("/api/generate", json={"model": "qwen2.5:3b", "prompt": "x", "stream": False})
        self.h.wait_idle()
        r = self.get("api/callers")
        # the machine as the browser reached the page, with the gateway's published port
        self.assertEqual(r["connection"]["address"], "http://testserver:11434")
        self.assertEqual(r["connection"]["openaiAddress"], "http://testserver:11434/v1")
        other = TestClient(self.h.api.app, base_url="http://192.168.1.20:8080").get("api/callers").json()
        self.assertEqual(other["connection"]["address"], "http://192.168.1.20:11434")
        row = next(x for x in r["callers"] if x["caller"] == "household_docs")
        self.assertEqual((row["name"], row["requests"], row["answerFirst"]), ("Household Docs", 1, False))
        self.assertIn("300 s", row["advice"])
        self.c.put("api/callers/household_docs/answer-first", json={"on": True}, headers=A)
        self.assertEqual(settings.get("answer_first"), ["household_assistant", "household_docs"])
        self.c.put("api/callers/household_assistant/answer-first", json={"on": False}, headers=A)
        self.assertEqual(settings.get("answer_first"), ["household_docs"])
        self.assertEqual(self.c.put("api/callers/bad name/answer-first", json={"on": True}, headers=A).status_code,
                         400)
        days = self.get("api/usage")["days"]
        self.assertEqual(days[0]["name"], "Household Docs")

    def test_keys(self):
        r = self.c.post("api/keys", json={"label": "Docs"}, headers=A).json()
        self.assertEqual(len(r["key"]), 43)
        ks = self.get("api/keys")["keys"]
        self.assertEqual([k["label"] for k in ks], ["Docs"])
        self.assertNotIn("key", ks[0])
        self.assertNotIn("key_hash", ks[0])
        self.assertEqual(self.c.delete(f"api/keys/{r['id']}", headers=A).status_code, 200)
        self.assertEqual(self.c.delete(f"api/keys/{r['id']}", headers=A).status_code, 404)
        self.assertEqual(self.c.post("api/keys", json={"label": ""}, headers=A).status_code, 422)

    def test_settings_and_server_restart_on_context_change(self):
        s = self.get("api/admin/settings")
        self.assertEqual(s["values"]["day_start"], "05:00")
        self.assertTrue(s["meta"]["keep_model"].get("hidden"))
        r = self.c.put("api/admin/settings", json={"day_start": "25:00"}, headers=A)
        self.assertEqual(r.status_code, 422)
        pid = server.SERVER.proc.pid
        r = self.c.put("api/admin/settings", json={"context": 4096}, headers=A)
        self.assertEqual(r.status_code, 200)
        wait_for(lambda: server.SERVER.proc is not None and server.SERVER.proc.pid != pid
                 and server.SERVER.state == "ready", 20)
        self.h.wait_ready()

    def test_server_off_and_on(self):
        r = self.c.put("api/server", json={"on": False}, headers=A)
        self.assertEqual(r.json()["state"], "stopped")
        self.assertFalse(settings.get("server_on"))
        self.assertEqual(self.c.post("api/models/pull", json={"name": "x:1b"}, headers=A).status_code, 409)
        self.c.put("api/server", json={"on": True}, headers=A)
        self.h.wait_ready()

    def test_unload_now(self):
        self.h.run(ollama.set_keep_alive("qwen2.5:3b", -1))
        r = self.c.post("api/unload", headers=A).json()
        self.assertEqual(r["unloaded"], ["qwen2.5:3b"])
        self.assertTrue(server.SCHEDULE.manual_unload)
        # the next request ends "unloaded by hand"
        self.h.gw().post("/api/generate", json={"model": "qwen2.5:3b", "prompt": "x", "stream": False})
        self.assertFalse(server.SCHEDULE.manual_unload)

    def test_log(self):
        lines = self.get("api/log")["lines"]
        self.assertTrue(any("starting" in l for l in lines))
        self.assertLessEqual(len(lines), 50)

    def test_backup_download_and_restore(self):
        self.c.post("api/keys", json={"label": "Keep me"}, headers=A)
        r = self.c.get("api/admin-storage-download-db", headers=A)
        self.assertEqual(r.status_code, 200)
        data = r.content
        self.c.post("api/keys", json={"label": "After the backup"}, headers=A)
        r = self.c.post("api/admin-storage-import-db", headers=A,
                        files={"file": ("household-ai-backup.db", data, "application/octet-stream")})
        self.assertEqual(r.status_code, 200, r.text)
        labels = [k["label"] for k in self.get("api/keys")["keys"]]
        self.assertIn("Keep me", labels)
        self.assertNotIn("After the backup", labels)
        r = self.c.post("api/admin-storage-import-db", headers=A,
                        files={"file": ("x.db", b"not a database", "application/octet-stream")})
        self.assertEqual(r.status_code, 400)

    def test_page(self):
        r = ingress_client(self.h.api.app).get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Household AI", r.text)


if __name__ == "__main__":
    unittest.main()
