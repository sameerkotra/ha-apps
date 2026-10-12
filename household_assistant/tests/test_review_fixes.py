"""Regressions from the October 2026 review: the proposal button says the exact change; a proposal is sent once
however often it is tapped, and only for someone who may still ask; at most MAX_PROPOSALS per question; assist.ask
only from the companion integration and only for people the app knows; "Send me one now" is limited per hour and
reads aloud only on one of Home Assistant's speakers; a name of spaces doesn't break the spoken greeting.

All people, tasks and amounts here are invented."""
import _env  # noqa: F401  (must be first)

import json
import os
import time
from datetime import timedelta
from unittest import mock

from test_assistant import ADMIN, ALICE, Household

from app import ai_client, auth, briefing, catalogue, config, db, engine
from app.common import app_bus as bus
from fakes import Model

ADD_MILK = {"tool": "todo.items.add", "args": {"list": "Shopping", "text": "Milk"}}


class ProposalTests(Household):
    def propose(self, plan=None, say=None):
        act = {**ADD_MILK, **({"say": say} if say else {})}
        model = Model(json.dumps(plan or {"act": act}), '{"answer": "I can add milk to Shopping."}')
        q = self.ask("Add milk to the shopping list", model=model)
        return q

    def test_the_button_says_the_exact_change_not_the_models_words(self):
        q = self.propose(say="Add eggs to Groceries")                 # the model's words don't match its change
        [a] = q["actions"]
        self.assertEqual(a["label"], "Household Todo · todo.items.add (list: Shopping, text: Milk)")
        self.assertEqual(a["say"], "Add eggs to Groceries")             # only a note under the button
        q = self.propose(plan={"call": [ADD_MILK]})                    # a call to an acting tool, no words
        [a] = q["actions"]
        self.assertEqual(a["label"], "Household Todo · todo.items.add (list: Shopping, text: Milk)")
        self.assertIsNone(a["say"])
        js = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "app.js")
        with open(js, encoding="utf-8") as f:
            self.assertIn('h("button", { type: "button", class: "btn-primary" }, label)', f.read())

    def test_two_taps_send_once(self):
        q = self.propose()
        [a] = q["actions"]
        real = catalogue.for_user

        def other_tap_first(conn, user):                              # the other tap wins between read and update
            conn.execute("UPDATE calls SET state = 'sent' WHERE id = ?", (a["id"],))
            return real(conn, user)

        with mock.patch.object(engine.catalogue, "for_user", other_tap_first), \
                mock.patch.object(engine, "call", side_effect=AssertionError("sent twice")):
            r = self.c.post(f"/api/ask/{q['id']}/act/{a['id']}", headers=ALICE)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.apps["todo"].calls, [])

    def test_only_someone_who_may_ask_confirms(self):
        q = self.propose()
        [a] = q["actions"]
        self.assertEqual(self.c.put("/api/admin/people/u_alice", json={"enabled": False}, headers=ADMIN).status_code, 200)
        r = self.c.post(f"/api/ask/{q['id']}/act/{a['id']}", headers=ALICE)
        self.assertEqual(r.status_code, 403, r.text)
        self.c.put("/api/admin/people/u_alice", json={"enabled": True, "isChild": True}, headers=ADMIN)
        self.c.put("/api/admin/settings", json={"children_may_ask": False}, headers=ADMIN)
        self.assertEqual(self.c.post(f"/api/ask/{q['id']}/act/{a['id']}", headers=ALICE).status_code, 403)
        self.assertEqual(self.apps["todo"].calls, [])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT state FROM calls WHERE id = ?", (a["id"],)).fetchone()[0], "proposed")

    def test_proposals_are_capped(self):
        many = [{"tool": "todo.items.add", "args": {"list": "Shopping", "text": f"Item {i}"}} for i in range(6)]
        q = self.propose(plan={"call": many})
        self.assertEqual(len(q["actions"]), engine.MAX_PROPOSALS)
        self.assertEqual(engine.MAX_PROPOSALS, 3)


