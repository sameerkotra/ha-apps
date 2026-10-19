"""Quick answers with Finance Dashboard's own catalogue (FINANCE_ASSISTANT_TOOLS_SPEC.md §7): tests/data/
finance_tools.json is Finance's tool list as `assist.tools.list` sends it (Finance's tests check it's the same), put
in place of the fixture's Finance tools in the phrase corpus's household (quick_corpus.py).

- each Finance tool's own quick words reach that tool, by the rules alone;
- the spec's example questions the rules answer go to the right tool, the others to the AI or elsewhere as before;
- "Give me a finance summary" reaches finance.overview, "How did we do this month?" finance.summary;
- the whole phrase corpus still reads as it did, but for "finance summary" (now the overview, as decided).
"""
import _env  # noqa: F401  (must be first)

import json
import os
import unittest

import quick_corpus
from app.quick.intents import Ctx
from app.quick.understand import understand

HERE = os.path.dirname(os.path.abspath(__file__))


def finance_ctx() -> Ctx:
    with open(os.path.join(HERE, "data", "finance_tools.json"), encoding="utf-8") as f:
        finance = json.load(f)
    tools = {k: v for k, v in quick_corpus.tools().items() if not k.startswith("finance.")}
    for spec in finance:
        tools[spec["name"]] = {"app": "finance", "appName": "Finance Dashboard", "spec": spec}
    vocab = quick_corpus.VOCAB
    vocab.app_words = {k: list(v["spec"]["quick"].get("words") or []) for k, v in tools.items()
                       if v["spec"].get("quick")}
    return Ctx(tools=tools, today=quick_corpus.TODAY, vocab=vocab, me="Alice")


def tool_of(question: str, c: Ctx):
    r = understand(question, c)
    if r.kind != "answer":
        return r.kind
    return r.parts[0].calls[0].tool, r.parts[0].calls[0].args


class FinanceQuick(unittest.TestCase):
    def setUp(self):
        self.c = finance_ctx()

    def tearDown(self):
        quick_corpus.ctx()                       # the shared vocabulary back to the corpus's own

    def test_each_tools_own_words_reach_it(self):
        for name, entry in self.c.tools.items():
            if not name.startswith("finance."):
                continue
            for word in (entry["spec"].get("quick") or {}).get("words", []):
                with self.subTest(tool=name, word=word):
                    got = tool_of(word, self.c)
                    self.assertEqual(got if isinstance(got, str) else got[0], name)

    def test_the_specs_questions(self):
        cases = {
            "When is my Visa statement due?": "finance.statements",
            "Which statements are overdue?": "finance.statements",
            "When did I last upload the checking statement?": "finance.statements",
            "Am I missing any statements?": "finance.statements",
            "When is my credit card payment due?": "finance.payments_due",
            "Which card payments are due this week?": "finance.payments_due",
            "How has my Visa balance changed?": "finance.balance_history",
            "What's our net worth?": "finance.balance_history",
            "How much do I owe on my cards?": "finance.balance_history",
            "What new subscriptions started?": "finance.subscription_changes",
            "Has Netflix changed its price?": "finance.subscription_changes",
            "How much do we spend on subscriptions a year?": "finance.recurring",
            "What subscriptions do we pay for?": "finance.recurring",
            "What bills are coming up?": "finance.bills",
            "What finance dates are coming up?": "finance.dates",
            "Any due dates this week?": "finance.dates",
            "What do I need to do in Finance?": "finance.dates",
            "Give me a finance summary": "finance.overview",
            "How are my finances?": "finance.overview",
            "Finance overview": "finance.overview",
            "Where do I stand?": "finance.overview",
            "How did we do this month?": "finance.summary",
            "What's the balance on my accounts?": "finance.balances",
        }
        for q, want in cases.items():
            with self.subTest(q):
                got = tool_of(q, self.c)
                self.assertEqual(got if isinstance(got, str) else got[0], want)
        # the default windows the quick calls use
        self.assertEqual(tool_of("When is my credit card payment due?", self.c)[1], {"days": 60})
        self.assertEqual(tool_of("Which card payments are due this week?", self.c)[1]["days"], 7)
        self.assertEqual(tool_of("What do I need to do in Finance?", self.c)[1], {"days": 14})
        self.assertEqual(tool_of("What new subscriptions started?", self.c)[1], {"days": 90})
        self.assertEqual(tool_of("How did we do this month?", self.c)[1], {"month": "2026-10"})
        # questions whose words are other apps' or the AI's (FINANCE_ASSISTANT_TOOLS_SPEC deviations)
        self.assertEqual(tool_of("What's due today?", self.c)[0], "todo.tasks")
        self.assertEqual(understand("Did any subscription go up?", self.c).kind, "model")

    def test_the_corpus_reads_as_before_but_finance_summary(self):
        total, misses = 0, []
        for n, q, expected, args in quick_corpus.lines():
            total += 1
            if q == "finance summary":
                expected = "app:finance.overview"            # decided (§8.5): the overview
            why = quick_corpus.check(q, expected, args, self.c)
            if why:
                misses.append(f"line {n}: {q!r} wanted {expected}, got {why}")
        self.assertGreater(total, 800)
        self.assertEqual(misses, [])


if __name__ == "__main__":
    unittest.main()
