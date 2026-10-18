"""Quick answers, end to end (HOUSEHOLD_ASSISTANT_SPEC.md §17): a fake household on one in-process bus, a model that
fails the test whenever it is called unless a test allows it.

- a common question is answered by the rules: the app's own words, its Sources, the understood line, no model;
- Use AI off: no model call anywhere (questions, Not what I asked, learning, the connection test), and Quick answers
  can't be turned off with it;
- Did you mean: the choices, a tap that answers and is learned, "the first one" by voice;
- Not what I asked: a learned phrase forgotten; asked again of the model, or without that meaning;
- follow-ups, several questions in one, actions only as buttons, the household's names fetched from the apps;
- learning from the model (twice before it's used), the admin's phrases, misses without who asked;
- children and voice satellites get only the tools about themselves, and Vault never answers;
- 1.5: yes/no and counts said first, two months side by side, ticking the shopping list, moving a task, a spend and a
  note and a checklist change (all only as buttons), "my mum", an answer reused for the same question, learning from
  rephrasing, the admin's words (and the ones the misses suggest), export/import, the apps' speeds, who asks (counts
  only), replay, and the suggestions a person's habits put first.
All people, lists and amounts here are invented.
"""
import _env  # noqa: F401  (must be first)

import glob
import json
import os
import time
import unittest
from unittest import mock

from app import ai_client, catalogue, config, db, engine, settings
from app.common import app_bus as bus
from app.common.assist_tools import Arg
from app.main import app
from app.quick import vocab as quick_vocab
from common_tests.ingress import identity_headers, ingress_client
from fakes import FakeApp, Model, Router, wait_done

ADMIN = identity_headers("u_admin", "adminy", "Adminy")
ALICE = identity_headers("u_alice", "alice", "Alice")
KID = identity_headers("u_kid", "kid", "Kid")


class NoModel:
    """Fails the test if any model is asked anything (Use AI off, or a question the rules must answer)."""

    def __init__(self, test):
        self.test = test

    def __call__(self, *a, **kw):
        self.test.fail(f"a model was called: {kw.get('purpose')}")


