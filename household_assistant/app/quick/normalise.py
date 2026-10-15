"""Normalising a question before matching (HOUSEHOLD_ASSISTANT_SPEC.md §17.3): plain Python, no dependencies.

    "Whats on my lists 2day??"  ->  ["what", "is", "on", "my", "list", "today"]

In order: Unicode NFKC, lower case, accents off, quotes and dashes straightened; amounts, times and dates kept as one
token each; contractions and text-speak spelled out; number words as digits; common Hindi/Hinglish words in English
("aaj kya kaam hai" -> "today what task"); word forms from a small table (not a stemmer, which breaks names); filler
words off the start and the end. Typos are put right later, against the
lexicon and the household's own names (`correct`), so a word that is a name or an item is never "corrected".
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# ----------------------------------------------------------------------------------------- tokens

_TOKEN_RE = re.compile(
    r"(?P<money>(?:₹|rs\.?|inr|\$|£|€)\s?\d+(?:[.,]\d{1,2})?)"
    r"|(?P<time>\d{1,2}:\d{2}\s?(?:am|pm|a\.m\.|p\.m\.)?|\d{1,2}\s?(?:am|pm|a\.m\.|p\.m\.))(?![a-z])"
    r"|(?P<iso>\d{4}-\d{2}(?:-\d{2})?)"
    r"|(?P<date>\d{1,2}/\d{4}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
    r"|(?P<alnum>\d+[a-z]+)"
    r"|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<word>[a-z]+(?:'[a-z]+)*)"
)

# ----------------------------------------------------------------------------------------- tables

CONTRACTIONS = {
    "what's": "what is", "whats": "what is", "wats": "what is", "it's": "it is", "that's": "that is",
    "thats": "that is", "there's": "there is", "theres": "there is", "who's": "who is", "whos": "who is",
    "how's": "how is", "where's": "where is", "wheres": "where is", "when's": "when is", "whens": "when is",
    "here's": "here is", "i'm": "i am", "im": "i am", "i've": "i have", "ive": "i have", "i'd": "i would",
    "i'll": "i will", "we're": "we are", "we've": "we have", "we'd": "we would", "we'll": "we will",
    "you're": "you are", "you've": "you have", "they're": "they are", "they've": "they have",
    "don't": "do not", "dont": "do not", "doesn't": "does not", "doesnt": "does not", "didn't": "did not",
    "didnt": "did not", "isn't": "is not", "isnt": "is not", "aren't": "are not", "arent": "are not",
    "wasn't": "was not", "weren't": "were not", "haven't": "have not", "havent": "have not",
    "hasn't": "has not", "can't": "can not", "cant": "can not", "cannot": "can not", "won't": "will not",
    "wont": "will not", "shouldn't": "should not", "wouldn't": "would not", "couldn't": "could not",
    "let's": "let us", "lets": "let us", "gonna": "going to", "wanna": "want to", "gotta": "got to",
    "gimme": "give me", "lemme": "let me", "y'all": "you", "ya": "you",
}

TEXT_SPEAK = {
    "u": "you", "ur": "your", "r": "are", "pls": "please", "plz": "please", "plez": "please", "pleaze": "please",
    "thx": "thanks", "thnx": "thanks", "ty": "thanks",
    "2day": "today", "tday": "today", "tdy": "today", "2dy": "today", "todays": "today", "tod": "today",
    "2moro": "tomorrow", "2morrow": "tomorrow", "2mrw": "tomorrow", "tmrw": "tomorrow", "tmr": "tomorrow",
    "tmrow": "tomorrow", "tomoz": "tomorrow", "tomorow": "tomorrow", "tommorow": "tomorrow",
    "tommorrow": "tomorrow", "tomorrows": "tomorrow", "2nite": "tonight", "tonite": "tonight",
    "bday": "birthday", "bdays": "birthday", "b'day": "birthday", "b'days": "birthday", "birthdays": "birthday",
    "wk": "week", "nxt": "next", "wat": "what", "wot": "what", "wht": "what", "hav": "have", "hv": "have",
    "msg": "message", "msgs": "message", "txt": "message", "txts": "message", "cals": "calorie",
    "kcals": "kcal", "groc": "groceries", "grocs": "groceries", "info": "information", "abt": "about",
    "b4": "before", "n": "and", "w": "with", "bc": "because", "cuz": "because", "pic": "picture",
    "todos": "todo", "to-dos": "todo", "to-do": "todo", "2do": "todo", "2dos": "todo",
    "mins": "minutes", "min": "minutes", "hrs": "hours", "hr": "hour", "xmas": "christmas",
    "anniv": "anniversary", "pmt": "payment", "acct": "account", "acc": "account",
}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "dozen": 12, "fortnight": None,
}
# phrases that are numbers: "a couple of" -> 2
NUMBER_PHRASES = (("a", "couple", "of"), ("a", "couple"), ("couple", "of"), ("a", "few"), ("a", "dozen"),
                  ("half", "a"))
NUMBER_PHRASE_VALUE = {("a", "couple", "of"): 2, ("a", "couple"): 2, ("couple", "of"): 2, ("a", "few"): 3,
                       ("a", "dozen"): 12, ("half", "a"): 0.5}

# word forms: a table, never a stemmer ("Bills" the person stays "bills" only where it means a bill)
FORMS = {
    "tasks": "task", "todos": "todo", "chores": "chore", "jobs": "job", "errands": "errand",
    "reminders": "reminder", "anniversaries": "anniversary", "bills": "bill", "payments": "payment",
    "expenses": "expense", "subscriptions": "subscription", "messages": "message", "notes": "note",
    "documents": "document", "docs": "document", "doc": "document", "files": "file", "manuals": "manual",
    "warranties": "warranty", "checklists": "checklist", "scores": "score", "deals": "deal", "offers": "offer",
    "budgets": "budget", "items": "item", "discounts": "discount", "bargains": "bargain", "things": "thing", "owes": "owe", "owed": "owe",
    "owing": "owe", "spent": "spend", "spending": "spend", "spends": "spend", "paid": "pay", "paying": "pay",
    "pays": "pay", "buying": "buy", "buys": "buy", "ate": "eat", "eaten": "eat", "eating": "eat",
    "eats": "eat", "cheaper": "cheap", "cheapest": "cheap", "reminded": "remind", "reminding": "remind",
    "reminds": "remind", "costs": "cost", "costing": "cost", "calories": "calorie", "macros": "macro",
    "carbs": "carb", "proteins": "protein", "days": "day", "weeks": "week", "months": "month",
    "accounts": "account", "cards": "card", "balances": "balance", "debts": "debt", "groups": "group",
    "earned": "earn", "earning": "earn", "earns": "earn", "finished": "finish", "completed": "complete",
    "ticked": "tick", "logged": "log", "logging": "log", "logs": "log", "added": "add", "adding": "add",
    "adds": "add", "needs": "need", "needed": "need", "schedules": "schedule", "collections": "collection",
    "leaderboards": "leaderboard", "relatives": "relative", "cousins": "cousin",
    "children": "child", "kids": "kid", "charges": "charge", "renewals": "renewal", "statements": "statement",
    "incomes": "income", "salaries": "salary", "settled": "settle", "splitting": "split", "splits": "split",
    "colour": "color", "favourite": "favorite", "organise": "organize", "summarise": "summarize",
    "groceries": "groceries", "grocery": "groceries", "chats": "chat", "games": "game", "stores": "store",
    "shops": "shop", "receipts": "receipt", "categories": "category", "merchants": "merchant",
    "bins": "bin", "dues": "due", "routines": "routine", "ious": "iou", "texts": "text", "dms": "dm", "earnings": "earnings", "emis": "emi", "meals": "meal", "snacks": "snack", "foods": "food",
    "drinks": "drink", "coming": "coming", "upcoming": "upcoming",
}

# Hindi and Hinglish (§17.3): phrases first, longest first; "" drops a word that carries no meaning here. "kal" is
# tomorrow, or yesterday when the question is in the past tense ("kal kitna kharcha hua").
HINGLISH_PHRASES = {
    "is hafte": "this week", "iss hafte": "this week", "is week": "this week", "agle hafte": "next week",
    "agla hafta": "next week", "pichle hafte": "last week", "pichhle hafte": "last week",
    "is mahine": "this month", "iss mahine": "this month", "pichle mahine": "last month",
    "pichhle mahine": "last month", "pichla mahina": "last month", "agle mahine": "next month",
    "mein kya hai": "what is on", "me kya hai": "what is on", "pe kya hai": "what is on", "par kya hai": "what is on",
    "mein kya kya hai": "what is on", "kya kya hai": "what is", "kya hai": "what is",
    "kisko kitna dena hai": "who do i owe", "kitna dena hai": "who do i owe", "kitne dene hain": "who do i owe",
    "kisko dena hai": "who do i owe", "kitna lena hai": "who owes me", "kisse lena hai": "who owes me",
    "yaad dila do": "remind me", "yaad dilana": "remind me", "yaad dilao": "remind me", "yaad dilaana": "remind me",
    "yaad karana": "remind me", "yaad kara do": "remind me",
    "kharidna hai": "to buy", "khareedna hai": "to buy", "lana hai": "to buy", "laana hai": "to buy",
    "lena hai": "to buy", "mangwana hai": "to buy",
    "daal do": "add", "dal do": "add", "add karo": "add", "add kar do": "add", "jod do": "add", "likh do": "add",
    "khatam ho gaya": "we need", "khatam ho gya": "we need", "khatam ho gayi": "we need", "khatam hai": "we need",
    "ho gaya": "done", "ho gya": "done", "ho gayi": "done", "kar diya": "done", "kar liya": "done",
    "ke liye": "for", "kitne paise": "how much money", "kitna paisa": "how much money",
    "bata do": "", "dikha do": "", "kya aap": "",
}
HINGLISH_WORDS = {
    "aaj": "today", "aj": "today", "abhi": "now", "hafta": "week", "hafte": "week", "mahina": "month",
    "mahine": "month", "kitna": "how much", "kitni": "how many", "kitne": "how many", "kharcha": "spend",
    "kharch": "spend", "kharche": "spend", "kharchaa": "spend", "paisa": "money", "paise": "money",
    "bijli": "electricity", "kiraya": "rent", "kiraaya": "rent", "kaam": "task", "mera": "my", "meri": "my",
    "mere": "my", "hamara": "our", "hamari": "our", "hamare": "our", "humara": "our", "humari": "our",
    "humare": "our", "apna": "my", "apni": "my", "apne": "my", "kya": "what", "kaun": "who", "kon": "who",
    "kab": "when", "kahan": "where", "kaha": "where", "kisko": "who", "kise": "who", "kisne": "who",
    "udhaar": "owe", "udhar": "owe", "udhari": "owe", "hisaab": "owe", "hisab": "owe", "janamdin": "birthday",
    "janmdin": "birthday", "janmadin": "birthday", "saalgirah": "anniversary", "salgirah": "anniversary",
    "sabzi": "vegetables", "sabji": "vegetables", "doodh": "milk", "dudh": "milk", "anda": "eggs", "ande": "eggs",
    "chawal": "rice", "cheeni": "sugar", "chini": "sugar", "namak": "salt", "pyaz": "onions", "pyaaz": "onions",
    "kanda": "onions", "aloo": "potatoes", "alu": "potatoes", "tamatar": "tomatoes", "dhaniya": "coriander",
    "adrak": "ginger", "lahsun": "garlic", "lehsun": "garlic", "khana": "food", "khaana": "food", "khaya": "ate",
    "khaaya": "ate", "naya": "new", "naye": "new", "nayi": "new", "sandesh": "message", "koi": "any", "kuch": "any",
    "sasta": "cheap", "sasti": "cheap", "saste": "cheap", "daam": "price", "bhav": "price", "bhaav": "price",
    "bachat": "savings", "kamai": "income", "tankhwah": "salary", "tankha": "salary", "tankhwa": "salary",
    "aur": "and", "khatam": "done", "maine": "i", "humne": "we", "hamne": "we",
    # no meaning of their own here
    "hai": "", "hain": "", "ho": "", "tha": "", "thi": "", "hua": "", "hui": "", "huwa": "", "gaya": "", "gayi": "",
    "ka": "", "ki": "", "ke": "", "ko": "", "se": "", "mein": "", "pe": "", "bhi": "", "wala": "", "wali": "",
    "wale": "", "ji": "", "yaar": "", "batao": "", "bataiye": "", "bata": "", "dikhao": "",
    "dikhaiye": "", "karo": "", "kijiye": "", "diya": "", "liya": "", "kiya": "", "kia": "", "kiye": "",
}
PAST_MARKERS = {"tha", "thi", "hua", "hui", "huwa", "kiya", "kia", "gaya", "gayi", "gya", "liya", "diya", "khaya",
                "khaaya", "kiye"}
_HINGLISH_BY_FIRST: dict[str, list] = {}
for _p, _r in sorted(HINGLISH_PHRASES.items(), key=lambda kv: -len(kv[0].split())):
    _HINGLISH_BY_FIRST.setdefault(_p.split()[0], []).append((tuple(_p.split()), _r))


def _hinglish(toks: list["Token"]) -> list["Token"]:
    """Hindi/Hinglish words as the English the lexicon knows (a question that has none comes back as it was)."""
    words = [t.text for t in toks]
    if not any(w in HINGLISH_WORDS or w in _HINGLISH_BY_FIRST or w == "kal" for w in words):
        return toks
    past = bool(PAST_MARKERS & set(words)) and "kal" in words
    out: list[Token] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind != WORD:
            out.append(t)
            i += 1
            continue
        hit = None
        for ph, rep in _HINGLISH_BY_FIRST.get(t.text, ()):
            if tuple(x.text for x in toks[i:i + len(ph)]) == ph:
                hit = (ph, rep)
                break
        if hit:
            out.extend(Token(FORMS.get(w, w), orig=w) for w in hit[1].split())
            i += len(hit[0])
            continue
        if t.text == "kal":
            out.append(Token("yesterday" if past else "tomorrow"))
        elif t.text in HINGLISH_WORDS:
            out.extend(Token(FORMS.get(w, w), orig=w) for w in HINGLISH_WORDS[t.text].split())
        else:
            out.append(t)
        i += 1
    return out


FILLER_START = (
    ("hey", "assistant"), ("ok", "assistant"), ("okay", "assistant"), ("i", "want", "to", "know"),
    ("i", "would", "like", "to", "know"), ("i", "need", "to", "know"), ("do", "you", "know"), ("can", "you"),
    ("could", "you"), ("would", "you"), ("will", "you"), ("tell", "me"), ("show", "me"), ("let", "me", "know"),
    ("give", "me"), ("hey",), ("hi",), ("hello",), ("ok",), ("okay",), ("so",), ("um",), ("umm",), ("uh",),
    ("er",), ("please",), ("quickly",), ("just",), ("assistant",), ("well",), ("yo",), ("and", "so"),
)
FILLER_END = (("please",), ("thanks",), ("thank", "you"), ("for", "me"), ("quickly",), ("again",), ("now", "please"),
              ("um",), ("uh",), ("then",), ("ok",), ("okay",), ("assistant",), ("mate",))

# what a token is, for the matcher
WORD, NUM, MONEY, TIME, DATE, ISO = "word", "num", "money", "time", "date", "iso"


class Token:
    """One word or value of a normalised question: `text` (normalised), `kind`, and `fixed` when a typo was put
    right (the matcher gives it a little less weight)."""
    __slots__ = ("text", "kind", "fixed", "value", "orig")

    def __init__(self, text: str, kind: str = WORD, value=None, fixed: bool = False, orig: str | None = None):
        self.text, self.kind, self.value, self.fixed = text, kind, value, fixed
        self.orig = orig or text                      # the word as typed (lower case), for a task's or item's text

    def __repr__(self):
        return f"{self.text}{'*' if self.fixed else ''}"


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'").replace("´", "'")
    text = text.replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = text.casefold()
    text = text.replace("&", " and ").replace("w/", " with ").replace("+", " plus ")
    text = re.sub(r"\bto-do\b|\bto - do\b", "todo", text)
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)               # "follow-up" -> "follow up"
    return text


def _money(raw: str) -> float:
    digits = re.sub(r"[^\d.,]", "", raw).replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return 0.0


def _time(raw: str) -> str | None:
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s?(am|pm|a\.m\.|p\.m\.)?$", raw.strip())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def tokens(text: str) -> list[Token]:
    """The question as tokens, before typo correction and filler removal."""
    out: list[Token] = []
    for m in _TOKEN_RE.finditer(_plain(text)):
        kind = m.lastgroup
        raw = m.group(0)
        if kind == "money":
            out.append(Token(raw.replace(" ", ""), MONEY, _money(raw)))
        elif kind == "time":
            t = _time(raw)
            out.append(Token(t, TIME, t) if t else Token(re.sub(r"\D", "", raw) or raw, NUM, None))
        elif kind == "iso":
            out.append(Token(raw, ISO, raw))
        elif kind == "date":
            out.append(Token(raw, DATE, raw))
        elif kind == "alnum":
            if raw in TEXT_SPEAK or raw in ("1st", "2nd", "3rd"):
                out.extend(_word(raw) if raw in TEXT_SPEAK else [Token(raw)])
            else:                                         # "10kg" -> 10 kg, "2x" -> 2 x
                n = re.match(r"\d+", raw).group(0)
                out.append(Token(n, NUM, int(n)))
                out.extend(_word(raw[len(n):]))
        elif kind == "num":
            out.append(Token(raw, NUM, float(raw) if "." in raw else int(raw)))
        else:
            out.extend(_word(raw))
    return _numbers(_hinglish(out))


def _word(raw: str) -> list[Token]:
    if raw in CONTRACTIONS:
        return [Token(w) for w in CONTRACTIONS[raw].split()]
    if raw in TEXT_SPEAK:
        return [Token(w) for w in TEXT_SPEAK[raw].split()]
    if raw.endswith("'s") and len(raw) > 2:
        raw = raw[:-2]                                # "meera's" -> "meera"; "what's" was a contraction above
    elif raw.endswith("s'") and len(raw) > 2:
        raw = raw[:-1]
    raw = raw.replace("'", "")
    if raw in TEXT_SPEAK:
        return [Token(w) for w in TEXT_SPEAK[raw].split()]
    if raw in CONTRACTIONS:
        return [Token(w) for w in CONTRACTIONS[raw].split()]
    if raw in NUMBER_WORDS and NUMBER_WORDS[raw] is not None:
        return [Token(raw, NUM, NUMBER_WORDS[raw])]
    return [Token(FORMS.get(raw, raw), orig=raw)]


def _numbers(toks: list[Token]) -> list[Token]:
    """"a couple of" -> 2, "twenty five" -> 25, "half a" -> 0.5 (number words are already NUM tokens)."""
    out: list[Token] = []
    i = 0
    while i < len(toks):
        hit = None
        for ph in NUMBER_PHRASES:
            n = len(ph)
            if tuple(t.text for t in toks[i:i + n]) == ph and all(t.kind == WORD or t.kind == NUM for t in toks[i:i + n]):
                hit = ph
                break
        if hit:
            v = NUMBER_PHRASE_VALUE[hit]
            out.append(Token(str(v), NUM, v))
            i += len(hit)
            continue
        t = toks[i]
        if (t.kind == NUM and isinstance(t.value, int) and t.value in (20, 30, 40, 50, 60, 70, 80, 90)
                and i + 1 < len(toks) and toks[i + 1].kind == NUM and isinstance(toks[i + 1].value, int)
                and 1 <= toks[i + 1].value <= 9 and toks[i + 1].text.isalpha()):
            v = t.value + toks[i + 1].value
            out.append(Token(str(v), NUM, v))
            i += 2
            continue
        if t.kind == NUM and t.text.isalpha():          # "five" -> 5
            out.append(Token(str(t.value), NUM, t.value))
        else:
            out.append(t)
        i += 1
    return out


def strip_filler(toks: list[Token]) -> list[Token]:
    """Filler off the start and the end ("hey, can you tell me …, please"), as often as it's there."""
    changed = True
    while changed and toks:
        changed = False
        for ph in FILLER_START:
            n = len(ph)
            if len(toks) > n and tuple(t.text for t in toks[:n]) == ph:
                toks = toks[n:]
                changed = True
                break
        for ph in FILLER_END:
            n = len(ph)
            if len(toks) > n and tuple(t.text for t in toks[-n:]) == ph:
                toks = toks[:-n]
                changed = True
                break
    return toks


