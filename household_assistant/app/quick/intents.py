"""The quick intents (HOUSEHOLD_ASSISTANT_SPEC.md §17.6): one small rule per kind of question — the tool it calls,
the concepts that must be there, those that help, those that rule it out, and how the slots become arguments.

Requirements are concept names (upper case) or these (lower case):

    time        any when (today, this week, a month, a date)       only_time  nothing else but when-words
    text        words left over (a search, a task)                  name_text  a left-over word that looks like a name
    item        a shopping item (the list's, or an everyday one)    item_only  a shopping item and nothing else
    list_name   one of the person's Todo lists                      store      a store (Receipts, or a well-known one)
    category    a spending category                                  group      a Splitpot group
    person      a person of the household                            game       an Arcade game
    amount      a sum of money or a number                           clock      a time of day (17:00, 5pm, noon)
    app_word    a word the tool's own app taught (§17.4 "quick")     at_name    "at <a shop>" that isn't a store
    kin         "my mum", "Ravi's wife" (u.kin)                     item_left  an item and nothing else but the
                                                                               concept words around it

Weights (§17.6): a concept 3 (0.7 of it when a typo was put right), a household name 3, an item 3, a time 1, left-over
text 1, an amount 2, a time of day 2; each "helps" that is there adds 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import lexicon
from .match import Utterance

def _norm(phrase: str) -> str:
    from .normalise import FORMS
    return " ".join(FORMS.get(w, w) for w in phrase.split())


# the categories as the questions' own words come out of normalising ("eating out" -> "eat out")
CATEGORIES = {_norm(k): v for k, v in lexicon.CATEGORIES.items()}

WEIGHTS = {"time": 1.0, "only_time": 3.0, "text": 1.0, "name_text": 3.0, "item": 3.0, "item_only": 3.0,
           "list_name": 3.0, "store": 3.0, "category": 3.0, "group": 3.0, "person": 3.0, "game": 3.0, "amount": 2.0,
           "clock": 2.0, "app_word": 3.0, "at_name": 3.0, "rel_pattern": 3.0, "log_verb": 3.0, "kin": 3.0,
           "item_left": 3.0, "account": 3.0, "money": 3.0, "note_lead": 7.0}


@dataclass
class Call:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class Match:
    intent: str
    calls: list
    label: str
    score: float = 0.0
    source: str = "rules"           # rules | learned | admin | follow-up | choice
    acts: bool = False
    phrase_id: str | None = None
    slot_args: tuple = ()           # the arguments that came from this question's slots (a learned phrase keeps
                                    # the others as they were)
    note: str | None = None         # a line the answer adds (e.g. "milk is on the list")
    ask: str | None = None          # "yesno" ("any bills due?") or "count" ("how many tasks today?"): said first

    def key(self) -> str:
        return self.intent + repr(sorted((c.tool, sorted(c.args.items(), key=str)) for c in self.calls))


@dataclass
class Ctx:
    """What the rules may know about the asker: the tools they may use, their names, today."""
    tools: dict                     # tool name -> catalogue entry (catalogue.for_user)
    today: object
    vocab: object = None
    me: str = ""                    # the asker's name


class Intent:
    def __init__(self, key, tool, label, question, must, *, helps=(), out=(), build=None, acts=False,
                 tools=None, group=None):
        self.key, self.tool, self.label, self.question = key, tool, label, question
        self.must, self.helps, self.out = must, helps, out
        self.build = build or (lambda u, ctx: [Call(tool)])
        self.acts = acts
        self.tools = tools or (tool,)
        self.group = group or tool

    def available(self, ctx: Ctx) -> bool:
        return all(t in ctx.tools for t in self.tools)


def weight(u: Utterance, req: str, intent: Intent | None = None) -> float:
    if req.isupper():
        return u.has(req)
    s = u.slots
    if req == "time":
        return WEIGHTS[req] if s.any_time else 0.0
    if req == "only_time":
        return WEIGHTS[req] if s.any_time and u.only() else 0.0
    if req == "text":
        return WEIGHTS[req] if u.leftover() else 0.0
    if req == "name_text":
        return WEIGHTS[req] if u.name_words() else 0.0
    if req == "done_text":
        return 1.0 if u.own_text("DONE", drop=_DONE_DROP) else 0.0
    if req == "item":
        return WEIGHTS[req] if u.names.get("item") else 0.0
    if req == "item_only":
        return WEIGHTS[req] if u.names.get("item") and u.only("item", "store") else 0.0
    if req in ("list_name", "store", "category", "group", "person", "game", "account"):
        return WEIGHTS[req] if u.names.get(req) else 0.0
    if req == "kin":
        return WEIGHTS[req] if u.kin else 0.0
    if req == "money":                             # an amount with its currency ("₹450", "60 rupees")
        return WEIGHTS[req] if s.amount is not None else 0.0
    if req == "note_lead":                         # "make a note …" at the start: a note, whatever it says
        return WEIGHTS[req] if any(a == 0 for a, _b, _f in u.hits.get("NOTE_ADD", [])) else 0.0
    if req == "item_left":
        return WEIGHTS[req] if u.names.get("item") and not item_rest(u) else 0.0
    if req == "amount":
        return WEIGHTS[req] if s.amount is not None or s.numbers else 0.0
    if req == "back":
        return 1.0 if s.back else 0.0
    if req == "clock":
        return WEIGHTS[req] if s.time else 0.0
    if req == "app_word":
        return WEIGHTS[req] if intent and any(v == intent.tool for _a, _b, v in u.names.get("app_word", [])) else 0.0
    if req == "other_app_word":                    # an app's own word for another tool than this intent's
        return 1.0 if intent and any(v != intent.tool for _a, _b, v in u.names.get("app_word", [])) and not any(
            v == intent.tool for _a, _b, v in u.names.get("app_word", [])) else 0.0
    if req == "at_name":
        return WEIGHTS[req] if merchant_of(u) else 0.0
    if req == "rel_pattern":
        return WEIGHTS[req] if relation_names(u) else 0.0
    if req == "log_verb":
        return WEIGHTS[req] if any(w in u.words for w in ("log", "track", "record")) else 0.0
    if req.startswith("word:"):
        return 2.0 if req[5:] in u.words else 0.0
    if req.startswith("no:"):                      # "no:X" is there when X is NOT
        return 0.0 if weight(u, req[3:], intent) else 0.001
    raise ValueError(req)


def score(intent: Intent, u: Utterance) -> float:
    best = 0.0
    for alt in intent.must:
        ws = [weight(u, r, intent) for r in alt]
        if all(w > 0 for w in ws):
            best = max(best, sum(ws))
    if not best:
        return 0.0
    for r in intent.out + (() if intent.acts else ("other_app_word",)):
        if all(weight(u, x, intent) > 0 for x in r.split("+")):
            return 0.0
    return best + sum(1.0 for h in intent.helps if weight(u, h, intent) > 0)


# ----------------------------------------------------------------------------------------- slot helpers

WHEN_WORDS = {"today": "today", "tomorrow": "tomorrow", "week": "this week", "next_week": "the next 2 weeks",
              "overdue": "overdue", "all": "all"}


def when_arg(u: Utterance, default: str = "today") -> str:
    w = u.slots.when
    if w == "next_week":
        return "week"
    if w in ("today", "tomorrow", "week", "overdue", "all"):
        return w
    if u.slots.date:
        d = u.slots.date
        today = None
        try:
            from datetime import date as _d
            today = _d.fromisoformat(d)
        except ValueError:
            pass
        return "week" if today else default
    return default


def days_arg(u: Utterance, default: int, lo: int, hi: int, ctx: Ctx) -> int:
    s = u.slots
    if s.days:
        n = s.days
    elif s.when == "today":
        n = 1
    elif s.when == "tomorrow":
        n = 2
    elif s.month:
        from . import slots as _sl
        n = _sl.days_left_in_month(ctx.today) if s.month == f"{ctx.today:%Y-%m}" else default
    else:
        n = default
    return max(lo, min(hi, int(n)))


def when_words(u: Utterance, default: str) -> str:
    s = u.slots
    if s.days and s.when not in ("today", "tomorrow"):
        return f"the next {s.days} days" if s.days not in (7, 14) else ("this week" if s.days == 7 else
                                                                        "the next 2 weeks")
    return WHEN_WORDS.get(s.when or "", default)


def month_words(m: str | None, default: str = "this month") -> str:
    if not m:
        return default
    import calendar
    y, mo = m.split("-")
    return f"{calendar.month_name[int(mo)]} {y}"


def person_arg(u: Utterance, ctx: Ctx) -> str | None:
    p = u.name("person")
    if not p:
        return None
    if ctx.me and p.casefold() == ctx.me.casefold():
        return None
    return p


def merchant_of(u: Utterance, leads=("at", "on", "with", "from", "to")) -> str | None:
    """"at Amazon", "on Netflix", "with Uber": a name after at/on/with that isn't a category, store or month."""
    w = u.words
    for i, t in enumerate(w):
        if t in leads and i + 1 < len(w):
            j = i + 1
            alone = j + 1 >= len(w) or w[j + 1] in lexicon.STOPWORDS or (j + 1) in u.slots.used
            if any(w[j:j + len(c.split())] == c.split() for c in CATEGORIES if " " in c):
                continue                                 # "eating out": a category, not a shop
            if (j in u.slots.used or w[j] in lexicon.STOPWORDS or (w[j] in CATEGORIES and alone)
                    or w[j] in lexicon.STORES):
                continue
            words = []
            while j < len(w) and j not in u.slots.used and w[j] not in lexicon.STOPWORDS:
                words.append(w[j])
                j += 1
            if words and " ".join(words) not in CATEGORIES:
                return " ".join(words).title()
    return None