def household(router: Router) -> dict:
    """Todo (tasks, lists, add), Receipts (list, price, add), Finance (bills, balances), Splitpot (balances), Family
    Tree (birthdays) and a Vault that must never be asked."""
    people = ("u_alice", "u_bob", "u_kid", "u_admin")
    todo = FakeApp(router, "household_todo", "Household Todo", "todo", people=people)
    rec = FakeApp(router, "receipt_price_intelligence", "Receipt Price Intelligence", "receipt", people=people)
    fin = FakeApp(router, "finance", "Finance Dashboard", "finance", people=people)
    split = FakeApp(router, "splitpot", "Splitpot", "splitpot", people=people)
    tree = FakeApp(router, "family_tree", "Family Tree", "tree", people=people)
    vault = FakeApp(router, "household_vault", "Household Vault", "vault", people=people)
    docs = FakeApp(router, "household_docs", "Household Docs", "docs", people=people)

    def rec_call(a, ctx):
        a.calls.append({"tool": ctx.tool.name, "by": ctx.user["id"], "args": dict(ctx.args), "confirm": ctx.confirmed})

    @todo.tools.tool("todo.tasks", "Tasks on the person's lists.",
                     args={"when": Arg("enum", "which", required=True, values=("today", "tomorrow", "week", "overdue",
                                                                               "all")),
                           "list": Arg("string", "a list"), "person": Arg("string", "a person")},
                     returns="tasks", examples=("What's on my list today?",))
    def tasks(ctx):
        rec_call(todo, ctx)
        when = ctx.args["when"]
        return ctx.result(f"2 tasks {when}: Bins, Call plumber.", items=[{"title": "Bins"}, {"title": "Call plumber"}],
                          links=[ctx.link("Today in Household Todo", "/dashboard")])

    @todo.tools.tool("todo.lists", "The person's lists.", returns="lists")
    def lists(ctx):
        rec_call(todo, ctx)
        return ctx.result("2 lists: Personal, Costco.", items=[{"list": "Costco", "kind": "shared"},
                                                               {"list": "Personal", "kind": "personal"}])

    @todo.tools.tool("todo.items.add", "Adds a task (after the person confirms it).",
                     args={"list": Arg("string", "the list", required=True), "text": Arg("string", "the task",
                                                                                         required=True),
                           "due": Arg("date", "when")}, acts=True, returns="the task")
    def add(ctx):
        rec_call(todo, ctx)
        return ctx.result(f"Added “{ctx.args['text']}” to {ctx.args['list']}.")

    @todo.tools.tool("todo.items.due", "Moves a task to another day (after the person confirms it).",
                     args={"task": Arg("string", "the task", required=True), "due": Arg("date", "the day", required=True),
                           "list": Arg("string", "the list")}, acts=True, returns="the task")
    def due(ctx):
        rec_call(todo, ctx)
        return ctx.result(f"“{ctx.args['task']}” is now due {ctx.args['due']}.")

    @rec.tools.tool("receipt.shopping_list.tick", "Ticks an item off the shopping list (after the person confirms it).",
                    args={"item": Arg("string", "the item", required=True)}, acts=True, scope="household",
                    returns="the item")
    def shop_tick(ctx):
        rec_call(rec, ctx)
        return ctx.result(f"Ticked {ctx.args['item']} off the shopping list.")

    @fin.tools.tool("finance.spending", "A month's spending by category.",
                    args={"month": Arg("month", "the month"), "category": Arg("string", "a category")},
                    returns="spending per category")
    def spending(ctx):
        rec_call(fin, ctx)
        this = config.today().strftime("%Y-%m")
        rows = ([("Groceries", 200.0), ("Fuel", 100.0)] if ctx.args.get("month", this) == this
                else [("Groceries", 150.0), ("Fuel", 50.0)])
        return ctx.result(f"{ctx.args.get('month', this)}: {sum(v for _c, v in rows):.2f} spent.",
                          items=[{"category": c, "spend": v} for c, v in rows])

    @fin.tools.tool("finance.expense.add", "Adds a spend by hand (after the person confirms it).",
                    args={"amount": Arg("number", "how much", required=True, min=0.01, max=1e7),
                          "description": Arg("string", "where", required=True), "category": Arg("string", "category"),
                          "date": Arg("date", "when"), "account": Arg("string", "which account")},
                    acts=True, returns="the spend")
    def spend_add(ctx):
        rec_call(fin, ctx)
        return ctx.result(f"Added {ctx.args['amount']} at {ctx.args['description']}.")

    @tree.tools.tool("tree.person", "One person.", args={"person": Arg("string", "who", required=True)},
                     returns="the person", scope="household")
    def person(ctx):
        rec_call(tree, ctx)
        return ctx.result(f"{ctx.args['person']}: born 12 March 1960.")

    @docs.tools.tool("docs.search", "Finds documents.", args={"query": Arg("string", "words", required=True),
                                                              "kind": Arg("string", "kind")}, returns="documents")
    def search(ctx):
        rec_call(docs, ctx)
        if ctx.args["query"] == "packing":
            return ctx.result("1 checklist: Packing.", items=[{"id": "d_pack", "name": "Packing", "kind": "checklist"}])
        return ctx.result(f"Nothing matches “{ctx.args['query']}”.")

    @docs.tools.tool("docs.checklist", "A checklist's items.", args={"id": Arg("string", "its id", required=True)},
                     returns="items")
    def checklist(ctx):
        rec_call(docs, ctx)
        return ctx.result("Packing: passport (open).", items=[{"item": "passport", "done": False}])

    @docs.tools.tool("docs.checklist.tick", "Ticks a checklist item (after the person confirms it).",
                     args={"id": Arg("string", "its id", required=True), "item": Arg("string", "the item", required=True),
                           "done": Arg("boolean", "ticked")}, acts=True, returns="the item")
    def tick(ctx):
        rec_call(docs, ctx)
        return ctx.result(f"Ticked “{ctx.args['item']}”.")

    @docs.tools.tool("docs.note.create", "Makes a note (after the person confirms it).",
                     args={"name": Arg("string", "its name", required=True), "text": Arg("string", "what it says",
                                                                                          required=True)},
                     acts=True, returns="the note")
    def note(ctx):
        rec_call(docs, ctx)
        return ctx.result(f"Made the note “{ctx.args['name']}”.")

    @rec.tools.tool("receipt.shopping_list", "The shopping list.", scope="household", returns="items")
    def shopping(ctx):
        rec_call(rec, ctx)
        return ctx.result("3 items on the list: milk, eggs, basmati rice.",
                          items=[{"item": "milk", "cheapest_at": "Dmart"}, {"item": "eggs"}, {"item": "basmati rice"}])

    @rec.tools.tool("receipt.price", "An item's prices.", args={"item": Arg("string", "the item", required=True)},
                    scope="household", returns="prices")
    def price(ctx):
        rec_call(rec, ctx)
        return ctx.result(f"{ctx.args['item'].capitalize()}: 1.10 at Dmart, 1.25 at Costco.")

    @rec.tools.tool("receipt.shopping_list.add", "Adds an item to the shopping list (after the person confirms it).",
                    args={"item": Arg("string", "what", required=True), "qty": Arg("number", "how many", min=0.01,
                                                                                   max=999)},
                    acts=True, scope="household", returns="the item")
    def shop_add(ctx):
        rec_call(rec, ctx)
        return ctx.result(f"Added {ctx.args['item']} to the shopping list.")

    @fin.tools.tool("finance.bills", "Bills due.", args={"days": Arg("number", "days ahead", min=1, max=60)},
                    returns="bills")
    def bills(ctx):
        rec_call(fin, ctx)
        return ctx.result(f"1 bill in the next {int(ctx.args.get('days', 7))} days: Electricity 54.20 on Friday.",
                          items=[{"merchant": "Electricity", "amount": 54.2}])

    @fin.tools.tool("finance.balances", "Account balances.", returns="accounts")
    def balances(ctx):
        rec_call(fin, ctx)
        return ctx.result("Current account 1,200.00; card owes 300.00.")

    @split.tools.tool("splitpot.balances", "Who owes whom.", args={"group": Arg("string", "a group")},
                      returns="balances")
    def owe(ctx):
        rec_call(split, ctx)
        return ctx.result("Flat: Bob owes you 12.00.", items=[{"group": "Flat", "from": "Bob", "to": "Alice",
                                                               "amount": 12}])

    @tree.tools.tool("tree.birthdays", "Coming birthdays.", args={"days": Arg("number", "days", min=1, max=90)},
                     returns="birthdays", scope="household")
    def birthdays(ctx):
        rec_call(tree, ctx)
        return ctx.result(f"Meera turns 30 on Saturday (next {int(ctx.args.get('days', 30))} days).",
                          items=[{"name": "Meera", "turns": 30}])

    @vault.tools.tool("vault.list", "Every password.", returns="passwords")
    def secrets(ctx):
        raise AssertionError("the assistant must never call Vault")

    apps = {"todo": todo, "receipt": rec, "finance": fin, "splitpot": split, "tree": tree, "vault": vault,
            "docs": docs}
    for a in apps.values():
        a.start()
    return apps


