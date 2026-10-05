"""Step 11 (SPEC §17.6): optional AI against a fake provider that records every request — nothing is sent while AI is off
or not set up, the prompts carry what DOCS.md says and nothing else, pictures go with their real type, the monthly
limit, usage rows and Admin → AI usage, reading text (pictures, scanned PDFs, the folder opt-in), summaries,
checklists, sheets, and questions about a folder. Every person and document here is invented."""
import _env  # noqa: F401

import base64
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import ai, ai_client, db, pdftext
from base import ASHA, DEV, KABIR, MEERA, ApiBase, person_dir, read, write
from test_bring import jpeg, node_pdf, text_pdf

KEY = "sk-test-not-a-real-key"


class FakeProvider:
    """OpenAI-compatible, Anthropic and Ollama endpoints; answers by what the prompt asks for."""

    def __init__(self):
        self.requests = []
        self.answer = None
        self.status = 200

    def reply_text(self, prompt: str) -> str:
        if self.answer is not None:
            return self.answer
        if "checklist" in prompt:
            return json.dumps({"items": ["Flour", "Eggs", "Bake 30 minutes"]})
        if "Read the table" in prompt:
            return json.dumps({"rows": [["Item", "Cost"], ["Rent", 1200], ["Food", 35.5]]})
        if "Write out all the text" in prompt:
            return "INVOICE 4417\nTotal 99.00"
        if "Summarise" in prompt:
            return "- A short summary"
        if "Answer the question" in prompt:
            return "The boiler was serviced in May [2]."
        return '{"ok": true}'

    def start(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append(("GET", self.path, None, dict(self.headers)))
                if self.path.startswith("/models") or self.path.startswith("/v1/models"):
                    return self._send(200, {"data": [{"id": "text-model"}, {"id": "vision-model"}]})
                return self._send(200, {"models": [{"name": "llama"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.requests.append(("POST", self.path, body, dict(self.headers)))
                if outer.status != 200:
                    return self._send(outer.status, {"error": {"message": f"bad key {KEY}"}})
                if self.path == "/chat/completions":
                    content = body["messages"][-1]["content"]
                    prompt = content if isinstance(content, str) else content[0]["text"]
                    return self._send(200, {"choices": [{"message": {"content": outer.reply_text(prompt)}}],
                                            "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
                if self.path == "/v1/messages":
                    prompt = body["messages"][0]["content"][-1]["text"]
                    return self._send(200, {"content": [{"type": "text", "text": outer.reply_text(prompt)}],
                                            "usage": {"input_tokens": 50, "output_tokens": 10}})
                if self.path == "/api/generate":
                    return self._send(200, {"response": outer.reply_text(body["prompt"]), "prompt_eval_count": 7,
                                            "eval_count": 3})
                return self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]


class AiBase(ApiBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = FakeProvider()
        cls.url = cls.provider.start()

    @classmethod
    def tearDownClass(cls):
        cls.provider.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.provider.requests.clear()
        self.provider.answer = None
        self.provider.status = 200
        ai._failed.clear()

    def turn_on(self, provider="openai", **more):
        body = {"ai_enabled": True, "ai_provider": provider, "ai_url": self.url, "ai_model": "text-model",
                "ai_api_key": KEY}
        body.update(more)
        self.settings(body)

    def prompts(self):
        out = []
        for _m, path, body, _h in self.provider.posts():
            if path == "/chat/completions":
                c = body["messages"][-1]["content"]
                out.append(c if isinstance(c, str) else c[0]["text"])
            elif path == "/v1/messages":
                out.append(body["messages"][0]["content"][-1]["text"])
            else:
                out.append(body.get("prompt"))
        return out


class NothingWhileOff(AiBase):
    def test_nothing_is_sent_until_set_up(self):
        n = self.note("Boiler", text="serviced in May")
        write(os.path.join(person_dir("Kabir Rao"), "photo.jpg"), jpeg())
        self.scan()
        photo = next(i for i in self.ok(self.get("/api/space/mine"))["items"] if i["name"] == "photo.jpg")
        calls = [("/api/ai/summarise", {"id": n["id"]}), ("/api/ai/to-checklist", {"text": "eggs and flour"}),
                 ("/api/ai/to-sheet", {"text": "a,b"}), ("/api/ai/ask", {"folder": "root:x", "question": "when?"}),
                 ("/api/ai/ocr", {"id": photo["id"]})]
        for path, body in calls:
            self.assertEqual(self.post(path, body).status_code, 409, path)
        self.assertEqual(self.put(f"/api/ai/folder/{n['id']}", {"on": True}).status_code, 409)
        self.assertFalse(self.ok(self.get("/api/me"))["ai"]["on"])
        self.settings({"ai_enabled": True})                       # on, but no model or address yet
        for path, body in calls:
            self.assertEqual(self.post(path, body).status_code, 409, path)
        self.assertEqual(ai.run_folder_jobs(), 0)
        self.assertEqual(self.provider.requests, [])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0], 0)
        self.turn_on()
        me = self.ok(self.get("/api/me"))["ai"]
        self.assertEqual((me["on"], me["provider"], me["model"]), (True, "OpenAI-compatible", "text-model"))
        self.settings({"ai_enabled": False})                      # off again: nothing
        self.assertEqual(self.post("/api/ai/summarise", {"id": n["id"]}).status_code, 409)
        self.assertEqual(self.provider.requests, [])


class Actions(AiBase):
    def test_summarise_sends_only_that_document(self):
        self.turn_on()
        n = self.note("Boiler", text="The boiler was serviced in May.")
        self.note("Other", text="unrelated private words")
        out = self.ok(self.post("/api/ai/summarise", {"id": n["id"]}))
        self.assertEqual(out["summary"], "- A short summary")
        (prompt,) = self.prompts()
        self.assertIn("The boiler was serviced in May.", prompt)
        self.assertIn("Boiler.txt", prompt)
        self.assertNotIn("unrelated private words", prompt)
        _m, _p, body, headers = self.provider.posts()[0]
        self.assertEqual(body["model"], "text-model")
        self.assertEqual(headers.get("Authorization"), f"Bearer {KEY}")
        self.assertEqual(self.post("/api/ai/summarise", {"id": n["id"]}, MEERA).status_code, 404)   # not hers
        self.assertEqual(len(self.provider.posts()), 1)
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM ai_calls").fetchone()
        self.assertEqual((row["purpose"], row["tokens_in"], row["tokens_out"], row["ok"]), ("summarise", 100, 20, 1))
        self.assertNotIn("boiler", json.dumps(dict(row)).lower())

    def test_checklist_and_sheet(self):
        self.turn_on(provider="anthropic", ai_vision_model="vision-model")
        out = self.ok(self.post("/api/ai/to-checklist", {"text": "Mix flour and eggs, bake for 30 minutes"}))
        self.assertEqual(out["items"], ["Flour", "Eggs", "Bake 30 minutes"])
        self.assertIn("Mix flour and eggs", self.prompts()[0])
        photo = base64.b64encode(jpeg()).decode()
        out = self.ok(self.post("/api/ai/to-sheet", {"image": "data:image/jpeg;base64," + photo}))
        self.assertEqual(out["rows"][1], ["Rent", 1200])
        _m, path, body, headers = self.provider.posts()[1]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(body["model"], "vision-model")
        img = body["messages"][0]["content"][0]
        self.assertEqual(img["source"]["media_type"], "image/jpeg")      # its real type, not image/png
        self.assertEqual(img["source"]["data"], photo)
        self.assertEqual({k.lower(): v for k, v in headers.items()}.get("x-api-key"), KEY)
        self.assertEqual(self.post("/api/ai/to-sheet", {"image": base64.b64encode(b"<svg/>").decode()}).status_code, 415)
        self.provider.answer = "not json at all"
        self.assertEqual(self.post("/api/ai/to-checklist", {"text": "x"}).status_code, 502)

    def test_ask_about_a_folder(self):
        self.turn_on(provider="ollama")
        folder = self.create("folder", "House")
        self.note("Insurance", parent=folder["id"], text="Policy renews in March")
        b = self.note("Boiler", parent=folder["id"], text="Serviced in May by the plumber")
        outside = self.note("Diary", text="private diary words")
        out = self.ok(self.post("/api/ai/ask", {"folder": folder["id"], "question": "When was the boiler serviced?"}))
        self.assertIn("[2]", out["answer"])
        (prompt,) = self.prompts()
        cited = next(f["n"] for f in out["sent"] if f["name"] == "Boiler.txt")
        if cited != 2:                                    # the fake model always cites [2]
            self.assertEqual([f["n"] for f in out["files"]], [2])
        else:
            self.assertEqual([f["id"] for f in out["files"]], [b["id"]])
        self.assertIn(f"[{cited}] Boiler.txt", prompt)
        self.assertIn("When was the boiler serviced?", prompt)
        self.assertIn("Serviced in May", prompt)
        self.assertNotIn("private diary words", prompt)
        self.assertEqual(self.post("/api/ai/ask", {"folder": folder["id"], "question": "x"}, MEERA).status_code, 404)
        self.ok(self.share(folder["id"], MEERA, "viewer"))
        self.ok(self.post("/api/ai/ask", {"folder": folder["id"], "question": "x"}, MEERA))
        self.assertEqual(outside["name"], "Diary.txt")
        for i in range(35):
            self.note(f"N{i}", parent=folder["id"], text="word " * 10)
        out = self.ok(self.post("/api/ai/ask", {"folder": folder["id"], "question": "x"}))
        self.assertEqual(len(out["sent"]), ai.ASK_FILES)

    def test_monthly_limit_and_errors(self):
        self.turn_on(ai_monthly_tokens=150)
        n = self.note("A", text="some text")
        self.ok(self.post("/api/ai/summarise", {"id": n["id"]}))          # 120 tokens
        self.ok(self.post("/api/ai/summarise", {"id": n["id"]}))          # 240: over now
        self.assertEqual(self.post("/api/ai/summarise", {"id": n["id"]}).status_code, 429)
        self.assertEqual(len(self.provider.posts()), 2)
        self.settings({"ai_monthly_tokens": 0})
        self.provider.status = 401
        r = self.post("/api/ai/summarise", {"id": n["id"]})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn(KEY, r.text)                                     # the key never comes back
        u = self.ok(self.get("/api/admin/ai/usage", ASHA))
        self.assertEqual(u["totals"]["all"]["calls"], 3)
        self.assertEqual(u["totals"]["all"]["failed"], 1)
        self.assertEqual(u["byPurpose"][0]["purpose"], "summarise")
        self.assertEqual(self.get("/api/admin/ai/usage").status_code, 403)
        self.assertNotIn(KEY, json.dumps(self.ok(self.get("/api/admin/settings", ASHA))))

    def test_connection_test(self):
        self.turn_on()
        out = self.ok(self.post("/api/admin/ai/test", {}, ASHA))
        self.assertTrue(out["ok"])
        self.assertIn("text-model", out["models"])
        self.assertEqual(self.post("/api/admin/ai/test", {}, KABIR).status_code, 403)


class ReadText(AiBase):
    def test_picture_text_is_kept_searchable_and_editable(self):
        self.turn_on(ai_vision_model="vision-model")
        write(os.path.join(person_dir("Kabir Rao"), "receipt.jpg"), jpeg())
        self.scan()
        photo = self.ok(self.get("/api/space/mine"))["items"][0]
        self.ok(self.share(photo["id"], MEERA, "viewer"))
        self.assertEqual(self.post("/api/ai/ocr", {"id": photo["id"]}, MEERA).status_code, 403)
        self.assertEqual(self.provider.requests, [])
        out = self.ok(self.post("/api/ai/ocr", {"id": photo["id"]}))
        self.assertEqual(out["ai"]["text"], "INVOICE 4417\nTotal 99.00")
        self.assertEqual(out["ai"]["model"], "vision-model")
        body = self.provider.posts()[0][2]
        self.assertTrue(body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        r = self.ok(self.get("/api/search?q=4417", MEERA))["results"]
        self.assertEqual([x["id"] for x in r], [photo["id"]])
        self.assertTrue(self.ok(self.get("/api/space/mine"))["items"][0]["aiText"])
        self.assertEqual(self.put(f"/api/nodes/{photo['id']}/text", {"text": "x"}, MEERA).status_code, 403)
        out = self.ok(self.put(f"/api/nodes/{photo['id']}/text", {"text": "INVOICE 4418 corrected"}))
        self.assertEqual(out["ai"]["editedByName"], "Kabir Rao")
        self.assertEqual(self.ok(self.get("/api/search?q=4418"))["results"][0]["id"], photo["id"])
        self.assertEqual(self.ok(self.get("/api/search?q=4417"))["results"], [])
        self.assertNotIn(b"INVOICE", read(self.real(photo["id"]), binary=True))   # never into the file

    def test_scanned_pdf_and_folder_opt_in(self):
        self.turn_on()
        folder = self.create("folder", "Bills")
        pdf = node_pdf([jpeg(), jpeg(30, 50)])
        scan = self.ok(self.c.post(f"/api/nodes/{folder['id']}/scan?name=Bill&type=pdf", content=pdf, headers=KABIR), 201)
        write(os.path.join(person_dir("Kabir Rao"), "Bills", "photo.png"), _png())
        write(os.path.join(person_dir("Kabir Rao"), "elsewhere.jpg"), jpeg())
        self.scan()
        self.assertEqual(ai.run_folder_jobs(), 0)                 # nothing until the folder opts in
        self.assertEqual(self.provider.requests, [])
        st = self.ok(self.get(f"/api/ai/folder/{folder['id']}"))
        self.assertEqual((st["on"], st["waiting"]), (False, 2))
        self.assertEqual(self.put(f"/api/ai/folder/{folder['id']}", {"on": True}, MEERA).status_code, 404)
        self.ok(self.put(f"/api/ai/folder/{folder['id']}", {"on": True}))
        self.assertTrue(next(i for i in self.ok(self.get("/api/space/mine"))["items"] if i["name"] == "Bills")["aiScans"])
        self.assertEqual(ai.run_folder_jobs(), 2)
        self.assertEqual(len(self.provider.posts()), 3)          # two PDF pages + one picture; not elsewhere.jpg
        text = self.ok(self.get(f"/api/nodes/{scan['id']}/text"))["ai"]["text"]
        self.assertIn("— page 2 —", text)
        self.assertEqual(ai.run_folder_jobs(), 0)                 # read once
        self.ok(self.put(f"/api/ai/folder/{folder['id']}", {"on": False}))
        write(os.path.join(person_dir("Kabir Rao"), "Bills", "new.jpg"), jpeg(20, 20))
        self.scan()
        self.assertEqual(ai.run_folder_jobs(), 0)
        self.settings({"ai_enabled": False})
        self.ok(self.put(f"/api/ai/folder/{folder['id']}", {"on": False}))
        self.assertEqual(self.post("/api/ai/ocr", {"id": scan["id"]}).status_code, 409)
        self.assertEqual(pdftext.run_worker(pdf, mode="images")["status"], "ok")


def _png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(b, "PNG")
    return b.getvalue()


class ClientDetails(AiBase):
    def test_image_types_are_fixed_before_sending(self):
        jpg = base64.b64encode(jpeg()).decode()
        body = {"messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": jpg}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + jpg}}, {"type": "text", "text": "x"}]}]}
        out = ai_client._fix_images(body)
        self.assertEqual(out["messages"][0]["content"][0]["source"]["media_type"], "image/jpeg")
        self.assertTrue(out["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(ai_client.image_type(base64.b64encode(_png()).decode()), "image/png")
        self.assertEqual(DEV["X-Remote-User-Id"], "u_dev")


class FilingSuggestion(AiBase):
    """§17.18: a name and a folder for a file from its text — only its text and the folder names go out; nothing
    changes until the person accepts (the page renames and moves through the ordinary routes)."""

    def test_suggest_name_and_folder(self):
        bills = self.create("folder", "Bills")
        self.create("folder", "Electric", bills["id"])
        self.create("folder", "Private", None, MEERA)                         # someone else's: never named
        up = self.c.post("/api/nodes/mine/upload?name=scan.pdf", content=text_pdf(["Power bill October 2026 total 42"]),
                         headers=KABIR)
        fid = self.ok(up, 201)["id"]
        self.assertEqual(self.post("/api/ai/suggest-filing", {"id": fid}).status_code, 409)     # AI off: nothing sent
        self.assertEqual(self.provider.requests, [])
        self.turn_on()
        pdftext.run_pending()
        self.provider.answer = json.dumps({"name": "2026-10 Power bill", "folder": 2})
        out = self.ok(self.post("/api/ai/suggest-filing", {"id": fid}))
        self.assertEqual((out["name"], out["folderPath"]), ("2026-10 Power bill.pdf", "Bills/Electric"))
        (prompt,) = self.prompts()
        self.assertIn("Power bill October 2026", prompt)
        self.assertIn("1. Bills\n2. Bills/Electric", prompt)
        self.assertNotIn("Private", prompt)
        self.assertEqual(self.ok(self.get(f"/api/nodes/{fid}"))["name"], "scan.pdf")       # nothing changed
        self.provider.answer = json.dumps({"name": "x:/bad", "folder": 99})
        out = self.ok(self.post("/api/ai/suggest-filing", {"id": fid}))
        self.assertIsNone(out["folderId"])
        self.assertNotIn("/", out["name"])
        self.ok(self.share(fid, MEERA, "viewer"))
        self.assertEqual(self.post("/api/ai/suggest-filing", {"id": fid}, MEERA).status_code, 403)
        with db.get_conn() as conn:
            self.assertEqual({r[0] for r in conn.execute("SELECT purpose FROM ai_calls")}, {"filing"})