def relation_names(u: Utterance) -> list[str]:
    """"who is Meera to me" / "how is Meera related to Ravi" / "what is Ravi to Meera" / "is Nisha my cousin": the
    names, asker last. "Who is Meera married to" is not one (the word before "to" isn't a name)."""
    names = u.name_words()
    w = u.words
    if not names:
        return []
    if u.hits.get("RELATION") or (u.hits.get("KIN") and ("my" in w or "our" in w)):
        return names[:2]
    if "to" in w:
        i = w.index("to")
        before = w[i - 1] if i > 0 else ""
        after = w[i + 1] if i + 1 < len(w) else ""
        if before in names and (after in ("me", "us") or after in names):
            return names[:2]
    return []


def category_of(u: Utterance, ctx: Ctx) -> str | None:
    named = u.name("category")
    if named:
        return named
    text = u.words
    for phrase, label in sorted(CATEGORIES.items(), key=lambda kv: -len(kv[0].split())):
        p = phrase.split()
        for i in range(len(text)):
            if text[i:i + len(p)] == p:
                for c in (ctx.vocab.categories if ctx.vocab else []):
                    if phrase in c.casefold() or label.casefold() in c.casefold():
                        return c
                return label
    return None


def items_of(u: Utterance) -> list[tuple[str, float | None]]:
    """"2 litres of milk and a loaf of bread" -> [("milk", 2), ("bread", 1)] (units dropped; quantities kept)."""
    from .slots import UNITS
    out, cur, qty = [], [], None
    skip = {"to", "the", "a", "an", "some", "more", "of", "on", "my", "our", "list", "please", "for", "too", "also"}

    def flush():
        words = list(cur)
        while words and (words[0] in skip or words[0] in lexicon.STOPWORDS or words[0] in UNITS):
            words.pop(0)
        while words and (words[-1] in skip or words[-1] in lexicon.STOPWORDS or words[-1] in UNITS):
            words.pop()
        if words:
            out.append((" ".join(words), qty))

    for i, t in enumerate(u.toks):
        txt = t.text
        if txt in ("and", ",", "plus"):
            flush()
            cur, qty = [], None
            continue
        if i in u.used and not any(s <= i < e for s, e, _v in u.names.get("item", [])):
            if cur:                                      # "milk to the shopping list": the item ends here
                flush()
                cur = []
            continue
        if t.kind == "num":
            qty = float(t.value)
            continue
        if txt in UNITS and not cur:
            continue
        cur.append(t.orig)
    flush()
    return out[:3]