class AssistSenderTests(Household):
    def assist(self, uid="u_alice", sender="ha_assist", text="What's on my list today?"):
        now = config.utcnow()
        env = {"id": bus.new_ulid(now), "v": 1, "from": sender, "to": config.SLUG, "kind": "assist.ask",
               "kv": 1, "reply_to": None, "ref": "assist:c1", "sent": now.isoformat(),
               "expires": (now + timedelta(minutes=5)).isoformat(),
               "data": {"requested_by": uid, "name": "Someone", "text": text}}
        self.router.post(env)
        return env

    def reply(self, env, kind, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = [e for e in self.router.sent if e.get("reply_to") == env["id"] and e["kind"] == kind]
            if got:
                return got[0]["data"]
            time.sleep(0.02)
        self.fail(f"no {kind}")

    def test_only_the_integration_asks(self):
        self.c.put("/api/admin/settings", json={"assist_answers": True}, headers=ADMIN)
        with mock.patch.object(ai_client, "generate", side_effect=AssertionError("never asked")):
            self.assertEqual(self.reply(self.assist(sender="household_todo"), "nack"),
                             {"reason": "not_allowed", "detail": "sender"})
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 0)

    def test_unknown_people_are_refused_and_not_made(self):
        self.c.put("/api/admin/settings", json={"assist_answers": True}, headers=ADMIN)
        with mock.patch.object(ai_client, "generate", side_effect=AssertionError("never asked")):
            answer = self.reply(self.assist(uid="u_mallory"), "assist.answer")
        self.assertEqual(answer, {"question": None, "state": "failed", "error": auth.UNKNOWN_PERSON, "sources": []})
        with db.get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE id = 'u_mallory'").fetchone())


class BriefingSendTests(Household):
    BILLS = True
    STATES = [{"entity_id": "media_player.kitchen", "attributes": {"friendly_name": "Kitchen speaker"}},
              {"entity_id": "tts.home_assistant_cloud", "attributes": {}}]

    def phones(self, uid):
        return {"u_alice": [{"service": "mobile_app_alice_phone", "label": "Alice's phone"}]}.get(uid, [])

    def send_now(self, who=ALICE):
        sent = []
        with mock.patch.object(briefing, "phones", self.phones), \
                mock.patch.object(briefing.ha_notify, "send_to_services",
                                  side_effect=lambda svcs, *a, **kw: sent.append(svcs) or {x: True for x in svcs}):
            r = self.c.post("/api/briefing/send", headers=who)
        return r, sent

    def test_send_now_is_limited_per_hour(self):
        for _ in range(briefing.SENDS_PER_HOUR):
            self.assertEqual(self.send_now()[0].status_code, 200)
        r, sent = self.send_now()
        self.assertEqual(r.status_code, 429, r.text)
        self.assertEqual(sent, [])

    def test_only_home_assistants_speakers(self):
        with mock.patch.object(briefing.ha_client, "fetch_states_blocking", return_value=self.STATES):
            r = self.c.put("/api/briefing", json={"on": True, "time": "07:30", "days": "every",
                                                  "speaker": "media_player.neighbours_tv"}, headers=ALICE)
            self.assertEqual(r.status_code, 422, r.text)
            self.assertEqual(self.c.put("/api/briefing", json={"on": True, "time": "07:30", "days": "every",
                                                               "speaker": "media_player.kitchen"},
                                        headers=ALICE).status_code, 200)
        calls = []
        gone = [s for s in self.STATES if s["entity_id"] != "media_player.kitchen"]     # removed from HA since
        with mock.patch.object(briefing.ha_client, "fetch_states_blocking", return_value=gone), \
                mock.patch.object(briefing.ha_client, "request",
                                  side_effect=lambda *a, **kw: calls.append(a) or (200, b"[]")):
            r, _sent = self.send_now()
        self.assertFalse(r.json()["spoken"])
        self.assertEqual(calls, [])

    def test_a_name_of_spaces_is_greeted(self):
        with db.get_conn() as conn:
            briefing.save(conn, "u_alice", True, "07:30", "every", speaker=None)
            conn.execute("UPDATE briefings SET speaker = 'media_player.kitchen' WHERE user_id = 'u_alice'")
        user = {"id": "u_alice", "name": "   ", "username": None, "is_admin": False, "enabled": True,
                "is_child": False}
        calls = []
        with mock.patch.object(briefing.ha_client, "fetch_states_blocking", return_value=self.STATES), \
                mock.patch.object(briefing.ha_client, "request",
                                  side_effect=lambda m, path, body=None, **kw: calls.append(body) or (200, b"[]")), \
                mock.patch.object(briefing, "phones", self.phones):
            out = briefing.send(user)
        self.assertTrue(out["spoken"])
        self.assertTrue(calls[0]["message"].startswith("Good morning, there. "))