class QuickHousehold(unittest.TestCase):
    AI = True                               # a model is set up (scripted per test); Use AI on

    @classmethod
    def setUpClass(cls):
        cls._ctx = ingress_client(app)
        with mock.patch.object(engine, "housekeeping", lambda: None):
            cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        bus.stop()
        for f in glob.glob(config.DB_PATH + "*"):
            try:
                os.remove(f)
            except FileNotFoundError:                  # SQLite removed its own -shm/-wal meanwhile
                pass
        db.init_db()
        settings.invalidate()
        quick_vocab.forget()
        engine.forget_recent()
        self.router = Router()
        bus.default.configure(post=self.router.post, outbox_interval=0.02, send_limit=100000, receive_limit=100000,
                              receive_limit_total=100000)
        self.router.buses[config.SLUG] = bus.default
        bus.start(config.SLUG, config.APP_TITLE, config.APP_VERSION, can=[], db=db.get_conn, token="",
                  outbox_thread=True)
        self.apps = household(self.router)
        for a in self.apps.values():
            a.hello()
        catalogue.refresh()
        end = time.monotonic() + 5
        while time.monotonic() < end:
            with db.get_conn() as conn:
                if conn.execute("SELECT COUNT(*) FROM assist_apps WHERE learned_at IS NOT NULL").fetchone()[0] >= 6:
                    break
            time.sleep(0.02)
        if self.AI:
            settings.update({"ai_provider": "ollama", "ai_url": "http://192.0.2.1:11434", "ai_model": "tiny",
                             "ai_tool_calls": "json"}, None)
        for h in (ADMIN, ALICE, KID):
            self.assertEqual(self.c.get("/api/me", headers=h).status_code, 200)
        self._gen = NoModel(self)                              # swapped by allow_model(); one patch, undone once
        patcher = mock.patch.object(ai_client, "generate", lambda *a, **kw: self._gen(*a, **kw))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        end = time.monotonic() + 5
        while engine._stops and time.monotonic() < end:
            time.sleep(0.02)
        bus.stop()
        for a in self.apps.values():
            a.stop()

    def ask(self, text, who=ALICE, status=200):
        r = self.c.post("/api/ask", json={"text": text}, headers=who)
        self.assertEqual(r.status_code, status, r.text)
        return wait_done(r.json()["id"]) if status == 200 else r

    def calls(self, app_key, tool=None):
        """The calls an app got (only those of `tool` when given; the names are fetched in the background too)."""
        return [c for c in self.apps[app_key].calls if tool is None or c["tool"] == tool]

    def allow_model(self, *script):
        self._gen = Model(*script)
        return self._gen

    def no_model(self):
        self._gen = NoModel(self)