# ----------------------------------------------------------------------------------------- typos

def distance(a: str, b: str, limit: int) -> int:
    """Damerau–Levenshtein (optimal string alignment) distance, or limit + 1 once it is past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _transposed(a: str, b: str) -> bool:
    if len(a) != len(b) or a == b:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


@lru_cache(maxsize=20000)
def _mask(word: str) -> int:
    """Which letters a word has, as bits."""
    m = 0
    for c in word:
        m |= 1 << (ord(c) & 63)
    return m


def buckets(words) -> dict[int, list[str]]:
    """Words by their length (the typo correction only looks at those within reach of a word's length)."""
    out: dict[int, list[str]] = {}
    for w in words:
        out.setdefault(len(w), []).append(w)
    return out


def correct(toks: list[Token], targets: set[str], known: set[str], by_length: dict | None = None) -> list[Token]:
    """Put typos right: a word that isn't `known` (the lexicon, everyday words, the household's names and items) is
    replaced by the one `targets` word within reach — 4 letters: two neighbours swapped only; 5–7 letters: one
    edit; 8 or more: two. Never a number, never a word of 3 letters or fewer, never when two targets are as close."""
    out = []
    by_length = by_length if by_length is not None else buckets(targets)
    for t in toks:
        w = t.text
        if t.kind != WORD or len(w) <= 3 or w in known or w in targets:
            out.append(t)
            continue
        if len(w) == 4:
            cands = [x for x in by_length.get(4, ()) if _transposed(w, x)]
            best = 1 if cands else None
        else:
            limit = 1 if len(w) <= 7 else 2
            scored = {}
            near = [x for n in range(len(w) - limit, len(w) + limit + 1) for x in by_length.get(n, ())]
            mw = _mask(w)
            for x in near:
                # each edit changes at most two letters' presence: a cheap test before the real distance
                if len(x) <= 3 or (mw ^ _mask(x)).bit_count() > 2 * limit:
                    continue
                d = distance(w, x, limit)
                if d <= limit:
                    scored[x] = d
            best = min(scored.values()) if scored else None
            cands = [x for x, d in scored.items() if d == best] if scored else []
        if best is not None and len(cands) == 1:
            word = FORMS.get(cands[0], cands[0])            # "calries" -> "calories" -> "calorie"
            out.append(Token(word, WORD, None, fixed=True, orig=cands[0]))
        else:
            out.append(t)
    return out


def text_of(toks: list[Token]) -> str:
    return " ".join(t.text for t in toks)
