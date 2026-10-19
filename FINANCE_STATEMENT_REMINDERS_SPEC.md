# Finance Dashboard — expected statement dates and overdue reminders

Status: **spec only, nothing built.** Each account can have an **expected statement date**. When a statement
hasn't arrived by then, its owner is reminded: on their phone through Home Assistant, and in the app. Each
person chooses **whether and when** the reminders come, for example 2 days, 5 days and 1 week after the
expected date. There is no default: nobody gets a reminder until they set their own.
The decisions made are in §11.

---

## 1. What it is

- **The problem:** statements are downloaded by hand from each bank. A missed month only shows up much later, as a
  gap in the charts or a balance that doesn't add up.
- **What it does:** the account's owner sets when its statement is ready ("monthly, on the 12th"). When that
  date passes with nothing uploaded, Finance reminds the owner on the days they chose, until the statement is
  uploaded or they dismiss it.

## 2. Setting the expected date (per account)

Set on **Setup → Accounts**, on the add form and each account's edit page (`accounts_list.html`,
`account_detail.html`), under a new **Expected statement** group:

| Field | Choices | Default |
|---|---|---|
| **Expected statement** | Off · Monthly · Every 2 months · Every 3 months · Every 6 months · Yearly | Off |
| **On day** | 1–31, or **Last day of the month** | none: the person picks it |
| **Starting** (only when not monthly) | a month (`YYYY-MM`) the cycle counts from | none: the person picks it |
| **Remind me** | on / off for this account | on |

- **Day 29, 30 or 31** in a shorter month falls on that month's last day (Feb 30 → Feb 28/29). **Last day** is
  always the last day.
- **Set by the person, never worked out:** Finance doesn't guess the day from past statements. With anything but
  Off, saving needs a day (and a starting month when not monthly).
- **Not set for:** archived or deleted accounts. Archiving keeps the setting but stops all reminders; restoring
  starts them again from the next expected date, never for the missed ones.
- **Shown as:**
  - a **Next statement** column on Transactions → Accounts and the Accounts list: "Oct 12", "Overdue 3 days"
    (warning colour), "Dismissed", or blank when Off;
  - the account's page says when the last statement arrived and when the next is expected.

## 3. When a statement counts as received

For each account the **expected dates** form a series: E₁, E₂, … (monthly on the 12th means Sep 12, Oct 12, …).
The **cycle** of Eₖ runs from the day after Eₖ₋₁ to Eₖ.

The expected date is the day the person says the statement is ready, so Finance doesn't look at transaction dates
or guess. Cycle Eₖ counts as **received** when the account gets an upload **on or after Eₖ**. An upload is:
- a statement (PDF or QFX), in any status except `error`; or
- a CSV import with at least one row.

Hand-entered transactions don't count. A statement still `processing` counts as received, so nobody is reminded
while it's being read.

- **Dismissed:** the owner can **Dismiss** an overdue notice (a month with no statement, or one they'll upload
  later). That cycle then gets no more reminders, and its notice is gone from Home, Upload and the badge. The
  next expected date starts fresh. Dismissing is per cycle; there's no undo button, but uploading still works as
  normal.
- **Overdue:** a cycle is **overdue** from the day after Eₖ, when not received and not dismissed.
- **Only the latest cycle:** once the next expected date Eₖ₊₁ arrives, cycle Eₖ is no longer reminded about or
  shown, even if it was never received. There are no reminders or notices for older statements, so a missed
  month never piles up into a list.
- **New setting:** when an account's expected date is first set, or changed, cycles before today are never
  overdue. Reminders start with the next expected date.

## 4. When reminders are sent (per person)