_TICK_WORDS = {"done", "finish", "complete", "tick", "cross", "check", "mark", "as", "off", "bought", "buy", "got",
               "picked", "up", "purchased", "grabbed", "have", "just", "list", "shopping", "from", "the", "on", "of",
               "my", "our", "we", "i", "already", "item"}


def item_rest(u: Utterance) -> list[str]:
    """The words of a "tick milk off the list" question that are neither its item nor its tick words."""
    inside = {i for a, b, _v in u.names.get("item", []) for i in range(a, b)}
    return [t.text for i, t in enumerate(u.toks) if i not in inside and i not in u.slots.used
            and t.text not in _TICK_WORDS and t.text not in lexicon.STOPWORDS]


def due_of(u: Utterance, ctx: Ctx) -> str | None:
    """The day a task moves to: a date, tomorrow, a weekday, "next week" (a week from today), "in 3 days"."""
    from datetime import timedelta
    s = u.slots
    if s.date:
        return s.date
    if s.when == "today":
        return ctx.today.isoformat()
    if s.when == "tomorrow":
        return (ctx.today + timedelta(days=1)).isoformat()
    if s.when in ("week", "next_week") or s.days:
        return (ctx.today + timedelta(days=7 if not s.days or s.when == "next_week" else s.days)).isoformat()
    return None


def kin_person(u: Utterance) -> str | None:
    """"my mum" -> "my mother", "Ravi's wife": the person as Family Tree reads them."""
    return u.kin[0][2] if u.kin else None


def text_after(u: Utterance, drop=()) -> str:
    """The words left for a task's or a reminder's text, keeping its small words ("call the plumber")."""
    words = []
    for i, t in enumerate(u.toks):
        if i in u.used or t.text in drop:
            continue
        words.append(t.text)
    while words and words[0] in ("to", "that", "me", "about", "i", "need", "please", "a", "the", "for", "on", "of",
                                 "is", "it", "as"):
        words.pop(0)
    while words and words[-1] in ("to", "on", "in", "my", "list", "the", "please", "a", "for", "as", "is", "it",
                                  "of", "off"):
        words.pop()
    return " ".join(words)


# ----------------------------------------------------------------------------------------- builders

def b_tasks(u, ctx):
    args = {"when": when_arg(u, "all" if u.name("list_name") else "today")}
    if u.name("list_name"):
        args["list"] = u.name("list_name")
    p = person_arg(u, ctx)
    if p:
        args["person"] = p
    return [Call("todo.tasks", args)]


def l_tasks(u, calls):
    a = calls[0].args
    who = f"{a['person']}'s tasks" if a.get("person") else "Your tasks"
    where = f" on {a['list']}" if a.get("list") else ""
    return f"{who}{where} · {WHEN_WORDS.get(a.get('when'), a.get('when'))}"


def b_schedule(u, ctx):
    return [Call("todo.schedule", {"days": days_arg(u, 7, 1, 14, ctx)})]


_DONE_DROP = ("done", "finish", "complete", "tick", "cross", "check", "mark", "as", "todo", "task", "list")


def b_reminder(u, ctx):
    text = u.own_text("REMIND", drop=("remind", "reminder", "alert", "ping", "notify", "nudge"))
    if not text or not u.slots.time:
        return None
    args = {"text": text[:200], "time": u.slots.time}
    if u.slots.date:
        args["date"] = u.slots.date
    elif u.slots.when == "tomorrow":
        args["date"] = (ctx.today.fromordinal(ctx.today.toordinal() + 1)).isoformat()
    return [Call("todo.reminder.add", args)]


def _default_list(ctx) -> str | None:
    v = ctx.vocab
    return v.lists[0] if v and v.lists else None


def b_task_add(u, ctx):
    lst = u.name("list_name") or _default_list(ctx)
    text = u.own_text("ADD", "TASK", "LIST", names=("list_name",),
                      drop=("todo", "task", "list", "lists", "add", "put", "stick", "include"))
    if not lst or not text:
        return None
    args = {"list": lst, "text": text[:200]}
    if u.slots.date:
        args["due"] = u.slots.date
    elif u.slots.when == "today":
        args["due"] = ctx.today.isoformat()
    elif u.slots.when == "tomorrow":
        args["due"] = ctx.today.fromordinal(ctx.today.toordinal() + 1).isoformat()
    return [Call("todo.items.add", args)]


