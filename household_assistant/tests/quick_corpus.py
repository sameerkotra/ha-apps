"""The quick answers' phrase corpus (HOUSEHOLD_ASSISTANT_SPEC.md §17.12): tests/data/quick_corpus.tsv, one question
per line, read against every household tool (tests/data/quick_tools.json) and an invented household's names, on a
fixed day (Wednesday 14 October 2026).

    question <TAB> expected <TAB> arguments (optional)

expected: an intent ("tasks"), several for a question with several parts ("tasks+birthdays"), "choices:owe,balances"
(a Did you mean between those), "none" (not placed), or "model" (needs the AI). arguments: a JSON object the first
call's arguments must contain ({"when": "tomorrow"}); "-" for a key that must not be there ({"list": "-"}).
Lines starting with # and empty lines are skipped.

Run alone to see every miss: python3 tests/quick_corpus.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from app.quick.intents import Ctx  # noqa: E402
from app.quick.match import Vocab  # noqa: E402
from app.quick.understand import understand  # noqa: E402

TODAY = date(2026, 10, 14)            # a Wednesday
VOCAB = Vocab(lists=["Personal", "Home jobs", "Costco", "Work"], items=["milk", "basmati rice", "eggs", "dish soap", "Curd"],
              stores=["Costco", "Dmart"], groups=["Flat", "Goa trip"], people=["Alice", "Ravi", "Meera", "Sam"],
              categories=["Groceries", "Food & Dining", "Fuel", "Utilities", "Entertainment"],
              games=["Snake", "Brick Breaker", "Sudoku"], accounts=["Visa", "Checking"])


def tools() -> dict:
    with open(os.path.join(HERE, "data", "quick_tools.json"), encoding="utf-8") as f:
        return json.load(f)


def ctx() -> Ctx:
    t = tools()
    VOCAB.app_words = {k: list(v["spec"]["quick"].get("words") or []) for k, v in t.items() if v["spec"].get("quick")}
    return Ctx(tools=t, today=TODAY, vocab=VOCAB, me="Alice")


def lines():
    with open(os.path.join(HERE, "data", "quick_corpus.tsv"), encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                raise ValueError(f"line {n}: question <TAB> expected")
            yield n, parts[0], parts[1].strip(), (json.loads(parts[2]) if len(parts) > 2 and parts[2].strip() else {})


def check(question: str, expected: str, args: dict, c: Ctx) -> str | None:
    """None when the question is understood as expected, else what happened instead."""
    r = understand(question, c)
    got = r.kind
    if expected in ("none", "model"):
        return None if got == expected else f"{got} {[m.intent for m in r.parts or r.choices]}"
    if expected.startswith("choices:"):
        want = set(expected[8:].split(","))
        have = {m.intent for m in r.choices}
        return None if got == "choices" and want <= have else f"{got} {[m.intent for m in r.parts or r.choices]}"
    if got != "answer":
        return f"{got} {[(m.intent, m.score) for m in r.choices]}"
    want = expected.split("+")
    have = [m.intent for m in r.parts]
    if have != want:
        return f"answer {have}"
    first = r.parts[0].calls[0].args if r.parts[0].calls else {}
    for k, v in args.items():
        if v == "-":
            if k in first:
                return f"args {first} (didn't want {k})"
        elif first.get(k) != v:
            return f"args {first}"
    return None


def run() -> tuple[int, list]:
    c = ctx()
    misses, total = [], 0
    for n, q, exp, args in lines():
        total += 1
        why = check(q, exp, args, c)
        if why:
            misses.append((n, q, exp, args, why))
    return total, misses


def per_intent() -> dict:
    counts: dict[str, int] = {}
    for _n, _q, exp, _a in lines():
        for k in exp.split("+"):
            counts[k] = counts.get(k, 0) + 1
    return counts


if __name__ == "__main__":
    total, misses = run()
    for n, q, exp, args, why in misses:
        print(f"{n:4} {q!r:60} want {exp} {json.dumps(args) if args else ''}\n      got  {why}")
    print(f"{total - len(misses)} of {total} as expected")
    small = {k: v for k, v in per_intent().items() if v < 25 and k not in ("none", "model") and not k.startswith("choices")}
    if small:
        print("fewer than 25 lines:", small)
