"""LLM extraction path (SPEC.md section 4) — genuinely independent
of the deterministic path. Pages are rasterized to images via pdftoppm
(poppler-utils — same package as pdftotext, no new dependency) and sent
to Ollama as a vision request, NOT fed pdftotext's own text output. A
pdftotext extraction bug must not be able to silently propagate into both
"independent" paths at once — that's the entire reason this path uses
images instead of text.
"""
import base64
import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

from .. import ai_client

MAX_JSON_RETRIES = 2  # section 5: retry limit on JSON parse failures


@dataclass
class ParsedTransaction:
    """One extracted transaction (only the vision pass produces them)."""
    date: str          # ISO-8601, normalized from whatever the statement used
    amount: float       # signed — negative=outflow, positive=inflow (section 5)
    description: str

# Ollama unloads an idle model after its keep_alive window and reloads it
# lazily on the next request — for a large local model (e.g. 122B params)
# that reload alone can take minutes, on top of however long the actual
# vision generation takes over several page images. A short-ish timeout
# here doesn't fail because Ollama is broken; it fails because the model
# genuinely hadn't finished loading yet. Generous on purpose.
WARMUP_TIMEOUT_SECONDS = 300     # loading a large model into memory
GENERATE_TIMEOUT_SECONDS = 900   # the real multi-image extraction call

# Skip the warm-up when any Ollama call succeeded this recently: the model is
# still loaded (Ollama keeps it for a few minutes by default), so back-to-back
# statements don't each pay the load time. Module-level, because "loaded" is a
# fact about Ollama, not about one statement.
RECENT_USE_SKIP_WARMUP_SECONDS = 120

_last_ollama_success_at: float | None = None


def _mark_ollama_success() -> None:
    global _last_ollama_success_at
    _last_ollama_success_at = time.monotonic()


def _model_recently_used() -> bool:
    """True when some call to Ollama completed recently enough that the
    model is almost certainly still loaded in memory — see
    RECENT_USE_SKIP_WARMUP_SECONDS above for why this specific window."""
    return (
        _last_ollama_success_at is not None
        and (time.monotonic() - _last_ollama_success_at) < RECENT_USE_SKIP_WARMUP_SECONDS
    )


@dataclass
class VisionResult:
    transactions: list[ParsedTransaction]
    raw_response: str  # kept for the admin debug view (section 13)


class VisionExtractionError(Exception):
    """Covers every way this path can fail: Ollama unreachable, the model
    doesn't support images, JSON extraction exhausted its retries. The
    caller treats all of these identically — flag the statement as
    `error`, never silently fall back to text-based extraction (section 4:
    a silent fallback would quietly undo the independence this path
    exists for)."""
    def __init__(self, message: str, raw_response: str = ""):
        super().__init__(message)
        self.raw_response = raw_response  # whatever Ollama actually said, if anything — for the debug view


