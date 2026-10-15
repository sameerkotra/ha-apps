"""Understanding a question without a model (HOUSEHOLD_ASSISTANT_SPEC.md §17.6–17.8).

    result = understand(text, ctx, previous=..., learned=..., exclude=...)
    result.kind:  "answer"   one clear meaning (result.parts: one Match per question in it)
                  "choices"  a close call: result.choices, up to 3 (Did you mean …)
                  "none"     nothing scores enough (result.choices: the closest, for the suggestions)
                  "model"    a question that needs the model (why, should, compare …)

In order: a learned phrase (an exact match wins at once), an answer to the last question's *Did you mean*, a
follow-up that only changes a slot ("and tomorrow?"), several questions in one ("tasks and birthdays this week"),
then every intent scored against the question.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import lexicon
from .intents import BY_KEY, INTENTS, Call, Ctx, Intent, Match, generic_intent, score
from .match import Utterance, read, template

THRESHOLD = 2.5             # one concept word is enough (3), also when a typo was put right (2.7)
MARGIN = 1.5
MAX_PARTS = 4
SPLITTERS = ("and", "also", "plus", "then", "as well as")
ACTION_WORDS = {"add", "put", "remind", "log", "tick", "split", "pay", "mark", "track", "record", "stick", "include",
                "bought", "purchased", "grabbed", "snooze", "postpone", "reschedule", "note", "cross"}
# "any bills due?", "do I have anything overdue?" are answered yes or no first; "how many tasks today?" with the
# number (§17.7)
YESNO_LEADS = {"is", "are", "do", "does", "did", "have", "has", "any", "anything", "am", "was", "were", "got", "will"}
COMPARABLE = {"compare"}
FOLLOW_UP_LEADS = ("and", "what about", "how about", "and for", "and what about", "same for", "now", "also", "ok and",
                   "and on", "and in", "for", "what about for")


@dataclass
class Result:
    kind: str
    parts: list = field(default_factory=list)          # Match per question part
    choices: list = field(default_factory=list)        # Match per choice
    template: str = ""
    norm: str = ""
    why: str = ""
    scores: list = field(default_factory=list)         # (intent key, score) for the admin's Test

    @property
    def label(self) -> str:
        return " · ".join(m.label for m in self.parts)


def intents_for(ctx: Ctx, exclude=()) -> list[Intent]:
    """The built-in intents whose tools the person may use, plus the apps' own (§17.4)."""
    have = [i for i in INTENTS if i.available(ctx) and i.key not in exclude]
    covered = {i.tool for i in have}
    for tool, entry in ctx.tools.items():
        q = entry["spec"].get("quick")
        if q and tool not in covered and f"app:{tool}" not in exclude:
            have.append(generic_intent(tool, entry))
    # a tool the app taught words for: its words alone are enough for its built-in intent too
    taught = {t for t, e in ctx.tools.items() if (e["spec"].get("quick") or {}).get("words")}
    out = []
    for i in have:
        if i.tool in taught and ("app_word",) not in i.must:
            i = Intent(i.key, i.tool, i.label, i.question, i.must + (("app_word",),), helps=i.helps, out=i.out,
                       build=i.build, acts=i.acts, tools=i.tools, group=i.group)
        out.append(i)
    return out


def _match(intent: Intent, u: Utterance, ctx: Ctx, s: float, source="rules") -> Match | None:
    calls = intent.build(u, ctx)
    if not calls:
        return None
    m = Match(intent=intent.key, calls=calls, label=intent.label(u, calls), score=s, source=source,
              acts=intent.acts)
    if intent.key == "shopping" and u.name("item"):
        m.note = u.name("item")                       # "is milk on the list": say whether it is
    return m


def _rank(u: Utterance, ctx: Ctx, exclude=()) -> list[tuple[float, Intent]]:
    ranked = []
    for i in intents_for(ctx, exclude):
        s = score(i, u)
        if s > 0:
            ranked.append((s, i))
    ranked.sort(key=lambda x: (-x[0], INTENTS.index(x[1]) if x[1] in INTENTS else 99))
    return ranked


