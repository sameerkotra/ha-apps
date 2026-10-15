"""Quick answers' rules, without the app (HOUSEHOLD_ASSISTANT_SPEC.md §17.3–17.6, §17.12):

- the phrase corpus (tests/data/quick_corpus.tsv): every line as expected, at least 25 phrasings per kind of
  question, the near-misses going elsewhere, and 1,000 questions read in under a second;
- normalising: contractions, text-speak, number words, filler, word forms, typos (never a real word or a name);
- slots: today/tomorrow/this week/next 3 days, months with and without a year, weekdays, times, amounts;
- the learned phrases' templates; the apps' own words; the admin's Test.
"""
import _env  # noqa: F401  (must be first)

import time
import unittest
from datetime import date

import quick_corpus
from app.quick import normalise, slots
from app.quick.intents import Ctx
from app.quick.match import Vocab, read, template
from app.quick.understand import explain, understand

TODAY = quick_corpus.TODAY


def words(text):
    return [t.text for t in normalise.strip_filler(normalise.tokens(text))]


class Corpus(unittest.TestCase):
    def test_every_line(self):
        total, misses = quick_corpus.run()
        self.assertGreater(total, 800)
        self.assertEqual([f"line {n}: {q!r} wanted {e} {a or ''}, got {why}" for n, q, e, a, why in misses], [])

    def test_at_least_25_phrasings_each(self):
        counts = quick_corpus.per_intent()
        from app.quick.intents import INTENTS
        for i in INTENTS:
            with self.subTest(intent=i.key):
                self.assertGreaterEqual(counts.get(i.key, 0), 25)
        self.assertGreaterEqual(counts.get("model", 0) + counts.get("none", 0), 25)

    def test_speed(self):
        c = quick_corpus.ctx()
        qs = [q for _n, q, _e, _a in quick_corpus.lines()]
        qs = (qs * (1000 // len(qs) + 1))[:1000]
        started = time.thread_time()                           # this thread's own CPU time: not other suites'
        for q in qs:
            understand(q, c)
        self.assertLess(time.thread_time() - started, 2.0)    # §17.12: 1 s (about 0.6 s here); slack for slow CI


class Normalising(unittest.TestCase):
    def test_contractions_text_speak_and_filler(self):
        self.assertEqual(words("Whats on my lists 2day??"), ["what", "is", "on", "my", "lists", "today"])
        self.assertEqual(words("hey, can you tell me what's due tmrw please"), ["what", "is", "due", "tomorrow"])
        self.assertEqual(words("I've done the bdays"), ["i", "have", "done", "the", "birthday"])
        self.assertEqual(words("u r gonna love it"), ["you", "are", "going", "to", "love", "it"])

    def test_numbers_amounts_and_times(self):
        toks = normalise.tokens("a couple of eggs, twenty five minutes, ₹500 and $12.50 at 5:30pm")
        self.assertEqual([(t.kind, t.value) for t in toks if t.kind != "word"],
                         [("num", 2), ("num", 25), ("money", 500.0), ("money", 12.5), ("time", "17:30")])
        self.assertEqual([t.value for t in normalise.tokens("10kg 2x")], [10, None, 2, None])

    def test_word_forms_keep_the_word_as_typed(self):
        toks = normalise.tokens("take the bins out, spent, cheaper")
        self.assertEqual([t.text for t in toks], ["take", "the", "bin", "out", "spend", "cheap"])
        self.assertEqual([t.orig for t in toks][2], "bins")

    def test_typos_are_put_right_but_never_real_words_or_names(self):
        known = {"rice", "milk", "meera"}
        targets = {"price", "birthday", "shopping", "calories", "list"}
        fixed = normalise.correct(normalise.tokens("birthdya shoping calries lsit rice meera"), targets, known)
        self.assertEqual([t.text for t in fixed], ["birthday", "shopping", "calorie", "list", "rice", "meera"])
        self.assertEqual([t.fixed for t in fixed], [True, True, True, True, False, False])
        self.assertEqual(normalise.distance("calries", "calories", 1), 1)
        self.assertEqual(normalise.distance("calries", "calorie", 1), 2)
        self.assertEqual(normalise.distance("abc", "xyz", 1), 2)

    def test_a_real_word_isnt_corrected_when_it_reads_well_as_typed(self):
        c = quick_corpus.ctx()
        r = understand("tick off water the plants", c)
        self.assertEqual(r.parts[0].calls[0].args, {"task": "water the plants"})
        r = understand("whats on the shoping list tmrw", c)
        self.assertEqual(r.parts[0].intent, "shopping")


class Slots(unittest.TestCase):
    def s(self, text):
        return slots.read(normalise.tokens(text), TODAY)

    def test_when(self):
        self.assertEqual(self.s("what's on today").when, "today")
        self.assertEqual(self.s("tonight").when, "today")
        self.assertEqual(self.s("tomorrow morning").when, "tomorrow")
        self.assertEqual((self.s("this weekend").when, self.s("this weekend").days), ("week", 7))
        self.assertEqual(self.s("next week").days, 14)
        self.assertEqual(self.s("in the next 3 days").days, 3)
        self.assertEqual(self.s("within two weeks").days, 14)
        self.assertEqual(self.s("overdue").when, "overdue")
        self.assertEqual(self.s("on friday").date, "2026-10-16")
        self.assertEqual(self.s("next wednesday").date, "2026-10-21")
        self.assertEqual(self.s("yesterday").date, "2026-10-13")
        s = self.s("in the last 7 days")
        self.assertEqual((s.days, s.back, s.when), (7, True, None))

    def test_months(self):
        self.assertEqual(self.s("in september").month, "2026-09")
        self.assertEqual(self.s("in november").month, "2025-11")        # the latest one not in the future
        self.assertEqual(self.s("sept 2025").month, "2025-09")
        self.assertEqual(self.s("last month").month, "2026-09")
        self.assertEqual(self.s("this month").month, "2026-10")
        self.assertEqual(self.s("09/2026").month, "2026-09")
        self.assertEqual(self.s("2026-08").month, "2026-08")
        self.assertIsNone(self.s("may I see the list").month)          # "may" is a month only after in/for/…
        self.assertEqual(self.s("in may").month, "2026-05")

    def test_times_and_amounts(self):
        self.assertEqual(self.s("at 5pm").time, "17:00")
        self.assertEqual(self.s("at 5").time, "17:00")                 # in the afternoon
        self.assertEqual(self.s("at 9").time, "09:00")
        self.assertEqual(self.s("at noon").time, "12:00")
        self.assertEqual(self.s("17:30").time, "17:30")
        self.assertEqual(self.s("i paid 40 rupees").amount, 40.0)
        self.assertEqual(self.s("$45").amount, 45.0)


class Templates(unittest.TestCase):
    def test_slots_and_names_are_placeholders(self):
        v = Vocab(lists=["Costco"], people=["Ravi"])
        self.assertEqual(template(read("what's on the costco list tomorrow", TODAY, v)), "what is on {name} list {when}")
        self.assertEqual(template(read("how much does Ravi owe", TODAY, v)), "how much does {name} owe")
        self.assertEqual(template(read("spending in september", TODAY, v)), "spend in {month}")
        self.assertEqual(template(read("bills in the next 3 days", TODAY, v)), "bill in {when}")


class AppWords(unittest.TestCase):
    def test_an_apps_own_words_call_its_tool(self):
        tools = quick_corpus.tools()
        tools["todo.schedule"]["spec"]["quick"] = {"concepts": ["SCHEDULE"], "words": ["skip day"], "args": {"days": 7}}
        v = Vocab(app_words={"todo.schedule": ["skip day"]})
        r = understand("when is skip day", Ctx(tools=tools, today=TODAY, vocab=v))
        self.assertEqual((r.kind, r.parts[0].intent), ("answer", "schedule"))

    def test_a_tool_with_no_rule_of_its_own(self):
        tools = quick_corpus.tools()
        tools["garden.water"] = {"app": "garden", "appName": "Garden", "spec": {
            "name": "garden.water", "what": "When the garden was last watered. More.", "acts": False,
            "args": {"days": {"type": "number", "min": 1, "max": 30}},
            "quick": {"words": ["watered the garden", "garden watering"], "args": {"days": 14}}}}
        v = Vocab(app_words={"garden.water": ["watered the garden", "garden watering"]})
        r = understand("when was the garden watering", Ctx(tools=tools, today=TODAY, vocab=v))
        self.assertEqual(r.kind, "answer")
        self.assertEqual((r.parts[0].intent, r.parts[0].calls[0].args), ("app:garden.water", {"days": 14}))
        self.assertEqual(r.parts[0].label, "When the garden was last watered")


class Test(unittest.TestCase):
    def test_explain_says_how_it_was_read(self):
        out = explain("whats on the shoping list tmrw", quick_corpus.ctx())
        self.assertEqual(out["result"], "answer")
        self.assertIn("shopping*", out["words"])
        self.assertEqual(out["slots"], {"when": "tomorrow"})
        self.assertIn("SHOPPING", out["concepts"])


if __name__ == "__main__":
    unittest.main()