def b_task_done(u, ctx):
    text = u.own_text("DONE", drop=_DONE_DROP)
    if not text:
        return None
    args = {"task": text[:200]}
    if u.name("list_name"):
        args["list"] = u.name("list_name")
    return [Call("todo.items.done", args)]


def b_shopping(u, ctx):
    return [Call("receipt.shopping_list")]


def b_shopping_add(u, ctx):
    items = items_of(u)
    if not items:
        return None
    out = []
    for name, qty in items:
        args = {"item": name[:200]}
        if qty and qty != 1:
            args["qty"] = qty
        out.append(Call("receipt.shopping_list.add", args))
    return out


def b_price(u, ctx):
    item = u.name("item") or u.free(drop=("price", "cost", "rate"))
    if not item:
        return None
    return [Call("receipt.price", {"item": item[:100]})]


def b_item_alone(u, ctx):
    item = u.name("item")
    return [Call("receipt.price", {"item": item}), Call("receipt.shopping_list")]


def b_store_spend(u, ctx):
    args = {"store": u.name("store")}
    if u.slots.month:
        args["month"] = u.slots.month
    return [Call("receipt.spending", args)]


def b_spend(u, ctx):
    args = {}
    if u.slots.month:
        args["month"] = u.slots.month
    c = category_of(u, ctx)
    if c:
        args["category"] = c
    return [Call("finance.spending", args)]


def b_merchant(u, ctx):
    m = merchant_of(u)
    return [Call("finance.merchant", {"merchant": m})] if m else None


def b_month(tool):
    def b(u, ctx):
        return [Call(tool, {"month": u.slots.month} if u.slots.month else {})]
    return b


def b_bills(u, ctx):
    return [Call("finance.bills", {"days": days_arg(u, 7, 1, 60, ctx)})]


def b_group(tool):
    def b(u, ctx):
        args = {}
        if u.name("group"):
            args["group"] = u.name("group")
        return [Call(tool, args)]
    return b


def b_recent(u, ctx):
    args = {}
    if u.name("group"):
        args["group"] = u.name("group")
    if u.slots.days:
        args["days"] = max(1, min(90, u.slots.days))
    return [Call("splitpot.recent", args)]


def b_expense(u, ctx):
    amount = u.slots.amount
    nums = [v for _i, v in u.slots.numbers]
    if amount is None and nums:
        amount = float(nums[0])
    if not amount or amount <= 0:
        return None
    people = [p for p in u.all_names("person") if not (ctx.me and p.casefold() == ctx.me.casefold())]
    drop = ("split", "pay", "paid", "for", "with", "and", "expense", "shared", "add", "an", "i", "we", "covered",
            "rupees", "rupee", "rs", "dollars", "pounds", "euros", "bucks", "of")
    desc = u.own_text("SPLIT_ADD", "OWE", names=("person", "group"), drop=drop)
    desc = " ".join(w for w in desc.split() if not w.replace(".", "").isdigit() and not w.startswith(("₹", "$", "£",
                                                                                                       "€")))
    if not desc:
        return None
    args = {"description": desc[:200], "amount": amount}
    if u.name("group"):
        args["group"] = u.name("group")
    if people:
        args["split_with"] = ", ".join(people)[:200]
    return [Call("splitpot.expense.add", args)]


def b_birthdays(u, ctx):
    return [Call("tree.birthdays", {"days": days_arg(u, 30, 1, 90, ctx)})]


def b_person(u, ctx):
    if u.kin:                                                  # "my mum", "Ravi's wife": Family Tree works it out
        return [Call("tree.person", {"person": kin_person(u)[:80]})]
    names = u.name_words() or [p.casefold() for p in u.all_names("person")]
    if not names:
        return None
    return [Call("tree.person", {"person": " ".join(names[:2]).title()[:80]})]


def b_relation(u, ctx):
    names = relation_names(u)
    if u.kin and u.hits.get("RELATION"):                       # "how is my wife related to Ravi"
        names = [kin_person(u)] + [n for n in u.name_words() if n not in kin_person(u).casefold()]
    if not names:
        return None
    title = lambda n: n if n == kin_person(u) else n.title()       # "my wife" goes as it is
    args = {"person": title(names[0])}
    if len(names) > 1:
        args["to"] = title(names[1])
    return [Call("tree.relation", args)]


def b_calories(u, ctx):
    if u.slots.when in ("week", "next_week") or (u.slots.days and u.slots.days > 1):
        return [Call("calorie.week", {})]
    if u.slots.date:
        return [Call("calorie.today", {"date": u.slots.date})]
    return [Call("calorie.today", {})]


def b_food(u, ctx):
    meal = next((lexicon.MEALS[w] for w in u.words if lexicon.MEALS.get(w)), None)
    drop = {"log", "track", "record", "had", "eat", "ate", "for", "i", "at", "my", "as", "a", "an", "some"} | set(
        lexicon.MEALS)
    food = [t.orig for i, t in enumerate(u.toks) if t.kind == "word" and t.text not in drop
            and t.text not in lexicon.STOPWORDS and (i not in u.used or any(
                s <= i < e for s, e, _v in u.names.get("item", [])))]
    if not food:
        return None
    args = {"food": " ".join(food)[:200]}
    if meal:
        args["meal"] = meal
    nums = [v for _i, v in u.slots.numbers]
    if nums and 0.1 <= float(nums[0]) <= 20:
        args["servings"] = float(nums[0])
    if u.slots.date:
        args["date"] = u.slots.date
    return [Call("calorie.food.log", args)]


