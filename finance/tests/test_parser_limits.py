"""Text a statement PDF can't make the parsers spend minutes on (review 2026-10: F1, F2, F4).

Each pattern that was rewritten is compared with the old one on many generated texts: what is found
must not change, only how long a hostile text takes."""
import random
import re
import time
from collections import Counter

import pytest

from app.parser import deterministic as det
from app.parser import toll_deterministic as td


# ---- the patterns as they were (1.4.1), for the comparisons -------------------------------------

_OLD_AMOUNT_TOKEN = re.compile(r"-?\$?\s*(?<![\d,.])(?P<amount>(?:\d[\d,]*)?\.\d{2})\s*(?:CR)?", re.IGNORECASE)


def _old_extract_amounts(text):
    counts = Counter()
    for m in _OLD_AMOUNT_TOKEN.finditer(text):
        counts[round(float(m.group("amount").replace(",", "")), 2)] += 1
    return counts


def _old_labeled_amount_re(label):
    return re.compile(
        rf"{label}\s*:?\s*(?:\+\s*)?(?P<minus>[-−]\s*)?(?P<open>\(\s*)?\$?\s*(?P<minus2>[-−])?\s*"
        rf"(?P<num>(?:\d[\d,]*)?\.\d{{2}})(?P<trail>-(?![\d$]))?(?P<close>\s*\))?(?P<cr>\s*CR\b)?",
        re.IGNORECASE,
    )


def _old_find(text, labels):
    for label in labels:
        m = _old_labeled_amount_re(label).search(text)
        if m:
            value = round(float(m.group("num").replace(",", "")), 2)
            negative = bool(m.group("minus") or m.group("minus2") or (m.group("open") and m.group("close"))
                            or m.group("cr") or m.group("trail"))
            return -value if negative else value
    return None


def _old_balances(text):
    return _old_find(text, det._PREVIOUS_BALANCE_LABELS), _old_find(text, det._NEW_BALANCE_LABELS)


_PIECES = ["Previous Balance", "previous\nbalance", "New Balance", "Ending Balance", "Current  Balance",
           "Balance Forward", ":", "+", "-", "−", "(", ")", "$", "CR", "cr", "DR", "1,234.56", "14.01",
           ".50", "0.00", "12", "7.5", "99.999", "1.234,56", "abc", "Total", "\t", "\n", "...", "-14.01-", "5"]


def _texts(n=1500, seed=7):
    rng = random.Random(seed)
    for _ in range(n):
        parts = []
        for _ in range(rng.randint(1, 14)):
            parts.append(rng.choice(_PIECES))
            parts.append(rng.choice(["", " ", "  ", "\n", " \t ", ""]))
        yield "".join(parts)


def test_amounts_are_found_exactly_as_before():
    for text in _texts():
        assert det.extract_amounts(text) == _old_extract_amounts(text), repr(text)


def test_balances_are_found_exactly_as_before():
    for text in _texts():
        assert det.extract_balances(text) == _old_balances(text), repr(text)


def test_the_existing_balance_examples_still_read_the_same():
    assert det.extract_balances("Previous Balance $0.00\nNew Balance -$14.01") == (0.0, -14.01)
    assert det.extract_balances("Previous   Balance :   $  0.00    New Balance   $14.01   CR") == (0.0, -14.01)
    assert det.extract_balances("Previous Balance +$10.00\nNew Balance (  $14.01  )") == (10.0, -14.01)


@pytest.mark.parametrize("gap", [" " * 5000, "\n \t" * 3000])
def test_a_long_gap_after_a_label_is_quick(gap):
    text = ("Previous Balance" + gap + "x\n") * 20 + "New Balance" + gap + "$12.00"
    started = time.perf_counter()
    assert det.extract_balances(text) == (None, 12.0)
    assert det.extract_amounts(" " * 100000 + "1.25" + " " * 100000) == Counter({1.25: 1})
    assert time.perf_counter() - started < 2.0


def test_a_long_run_of_spaces_before_amounts_is_quick():
    text = ("$" + " " * 20000 + "x ") * 20
    started = time.perf_counter()
    assert det.extract_amounts(text) == Counter()
    assert time.perf_counter() - started < 2.0


def test_a_very_long_toll_line_is_skipped_quickly():
    line = "8/10/2026 6:30 AM XX ROAD " + "PLAZA " * 20000 + "Lane"
    started = time.perf_counter()
    ex = td.parse_toll_text("Transactions For Device # 1234567 Plate # ABC-CO\n" + line + "\n")
    assert time.perf_counter() - started < 2.0
    assert ex.unparsed and "too long" in ex.unparsed[0]
    assert not ex.cars[0].passes


def test_toll_rows_still_read_including_wrapped_plaza_names():
    text = ("Transactions For Device # 1234567 Plate # ABC-CO\n"
            "8/10/2026 6:30:53 AM XX TOLLWAY1 SMOKY HILL RD Lane 1 South VTOLL 1 $1.25\n"
            "8/10/2026 6:37:12 AM XX TOLLWAY1 A VERY LONG\nPLAZA NAME Lane 3 South VTOLL 2 $2.60\n")
    ex = td.parse_toll_text(text)
    assert [(p.plaza, p.amount) for p in ex.cars[0].passes] == [("SMOKY HILL RD", 1.25), ("A VERY LONG PLAZA NAME", 2.6)]
    assert ex.unparsed == []


def test_pdftotext_output_is_capped(env):
    path = env.pdf(["Previous Balance $1.00", "New Balance $2.00"] + ["filler text line"] * 30)
    assert "Previous Balance" in det.run_pdftotext(path)
    with pytest.raises(RuntimeError, match="larger than"):
        det.run_pdftotext(path, max_bytes=50)
