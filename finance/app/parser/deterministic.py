"""Deterministic path (SPEC.md sections 4-5): amount validation
and balance reconciliation, not transaction extraction.

Two independent, cheap, non-LLM checks against the statement's own
`pdftotext -layout` text — neither tries to independently reconstruct
each transaction (date + description + amount, column-aligned): real
statements vary layout too much for a generic regex to reliably survive
(dual Trans-Date/Post-Date columns, extra reference-number columns,
multi-line descriptions), which is why the old row-level parser found
zero transactions against a real credit-card statement and provided no
verification value at all.

1. extract_amounts() — every money-shaped token anywhere in the text,
   counted with multiplicity. Used (pipeline.py) to validate that each
   LLM-extracted transaction's amount was actually printed somewhere on
   the statement. Catches a real observed failure: a vision model
   misreading a figure ($287.00 read back as $2187).

2. extract_balances() — the statement's previous/beginning and new/
   ending balance, found by label rather than position. Used (pipeline.py)
   to check that the extracted transactions' sum actually accounts for
   the statement's own printed balance movement — a different failure
   axis from (1): two extractions can agree on every individual row and
   still be missing (or duplicating) one entirely, which no amount of
   per-row agreement can catch but arithmetic against the statement's own
   totals will.

Neither check needs to understand a statement's full summary layout or
column structure, which is what made the old row-level parser brittle
per-bank. Both look for a short, universal vocabulary (a money-shaped
number; "previous/new balance" style labels) that's far more standardized
across issuers than transaction-row layout ever is.

What neither of these catches: a wrong description, a wrong date, or a
fabricated transaction whose amount happens to coincidentally match an
unrelated real number elsewhere on the statement (a fee total, a credit
limit) while also failing to move the reconciliation total noticeably.
They're cheap sanity checks, not a second full extraction.
"""
import re
import subprocess
from collections import Counter
from dataclasses import dataclass

from ..common import sandbox_run


class NoTextLayer(Exception):
    """Raised when a PDF has no extractable text — scanned/image-only.
    Section 4: rejected outright, no OCR fallback in v1."""


@dataclass
class DeterministicResult:
    amount_counts: Counter            # magnitude (rounded to cents) -> how many times it's printed in the statement text
    previous_balance: float | None    # None if no recognizable label was found — reconciliation is then skipped, not flagged
    new_balance: float | None
    raw_text: str                     # full pdftotext output — the caller persists this length for raw_text_length
    raw_text_length: int


# A near-zero extracted-text length is the signal a PDF has no text layer
# (section 4's raw_text_length check) — real statements run at minimum a
# few hundred characters even on a single page.
MIN_TEXT_LENGTH = 50

# Any money-shaped token, anywhere in the text — no date/description
# anchor, no line anchor, no column alignment assumed. Deliberately loose:
# this only needs to find candidate amounts, not decide which line (or
# which transaction) they belong to.
# The integer part is optional: statements print amounts under a dollar as
# ".50" as often as "0.50", and the lookbehind stops a bare ".50" from being
# carved out of the tail of a longer number or an ellipsis ("....50").
# Only the amount itself is matched: a sign, "$" or "CR" around it was never captured, and an
# uncaptured "\s*" in front made every position of a long run of spaces rescan the whole run
# (quadratic). The lookbehind alone decides where an amount may start, so the same amounts are found.
_AMOUNT_TOKEN = re.compile(r"(?<![\d,.])(?P<amount>(?:\d[\d,]*)?\.\d{2})")

# Label phrasings for the balance a statement period started/ended at.
# Ordered roughly by how common each phrasing is; the first match wins —
# a statement prints this once, prominently, in its summary, so a second
# incidental match (e.g. restated in fine print) isn't expected to matter
# in practice. Deliberately NOT including "Balance Due"/"Minimum Payment
# Due" — those are the amount owed *this cycle*, not the account's actual
# balance, and conflating them would make reconciliation wrong instead of
# just skipped.
_PREVIOUS_BALANCE_LABELS = [
    r"previous\s+balance",
    r"beginning\s+balance",
    r"opening\s+balance",
    r"balance\s+forward",
]
_NEW_BALANCE_LABELS = [
    r"new\s+balance",
    r"ending\s+balance",
    r"current\s+balance",
]


# The most text one PDF may give (a real statement is a few hundred KB at most). The tool writes
# to a file in its scratch folder (capped by the sandbox's file-size limit) and only this much is read.
MAX_TEXT_BYTES = 5 * 1024 * 1024