def rasterize_pdf(pdf_path: str) -> tuple[str, list[str]]:
    """pdftoppm -png, one file per page, in a fresh temp dir the caller
    is responsible for cleaning up — returns (dir, image_paths); the dir
    itself, not just the files in it, since the caller needs it to
    actually remove the directory afterward."""
    out_dir = tempfile.mkdtemp(prefix="pages-")
    prefix = os.path.join(out_dir, "page")
    result = subprocess.run(
        ["pdftoppm", "-png", "-r", "150", pdf_path, prefix],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        # mkdtemp already created out_dir by this point, and we're about
        # to raise without ever returning it to the caller — clean it up
        # here or it's orphaned permanently, not just until the caller's
        # own cleanup runs (which never gets a chance to on this path).
        shutil.rmtree(out_dir, ignore_errors=True)
        raise VisionExtractionError(f"pdftoppm failed: {result.stderr.strip()}")
    return out_dir, sorted(glob.glob(f"{prefix}-*.png"))


def _extract_json(text: str) -> dict:
    """First '{' to last '}' — Ollama occasionally wraps JSON in a
    markdown fence or adds conversational text around it despite the
    system prompt (section 5). Raises ValueError if no braces are found
    or the extracted substring still doesn't parse; the caller is what
    turns that into a retry."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in response")
    return json.loads(text[start:end + 1])


_PROMPT = """You are extracting transactions from a bank/credit-card statement.
Respond with ONLY a JSON object, no other text, in this exact shape:
{"transactions": [{"date": "YYYY-MM-DD", "amount": -42.50, "description": "..."}]}
Amounts are signed: negative for money leaving the account (purchases,
payments made), positive for money entering it (deposits, refunds,
credits). Include every transaction line you can find across all pages."""

# A card's amounts are signed the way the app stores them (app/matching.py): what is owed goes up
# with a purchase and down with a payment.
_CARD_PROMPT = """You are extracting transactions from a CREDIT CARD statement.
Respond with ONLY a JSON object, no other text, in this exact shape:
{"transactions": [{"date": "YYYY-MM-DD", "amount": 42.50, "description": "..."}]}
Amounts are signed from the card's point of view: POSITIVE for anything that
increases the balance owed (purchases, fees, interest charged, cash advances),
NEGATIVE for anything that reduces it (payments, credits, refunds, returns).
Include every transaction line you can find across all pages. Do not include
summary lines (previous balance, new balance, totals)."""

MAX_NOTES_CHARS = 2000


def statement_prompt(account_type: str | None = None, notes: str = "") -> str:
    """The statement prompt for this account type, plus the person's notes on an earlier reading."""
    prompt = _CARD_PROMPT if account_type == "credit_card" else _PROMPT
    notes = (notes or "").strip()[:MAX_NOTES_CHARS]
    if notes:
        prompt += ("\n\nA person checked an earlier extraction of this same statement against the PDF and "
                   "wrote the notes below. Follow them; they override the rules above where they conflict:\n"
                   + notes)
    return prompt


def _ask(ollama_url: str, model: str, prompt: str, *, images: list[str] | None = None, want_json: bool = False,
         timeout: float | None, purpose: str) -> ai_client.Reply:
    """One request through ai_client (whichever provider App settings names), as a VisionExtractionError on failure."""
    try:
        reply = ai_client.generate(prompt, url=ollama_url, model=model, images=images, want_json=want_json,
                                   timeout=timeout, purpose=purpose)
    except ai_client.AIError as e:
        raise VisionExtractionError(str(e), raw_response=e.raw) from e
    if ai_client.current(ollama_url, model).provider == "ollama":
        # Ollama answered, so it has the model loaded (its keep_alive timer restarted).
        _mark_ollama_success()
    return reply


def warmup_model(ollama_url: str, model: str, timeout: int | None = WARMUP_TIMEOUT_SECONDS) -> None:
    """Ollama only: a tiny "hi" before the slow image request. Ollama loads a model lazily and
    unloads it when idle; for a large local model the load alone can take minutes, which would
    otherwise show up as the real request timing out or answering nonsense. A cloud provider has
    nothing to load, so nothing is sent (and nothing is paid for)."""
    if ai_client.current(ollama_url, model).provider != "ollama":
        return
    _ask(ollama_url, model, "hi", timeout=timeout, purpose="Warm-up")


def _images_b64(image_paths: list[str]) -> list[str]:
    out = []
    for path in image_paths:
        with open(path, "rb") as f:
            out.append(base64.b64encode(f.read()).decode("ascii"))
    return out


def ask_vision_json(
    pdf_path: str, ollama_url: str, model: str, prompt: str,
    on_step: Callable[[str], None] | None = None,
    timeout: int | None = GENERATE_TIMEOUT_SECONDS,
) -> tuple[dict, str]:
    """Shared by bank statements, utility bills and toll statements: warm the
    model up (Ollama, unless it was just used), rasterize every page, send them
    with `prompt`, and retry until the answer contains parseable JSON.
    Returns (data, raw_text). Raises VisionExtractionError — never a partial or
    guessed result — always carrying whatever the model last returned."""
    def step(msg: str) -> None:
        if on_step:
            on_step(msg)

    cfg = ai_client.current(ollama_url, model)
    if not cfg.url or not cfg.model:
        raise VisionExtractionError("AI isn't set up: the admin adds the provider, address and model on Admin → App settings.")

    if cfg.provider == "ollama":
        if _model_recently_used():
            step("Model already loaded from a recent statement — skipping warm-up")
        else:
            step("Warming up AI model")
            warmup_model(ollama_url, model)

    step("Rasterizing pages for AI vision")
    out_dir, page_images = rasterize_pdf(pdf_path)
    try:
        images = _images_b64(page_images)
        last_error: Exception | None = None
        raw = ""
        slow = ", can take several minutes on a large local model" if cfg.provider == "ollama" else ""
        for attempt in range(MAX_JSON_RETRIES + 1):
            step(f"Analyzing with AI ({cfg.label}, attempt {attempt + 1}/{MAX_JSON_RETRIES + 1}{slow})")
            reply = _ask(ollama_url, model, prompt, images=images, want_json=True, timeout=timeout,
                         purpose=f"Extraction (attempt {attempt + 1})")
            raw = reply.text
            try:
                return _extract_json(raw), raw
            except (ValueError, json.JSONDecodeError) as e:
                last_error = e
        raise VisionExtractionError(
            f"JSON extraction failed after {MAX_JSON_RETRIES + 1} attempts: {last_error}",
            raw_response=raw,
        )
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def debug_call(pdf_path: str, ollama_url: str, model: str, prompt: str = _PROMPT) -> tuple[float, str]:
    """Admin debug action: re-send a PDF with no timeout and no retries and
    return (elapsed_seconds, the provider's whole raw response) — the whole
    response, since a reasoning model's real output may be in another field
    ("thinking"). Raises VisionExtractionError when the AI or pdftoppm fails."""
    start = time.monotonic()
    warmup_model(ollama_url, model, timeout=None)
    out_dir, page_images = rasterize_pdf(pdf_path)
    try:
        reply = _ask(ollama_url, model, prompt, images=_images_b64(page_images), want_json=True,
                     timeout=None, purpose="Debug re-send")
        try:
            raw = json.dumps(json.loads(reply.raw), indent=2)
        except json.JSONDecodeError:
            raw = reply.raw
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    return time.monotonic() - start, raw


def extract_vision(
    pdf_path: str, ollama_url: str, model: str,
    on_step: Callable[[str], None] | None = None,
    account_type: str | None = None, notes: str = "",
) -> VisionResult:
    """Bank/credit-card statement transactions. A malformed row is skipped, not fatal."""
    data, raw = ask_vision_json(pdf_path, ollama_url, model, statement_prompt(account_type, notes), on_step)
    transactions = []
    for row in data.get("transactions", []):
        try:
            transactions.append(ParsedTransaction(
                date=row["date"], amount=float(row["amount"]), description=str(row["description"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return VisionResult(transactions=transactions, raw_response=raw)
