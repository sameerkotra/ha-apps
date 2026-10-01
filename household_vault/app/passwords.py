"""Password generator, passphrases and a simple strength estimate (SPEC §9.3).

The estimate is deliberately conservative: character-class entropy, minus
penalties for common passwords, dictionary words, repeats and sequences, and
a passphrase path for word-separated passwords. Score 0–4:
0 very weak · 1 weak · 2 fair · 3 good · 4 strong.
"""
import math
import os
import re
import secrets
import string

_WORDS_PATH = os.path.join(os.path.dirname(__file__), "data", "words.txt")
with open(_WORDS_PATH, encoding="utf-8") as _f:
    WORDS = [w.strip() for w in _f if w.strip()]
_WORDSET = set(WORDS)

COMMON = {
    "password", "passw0rd", "p@ssword", "letmein", "welcome", "qwerty", "qwertyuiop", "asdfgh", "zxcvbn", "iloveyou",
    "admin", "administrator", "login", "master", "monkey", "dragon", "football", "baseball", "sunshine", "princess",
    "shadow", "superman", "batman", "trustno1", "abc123", "123456", "1234567", "12345678", "123456789", "1234567890",
    "111111", "000000", "654321", "secret", "changeme", "default", "homeassistant", "hunter2", "cricket", "india",
}
SEQS = ["abcdefghijklmnopqrstuvwxyz", "qwertyuiop", "asdfghjkl", "zxcvbnm", "0123456789"]
# A dictionary word counts as one pick from a 7,776-word list (the EFF list size) —
# what an attacker would try first — even though our own list is smaller.
WORD_BITS = math.log2(7776)
LABELS = ["very weak", "weak", "fair", "good", "strong"]
SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/"
AMBIGUOUS = set("Il1O0o")


def generate(length: int = 20, lower: bool = True, upper: bool = True, digits: bool = True, symbols: bool = True,
             avoid_ambiguous: bool = False) -> str:
    sets = [s for s, on in ((string.ascii_lowercase, lower), (string.ascii_uppercase, upper),
                            (string.digits, digits), (SYMBOLS, symbols)) if on]
    if not sets:
        sets = [string.ascii_lowercase]
    if avoid_ambiguous:
        sets = ["".join(c for c in s if c not in AMBIGUOUS) for s in sets]
    length = max(length, len(sets))
    alphabet = "".join(sets)
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(c in s for c in pw) for s in sets):             # at least one of each chosen set
            return pw


def passphrase(words: int = 6, separator: str = "-", capitalize: bool = False, number: bool = False) -> str:
    chosen = [secrets.choice(WORDS) for _ in range(words)]
    if capitalize:
        chosen = [w.capitalize() for w in chosen]
    if number:
        i = secrets.randbelow(len(chosen))
        chosen[i] = chosen[i] + str(secrets.randbelow(10))
    return separator.join(chosen)


def random_vault_password() -> str:
    """32 random bytes, base64url: a random-password vault's password (SPEC §5.1)."""
    return secrets.token_urlsafe(32)


def _charset_bits(pw: str) -> float:
    size = 0
    if re.search(r"[a-z]", pw):
        size += 26
    if re.search(r"[A-Z]", pw):
        size += 26
    if re.search(r"[0-9]", pw):
        size += 10
    if re.search(r"[^A-Za-z0-9]", pw):
        size += 33
    return len(pw) * math.log2(size or 1)


def _penalised(pw: str) -> float:
    low = pw.lower()
    bits = _charset_bits(pw)
    leet = low.translate(str.maketrans("@4310$5!7", "aaeiossit"))
    for word in COMMON:
        if word in low or word in leet:
            bits -= len(word) * 3.5
    for seq in SEQS:
        for n in range(len(seq), 3, -1):
            hit = False
            for i in range(len(seq) - n + 1):
                chunk = seq[i:i + n]
                if chunk in low or chunk[::-1] in low:
                    bits -= n * 3
                    hit = True
                    break
            if hit:
                break
    for m in re.finditer(r"(.)\1{2,}", pw):
        bits -= (len(m.group(0)) - 1) * 3.5
    if re.search(r"(19|20)\d\d", pw):
        bits -= 6
    return bits


def _passphrase_bits(pw: str) -> float:
    parts = [p for p in re.split(r"[\s\-_.,;:+/|]+", pw.lower()) if p]
    parts = [re.sub(r"\d+$", "", p) for p in parts]
    if len(parts) < 3:
        return 0
    known = sum(1 for p in parts if p in _WORDSET)
    unknown = len(parts) - known
    return known * WORD_BITS + unknown * 14


def strength(pw: str) -> dict:
    if not pw:
        return {"score": 0, "label": LABELS[0], "bits": 0}
    parts = [re.sub(r"\d+$", "", x) for x in re.split(r"[\s\-_.,;:+/|]+", pw.lower()) if x]
    if len(parts) >= 2 and all(x in _WORDSET for x in parts):
        bits = len(parts) * WORD_BITS + (3 if re.search(r"\d", pw) else 0) + (1 if pw != pw.lower() else 0)
    else:
        bits = max(_penalised(pw), _passphrase_bits(pw))
    bits = max(bits, 0)
    if pw.lower() in COMMON:
        bits = 0
    score = 0 if bits < 28 else 1 if bits < 40 else 2 if bits < 55 else 3 if bits < 75 else 4
    return {"score": score, "label": LABELS[score], "bits": round(bits)}


def check_master(pw: str, min_length: int) -> str | None:
    """None when fine, else a message."""
    if len(pw) < min_length:
        return f"Use at least {min_length} characters — a passphrase of 4–5 words is easiest."
    if strength(pw)["score"] < 3:
        return "That password is too easy to guess. Add words or make it longer (a 4–5 word passphrase works well)."
    return None
