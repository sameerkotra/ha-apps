"""A tiny fake Home Assistant Core API for tests (shared: common/tests/fake_ha.py): records
every request and answers like the Supervisor proxy would."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeHA:
    def __init__(self, time_zone="UTC"):
        self.requests = []          # (method, path, body_dict|None, auth_header)
        self.states = {}            # entity_id -> {"state", "attributes"}
        self.fail = False           # answer 500 to everything
        self.time_zone = time_zone
        self.notify_services = None  # None = accept any notify service; a list = only these exist
        self.notify_entities = []    # notify.<name> entities reachable via notify.send_message
        self.people = None           # what POST /api/template renders for ha_people (list), None = 404
        self._server = None

    # -- handler ---------------------------------------------------------
    def _make_handler(outer):  # noqa: N805
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if not n:
                    return None
                try:
                    return json.loads(self.rfile.read(n))
                except ValueError:
                    return None

            def _reply(self, code, obj=None):
                data = json.dumps(obj if obj is not None else {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                body = self._body()
                outer.requests.append((method, self.path, body, self.headers.get("Authorization")))
                if outer.fail:
                    return self._reply(500)
                if method == "GET" and self.path == "/api/config":
                    return self._reply(200, {"time_zone": outer.time_zone})
                if self.path.startswith("/api/states/"):
                    eid = self.path[len("/api/states/"):]
                    if method == "POST":
                        existed = eid in outer.states
                        outer.states[eid] = body
                        return self._reply(200 if existed else 201, body)
                    if method == "DELETE":
                        if eid in outer.states:
                            del outer.states[eid]
                            return self._reply(200)
                        return self._reply(404)
                if method == "GET" and self.path == "/api/services" and outer.notify_services is not None:
                    return self._reply(200, [{"domain": "notify",
                                              "services": {n: {} for n in outer.notify_services}}])
                if method == "POST" and self.path == "/api/template":
                    if outer.people is None:
                        return self._reply(404)
                    data = json.dumps(outer.people).encode()          # HA answers the rendered text
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return None
                if method == "GET" and self.path == "/api/states":
                    return self._reply(200, [{"entity_id": f"notify.{n}", "state": "unknown"} for n in outer.notify_entities]
                                       + [{"entity_id": k, **(v or {})} for k, v in outer.states.items()])
                if method == "POST" and self.path == "/api/services/notify/send_message":
                    eid = (body or {}).get("entity_id", "")
                    if eid.startswith("notify.") and eid[len("notify."):] in outer.notify_entities:
                        return self._reply(200, [])
                    return self._reply(400, {"message": f"Referenced entities {eid} are missing or not currently available"})
                if method == "POST" and self.path.startswith("/api/services/notify/"):
                    name = self.path.rsplit("/", 1)[-1]
                    if outer.notify_services is not None and name not in outer.notify_services:
                        return self._reply(400, {"message": f"Service notify.{name} not found."})
                    return self._reply(200, [])
                return self._reply(404)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        return H

    def start(self):
        self._server = HTTPServer(("127.0.0.1", 0), self._make_handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_port}/api"

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def notifications(self):
        return [(p.rsplit("/", 1)[-1], b) for m, p, b, _ in self.requests
                if m == "POST" and p.startswith("/api/services/notify/")]
