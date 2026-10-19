# Finance Dashboard — asking the Household Assistant about statements, due dates, balances and subscriptions

Status: **spec only, nothing built; decisions made (§8).** New read-only questions the Household Assistant can answer from Finance:
- expected statement dates and overdue statements;
- card payment due dates;
- balances over time and net worth;
- subscription price changes;
- more about recurring charges;
- two **combined** questions that answer everything at once: **all the dates** (§6a) and a **finance
  overview** with balances and due dates together (§6b).

The decisions are in §8.

---

## 1. How it fits

- **Where the answers come from:** Finance answers the Household Assistant through its tool catalogue
  (`app/tools.py`, on the shared `common/python/assist_tools.py`). The Assistant learns each app's tools by itself
  (`assist.tools.list`), so **this is a Finance-only change**: new tools and two extended ones. The Household
  Assistant app needs no change and no new version.
- **Every tool here only reads.** None changes anything (`acts=False`).
- **The same rules as today's Finance tools:**
  - **Whose data:** the asking person's own, or with `person`, someone whose data an admin shared with them
    (`user_access`). An admin's "view as anyone" never applies.
  - **Switches:** **Answer the Household Assistant** (App settings) must be on, and the person's own **Let the
    Household Assistant answer for me** too.
  - **Accounts:** live and unarchived only. Account numbers show as the last 4 digits at most, never more;
    transaction notes are never shown.
  - **Links:** answers link to the page they came from (the month's dashboard, Recurring, an account's page,
    Upload). The catalogue's `targets` gain `/accounts/\d+` and `/uploads(/\d+)?`; both already redirect to the
    right page (sections 27 and 31).