def _one(u: Utterance, ctx: Ctx, exclude=()) -> tuple[str, list, list]:
    """("answer", [match], ranked) | ("choices", [matches], ranked) | ("none", [closest], ranked)."""
    ranked = _rank(u, ctx, exclude)
    built: list[Match] = []
    seen_groups = set()
    for s, i in ranked:
        if i.group in seen_groups:
            continue
        m = _match(i, u, ctx, s)
        if m:
            built.append(m)
            seen_groups.add(i.group)
    if not built or built[0].score < THRESHOLD:
        return "none", built[:3], ranked
    top = built[0]
    close = [m for m in built[1:] if m.score * MARGIN > top.score and m.score >= THRESHOLD]
    if close:
        return "choices", [top] + close[:2], ranked
    return "answer", [top], ranked


def _lone(u: Utterance, ctx: Ctx) -> Result | None:
    """The fixed table for one-word questions (§17.6) where the rules alone would guess."""
    words = [w for w in u.words if w not in lexicon.STOPWORDS]
    if words in (["balance"],):
        choices = [m for m in (_match(BY_KEY[k], u, ctx, THRESHOLD) for k in ("owe", "balances")
                               if BY_KEY[k].available(ctx)) if m]
        if len(choices) > 1:
            return Result("choices", choices=choices)
        if choices:
            return Result("answer", parts=choices)
    return None


def _split(u_text: str) -> list[str]:
    """"tasks and birthdays this week" -> ["tasks", "birthdays this week"] (on and/also/plus/then/,)."""
    import re
    parts = re.split(r"\s*(?:,|;|\band also\b|\balso\b|\bplus\b|\band then\b|\bthen\b|\bas well as\b|\band\b)\s*",
                     u_text)
    return [p for p in (x.strip() for x in parts) if p]


def normalise_for_split(text: str) -> str:
    """The question in lower case with "&" as "and" (commas kept: they split too)."""
    return " ".join(str(text or "").lower().replace("&", " and ").replace("+", " plus ").split())


def _has_splitter(text: str) -> bool:
    t = f" {normalise_for_split(text)} "
    return "," in t or ";" in t or any(f" {w} " in t for w in SPLITTERS)


def _compound(text: str, u: Utterance, ctx: Ctx, exclude) -> Result | None:
    if set(u.words) & ACTION_WORDS or u.has("COMPARE"):
        return None                                      # (and "september and august" are one comparison)                                      # "add milk and bread": one action, several items
    pieces = _split(normalise_for_split(text))
    if len(pieces) < 2:
        return None
    parts = [read(p, ctx.today, ctx.vocab) for p in pieces[:MAX_PARTS]]
    # a part without a time borrows the time another part has ("tasks and birthdays this week")
    timed = next((p for p in parts if p.slots.any_time), None)
    found = []
    for p in parts:
        if timed and not p.slots.any_time:
            for k in ("when", "date", "days", "month"):
                setattr(p.slots, k, getattr(timed.slots, k))
        kind, ms, _r = _one(p, ctx, exclude)
        if kind != "answer":
            return None
        found.append(ms[0])
    if len({m.intent for m in found}) < len(found) or len(found) < 2:
        return None
    return Result("answer", parts=found)


def _follow_up(u: Utterance, ctx: Ctx, previous: dict | None) -> Result | None:
    """"and tomorrow?", "what about next week", "same for September", "and for Ravi?": the last quick intent with
    that one thing changed."""
    if not previous or not previous.get("intents"):
        return None
    words = u.words
    if any(u.has(c) for c in lexicon.CONCEPTS if c not in ("MY", "QUESTION_WORDS")):
        return None
    lead = any(" ".join(words).startswith(p) for p in FOLLOW_UP_LEADS)
    rest = [w for i, w in enumerate(words) if i not in u.slots.used and w not in lexicon.STOPWORDS
            and w not in ("about", "same", "how", "what", "and", "for")]
    person = u.names.get("person") or u.names.get("group") or u.names.get("list_name")
    if not (u.slots.any_time or person or (lead and rest)):
        return None
    if rest and not person and not lead:
        return None
    first = previous["intents"][0]
    intent = BY_KEY.get(first["intent"])
    if intent is None or not intent.available(ctx):
        return None
    calls = intent.build(u, ctx)
    old = [Call(c["tool"], dict(c.get("args") or {})) for c in first.get("calls") or []]
    if not calls or not old:
        return None
    # keep the old arguments, change only what this question says
    merged = []
    for new, prev in zip(calls, old + old[len(old):]):
        args = dict(prev.args)
        for k, v in new.args.items():
            if k in ("when", "days", "month", "date") and not u.slots.any_time:
                continue
            args[k] = v
        if not u.slots.any_time:
            pass
        if person and intent.key in ("tasks",) and u.name("person"):
            args["person"] = u.name("person")
        merged.append(Call(new.tool, args))
    return Result("answer", parts=[Match(intent=intent.key, calls=merged, label=intent.label(u, merged),
                                         score=THRESHOLD, source="follow-up", acts=intent.acts)])