def b_docs(u, ctx):
    kinds = {"note": "note", "checklist": "checklist", "pdf": "pdf", "sheet": "sheet", "spreadsheet": "spreadsheet"}
    kind = next((kinds[w] for w in u.words if w in kinds), None)
    q = u.free(drop=("note", "document", "file", "find", "search", "look", "where", "put", "pdf", "paperwork",
                     "open", "show", "called", "named"))
    if not q:
        return None
    args = {"query": q[:200]}
    if kind:
        args["kind"] = kind
    return [Call("docs.search", args)]


def b_checklist(u, ctx):
    q = " ".join(w for w in u.own_text("CHECKLIST").split() if w not in lexicon.STOPWORDS and w not in (
        "checklist", "list", "open", "show", "see", "read", "left", "what", "whats"))
    if not q:
        return None
    return [Call("docs.search", {"query": q[:200], "kind": "checklist"})]


def b_scores(u, ctx):
    if "my" in u.words or "mine" in u.words:
        if "arcade.mine" in ctx.tools:
            return [Call("arcade.mine", {})]
    args = {}
    if u.name("game"):
        args["game"] = u.name("game")
    if u.slots.month:
        args["period"] = "month"
    return [Call("arcade.scores", args)]


def b_shop_tick(u, ctx):
    items = list(dict.fromkeys(v for _a, _b, v in sorted(u.names.get("item", []))))[:3]     # in the order said
    return [Call("receipt.shopping_list.tick", {"item": i[:200]}) for i in items] or None


def b_task_move(u, ctx):
    due = due_of(u, ctx)
    text = u.own_text("MOVE", names=("list_name",), drop=("move", "push", "postpone", "snooze", "reschedule", "shift",
                                                          "delay", "put", "bump", "defer", "back", "till", "until",
                                                          "task", "todo", "it"))
    if not due or not text:
        return None
    args = {"task": text[:200], "due": due}
    if u.name("list_name"):
        args["list"] = u.name("list_name")
    return [Call("todo.items.due", args)]


def b_expense_log(u, ctx):
    amount = u.slots.amount
    nums = [v for _i, v in u.slots.numbers]
    if amount is None and nums:
        amount = float(nums[0])
    if not amount or amount <= 0:
        return None
    merchant = merchant_of(u, ("at", "from")) or u.name("store") or merchant_of(u, ("on", "with"))
    category = category_of(u, ctx)
    if merchant and category and merchant.casefold() == category.casefold():
        merchant = None
    desc = merchant or (u.free(drop=("spend", "i", "we", "on", "at", "for", "rupees", "rupee", "rs", "dollars", "pounds",
                                     "euros", "bucks", "log", "track", "record", "today", "yesterday", "cash", "card"))
                        if not category else category)
    desc = " ".join(w for w in desc.split() if not w.replace(".", "").isdigit()).title() if not category and not \
        merchant else desc
    if not desc:
        return None
    args = {"amount": round(float(amount), 2), "description": desc[:200]}
    if category:
        args["category"] = category[:60]
    if u.slots.date:
        args["date"] = u.slots.date
    if u.name("account"):
        args["account"] = u.name("account")[:100]
    return [Call("finance.expense.add", args)]


def b_note_add(u, ctx):
    a, b, _f = u.hits["NOTE_ADD"][0]
    words = [t.orig for t in u.toks[b:]] or [t.orig for t in u.toks[:a]]      # all of it, its dates and numbers too
    while words and words[0] in ("that", "to", "about", "of", "on", "saying", "for"):
        words.pop(0)
    text = " ".join(words)
    if not text:
        return None
    words = text.split()
    name = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    return [Call("docs.note.create", {"name": name[:1].upper() + name[1:120], "text": text[:4000]})]


_CHECK_DROP = {"the", "a", "an", "my", "our", "mark", "tick", "cross", "check", "as", "off", "please", "with"}


def _checklist_parts(u) -> tuple[str, str] | None:
    """"tick passport on the packing checklist" -> ("passport", "packing")."""
    toks = u.toks
    cl = [(a, b) for a, b, _f in u.hits.get("CHECKLIST", [])]
    if not cl:
        return None
    a, b = cl[0]
    skip = set(u.slots.used)
    for c in ("DONE", "ADD", "BOUGHT"):
        for x, y, _f in u.hits.get(c, []):
            skip.update(range(x, y))
    before = [i for i in range(a) if i not in skip]
    # the checklist's own name: the words just before "checklist" back to on/in/to/from ("the packing checklist"),
    # or "packing" of "packing list"
    name, item = [], []
    j = a - 1
    while j >= 0 and toks[j].text not in ("on", "in", "to", "from", "off", "onto") and j not in skip:
        name.insert(0, toks[j].orig)
        j -= 1
    item = [toks[i].orig for i in before if i < j and toks[i].text not in _CHECK_DROP]
    name = [w for w in name if w not in ("the", "my", "our", "a")]
    if "packing" in " ".join(t.text for t in toks[a:b]):
        name = name or ["packing"]
    if not item:                                           # "tick off the packing checklist: passport"
        item = [toks[i].orig for i in range(b, len(toks)) if i not in skip and toks[i].text not in _CHECK_DROP]
    while item and item[0] in lexicon.STOPWORDS:
        item.pop(0)
    while item and item[-1] in lexicon.STOPWORDS:
        item.pop()
    if not item or not name:
        return None
    return " ".join(item), " ".join(name)


def b_checklist_change(tool):
    def b(u, ctx):
        parts = _checklist_parts(u)
        if not parts:
            return None
        item, name = parts
        act = {"item": item[:200]}
        if tool == "docs.checklist.tick":
            act["done"] = True
        return [Call("docs.search", {"query": name[:200], "kind": "checklist"}), Call(tool, act)]
    return b