Each person chooses their own schedule on a new **My reminders** card in **Setup → Settings** (their own settings,
not App settings; an admin acting as someone sees and changes that person's):

| Field | Choices | Until the person sets it |
|---|---|---|
| **Reminders** | **No notifications** · **Remind me** | **No notifications** |
| **Remind me after the expected date** (with Remind me) | any of: On the day · 1 day · 2 days · 3 days · 5 days · 1 week · 10 days · 2 weeks · 3 weeks · 4 weeks | none chosen |
| **Then keep reminding** | Never · Every week · Every 2 weeks | Never |
| **At** | a time of day (Home Assistant's time zone) | 09:00 |

- **No default schedule.** A person who hasn't set anything, or who chose **No notifications**, gets no phone
  reminders at all. Each person sets their own, for example 2 days, 5 days and 1 week.
- **Remind me** needs at least one choice from the second row (1 to 6, shown as toggle chips). Saving with none
  says "Pick at least one, or choose No notifications."
- Until the person has chosen, the card explains it in one line: "You won't be reminded about overdue
  statements until you choose when."
- "On the day" is the expected date itself; "1 week" is Eₖ + 7, and so on. "Then keep reminding" continues after
  the last chosen one until it's received or dismissed, and always stops when the next expected date arrives
  (§3).
- **Several accounts due the same day** come as **one** notification ("2 statements overdue: Visa (Oct 12),
  Checking (Oct 10)"), not one each.
- **Owners only:** an account's reminders go only to its owner. People it's shared with (section 20 of the app's
  spec) are never reminded and don't see its overdue notice. An admin acting as the owner sees the owner's.

## 5. The phone notification

- **How it's sent:** Finance uses the same shared code as the other household apps: `common/python/ha_client.py`,
  `ha_people.py` and `ha_notify.py`, added to Finance in `common/manifest.json`. `homeassistant_api: true` is
  already set.
- **Who gets one:** only an account's owner, and only when their My reminders is set to **Remind me** (§4).
- **Where it goes:**
  - every phone Home Assistant links to the person (Settings → People → Track device);
  - plus any extra notify services an admin assigns on **Admin → Users**: a new "Notify" field per person, the
    same as the other apps' `user_notify`.
  
  If none is found, the person's My reminders card says "No phone found for you in Home Assistant", and nothing is
  sent.
- **What it says:**
  - title: **Statement overdue** (or **2 statements overdue**);
  - message: "Visa — expected Oct 12, 5 days ago. Upload it in Finance.";
  - a tap opens **Upload** with that account picked (the shared `deeplinks` path, through ingress). With several
    accounts, it opens Upload with none picked.
- **Account numbers:** only the account's name, never a number. This is the same rule as the debug pages (last 4
  at most), and here not even that.
- **Delivery:**
  - a background loop checks every 15 minutes;
  - a reminder is sent at the first check on or after its day and time;
  - each (account, cycle, reminder) is sent once, recorded in `statement_reminders`, so a restart never repeats
    one;
  - if Finance was off when a reminder was due, only the **newest** missed one is sent when it's back on, not
    all of them.
- **Failures:** a failed send (Home Assistant down) is retried at the next check for up to 6 hours, then given
  up and logged.

## 6. In the app

- **Home:** an amber notice card above the charts, "2 statements overdue — Visa (expected Oct 12), Checking
  (Oct 10)". Each account has **Upload**, which opens Upload with the account picked, and **Dismiss** (§3).
- **Upload page:** the same list above the upload form, with the first overdue account preselected, and the same
  **Dismiss** buttons.
- **Menu:** the Upload item gets a small count badge while anything is overdue and not dismissed.
- **For everyone with an expected date set, their own accounts only:** the notices show whatever My reminders
  says. **No notifications** stops phone notifications only. The notice disappears as soon as the statement is
  uploaded, when it's dismissed, or when the next expected date arrives.

## 7. Data

Migration **0029**, assuming 0028 is QFX:

- `accounts`, three new columns:
  - `expected_every_months` INTEGER NULL: 1, 2, 3, 6 or 12; NULL means Off;
  - `expected_day` INTEGER: 1–31, or 0 for the last day;
  - `expected_from` TEXT `YYYY-MM`.
  
  Plus `reminders_on` INTEGER NOT NULL DEFAULT 1, and `expected_set_at` TEXT, so cycles before a change are never
  overdue (§3).
- `reminder_prefs`: user_id PRIMARY KEY, mode (`off` / `remind`), offsets (JSON list of day counts, e.g.
  `[2,5,7]`; empty with `off`), repeat_days (0, 7 or 14), at_time (`HH:MM`), updated_at. No row
  means **No notifications**: rows are never created ahead of time.
- `statement_dismissals`: account_id, cycle_date, dismissed_by, dismissed_at, UNIQUE(account_id, cycle_date).
- `statement_reminders`: user_id, account_id, cycle_date, offset_days, sent_at, result (`sent`, `no_phone`,
  `failed`); UNIQUE(user_id, account_id, cycle_date, offset_days). Rows older than a year are purged by the daily
  housekeeping.
- `user_notify`: user_id, service; the same shape as the other apps, for Admin → Users' extra services.

Cycle dates are worked out in code (`app/statement_due.py`: `expected_dates(account, from, to)`,
`cycle_status(conn, account, today)`, `due_reminders(conn, now)`), never stored ahead of time. That way, changing
an account's setting can never leave stale future rows.

## 8. What changes elsewhere

- **App settings** (Admin → App settings, never `config.yaml`): one new switch, **Statement reminders**: on/off
  for the whole app, default on. When it's off, there are no phone reminders, no notices and no badge, but the
  per-account settings are kept.
- **One person's data** (section 20a): the account columns travel with `accounts`, and `reminder_prefs` and
  `statement_dismissals` are exported. `statement_reminders` (a send log) isn't. The export schema goes up by one.
- **Delete a person / wipe:** their `reminder_prefs`, `statement_dismissals`, `statement_reminders` and `user_notify`
  rows go too.
- **Backups / restore:** the new tables come along; a restored database doesn't resend old reminders, because the
  sent rows come back with it.
- **Household Assistant** (optional, §10): a read-only tool `finance.statements.due`. "Which statements am I
  still waiting for?" gets the same list as Home's card.
- **Query tab:** the new tables are described there like the others; `statement_reminders` and `user_notify` are
  hidden.

## 9. Checks (tests)

**Expected dates:**
- monthly on the 12th; on the 31st in February, April and a leap year; the last day;
- every 3 months from a starting month; yearly.

**Received or overdue:**
- an upload on the expected date or after counts; one the day before is the previous cycle's;
- QFX, PDF and CSV uploads all count; hand-entered rows don't; `processing` counts; `error` doesn't;
- Dismiss stops that cycle's reminders and its notice and badge; the next cycle is reminded about as normal;
- when the next expected date arrives, the previous cycle is neither reminded about nor shown;
- a new or changed setting never makes past cycles overdue;
- archived and deleted accounts are never overdue, and restoring doesn't remind about missed cycles.

**Reminder timing:**
- a person with no `reminder_prefs` row, and one set to **No notifications**, gets nothing;
- **Remind me** with no choice can't be saved;
- a chosen 2 / 5 / 7 days each send once, at the chosen time;
- "On the day";
- "Then keep reminding every week" continues after the last one and stops after 3 months;
- an upload between two reminders stops the rest;
- "Then keep reminding" stops at the next expected date.

**Delivery:**
- two accounts due together come as one notification;
- a restart doesn't resend;
- after Finance was off for several reminder days, only the newest one is sent;
- a failed send is retried, then given up after 6 hours;
- no phone: nothing is sent, and the card says so.

**Shared accounts:** only the owner is reminded and sees the notice; a person it's shared with gets neither.

**The notification itself:**
- it contains no account number;
- the tap link opens Upload with the account picked;
- extra notify services from Admin → Users are used.

**App settings:** the switch off stops phone reminders, notices and the badge, and keeps the settings.

**Data:**
- one person's data: export and import keep the account columns, `reminder_prefs` and dismissals;
- delete and wipe remove that person's rows;
- migration 0029 on an old database.

**Pages:**
- the My reminders card saves and shows each field; there's a test of the 6-choice limit;
- the account form needs a day (and a starting month when not monthly) before an expected statement can be saved;
- Dismiss on Home and on Upload; the notice shows for someone set to No notifications.

## 10. Later ideas (not in this spec)

- **A Home Assistant sensor per person**, `sensor.finance_<person>_statements_overdue`: the count, with the
  accounts as attributes. Useful for dashboards and the person's own automations.
- **Before the date:** an optional "Your Visa statement should be ready tomorrow" reminder.
- **The Household Assistant's morning briefing:** "One statement is overdue: Visa."

## 11. Decided

1. **The expected date** is set by the person for each account. Finance never works it out or suggests it. A
   statement uploaded on or after that date counts as received (§2, §3).
2. **Each person sets their own reminder schedule** on Setup → Settings → My reminders. There's no default, and
   **No notifications** is a choice (§4).
3. **Only the account's owner** is reminded and sees the overdue notice. People it's shared with aren't (§4, §6).
4. **No reminders for older statements:** only the latest expected date is reminded about and shown. Once the next
   one arrives, the previous one is dropped (§3).
5. **Overdue shows on Home and Upload** for the owner, with **Dismiss**. This is so whatever their phone setting
   is (§6).