def ask_form(u: Utterance) -> str | None:
    """"count" for "how many …", "yesno" for a question that starts like one, else None."""
    w = u.words
    if any(w[i:i + 2] == ["how", "many"] for i in range(len(w))):
        return "count"
    if w and w[0] in YESNO_LEADS:
        return "yesno"
    return None


def _with_form(res: Result, u: Utterance) -> Result:
    form = ask_form(u)
    if res.kind == "answer" and form and len(res.parts) == 1 and not res.parts[0].acts:
        res.parts[0].ask = form
    return res


def _choice_answer(u: Utterance, ctx: Ctx, pending: list | None) -> Match | None:
    """The answer to the last *Did you mean*: "the first one", "second", "tasks", "the shopping list"."""
    if not pending:
        return None
    words = [w for w in u.words if w not in ("the", "one", "please", "a", "i", "mean", "meant", "it", "is", "1")
             or w == "1"]
    for w in u.words:
        if w in lexicon.ORDINALS:
            idx = lexicon.ORDINALS[w]
            if -len(pending) <= idx < len(pending):
                return pending[idx]
    if len(u.words) > 6:
        return None
    best, best_n = None, 0
    for m in pending:
        label_words = set(read(m.label, ctx.today, ctx.vocab).words) - lexicon.STOPWORDS
        n = len(label_words & set(words))
        if n > best_n:
            best, best_n = m, n
        elif n == best_n and n:
            best = None
    return best


def understand(text: str, ctx: Ctx, *, previous: dict | None = None, pending: list | None = None,
               learned=None, exclude=()) -> Result:
    """The question as typed first; only when that isn't understood, again with its typos put right (§17.3)."""
    first = _understand(text, ctx, previous=previous, pending=pending, learned=learned, exclude=exclude,
                        fix_typos=False)
    if first.kind == "model" or not has_unknown(text, ctx.vocab):
        return first
    second = _understand(text, ctx, previous=previous, pending=pending, learned=learned, exclude=exclude,
                         fix_typos=True)
    if first.kind == "answer":
        # "shoping list tmrw" reads as tasks until "shoping" is put right; the corrected reading wins when it rules
        # the first one out ("plants" -> "plans" doesn't: "tick off water the plants" stays as typed)
        if second.kind == "answer" and [m.intent for m in second.parts] != [m.intent for m in first.parts]:
            ruled_out = all(k not in dict(second.scores) for k in (m.intent for m in first.parts)) \
                if second.scores else False
            if ruled_out:
                return second
        return first
    if second.kind == "answer" or (second.kind == "choices" and first.kind == "none"):
        return second
    return first