def b_compare(u, ctx):
    from . import slots as _sl
    months = list(dict.fromkeys(u.slots.months))
    this = f"{ctx.today:%Y-%m}"
    if len(months) >= 2:
        a, b = months[0], months[1]
    elif months and months[0] != this:
        a, b = this, months[0]
    else:
        a = months[0] if months else this
        y, m = map(int, a.split("-"))
        b = _sl.month_back(ctx.today.replace(year=y, month=m, day=1), -1)
    store = u.name("store")
    if store and "receipt.spending" in ctx.tools:
        return [Call("receipt.spending", {"store": store, "month": a}), Call("receipt.spending", {"store": store,
                                                                                                    "month": b})]
    cat = category_of(u, ctx)
    calls = [Call("finance.spending", {"month": a}), Call("finance.spending", {"month": b})]
    if cat:
        for c in calls:
            c.args["_category"] = cat            # read from each month's categories (not an argument: removed later)
    return calls


def l_compare(u, calls):
    a, b = calls[0].args.get("month"), calls[1].args.get("month")
    what = (f"Spending at {calls[0].args['store']}" if calls[0].args.get("store") else
            "Spending" + (f" on {calls[0].args['_category']}" if calls[0].args.get("_category") else ""))
    return f"{what} · {month_words(a)} vs {month_words(b)}"


def _first(calls, key, default=""):
    return calls[0].args.get(key, default) if calls else default


# ----------------------------------------------------------------------------------------- the intents