- **Answering without the AI:** each tool lists quick-answer words (the Assistant's §17.4), using concepts it
  already knows (`BILL`, `BALANCE`, `RECURRING`, `PRICE`) plus plain words, so common questions are answered
  without the AI.

## 2. `finance.statements` — expected statement dates (new)

**Answers:** "When is my Visa statement due?", "Which statements are overdue?", "When did I last upload the
checking statement?", "Am I missing any statements?"

| Arg | Type | |
|---|---|---|
| `account` | string, ≤ 100 | one account, by name (any case) or last 4 digits; default all |
| `person` | string, ≤ 100 | as today |

**Returns, per account:**
- the expected schedule ("monthly on the 12th", "every 3 months on the last day", or "not set");
- the last statement received: its upload date and its period or newest transaction date, PDF, QFX or CSV;
- the next expected date;
- a status: **overdue N days**, **due today**, **dismissed**, **received**, or **not set**;
- the months with nothing uploaded, the same gap list as the Upload page's accounts strip.

The overdue / due / dismissed logic is `statement_due.cycle_status`; nothing is worked out a second way.

- **No accounts:** "You have no accounts in Finance yet."
- **Nothing set:** "None of your accounts has an expected statement date. You can set one on the account's page."
- **Link:** Upload, with the account picked when one account was asked about.
- **Quick words:** `statement due`, `statement date`, `last statement`, `missing statement`, `overdue statement`,
  `statements overdue`.

## 3. `finance.payments_due` — card payment due dates (new)

**Answers:** "When is my credit card payment due?", "Did I pay the Visa?", "Which card payments are due this week?"

| Arg | Type | |
|---|---|---|
| `account` | string, ≤ 100 | one card, by name or last 4 digits; default all cards |
| `days` | number 1–60 | only cards due within this many days (default all) |
| `person` | string, ≤ 100 | as today |

**Returns, per credit card:**
- the due-day setting ("the 22nd" or "not set");
- the next due date and days left, or "was due N days ago";
- the status from `card_payments`: **paid** (and by which payment), **unpaid**, **marked as paid**, or **not set**;
- the last payment found (date and amount);
- what the latest statement said was owed, and that statement's date: the amount to pay, as far as Finance
  knows.

A payment's description isn't shown, only its date and amount.

- **No cards:** "You have no credit card accounts in Finance."
- **Link:** the card's account page.
- **Quick:** concept `BILL`; words `card payment`, `payment due`, `due date`, `pay the card`, `credit card bill`,
  `did I pay`.

## 4. `finance.balance_history` — balances over time and net worth (new)

> **Removed in Finance 1.12.0** (with the account page's Balance over time and Home's Net worth). "What's our net
> worth?" and the balance questions now reach the older `finance.balances` (each account's latest printed balance).
> Kept below for the record.

**Answers:** "How has my Visa balance changed?", "What was the checking balance in June?", "What's our net
worth?", "How much do I owe on my cards?"

| Arg | Type | |
|---|---|---|
| `account` | string, ≤ 100 | one account; **left out = net worth** across all accounts |
| `month` | month | the balance at that month's end (the newest statement ending on or before it) |
| `months` | number 1–24 | how far back the history goes (default 12) |
| `person` | string, ≤ 100 | as today |

**Returns:**
- **One account:**
  - the latest balance and its date (a card: what's owed);
  - the change since the start of the window;
  - each statement's closing balance in the window, newest first, at most 24;
  - with `month`, just that month's figure and which statement it came from.
- **Net worth:**
  - the total: cash plus, cards minus what's owed;
  - each account's latest balance and as-of date;
  - the change over the window;
  - accounts with no balance yet, named, and left out of the total.

The numbers come from `app/balances.py` (section 28), the same as the account page and Home.

- **Nothing to show:** "No statement balances yet — balances come from uploaded statements."
- **Link:** the account's page, or Home for net worth.
- **Quick:** concept `BALANCE`; words `net worth`, `balance history`, `how much do I owe`, `owe on my cards`,
  `balance in`.
- **Relation to `finance.balances`:** that tool stays as it is (each account's latest printed balance). The two
  overlap only on "the latest". `finance.balances` keeps the plain "What's the balance on my accounts?";
  `finance.balance_history` takes "changed", "history", "in <month>" and "net worth".

## 5. `finance.subscription_changes` — price changes and new subscriptions (new)

**Answers:** "Did any subscription go up?", "What new subscriptions started?", "Has Netflix changed its price?"

| Arg | Type | |
|---|---|---|
| `merchant` | string, ≤ 100 | one merchant (matched like `finance.merchant`: any case, spaces ignored) |
| `days` | number 1–365 | how far back (default 90, the same window as Home's card) |
| `person` | string, ≤ 100 | as today |

**Returns:** each change from `app/subscriptions.py` (section 29):
- a **price change**: merchant, old amount → new amount, percent, since when, account;
- outside the **usual range**, for variable bills;
- a **new subscription**: merchant, amount, how often, first charge.

Dismissed changes are included and marked "(dismissed)": dismissing hides a card on Home, it doesn't make the
change untrue.

- **Nothing changed:** "No subscription price changes or new subscriptions in the last 90 days."
- **Link:** Recurring.
- **Quick:** concepts `RECURRING` + `PRICE`; words `price change`, `went up`, `price increase`, `new
  subscription`, `got more expensive`.

## 6. Extended tools

### 6.1 `finance.recurring` (more detail, new args)

| New arg | Type | |
|---|---|---|
| `merchant` | string, ≤ 100 | just that merchant: "How much is Netflix?" |
| `account` | string, ≤ 100 | just one account: "What's charged to my Visa every month?" |

Each charge now also says:
- the yearly cost: the amount times the charges per year, for example "≈ 215.88 a year";
- the last price change, when there is one ("was 15.49 until Sep 12").

The answer ends with the total a month and a year across the list ("about 142.30 a month, 1,707.60 a year").
Existing answers keep their wording; this only adds to it.

- **New examples:** "How much is Netflix?", "How much do we spend on subscriptions a year?"

### 6.2 `finance.bills` (card payments included)

"What bills are coming up?" also lists **card payments due** in the window, from §3's data: for example "Visa
payment — Oct 22 (in 3 days), last statement 440.00". These are marked as card payments and sorted by date with
the recurring charges.

- A card payment already paid isn't listed.
- When the Visa payment and a recurring charge on the same card fall in the window, both are listed. They're
  different things: what you pay the card, and what the card is charged.

## 6a. `finance.dates` — every date coming up, in one list (new)

**Answers:** "What finance dates are coming up?", "What's due?", "Any due dates this week?", "What do I need to do
in Finance?"

| Arg | Type | |
|---|---|---|
| `days` | number 1–60 | how far ahead (default 14); anything **overdue** is always included |
| `person` | string, ≤ 100 | as today |

**Returns** one list, overdue first, then soonest first. Each line has a date, what kind it is, the account and a
short status. It combines:

| Kind | From | Example line |
|---|---|---|
| **Statement** | §2 (`statement_due`) | "Visa statement — expected Oct 12, **overdue 7 days**" |
| **Card payment** | §3 (`card_payments`) | "Visa payment — **Oct 22 (in 3 days)**, last statement 440.00" |
| **Bill** | `finance.bills` (recurring charges expected) | "Netflix — about Oct 24, 17.99, Visa" |

- **Left out:** anything already received, paid, marked as paid or dismissed. A card payment and the same card's
  statement are separate lines.
- **Ends with** one count line: "2 overdue, 4 more in the next 14 days."
- **Empty:** "Nothing due in the next 14 days, and nothing overdue."
- **Link:** Home, where the same notices are.
- **Quick:** concept `BILL`; words `finance dates`, `due dates`, `what's due`, `coming up`, `anything due`,
  `overdue`.

## 6b. `finance.overview` — balances and due dates together (new)

**Answers:** "Give me a finance summary", "How are my finances?", "Finance overview", "Where do I stand?"

| Arg | Type | |
|---|---|---|
| `person` | string, ≤ 100 | as today |

**Returns**, in this order, each part short:
1. ~~**Net worth** and each account's latest balance, with its as-of date (cards: what's owed). This is §4 with no
   account, at most 10 accounts, then "and N more".~~ **Removed in Finance 1.12.0:** the overview is now parts 2–4
   below (due and overdue, this month so far, subscription changes).
2. **Due and overdue:** §6a's list for the next 14 days, at most 8 lines, then "and N more — ask for the finance
   dates".
3. **This month so far:** income, spending and net, the same numbers as `finance.summary` for this month, in one
   line.
4. **Subscription changes** from the last 30 days, at most 3 lines; left out when there are none.

- **Parts with nothing to say are left out**, so the answer stays short.
- **No accounts:** "You have no accounts in Finance yet."
- **Link:** Home.
- **Quick:** words `finance summary`, `finance overview`, `how are my finances`, `money overview`, `where do I
  stand`, `financial summary`.

**Relation to `finance.summary`:** that tool stays as it is, the month's income, spending and top categories for
"How did we do this month?" and "What did we spend in September?". The two are told apart by their words:
- "finance **summary**" / "overview" / "how are my finances" → `finance.overview`;
- "this month" / a month's name / "how did we do" → `finance.summary`.

The quick words above make the first group go to the overview without the AI. When the AI decides, the tool
descriptions say the same.

## 7. Tests

**Catalogue:**
- the four new tools are listed with their args and quick words;
- `targets` accept `/accounts/<id>` and `/uploads/<id>`;
- all are `acts=False`.

**`finance.statements`:**
- overdue, due today, dismissed, received and not set, all matching `cycle_status`;
- the gap list matches the Upload strip;
- one account by name and by last 4 digits.

**`finance.payments_due`:**
- paid by a payment, by a linked transfer, and marked as paid;
- unpaid and overdue;
- the `days` filter;
- the amount owed from the latest statement;
- no payment description in the answer.

**`finance.balance_history`** (removed in 1.12.0, with these tests):
- one account's history and its change;
- `month` picks the right statement;
- net worth with a card subtracted and an account with no balance left out;
- matches `balances.py` exactly.

**`finance.subscription_changes`:**
- a price change, a variable bill outside its range, and a new subscription;
- a dismissed change is marked, not hidden;
- the `merchant` and `days` filters.

**Extended tools:**
- `finance.recurring` with `merchant` and `account`, the yearly cost and the totals line, and the old wording
  unchanged;
- `finance.bills` includes an unpaid card payment and leaves out a paid one.

**Rules for every tool:**
- someone else's data is refused without a share;
- `person` works with a share;
- the App setting off, or the person's own switch off, means no answer;
- archived accounts are left out;
- no account number beyond the last 4 digits anywhere in an answer (checked across all answers).

**`finance.dates`:**
- overdue statements and card payments always come first;
- statements, card payments and bills in one list, soonest first;
- received, paid and dismissed items are left out;
- the `days` window; the count line; the empty answer.

**`finance.overview`:**
- the parts appear in order; empty parts are left out;
- the limits (10 accounts, 8 dates, 3 changes) with "and N more";
- its numbers equal §4, §6a and `finance.summary`'s for this month;
- "Give me a finance summary" reaches `finance.overview` and "How did we do this month?" reaches
  `finance.summary`, in the quick-flow fixture.

**Quick answers:** each tool's quick words reach it in the Assistant's quick-flow test fixture (the Assistant's
existing test helper with a Finance catalogue).

## 8. Decided

1. **Dismissed price changes** come up in answers, marked "(dismissed)" (§5).
2. **Net worth** is part of `finance.balance_history` with no account (§4) and of `finance.overview` (§6b); there's
   no separate tool. (Both removed in Finance 1.12.0.)
3. **No voice actions for now:** "Mark the Visa as paid" and "Dismiss the Netflix change" are not in this spec.
   They could come later as acting tools, only after the person taps the proposed change, like
   `finance.expense.add`.
4. **The overview's windows:** 14 days ahead for due dates, 30 days back for subscription changes (§6b).
5. **"Finance summary"** goes to the new overview (§6b). "How did we do this month?" and a named month stay with
   `finance.summary`.