def _understand(text: str, ctx: Ctx, *, previous: dict | None, pending: list | None, learned, exclude,
                fix_typos: bool) -> Result:
    """`previous`: the person's last quick question {intents: [{intent, calls}]} (follow-ups); `pending`: its
    unanswered choices (Match list); `learned(template)`: a learned phrase for this template or None
    ({id, intent, args, slot_args, source})."""
    u = read(text, ctx.today, ctx.vocab, fix_typos=fix_typos)
    tpl = template(u)
    res = Result("none", template=tpl, norm=" ".join(u.words))
    if not u.words:
        return res
    if u.has("QUESTION_WORDS"):
        res.kind, res.why = "model", "needs the AI"
        return res
    # an answer to the last Did you mean
    m = _choice_answer(u, ctx, pending)
    if m:
        res.kind, res.parts = "answer", [Match(intent=m.intent, calls=m.calls, label=m.label, score=m.score,
                                               source="choice", acts=m.acts)]
        return res
    # a learned phrase: the household's own way of asking
    if learned:
        hit = learned(tpl)
        if hit and BY_KEY.get(hit["intent"]) and BY_KEY[hit["intent"]].available(ctx) and hit["intent"] not in exclude:
            intent = BY_KEY[hit["intent"]]
            calls = intent.build(u, ctx) or [Call(intent.tool, {})]
            fixed = hit.get("args") or {}
            slot_args = set(hit.get("slot_args") or ())
            first = calls[0]
            args = {k: v for k, v in fixed.items() if k not in slot_args}
            for k in slot_args:
                if k in first.args:
                    args[k] = first.args[k]
            calls[0] = Call(first.tool, args)
            res.kind = "answer"
            res.parts = [Match(intent=intent.key, calls=calls, label=intent.label(u, calls), score=99.0,
                               source=hit.get("source") or "learned", acts=intent.acts, phrase_id=hit.get("id"))]
            return res
    lone = _lone(u, ctx)
    if lone:
        lone.template, lone.norm = tpl, res.norm
        return lone
    fu = _follow_up(u, ctx, previous)
    if fu:
        fu.template, fu.norm = tpl, res.norm
        return _with_form(fu, u)
    kind, ms, ranked = _one(u, ctx, exclude)
    res.scores = [(i.key, round(s, 2)) for s, i in ranked]
    if u.has("COMPARE") and not (kind == "answer" and ms[0].intent in COMPARABLE):
        res.kind, res.why = "model", "a comparison the rules don't make"     # "the difference between the accounts"
        return res
    if kind == "answer" or kind == "choices":
        # "tasks and birthdays this week": several questions win over one guess
        if kind == "choices" or _has_splitter(text):
            comp = _compound(text, u, ctx, exclude)
            if comp:
                comp.template, comp.norm, comp.scores = tpl, res.norm, res.scores
                return comp
        if kind == "answer":
            res.kind, res.parts = "answer", ms
            return _with_form(res, u)
        res.kind, res.choices = "choices", ms
        return res
    comp = _compound(text, u, ctx, exclude)
    if comp:
        comp.template, comp.norm, comp.scores = tpl, res.norm, res.scores
        return comp
    res.kind, res.choices = "none", ms
    return res


def has_unknown(text: str, vocab) -> bool:
    """Whether the question has a word the typo correction might put right (so a second reading is worth it)."""
    from . import normalise
    from .match import _KNOWN, _TARGETS, _name_tokens
    names = set()
    for _k, values in (vocab.kinds() if vocab else ()):
        for v in values:
            names.update(_name_tokens(v))
    return any(t.kind == "word" and len(t.text) > 3 and t.text not in _KNOWN and t.text not in _TARGETS
               and t.text not in names for t in normalise.tokens(text))


def explain(text: str, ctx: Ctx) -> dict:
    """The admin's Test (§17.11): how a question is read — words, concepts, slots, names, every intent's score."""
    r = understand(text, ctx)
    fixed = has_unknown(text, ctx.vocab) and any(m.score for m in r.parts + r.choices)
    u = read(text, ctx.today, ctx.vocab, fix_typos=True)
    if not any(t.fixed for t in u.toks) or not fixed:
        u = read(text, ctx.today, ctx.vocab, fix_typos=bool(has_unknown(text, ctx.vocab)))
    s = u.slots
    return {
        "words": [repr(t) for t in u.toks],
        "template": template(u),
        "concepts": {c: [" ".join(u.words[a:b]) for a, b, _f in spans] for c, spans in u.hits.items()},
        "names": {k: [v for _a, _b, v in spans] for k, spans in u.names.items()},
        "slots": {k: v for k, v in {"when": s.when, "date": s.date, "days": s.days, "month": s.month,
                                     "time": s.time, "amount": s.amount}.items() if v is not None},
        "scores": [{"intent": k, "score": v} for k, v in (r.scores or [(i.key, round(sc, 2)) for sc, i in
                                                                       _rank(u, ctx)])],
        "result": r.kind,
        "understood": [{"intent": m.intent, "label": m.label, "calls": [{"tool": c.tool, "args": c.args}
                                                                         for c in m.calls]}
                       for m in (r.parts or r.choices)],
    }