INTENTS: list[Intent] = [
    Intent("tasks", "todo.tasks", l_tasks, "What's on my list today?",
           must=(("TASK",), ("LIST", "time"), ("only_time",), ("list_name",), ("LISTS", "time"), ("person", "time"),
                 ("LIST", "word:my")),
           helps=("time", "list_name", "person"),
           out=("SHOPPING", "item+no:list_name", "SCHEDULE", "REMIND", "ADD", "NEED+item", "DONE", "LISTS+no:time",
                "MOVE", "BOUGHT", "NOTE_ADD", "kin",
                "DEAL", "BUDGET", "PRICE", "SPEND", "BILL", "BIRTHDAY", "OWE", "CALORIE", "UNREAD", "DOC", "SCORE",
                "BALANCE", "RECURRING", "INCOME", "CHECKLIST", "store+HOWMUCH", "PERSON_INFO", "RELATION",
                "rel_pattern"),
           build=b_tasks),
    Intent("schedule", "todo.schedule", lambda u, c: f"Coming up on the schedule · {when_words(u, 'next 7 days')}",
           "What's coming up on the schedule?", must=(("SCHEDULE",),), helps=("time", "TASK"),
           out=("REMIND", "DONE", "CHECKLIST", "MOVE"),
           build=b_schedule),
    Intent("lists", "todo.lists", lambda u, c: "Your lists", "Which lists do I have?", must=(("LISTS",),),
           out=("time", "SHOPPING"), build=lambda u, c: [Call("todo.lists")]),
    Intent("reminder", "todo.reminder.add", lambda u, c: f"Remind you: {_first(c, 'text')} at {_first(c, 'time')}",
           "Remind me to call the plumber at 5pm", must=(("REMIND", "clock"),), build=b_reminder, acts=True),
    Intent("task_add", "todo.items.add", lambda u, c: f"Add “{_first(c, 'text')}” to {_first(c, 'list')}",
           "Add call the plumber to my list", must=(("ADD", "TASK"), ("ADD", "list_name"), ("ADD", "LIST", "text")),
           out=("SHOPPING+no:list_name+no:LIST", "item+no:list_name", "REMIND", "CHECKLIST", "NOTE_ADD"),
           build=b_task_add, acts=True),
    Intent("task_done", "todo.items.done", lambda u, c: f"Tick off “{_first(c, 'task')}”", "Tick off the bins",
           must=(("DONE", "done_text"),), out=("SHOPPING", "CHECKLIST", "item_left", "BOUGHT"), build=b_task_done,
           acts=True),
    Intent("task_move", "todo.items.due", lambda u, c: f"Move “{_first(c, 'task')}” to {_first(c, 'due')}",
           "Move the dentist to Friday", must=(("MOVE", "time"),), out=("SHOPPING", "NOTE_ADD"),
           build=b_task_move, acts=True),
    Intent("shopping", "receipt.shopping_list", lambda u, c: "The shopping list", "What's on the shopping list?",
           must=(("SHOPPING",), ("LIST", "item")),
           out=("TASK+no:SHOPPING", "ADD", "NEED+item", "PRICE", "SPEND", "DEAL", "BUDGET", "REMIND",
                "SPLIT_ADD+amount", "CHECKLIST", "list_name+no:SHOPPING", "BOUGHT", "DONE+item"), build=b_shopping),
    Intent("shop_tick", "receipt.shopping_list.tick", lambda u, c: f"Tick {_first(c, 'item')} off the shopping list",
           "Bought the milk", must=(("BOUGHT", "item"), ("DONE", "item_left"), ("word:got", "item_left")),
           out=("ADD", "NEED", "PRICE", "SPEND", "CHECKLIST", "TASK", "list_name"), build=b_shop_tick, acts=True),
    Intent("shopping_add", "receipt.shopping_list.add",
           lambda u, c: "Add to the shopping list: " + ", ".join(x.args["item"] for x in c),
           "Add milk to the shopping list",
           must=(("ADD", "item"), ("NEED", "item"), ("ADD", "SHOPPING", "text"), ("NEED", "SHOPPING", "text")),
           out=("TASK", "list_name+no:word:shopping", "REMIND", "SPLIT_ADD+amount", "word:my+LIST+no:word:shopping",
                "CHECKLIST", "BOUGHT"),
           build=b_shopping_add,
           acts=True),
    Intent("price", "receipt.price", lambda u, c: f"The price of {_first(c, 'item')}", "How much is milk?",
           must=(("PRICE", "text"), ("PRICE", "item")), helps=("store",),
           out=("SPEND", "BUDGET", "DEAL", "RECURRING", "SPLIT_ADD+amount", "OWE"),
           build=b_price),
    Intent("item_alone", "receipt.price", lambda u, c: f"{_first(c, 'item').capitalize()}: its price and the list",
           "Milk", must=(("item_only",),), out=("ADD", "NEED", "BOUGHT"), build=b_item_alone,
           tools=("receipt.price", "receipt.shopping_list"), group="receipt.price+list"),
    Intent("deals", "receipt.deals", lambda u, c: "Best prices", "Any good deals?", must=(("DEAL",),),
           build=lambda u, c: [Call("receipt.deals")]),
    Intent("budgets", "receipt.budgets", lambda u, c: "This month's budgets", "Are we on track with the budget?",
           must=(("BUDGET",),), build=lambda u, c: [Call("receipt.budgets")]),
    Intent("store_spend", "receipt.spending",
           lambda u, c: f"Spending at {_first(c, 'store')} · {month_words(_first(c, 'month', None))}",
           "How much did we spend at Costco?", must=(("SPEND", "store"), ("HOWMUCH", "store")), helps=("time",),
           out=("PRICE", "COMPARE", "amount+word:i", "amount+word:we"), build=b_store_spend),
    Intent("spend", "finance.spending",
           lambda u, c: "Spending" + (f" on {_first(c, 'category')}" if _first(c, 'category') else "")
           + f" · {month_words(_first(c, 'month', None))}",
           "How much did we spend this month?", must=(("SPEND",),), helps=("category", "time"),
           out=("store", "at_name", "BUDGET", "DEAL", "RECENT+group", "RECENT+word:group", "COMPARE",
                "amount+word:i+no:HOWMUCH", "amount+word:we+no:HOWMUCH"), build=b_spend),
    Intent("merchant", "finance.merchant", lambda u, c: f"Spending at {_first(c, 'merchant')}",
           "How much have we spent at Amazon?", must=(("SPEND", "at_name"),),
           out=("store", "COMPARE", "amount+no:HOWMUCH"), build=b_merchant),
    Intent("compare", "finance.spending", l_compare, "Did we spend more than last month?",
           must=(("COMPARE", "SPEND"), ("COMPARE", "HOWMUCH", "time")), helps=("category", "store", "time"),
           build=b_compare, tools=("finance.spending",)),
    Intent("expense_log", "finance.expense.add", lambda u, c: f"Add {_first(c, 'amount')} at {_first(c, 'description')}"
           " to Finance", "I spent 200 on fuel at Shell",
           must=(("SPEND", "amount", "word:i"), ("SPEND", "amount", "word:we"), ("log_verb", "SPEND", "amount"),
                 ("SPEND", "money")),
           helps=("category", "account"),
           out=("HOWMUCH", "SPLIT_ADD", "OWE", "person", "group", "word:split", "COMPARE", "BUDGET", "QUESTION_WORDS"),
           build=b_expense_log, acts=True),
    Intent("summary", "finance.summary", lambda u, c: f"Month summary · {month_words(_first(c, 'month', None))}",
           "How did we do this month?", must=(("INCOME",),), helps=("time",), build=b_month("finance.summary")),
    Intent("bills", "finance.bills", lambda u, c: f"Bills due · {when_words(u, 'next 7 days')}", "Any bills due?",
           must=(("BILL",),), helps=("time",), out=("SPLIT_ADD", "REMIND", "RECURRING", "OWE", "MOVE", "NOTE_ADD"),
           build=b_bills),
    Intent("recurring", "finance.recurring", lambda u, c: "Recurring charges", "What subscriptions do we pay for?",
           must=(("RECURRING",),), build=lambda u, c: [Call("finance.recurring")]),
    Intent("balances", "finance.balances", lambda u, c: "Account balances", "What are our account balances?",
           must=(("BALANCE",),), out=("OWE+person", "OWE+group", "group"), build=lambda u, c: [Call("finance.balances")]),
    Intent("owe", "splitpot.balances",
           lambda u, c: "Who owes whom" + (f" · {_first(c, 'group')}" if _first(c, 'group') else ""),
           "Who owes who?", must=(("OWE",), ("BALANCE", "group")), helps=("group", "person"),
           out=("BALANCE+word:card", "BALANCE+word:account", "SPLIT_ADD+amount", "RECENT"), build=b_group(
               "splitpot.balances")),
    Intent("recent_split", "splitpot.recent", lambda u, c: "Recent shared expenses", "What did we split lately?",
           must=(("RECENT", "OWE"), ("RECENT", "SPLIT_ADD"), ("RECENT", "group"), ("RECENT", "word:group"),
                 ("OWE", "back"), ("SPLIT_ADD", "back")),
           helps=("group", "time"), build=b_recent),
    Intent("expense_add", "splitpot.expense.add",
           lambda u, c: f"Split {_first(c, 'description')}: {_first(c, 'amount')}", "I paid 40 for dinner",
           must=(("SPLIT_ADD", "amount"),), helps=("person", "group"), out=("RECENT", "SPEND+no:person+no:group"),
           build=b_expense,
           acts=True),
    Intent("birthdays", "tree.birthdays", lambda u, c: f"Coming birthdays · {when_words(u, 'next 30 days')}",
           "Any birthdays coming up?", must=(("BIRTHDAY",),), helps=("time",), out=("name_text", "kin"),
           build=b_birthdays),
    Intent("about_person", "tree.person", lambda u, c: f"About {_first(c, 'person')}", "When is Meera's birthday?",
           must=(("PERSON_INFO", "name_text"), ("BIRTHDAY", "name_text"), ("word:about", "person"),
                 ("PERSON_INFO", "kin"), ("BIRTHDAY", "kin")),
           out=("RELATION", "rel_pattern", "SCORE", "TASK", "DOC", "SHOPPING", "OWE", "PRICE", "SPEND", "BILL", "SCHEDULE",
                "CALORIE", "UNREAD"), build=b_person),
    Intent("relation", "tree.relation", lambda u, c: f"How you're related to {_first(c, 'person')}" if
           not _first(c, "to") else f"How {_first(c, 'person')} and {_first(c, 'to')} are related",
           "How is Meera related to me?",
           must=(("RELATION", "name_text"), ("rel_pattern",), ("KIN", "name_text", "word:my"), ("RELATION", "kin")),
           out=("BIRTHDAY", "TASK", "SCORE", "OWE", "SPEND"),
           build=b_relation),
    Intent("calories", "calorie.today", lambda u, c: "Calories · " + ("this week" if c[0].tool == "calorie.week"
                                                                       else (_first(c, "date") or "today")),
           "How many calories today?", must=(("CALORIE",),), helps=("time",), out=("log_verb+text",),
           build=b_calories, tools=("calorie.today",)),
    Intent("food_log", "calorie.food.log", lambda u, c: f"Log {_first(c, 'food')}"
           + (f" for {_first(c, 'meal')}" if _first(c, 'meal') else ""), "Log 2 rotis for lunch",
           must=(("log_verb", "text"), ("FOOD_LOG", "MEAL", "text")),
           out=("SHOPPING", "ADD", "SPLIT_ADD+amount", "SCORE", "game", "SPEND"),
           build=b_food, acts=True),
    Intent("unread", "chat.unread", lambda u, c: "Unread messages", "Any new messages?", must=(("UNREAD",),),
           out=("REMIND",), build=lambda u, c: [Call("chat.unread")]),
    Intent("docs", "docs.search", lambda u, c: f"Documents about “{_first(c, 'query')}”",
           "Find the note about the boiler", must=(("DOC", "text"),), out=("CHECKLIST", "NOTE_ADD"), build=b_docs),
    Intent("note_add", "docs.note.create", lambda u, c: f"New note: {_first(c, 'name')}",
           "Make a note: the plumber comes Tuesday", must=(("note_lead",), ("NOTE_ADD", "text")), build=b_note_add,
           acts=True),
    Intent("checklist", "docs.search", lambda u, c: f"The checklist “{_first(c, 'query')}”",
           "Show the packing checklist", must=(("CHECKLIST",),), build=b_checklist, out=("DONE", "ADD", "BOUGHT"),
           tools=("docs.search", "docs.checklist"), group="docs.checklist"),
    Intent("checklist_tick", "docs.checklist.tick", lambda u, c: f"Tick “{c[1].args['item']}” on {c[0].args['query']}",
           "Tick passport on the packing checklist", must=(("CHECKLIST", "DONE"), ("CHECKLIST", "BOUGHT")),
           build=b_checklist_change("docs.checklist.tick"), acts=True, tools=("docs.search", "docs.checklist.tick"),
           group="docs.checklist.tick"),
    Intent("checklist_add", "docs.checklist.add", lambda u, c: f"Add “{c[1].args['item']}” to {c[0].args['query']}",
           "Add sunscreen to the packing checklist", must=(("CHECKLIST", "ADD"),),
           build=b_checklist_change("docs.checklist.add"), acts=True, tools=("docs.search", "docs.checklist.add"),
           group="docs.checklist.add"),
    Intent("scores", "arcade.scores", lambda u, c: "Arcade scores" + (f" · {_first(c, 'game')}" if
                                                                      _first(c, 'game') else ""),
           "Who has the high score?", must=(("SCORE",),), helps=("game",), out=("TASK", "DOC", "MEAL"),
           build=b_scores),
]
BY_KEY = {i.key: i for i in INTENTS}