class QuickAnswers(QuickHousehold):
    def test_a_common_question_is_answered_without_the_model(self):
        q = self.ask("whats on my list 2day")
        self.assertEqual(q["state"], "done", q)
        self.assertEqual(q["mode"], "quick")
        self.assertEqual(q["answer"], "2 tasks today: Bins, Call plumber.")       # the app's own words
        self.assertEqual(q["understood"], "Your tasks · today · Household Todo")
        self.assertTrue(q["notThis"])
        self.assertEqual([s["appName"] for s in q["sources"]], ["Household Todo"])
        self.assertEqual(self.calls("todo", "todo.tasks")[-1], {"tool": "todo.tasks", "by": "u_alice",
                                                               "args": {"when": "today"}, "confirm": False})
        self.assertLess(q["seconds"], 2)
        self.assertFalse(any(e["to"] == "household_vault" for e in self.router.sent if e["kind"] == "assist.tool.call"))

    def test_several_questions_in_one(self):
        q = self.ask("tasks and bills this week")
        self.assertEqual(q["mode"], "quick")
        self.assertIn("**Your tasks · this week**", q["answer"])
        self.assertIn("**Bills due · this week**", q["answer"])
        self.assertEqual(self.calls("finance", "finance.bills")[-1]["args"], {"days": 7})
        self.assertEqual(self.calls("todo", "todo.tasks")[-1]["args"], {"when": "week"})

    def test_a_follow_up_changes_only_the_time(self):
        self.ask("bills this week")
        q = self.ask("and tomorrow?")
        self.assertEqual(q["mode"], "quick")
        self.assertEqual(self.calls("finance", "finance.bills")[-1]["args"], {"days": 2})

    def test_an_item_alone_asks_its_price_and_the_list(self):
        q = self.ask("milk")
        self.assertIn("Milk: 1.10 at Dmart", q["answer"])
        self.assertIn("**Milk** is on the shopping list.", q["answer"])
        q = self.ask("is ghee on the list")
        self.assertIn("Ghee isn't on the shopping list.", q["answer"])

    def test_actions_are_only_buttons(self):
        q = self.ask("add 2 litres of milk and bread to the shopping list")
        self.assertEqual([a["state"] for a in q["actions"]], ["proposed", "proposed"])
        self.assertFalse([c for c in self.calls("receipt") if c["tool"] == "receipt.shopping_list.add"])
        r = self.c.post(f"/api/ask/{q['id']}/act/{q['actions'][0]['id']}", headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        added = [c for c in self.calls("receipt") if c["tool"] == "receipt.shopping_list.add"]
        self.assertEqual(added, [{"tool": "receipt.shopping_list.add", "by": "u_alice",
                                  "args": {"item": "milk", "qty": 2.0}, "confirm": True}])

    def test_the_households_names_come_from_the_apps(self):
        self.ask("what's on my list today")                       # afterwards the names are fetched
        end = time.monotonic() + 5
        while time.monotonic() < end and not any(c["tool"] == "todo.lists" for c in self.calls("todo")):
            time.sleep(0.02)
        time.sleep(0.2)
        q = self.ask("what's on the costco list tomorrow")
        self.assertEqual(self.calls("todo", "todo.tasks")[-1]["args"], {"when": "tomorrow", "list": "Costco"})
        q = self.ask("add call the plumber to my list")           # no list named: the person's own list
        self.assertEqual(q["actions"][0]["args"], {"list": "Personal", "text": "call the plumber"})
        q = self.ask("who owes what in the flat")
        self.assertEqual(self.calls("splitpot", "splitpot.balances")[-1]["args"], {"group": "Flat"})

    def test_suggestions_are_the_quick_questions(self):
        me = self.c.get("/api/me", headers=ALICE).json()
        self.assertIn("What's on my list today?", me["suggestions"])
        self.assertFalse(me["quickOnly"])
        for _ in range(2):
            self.ask("any bills")
        s = self.c.get("/api/quick/suggestions", headers=ALICE).json()["suggestions"]
        self.assertEqual(s[0]["intent"], "bills")                 # the person's most used first


class DidYouMean(QuickHousehold):
    def test_choices_a_tap_answers_and_is_learned(self):
        q = self.ask("balances")
        self.assertEqual(q["mode"], "quick")
        self.assertEqual([c["intent"] for c in q["choices"]], ["owe", "balances"])
        self.assertTrue(q["answer"].startswith("Did you mean"))
        self.assertTrue(q["answer"].endswith("?"))                # Assist keeps listening for the answer
        r = self.c.post(f"/api/ask/{q['id']}/choose", json={"choice": 1}, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        q2 = wait_done(q["id"])
        self.assertEqual(q2["answer"], "Current account 1,200.00; card owes 300.00.")
        self.assertEqual(q2["choices"], [])
        self.assertEqual(self.c.post(f"/api/ask/{q['id']}/choose", json={"choice": 0}, headers=ALICE).status_code, 409)
        with db.get_conn() as conn:
            row = conn.execute("SELECT phrase, intent, source FROM quick_phrases").fetchone()
        self.assertEqual(tuple(row), ("balance", "balances", "tapped"))      # its normalised words
        q3 = self.ask("balances")                                 # learned: answered at once
        self.assertEqual(q3["understood"], "Account balances · Finance Dashboard")
        self.assertEqual(q3["choices"], [])

    def test_the_first_one_answers_a_spoken_choice(self):
        self.ask("balances")
        q = self.ask("the first one")
        self.assertEqual(q["understood"], "Who owes whom · Splitpot")
        self.assertEqual(self.calls("splitpot", "splitpot.balances")[-1]["args"], {})

    def test_someone_elses_question_cant_be_chosen(self):
        q = self.ask("balances")
        bob = identity_headers("u_bob", "bob", "Bob")
        self.assertEqual(self.c.get("/api/me", headers=bob).status_code, 200)
        self.assertEqual(self.c.post(f"/api/ask/{q['id']}/choose", json={"choice": 0}, headers=bob).status_code, 404)
        with mock.patch.object(engine, "_thread_running_for", lambda uid: True):   # even while Bob's own is running
            self.assertEqual(self.c.post(f"/api/ask/{q['id']}/choose", json={"choice": 0}, headers=bob).status_code,
                             404)


class NotWhatIAsked(QuickHousehold):
    def test_with_ai_it_goes_to_the_model_and_forgets_the_phrase(self):
        q = self.ask("balances")
        self.c.post(f"/api/ask/{q['id']}/choose", json={"choice": 1}, headers=ALICE)
        wait_done(q["id"])
        q = self.ask("balances")                                  # from the learned phrase
        m = self.allow_model('{"answer": "Bob owes you 12.00 in Splitpot."}')
        r = self.c.post(f"/api/ask/{q['id']}/not-this", headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        q2 = wait_done(r.json()["id"])
        self.assertEqual(q2["mode"], "ai")
        self.assertEqual(q2["answer"], "Bob owes you 12.00 in Splitpot.")
        self.assertIn("NOT about: Account balances", m.prompts[0][2])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM quick_phrases").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count FROM quick_misses WHERE phrase = 'balance'").fetchone()[0], 1)

    def test_without_ai_it_shows_the_other_meanings(self):
        settings.update({"ai_on": False}, None)
        q = self.ask("any bills")
        r = self.c.post(f"/api/ask/{q['id']}/not-this", headers=ALICE)
        q2 = wait_done(r.json()["id"])
        self.assertEqual(q2["mode"], "quick")
        self.assertNotIn("bills", [c["intent"] for c in q2["choices"]])
        self.assertTrue(q2["suggest"])


class UseAIOff(QuickHousehold):
    def setUp(self):
        super().setUp()
        settings.update({"ai_on": False}, None)

    def test_no_model_is_ever_called(self):
        q = self.ask("any birthdays this week")
        self.assertEqual(q["answer"], "**Yes** — 1 birthday coming up · this week.\n\n"
                                      "Meera turns 30 on Saturday (next 7 days).")
        q = self.ask("why did we spend so much")                  # needs the AI
        self.assertTrue(q["answer"].startswith(settings.AI_OFF))
        self.assertTrue(q["suggest"])
        q = self.ask("what's the weather like")                   # not placed
        self.assertIn("I can answer things like", q["answer"])
        with self.assertRaises(ai_client.AIError):                # the client itself refuses
            ai_client.check_configured(ai_client.current())
        self.assertFalse(ai_client.needs_warmup())

    def test_the_connection_test_asks_nothing(self):
        with mock.patch.object(ai_client, "test_connection", side_effect=AssertionError("asked")):
            r = self.c.post("/api/admin/settings/test-ai", json={}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["ok"])
        self.assertIn("Use AI is off", r.json()["message"])

    def test_quick_answers_cant_be_off_too(self):
        r = self.c.put("/api/admin/settings", json={"quick_on": False}, headers=ADMIN)
        self.assertEqual(r.status_code, 422, r.text)
        me = self.c.get("/api/me", headers=ALICE).json()
        self.assertTrue(me["canAsk"])
        self.assertTrue(me["quickOnly"])
        self.assertFalse(me["ai"]["on"])
        self.assertIsNone(me["privacy"])

    def test_without_any_model_it_still_answers_quick_questions(self):
        settings.update({"ai_on": True, "ai_provider": "", "ai_url": "", "ai_model": ""}, None)
        self.assertTrue(self.c.get("/api/me", headers=ALICE).json()["canAsk"])
        q = self.ask("bills")
        self.assertEqual(q["mode"], "quick")
        q = self.ask("why are bills so high")
        self.assertEqual(q["mode"], "quick")                      # nothing to ask: what it can answer


class LearningFromTheModel(QuickHousehold):
    def test_seen_twice_then_used(self):
        plan = json.dumps({"call": [{"tool": "finance.bills", "args": {"days": 7}}]})
        for n in range(2):
            self.allow_model(plan, '{"answer": "The electricity bill, Friday."}')
            q = self.ask("what's the damage this week")
            self.assertEqual(q["mode"], "ai", q)
        with db.get_conn() as conn:
            row = conn.execute("SELECT phrase, intent, source, seen FROM quick_phrases").fetchone()
        self.assertEqual(tuple(row), ("what is damage {when}", "bills", "ai", 2))     # the, a, please left out
        self.no_model()
        q = self.ask("what's the damage this week")
        self.assertEqual(q["mode"], "quick")
        self.assertEqual(self.calls("finance", "finance.bills")[-1]["args"], {"days": 7})
        q = self.ask("what's the damage tomorrow")                # its time is worked out again
        self.assertEqual(self.calls("finance", "finance.bills")[-1]["args"], {"days": 2})

    def test_unplaced_questions_go_to_the_model_and_are_counted(self):
        self.allow_model('{"answer": "Nothing in the apps about that."}')
        q = self.ask("what's happening with the garden")
        self.assertEqual(q["mode"], "ai")
        r = self.c.get("/api/admin/quick/misses", headers=ADMIN).json()
        self.assertEqual(r["misses"][0]["phrase"], "what is happening with the garden")
        self.assertNotIn("u_alice", json.dumps(r))


class AdminQuick(QuickHousehold):
    def test_test_phrases_and_misses(self):
        r = self.c.post("/api/admin/quick/test", json={"text": "whats on the shoping list tmrw"}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual(out["result"], "answer")
        self.assertEqual(out["understood"][0]["intent"], "shopping")
        self.assertIn("shopping*", out["words"])                  # the typo put right
        self.assertEqual(self.c.post("/api/admin/quick/test", json={"text": "x"}, headers=ALICE).status_code, 403)
        r = self.c.post("/api/admin/quick/phrases", json={"words": "how are we fixed", "intent": "balances"},
                        headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        q = self.ask("how are we fixed")
        self.assertEqual(q["understood"], "Account balances · Finance Dashboard")
        q = self.ask("so how are we fixed for money")             # an admin's phrase inside a longer question
        self.assertTrue(q["understood"].startswith("Account balances · Finance Dashboard"))   # (maybe "as of 1 s ago")
        self.assertEqual(self.c.post("/api/admin/quick/phrases", json={"words": "x", "intent": "task_add"},
                                     headers=ADMIN).status_code, 422)      # never to a change
        self.assertEqual(self.c.post("/api/admin/quick/phrases", json={"words": "x", "intent": "nope"},
                                     headers=ADMIN).status_code, 422)
        pid = r.json()["id"]
        self.assertEqual(self.c.delete(f"/api/admin/quick/phrases/{pid}", headers=ADMIN).status_code, 200)
        self.assertEqual(self.c.get("/api/admin/quick/phrases", headers=ADMIN).json()["phrases"], [])
        intents = self.c.get("/api/admin/quick/intents", headers=ADMIN).json()["intents"]
        self.assertTrue(next(i for i in intents if i["intent"] == "bills")["available"])
        self.assertFalse(next(i for i in intents if i["intent"] == "calories")["available"])


class ChildrenAndVoice(QuickHousehold):
    def test_a_child_only_gets_tools_about_themselves(self):
        settings.update({"children_may_ask": True}, None)
        r = self.c.put("/api/admin/people/u_kid", json={"isChild": True}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.allow_model('{"answer": "I can only answer about your own things."}')
        q = self.ask("any birthdays this week", who=KID)          # household-wide: not for children
        self.assertNotIn("tree.birthdays", [c["tool"] for c in q["shared"]])
        self.assertEqual(q["mode"], "ai")                         # the rules had nothing the child may use
        q = self.ask("what's on my list today", who=KID)
        self.assertEqual(q["mode"], "quick")
        self.assertEqual(self.calls("todo", "todo.tasks")[-1]["by"], "u_kid")
        q = self.ask("balances", who=KID)
        if q["choices"]:
            self.c.post(f"/api/ask/{q['id']}/choose", json={"choice": 0}, headers=KID)
            wait_done(q["id"])
        with db.get_conn() as conn:                               # a child's tap isn't learned
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM quick_phrases").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()


class MoreQuickAnswers(QuickHousehold):
    """1.5 (HOUSEHOLD_ASSISTANT_SPEC.md §17.15)."""

    def test_yes_no_and_how_many_are_said_first(self):
        q = self.ask("any bills due this week?")
        self.assertTrue(q["answer"].startswith("**Yes** — 1 bill due · this week.\n\n1 bill in the next 7 days"), q)
        q = self.ask("how many tasks today")
        self.assertTrue(q["answer"].startswith("**2 tasks** · today.\n\n2 tasks today"), q["answer"])
        q = self.ask("do i owe anyone")
        self.assertTrue(q["answer"].startswith("**Yes** — 1 open balance"), q["answer"])

    def test_two_months_side_by_side(self):
        q = self.ask("did we spend more than last month")
        self.assertEqual(q["mode"], "quick")
        months = [c["args"].get("month") for c in self.calls("finance", "finance.spending")]
        self.assertEqual(len(months), 2, q)
        self.assertTrue(q["answer"].startswith("**Spending: 300.00 in "), q["answer"])
        self.assertIn("200.00 in ", q["answer"])
        self.assertIn("100.00 more (+50%).**", q["answer"])
        q = self.ask("did we spend less on groceries than last month")
        self.assertIn("**Spending on Groceries: 200.00 in ", q["answer"])
        self.assertIn("50.00 more (+33%)", q["answer"])
        self.assertNotIn("_category", json.dumps(self.calls("finance", "finance.spending")))   # never sent to the app

    def test_changes_are_buttons(self):
        cases = (("bought the milk", "receipt.shopping_list.tick", {"item": "milk"}),
                 ("move the dentist to tomorrow", "todo.items.due", {"task": "dentist"}),
                 ("I spent 200 on fuel at Shell", "finance.expense.add",
                  {"amount": 200, "description": "Shell", "category": "Fuel"}),
                 ("make a note gate code 4521", "docs.note.create", {"name": "Gate code 4521", "text": "gate code 4521"}))
        for text, tool, args in cases:
            with self.subTest(text=text):
                q = self.ask(text)
                self.assertEqual([(a["tool"], a["state"]) for a in q["actions"]], [(tool, "proposed")], q)
                for k, v in args.items():
                    self.assertEqual(q["actions"][0]["args"][k], v)
        self.assertFalse([c for app in self.apps.values() for c in app.calls if c["confirm"]])   # nothing changed

    def test_a_checklist_is_found_by_its_name_then_the_change_is_a_button(self):
        q = self.ask("tick passport on the packing checklist")
        self.assertEqual(self.calls("docs", "docs.search")[-1]["args"], {"query": "packing", "kind": "checklist"})
        self.assertEqual([(a["tool"], a["args"]) for a in q["actions"]],
                         [("docs.checklist.tick", {"id": "d_pack", "item": "passport", "done": True})])
        q = self.ask("tick sunscreen on the beach checklist")         # no such checklist: said, nothing proposed
        self.assertEqual(q["actions"], [])
        self.assertIn("No checklist called “beach”", q["answer"])

    def test_family_by_what_you_call_them(self):
        self.ask("when is mum's birthday")
        self.assertEqual(self.calls("tree", "tree.person")[-1]["args"], {"person": "my mother"})
        self.ask("how old is ravi's wife")
        self.assertEqual(self.calls("tree", "tree.person")[-1]["args"], {"person": "Ravi's wife"})

    def test_the_same_question_again_is_answered_from_the_last_answer(self):
        q1 = self.ask("bills this week")
        q2 = self.ask("bills this week")
        self.assertEqual(len(self.calls("finance", "finance.bills")), 1)          # asked once
        self.assertEqual(q2["answer"], q1["answer"])
        self.assertEqual([s["tool"] for s in q2["shared"]], ["finance.bills"])   # What was shared still shows it
        q = self.ask("add bread to the shopping list")                           # a change: the next is asked afresh
        self.c.post(f"/api/ask/{q['id']}/act/{q['actions'][0]['id']}", headers=ALICE)
        self.ask("bills this week")
        self.assertEqual(len(self.calls("finance", "finance.bills")), 2)
        settings.update({"quick_recent": 0}, None)
        self.ask("bills this week")
        self.assertEqual(len(self.calls("finance", "finance.bills")), 3)

    def test_a_rephrased_question_is_learned_once_it_happens_twice(self):
        settings.update({"quick_unsure": "choices"}, None)
        for _ in range(2):
            q = self.ask("whats the damage")
            self.assertEqual(q["understood"], None)                # not placed
            self.ask("any bills")                                   # the same person, in other words, soon after
        with db.get_conn() as conn:
            row = conn.execute("SELECT intent, source, seen FROM quick_phrases WHERE source = 'rephrased'").fetchone()
        self.assertEqual(tuple(row), ("bills", "rephrased", 2))
        q = self.ask("whats the damage")
        self.assertTrue(q["understood"].startswith("Bills due"), q)
        q2 = self.ask("whats the damage", who=KID)    # children's rephrasings aren't learned, but used
        self.assertEqual(q2["mode"], "quick")

    def test_the_admins_words_and_the_ones_the_misses_suggest(self):
        settings.update({"quick_unsure": "choices"}, None)
        for _ in range(2):
            self.ask("any rota today")
        r = self.c.get("/api/admin/quick/words", headers=ADMIN).json()
        self.assertIn({"word": "rota", "means": "TASK"}, [{k: g[k] for k in ("word", "means")} for g in r["suggestions"]])
        self.assertEqual(self.c.get("/api/admin/quick/words", headers=ALICE).status_code, 403)
        self.assertEqual(self.c.post("/api/admin/quick/words", json={"word": "Rota", "means": "TASK"},
                                     headers=ADMIN).status_code, 200)
        q = self.ask("any rota today")
        self.assertEqual(q["understood"], "Your tasks · today · Household Todo")
        self.assertEqual(self.c.post("/api/admin/quick/words", json={"word": "dahi", "means": "item:Curd"},
                                     headers=ADMIN).status_code, 200)
        q = self.ask("is dahi on the list")
        self.assertIn("Curd isn't on the shopping list.", q["answer"])
        for bad in ({"word": "x y z w", "means": "TASK"}, {"word": "chores", "means": "QUESTION_WORDS"},
                    {"word": "chores", "means": "nope:Curd"}):
            self.assertEqual(self.c.post("/api/admin/quick/words", json=bad, headers=ADMIN).status_code, 422, bad)
        self.assertEqual(self.c.delete("/api/admin/quick/words/rota", headers=ADMIN).status_code, 200)
        words = self.c.get("/api/admin/quick/words", headers=ADMIN).json()["words"]
        self.assertEqual([w["word"] for w in words], ["dahi"])

    def test_export_and_import(self):
        self.c.post("/api/admin/quick/phrases", json={"words": "how are we fixed", "intent": "balances"}, headers=ADMIN)
        self.c.post("/api/admin/quick/words", json={"word": "chores", "means": "TASK"}, headers=ADMIN)
        r = self.c.get("/api/admin/quick/export", headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        data = r.json()
        self.assertEqual((data["kind"], len(data["phrases"]), data["words"]),
                         ("quick-answers", 1, [{"word": "chores", "means": "TASK"}]))
        self.assertNotIn("u_alice", json.dumps(data))
        with db.get_conn() as conn:
            conn.execute("DELETE FROM quick_phrases")
            conn.execute("DELETE FROM quick_words")
        data["phrases"].append({"phrase": "add it", "intent": "task_add", "args": {}, "source": "admin"})   # a change
        r = self.c.post("/api/admin/quick/import", json=data, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({k: r.json()[k] for k in ("phrases", "words", "skipped")}, {"phrases": 1, "words": 1,
                                                                                     "skipped": 1})
        self.assertEqual(self.c.post("/api/admin/quick/import", json={"kind": "other"}, headers=ADMIN).status_code, 422)
        self.assertEqual(self.c.post("/api/admin/quick/import", json=data, headers=ALICE).status_code, 403)

    def test_app_speeds_who_asks_and_replay(self):
        self.ask("bills this week")
        self.ask("whats on my list today")
        self.ask("whats on my list today", who=KID)
        apps = self.c.get("/api/admin/quick/apps", headers=ADMIN).json()["apps"]
        fin = next(a for a in apps if a["app"] == "finance")
        self.assertEqual((fin["calls"], fin["ok"], fin["timeouts"]), (1, 1, 0))
        self.assertIsNotNone(fin["typical"])
        usage = self.c.get("/api/admin/quick/usage", headers=ADMIN).json()
        people = {p["name"]: (p["asked"], p["quick"]) for p in usage["people"]}
        self.assertEqual(people["Alice"], (2, 2))
        self.assertNotIn("text", json.dumps(usage["people"]))                      # counts only
        self.assertEqual(usage["kinds"][0]["intent"], "tasks")
        r = self.c.post("/api/admin/quick/replay", headers=ADMIN).json()
        self.assertEqual((r["checked"], r["same"], r["changed"]), (3, 3, []))
        self.c.post("/api/admin/quick/phrases", json={"words": "whats on my list today", "intent": "bills"},
                    headers=ADMIN)
        r = self.c.post("/api/admin/quick/replay", headers=ADMIN).json()
        self.assertEqual(len(r["changed"]), 2)
        self.assertEqual((r["changed"][0]["was"], r["changed"][0]["now"]), (["tasks"], ["bills"]))
        self.assertNotIn("Alice", json.dumps(r))
        self.assertEqual(self.c.post("/api/admin/quick/replay", headers=ALICE).status_code, 403)

    def test_a_persons_habits_come_first_in_their_suggestions(self):
        now = config.utcnow()
        with db.get_conn() as conn:
            for weeks in (1, 2, 3):
                asked = (now - __import__("datetime").timedelta(days=7 * weeks)).isoformat(timespec="seconds")
                conn.execute("INSERT INTO questions (id, user_id, asked_at, text, state, mode, quick) VALUES "
                             "(?, 'u_alice', ?, 'birthdays', 'done', 'quick', ?)",
                             (bus.new_ulid(), asked, json.dumps({"kind": "answer", "intents": [{"intent": "birthdays"}]})))
        me = self.c.get("/api/me", headers=ALICE).json()
        self.assertEqual(me["suggestions"][0], "Any birthdays coming up?")
        self.assertEqual(me["suggestionWhy"]["Any birthdays coming up?"],
                         f"You often ask this on {config.now().strftime('%A')}s")
        self.assertEqual(self.c.get("/api/me", headers=KID).json()["suggestionWhy"], {})   # only their own

    def test_told_when_done_even_if_it_already_is(self):
        """A quick answer can be written before Assist's hook is registered: the hook then runs at once (1.5.1)."""
        q = self.ask("bills this week")
        got = []
        engine.when_done(q["id"], got.append)
        self.assertEqual([(v["id"], v["state"]) for v in got], [(q["id"], "done")])

    def test_old_phrase_tables_are_widened_for_rephrasings(self):
        with db.get_conn() as conn:
            conn.execute("DROP TABLE quick_phrases")
            conn.execute("CREATE TABLE quick_phrases (id TEXT PRIMARY KEY, phrase TEXT NOT NULL, intent TEXT NOT NULL, "
                         "args TEXT NOT NULL DEFAULT '{}', slot_args TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL "
                         "CHECK (source IN ('admin', 'tapped', 'ai')), seen INTEGER NOT NULL DEFAULT 1, hits INTEGER "
                         "NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_used TEXT, UNIQUE (phrase, intent))")
            conn.execute("INSERT INTO quick_phrases (id, phrase, intent, source, created_at) VALUES ('p1', 'trash day', "
                         "'schedule', 'admin', '2026-01-01T00:00:00+00:00')")
        db.init_db()
        with db.get_conn() as conn:
            conn.execute("INSERT INTO quick_phrases (id, phrase, intent, source, created_at) VALUES ('p2', 'x y', "
                         "'bills', 'rephrased', '2026-01-01T00:00:00+00:00')")
            self.assertEqual([r[0] for r in conn.execute("SELECT id FROM quick_phrases ORDER BY id")], ["p1", "p2"])