def run_pdftotext(pdf_path: str, *args: str, max_bytes: int = MAX_TEXT_BYTES, timeout: float = 30) -> str:
    """Shells out to pdftotext -layout. -layout isn't load-bearing for
    either check here (no columns to line up — both scan the whole text),
    but it's kept because it's the one pdftotext call this path makes and
    losing layout would only ever hurt, never help, either regex.
    Runs on a copy of the file as the unprivileged pdfworker user, with
    resource limits (common/python/sandbox_run.py). The text goes to a file,
    never a pipe, so a huge output can't fill the app's memory."""
    with sandbox_run.Scratch(prefix="pdftotext-") as box:
        src = box.add_file(pdf_path, "in.pdf")
        result = box.run(
            ["pdftotext", *(args or ("-layout",)), src, "out.txt"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pdftotext failed: {result.stderr.decode(errors='replace').strip()[:500]}")
        try:
            data = box.read("out.txt", max_bytes=max_bytes)
        except sandbox_run.SandboxError:
            raise RuntimeError(f"The PDF's text is larger than {max_bytes // (1024 * 1024)} MB.") from None
    return data.decode("utf-8", errors="replace")


def extract_amounts(text: str) -> Counter:
    """Every money-shaped token in the text, magnitude only (sign/CR
    ignored — this validates that a figure was printed somewhere on the
    statement, not what it means), counted with multiplicity. Counting
    matters: a real amount can legitimately appear more than once (two
    $50 charges), and multiplicity is what stops a fabricated transaction
    from hiding behind an amount that's already accounted for by a real
    one (see pipeline.py's _consume_amount)."""
    counts: Counter = Counter()
    for m in _AMOUNT_TOKEN.finditer(text):
        counts[round(float(m.group("amount").replace(",", "")), 2)] += 1
    return counts


def _find_labeled_amount(text: str, label_patterns: list[str]) -> float | None:
    """First amount found after any of the given label phrasings — sign
    preserved this time (unlike extract_amounts), since a balance's sign
    is meaningful (a credit balance can legitimately be negative). A credit
    balance is printed several ways, and every one of them is negative:
    "-$14.01", "$-14.01", "−$14.01", "($14.01)", "$14.01-" and "$14.01 CR". A "+" or a
    colon between the label and the figure is just punctuation."""
    text = _collapse_spaces(text)
    for label in label_patterns:
        m = _labeled_amount_re(label).search(text)
        if m:
            value = round(float(m.group("num").replace(",", "")), 2)
            negative = bool(m.group("minus") or m.group("minus2") or (m.group("open") and m.group("close"))
                            or m.group("cr") or m.group("trail"))
            return -value if negative else value
    return None


_SPACES = re.compile(r"\s+")


def _collapse_spaces(text: str) -> str:
    """Every run of whitespace as one space. The pattern below has several optional-whitespace parts
    in a row; on a long run of spaces after a label with no figure they could split it in very many
    ways (a 400-space gap took minutes). With one space at most, each part has one choice, and since
    the pattern only asks whether there is whitespace, never how much, what it finds is unchanged."""
    return _SPACES.sub(" ", text)


def _labeled_amount_re(label: str) -> re.Pattern:
    return re.compile(
        rf"{label}\s*:?\s*(?:\+\s*)?(?P<minus>[-\u2212]\s*)?(?P<open>\(\s*)?\$?\s*(?P<minus2>[-\u2212])?\s*"
        rf"(?P<num>(?:\d[\d,]*)?\.\d{{2}})(?P<trail>-(?![\d$]))?(?P<close>\s*\))?(?P<cr>\s*CR\b)?",
        re.IGNORECASE,
    )


def extract_balances(text: str) -> tuple[float | None, float | None]:
    """The statement's previous/beginning and new/ending balance —
    everything pipeline.py's reconciliation check needs, and nothing
    more; it doesn't attempt to parse the rest of the summary block
    (payments/credits/purchases subtotals), since those field names and
    formulas vary per issuer far more than these two do. Either value is
    None if no recognized label was found, which the caller treats as
    "reconciliation not attempted," not as a mismatch."""
    return (
        _find_labeled_amount(text, _PREVIOUS_BALANCE_LABELS),
        _find_labeled_amount(text, _NEW_BALANCE_LABELS),
    )


def extract_deterministic(pdf_path: str) -> DeterministicResult:
    """Raises NoTextLayer if the PDF has no extractable text — the caller
    rejects the upload outright (section 4), never falls through to a
    fabricated result."""
    text = run_pdftotext(pdf_path)
    if len(text.strip()) < MIN_TEXT_LENGTH:
        raise NoTextLayer(f"Only {len(text.strip())} chars extracted — no text layer")
    previous_balance, new_balance = extract_balances(text)
    return DeterministicResult(
        amount_counts=extract_amounts(text),
        previous_balance=previous_balance,
        new_balance=new_balance,
        raw_text=text,
        raw_text_length=len(text),
    )