def generic_intent(tool: str, entry: dict) -> Intent:
    """An app's tool with a `quick` block (§17.4) and no built-in rule: its own words call it with its defaults."""
    q = entry["spec"].get("quick") or {}
    defaults = dict(q.get("args") or {})
    concepts = tuple(c for c in (q.get("concepts") or []) if c in lexicon.CONCEPTS)
    what = (entry["spec"].get("what") or tool).split(". ")[0].rstrip(".")

    def build(u, ctx):
        args = dict(defaults)
        argspec = entry["spec"].get("args") or {}
        if any(a.get("required") and k not in args for k, a in argspec.items()):
            return None                              # a required value the rules can't know: the model's job
        if "days" in argspec:
            lo, hi = argspec["days"].get("min", 1), argspec["days"].get("max", 60)
            args["days"] = days_arg(u, args.get("days", 7), lo, hi, ctx)
        if "month" in argspec and u.slots.month:
            args["month"] = u.slots.month
        if "date" in argspec and u.slots.date:
            args["date"] = u.slots.date
        return [Call(tool, args)]

    return Intent(f"app:{tool}", tool, lambda u, c: what[:80], what[:120],
                  must=(("app_word",),) + tuple((c,) for c in concepts), helps=("time",), build=build,
                  acts=bool(entry["spec"].get("acts")))
