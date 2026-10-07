# Messages between the household apps — spec

Status: **in use (2026-10-04).** The shared `app_bus.py` was built in the shared-code work (`SHARED_CODE_PLAN.md`,
phase 4b). First users: **Household Chat 2.2.0** and **Household Todo 2.3.0** receive what Household Docs sends
(§6.3 Send to chat, §6.4 Checklist → Todo list, §6.5 links between app pages); Household Docs 1.0.0 sends them.
Arcade's duel planning (§6.2, §7) and the Household Assistant app itself (§6.6; every app's side of it is built) are still to come. Each use gets its own short spec section and ships as an update
of the apps involved.

## 1. Purpose

Let one app ask another to do something, or tell it that something happened — for example Household Arcade asking
Household Chat to run a poll for a duel time and hearing back the result. Without apps calling each other directly,
without new permissions and without extra software in the household.

## 2. The channel: Home Assistant's event bus

- **Sending:** `POST http://supervisor/core/api/events/household_apps` with the Supervisor token (covered by
  `homeassistant_api: true`, which the apps already have). Receipt Price Intelligence already sends HA events this way.
- **Receiving:** one WebSocket per app to `ws://supervisor/core/websocket`, `subscribe_events` with
  `event_type: household_apps`. Household Chat already keeps such a WebSocket (`ha_events.py`, for phone notification
  buttons), now built on the shared client `common/python/ha_ws.py` (copied into Chat as `app/common/ha_ws.py`).
- **One event type** for everything: `household_apps`. Apps ignore messages not addressed to them.
- **Why this channel:** no network port, no app-to-app addresses, nothing to install; works with every app's current
  permissions.

Turned down: apps calling each other over HTTP (each app trusts only HA's ingress proxy; a second door with a shared
password and Supervisor access to find addresses would be needed); MQTT (needs the Mosquitto app set up in every
household); mailbox files in `/share` (Vault and Arcade don't map `/share`; polling).

## 3. The envelope

Every message is one `household_apps` event whose data is:

```json
{
  "id": "01J9…",                 // unique message id (ULID): sorting and de-duplication
  "v": 1,                        // envelope version
  "from": "household_arcade",    // sender's slug
  "to": "household_chat",        // receiver's slug, or "*" for everyone (hello only)
  "kind": "poll.create",         // what this message is (§6)
  "kv": 1,                       // version of this kind's data
  "reply_to": null,              // id of the message this answers
  "ref": "duel:3f2c…",           // the sender's own reference, echoed back in every answer
  "sent": "2026-10-03T21:51:00-06:00",
  "expires": "2026-10-04T21:51:00-06:00",
  "data": { … }                  // the kind's fields, small
}
```

- **Size:** the whole event at most 8 KB; bigger is refused by the sender's own `app_bus` before it's sent.
- **What may be in `data`:** HA user ids, names, times, choices, counts, ids and short titles the user typed for this
  purpose (a poll question). **Never** message text from chats, document contents, passwords, amounts from Finance, or
  anything the receiving app's users couldn't see in the sending app. One deliberate exception: the checklist items
  a person chooses to send to Household Todo (§6.4) travel as their text, because they *are* the request; Docs
  sends nothing else from the document.

## 4. Delivery

HA events are fire-and-forget: if the receiver is stopped, the event is gone. So:

- **Outbox** (sender): every message is written to the sender's own `bus_outbox` table first, then sent. It's re-sent
  after 30 s, 2 min, 10 min, then every 30 min until an `ack` arrives or it expires. Messages to an app that isn't
  installed (§5) aren't sent at all; the caller is told at once.
- **Ack** (receiver): on receipt the receiver stores the message id in `bus_seen` and answers `ack` (or `nack` with a
  reason) — **after** it has acted, in the same database transaction as the action, so a crash can't ack something
  that didn't happen.
- **De-duplication:** a message id already in `bus_seen` is acked again and not acted on twice. `bus_seen` keeps ids
  for 7 days.
- **Order:** not guaranteed; kinds are designed so order doesn't matter (each answer names what it answers; later
  states win by `sent`).
- **Expiry:** a message past `expires` is dropped by the receiver (`nack expired`) and by the sender's outbox (the
  calling feature is told, e.g. "Chat didn't answer — invite sent without a poll").
- **Restarts:** on start-up each app re-sends what's still in its outbox; the receiver's de-duplication makes that
  safe.

```sql
CREATE TABLE bus_outbox (id TEXT PRIMARY KEY, to_app TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
  ref TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT NOT NULL, expires TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','acked','nacked','expired')), result TEXT, created_at TEXT NOT NULL);
CREATE TABLE bus_seen (id TEXT PRIMARY KEY, from_app TEXT NOT NULL, kind TEXT NOT NULL, result TEXT,
  seen_at TEXT NOT NULL);
CREATE TABLE bus_apps (slug TEXT PRIMARY KEY, name TEXT, version TEXT, can TEXT NOT NULL,   -- JSON list of kinds
  last_seen TEXT NOT NULL);
```

## 5. Finding each other

- **`hello`** (to `*`) at start-up and every 6 hours: `{name, version, can: ["poll.create", …], wants: [...]}`.
  Every app records it in `bus_apps` and answers with its own `hello` (to that app) if it hasn't been seen for an hour.
- **`who`** (to `*`): "say hello" — sent at start-up so an app learns who's there without waiting.
- An app counts as **available** for a kind if it said `can` that kind in the last 24 hours. Features check this
  before offering anything ("Plan a duel" shows *Ask in Chat* only when Chat can run polls) and fall back otherwise.
- Admin pages show **Connected apps**: name, version, last seen, what they can do. A small read-only card (Chat:
  Admin → App settings; Todo: Admin → Settings), drawn by the shared `common/static/connected-apps.js` from
  `GET /api/admin/connected-apps` → `{on, connected, apps: [{slug, name, version, can, last_seen, active}]}`
  (`on`: the bus is running — off outside Home Assistant; `connected`: its WebSocket is up). Kinds are shown in
  words ("Post shared documents in chats"); an app not heard from in 24 hours is shown greyed as "not seen lately".

## 6. Kinds

General rules: kind names are `<area>.<verb>` or `<area>.<thing>.<verb>` (lower case, `_` allowed, each part at most
32 characters); a kind's data only ever gains optional fields within its `kv`; a breaking change is a new `kv`, and
receivers answer `nack unsupported_version` to versions they don't know.

Rules for every request kind below (§6.3, §6.4), as for §6.2:

- **`requested_by` is the actor.** The receiver checks the request against its own data and rules exactly as if
  that person did it in the app (membership, enabled, may post / may add, read-only chats, private lists), and acts
  as that person. Nothing is allowed because of who sent the message (`from` is a claim, §8). Household Todo's
  "acting as" (an admin working as someone else) never applies to messages.
- **Unknown fields are ignored**, missing optional ones take their defaults, and a wrongly typed or out-of-range
  field is `nack invalid` with the field's name as `detail`.
- **Nack details** (machine-readable, for the sender's wording): `no_access` (`requested_by` isn't an enabled user
  of the receiving app — never signed in there, or turned off), `chat` / `list` with `not_found` (no such chat or
  list, or one the person can't see — the two aren't told apart, as in the app's 404), `read_only` (Chat: a direct
  chat with someone who no longer has access, or two children while that's not allowed), and the field name for
  `invalid`. `busy` (the receiver's rate limit, or a backup being restored) is retried by the sender's outbox.
- **Lists are answered at once or not at all**: the `….list` kinds are sent with `expires_in` ≈ 2 minutes (an answer
  later than that is useless to a dialog); the action kinds with the default 24 hours, so a short restart of the
  receiver doesn't lose them.

### 6.1 Always present
| Kind | Data | Answer |
|---|---|---|
| `hello` | `{name, version, can[], wants[]}` | `hello` back (rate-limited) |
| `who` | `{}` | `hello` |
| `ack` | `{result?}` (kind-specific) | — |
| `nack` | `{reason: not_found \| not_allowed \| invalid \| unsupported_kind \| unsupported_version \| expired \| busy, detail?}` | — |

### 6.2 Polls (Household Chat) — first use
| Kind | Direction | Data |
|---|---|---|
| `poll.create` | app → Chat | `{requested_by: <HA user id>, people: [<HA user ids>], chat: "direct" \| "group" \| <chat id>?, question (≤ 200), options: [{id, label (≤ 80), at?: <ISO time>}] (2–6), closes_at, close_when_all_voted: bool, multiple: bool, badge: "Arcade"}` |
| ack result | Chat → app | `{poll_id, chat_id}` |
| `poll.updated` | Chat → app | `{poll_id, votes: {<option id>: [<HA user ids>]}, voters_left: [<HA user ids>]}` — at most once a minute |
| `poll.closed` | Chat → app | `{poll_id, winner: <option id> \| null (tie or no votes), votes: {…}, reason: "time" \| "all_voted" \| "closed_by_user"}` |
| `poll.cancel` | app → Chat | `{poll_id, why?}` — Chat closes the poll with a note "cancelled by Arcade" |
| `message.post` | app → Chat | `{chat_id, text (≤ 500, plain), badge}` — a short system note in a chat that was already used for this `ref` (e.g. "Duel booked for 7:30 pm") |

**Chat's checks:** `requested_by` must be an enabled Chat user allowed to post in the target chat; every person in
`people` must be a member of it (a `direct` chat is found or made between exactly `requested_by` and one other
person; a `group` must already exist and contain them all — the app never creates groups on another app's say-so).
Otherwise `nack not_allowed`. Chat posts the poll **as `requested_by`**, with the badge ("from Arcade"), so people
see who asked. Votes, notifications and closing work like any Chat poll; people can also close it in Chat.

### 6.3 Send to chat (Household Docs → Household Chat; Docs spec §17.15)
| Kind | Direction | Data |
|---|---|---|
| `chat.chats.list` | Docs → Chat | `{requested_by: <HA user id>, limit?: 1–50 (default 50), chat_id?: <chat id>}` |
| ack result | Chat → Docs | `{chats: [{id, kind: "group" \| "direct" \| "personal", name, icon?, household?: true, members: <count>, member_ids?: [<HA user ids>]}], more: bool}` — `member_ids` only with `chat_id` |
| `chat.card` | Docs → Chat | `{requested_by, chat_id, title (1–200), type: "note" \| "checklist" \| "sheet" \| "folder" \| "file", item_id (Docs id, 1–64 of A–Z a–z 0–9 _ -), owner_name? (≤ 80), panel?: "/<full slug>", target?: "/doc/<item id>" \| "/folder/<item id>" \| "/file/<item id>" (≤ 200), share_with_members?: bool (false), badge?: "Docs" (≤ 20; default: the sender's `hello` name without "Household ")}` |
| ack result | Chat → Docs | `{message_id, chat_id, member_ids?: [<HA user ids>]}` — `member_ids` only when `share_with_members` |

**`chat.chats.list`** — the chats `requested_by` may post in, in Chat's own order (personal room first, then pinned,
then most recent): groups and the Household group they're in, direct chats that are visible to them in Chat and
not read-only, and their 📌 My room (`kind: "personal"`, name "My room"). `name` is what that person sees (a
direct chat is named after the other person), `icon` a group's emoji, `members` the number of enabled members.
**The list names nobody** (security review 2026-10: answers go into Home Assistant's event history): no member ids.
With **`chat_id`** the answer is only that chat (one the person may post in; otherwise `nack not_found chat`) with
`member_ids` — its enabled members' HA user ids **except `requested_by`** — asked by Docs only when the person
ticks *Also give the chat's members access* for that chat (so Docs can show "these people can't open it yet" and let
the person choose). The answer is kept under the 8 KB limit: when it would be too big, the least recent chats are
left out and `more: true` (one chat: its `member_ids` are dropped). Nack: `not_allowed no_access`, `invalid`
(`limit`, `chat_id`), `not_found chat`, `busy`.

**`chat.card`** — Chat checks, in this order: `requested_by` is an enabled Chat user (`not_allowed no_access`);
`chat_id` is a chat they're a current member of (`not_found chat`); they may post there now (`not_allowed
read_only`); the fields (`invalid <field>`); the person's message rate limit (30 a minute, shared with what they
type in Chat; `busy`). Then Chat posts a message of the new kind **`card`** **as `requested_by`**, with the
badge ("from Docs"), in the same transaction as the ack. It follows the chat's disappearing-messages setting,
reaches everyone through the live stream, counts as unread, and notifies like any message (below). With
`share_with_members`, the ack's `member_ids` are the chat's enabled members except `requested_by`; **Docs** then
adds a normal Docs share for each of them who is a Docs user **and** was among the members Chat named for that chat
earlier **and** was left ticked by the person in Docs' dialog (Docs spec §17.15) — an answer can narrow that, never
widen it, and an ack whose `chat_id` isn't the one asked about shares with nobody. Chat never decides who may open a
document, and neither does an answer anyone could forge (§8).

How Chat shows a card (Chat spec §15.11):
- A bubble from the person: **"<person> shared <title>"** with an icon by `type` (📝 note, ✅ checklist, 🧮 sheet,
  📁 folder, 📄 file), a line "<Type> · <owner_name>'s · from Docs" and **Open in Docs** (§6.5). With
  `share_with_members`: "Shared with this chat's members". Nothing else: **a card never holds document
  content** — only the title, type, owner name and ids, as here.
- Whether a member may open the document is Docs' business: opening it without access shows Docs' own "🔒 No
  access" page ("This document isn't shared with you. Ask the person who sent it."), which names nothing about the item (Docs spec §17.15). Chat can't know, and doesn't guess.
- Like other messages: reactions, replies (quote "📝 <title>"), pins, stars, reminders, delete (the person who
  shared it, group owners/admins). Not editable and **not forwardable** (share it from Docs again instead).
- **Search** finds cards by title (the title is the message's searchable text); **notifications**: "<person>
  shared a checklist “Trip 2026”" with full previews, "New message from <person>" / "New message in Household
  Chat" with shorter previews, like any message; **chat list preview**: "✅ Trip 2026"; **chat downloads**:
  "✅ <title> — shared from Docs (<owner_name>'s)", without a link; **backups** keep cards like messages.

### 6.4 Checklist → Todo list (Household Docs → Household Todo; Docs spec §17.16)
| Kind | Direction | Data |
|---|---|---|
| `todo.lists.list` | Docs → Todo | `{requested_by}` |
| ack result | Todo → Docs | `{lists: [{id, name, kind: "shared" \| "personal", open: <open task count>, maintenance?: true}], more: bool}` |
| `todo.items.add` | Docs → Todo | `{requested_by, list_id? \| new?: {name (1–60), shared?: bool (false)}, items: [{text (1–200), done?: bool (false), level?: 0 \| 1 (0)}] (1–200), source?: "Docs" (≤ 20; default as `badge` in §6.3)}` |
| ack result | Todo → Docs | `{list_id, list_name, created: bool, tasks: <count>, subitems: <count>, ids?: [<one id per item, in order>]}` |

**`todo.lists.list`** — the lists `requested_by` may add to, in Todo's order: every shared list (the Maintenance
list marked `maintenance: true`), then their own personal lists. Never anyone else's personal list. Kept under
8 KB like `chat.chats.list` (`more: true`). Nack: `not_allowed no_access`, `busy`.

**`todo.items.add`** — exactly one of `list_id` (an existing list) or `new` (Todo makes the list: `shared: false` → a
personal list of `requested_by`, `true` → a shared list, placed last like a list made in the app). Todo checks:
`requested_by` is a known, enabled Todo user — someone who has opened Household Todo at least once and isn't
turned off in Admin → Users (`not_allowed no_access`; Docs says "Open Household Todo once first"); the list exists
and is shared or theirs (`not_found list`); the fields as the app's own forms do (`invalid <field>`: list name
1–60, item text 1–200 after trimming, 1–200 items, at most 100 checklist items under one task →
`invalid too_many_subitems`). Then, in one transaction with the ack:
- each `level: 0` item becomes a **task** at the end of the list (title = text), created by `requested_by`; each
  `level: 1` item becomes a **checklist item** of the task before it (a `level: 1` item with no task before it in
  the message becomes a task);
- `done: true` → a completed task (completed now, by `requested_by`) / a ticked checklist item;
- every task gets the source label (`source`, default the sender's name) shown as **"from Docs"** on the task;
- no due date, assignee, priority or reminder — those are set in Todo — so, as when a person adds tasks in the app,
  **nobody is notified** (Todo's only "new task" push is "assigned to you", and nothing is assigned); the
  dashboard, calendar and Home Assistant sensors pick the tasks up on their next refresh.
- `ids` (one per item, in order: the task's or the checklist item's id) is left out when the answer wouldn't fit
  in 8 KB; `tasks`/`subitems` always come.

**Sending more than fits:** a message is at most 8 KB, so a long checklist goes in several `todo.items.add` in order,
each waiting for the previous ack: the first with `new` (or `list_id`), the rest with the `list_id` from the first
answer. Docs keeps a `level: 1` item in the same message as its task. *Move* removes items from the Docs checklist
only after their message's ack; a nack or the local `nack expired` leaves them in Docs and says why.

### 6.5 Linking to another app's page ("Open in Docs")
Researched 2026-10-04 (Home Assistant core `components/hassio/addon_panel.py`, frontend
`src/panels/app/ha-panel-app.ts` and `src/common/navigate.ts`, Supervisor `apps/model.py` and
`api/middleware/security.py`):

- **Where an app's page lives.** Home Assistant registers a sidebar page for every app with *Show in sidebar* on, at
  **`/<full slug>`** (`frontend_url_path=<slug>`). The full slug is `<8-character repository hash>_<slug>` (for
  example `a1b2c3d4_household_docs`; `local_household_docs` for a local copy), so it depends on how the app was
  installed and can't be written into another app. `/hassio/ingress/<full slug>` (older Home Assistant) and
  `/app/<full slug>` (current) also open it, but those are the admin's Settings → Apps pages — not for everyone.
- **How Docs learns its own path** (at start-up, cached): `GET http://supervisor/addons/self/info` with the
  Supervisor token — allowed for every app without `hassio_api` (the Supervisor lets `/addons/self/…` through) —
  gives `data.slug` and `data.ingress_panel`. Fallback when that fails: the `HOSTNAME` environment variable, which
  the Supervisor sets to the full slug with `_` turned into `-` (`hostname = slug.replace("_", "-")`); our slugs use
  `_` only, so `HOSTNAME.replace("-", "_")` gives the slug back — accepted only if it matches
  `^([0-9a-f]{8}|local)_household_docs$`. No slug, or `ingress_panel` false → no `panel`.
- **What travels:** Docs sends `panel: "/<full slug>"` (if known) and `target` (its own route, e.g. `/doc/<id>`) in
  every `chat.card` — not in `hello`, whose fields are fixed by `app_bus`. **Each card keeps its own `panel`**
  (security review 2026-10: Chat used to apply the newest `panel` from any card to every card of that app, so one
  forged card in any chat could redirect all "Open in Docs" links, e.g. to `/config`). Chat accepts `panel` only as
  the **sending app's own page**: `/<1–16 of a–z 0–9>_<the sender's slug>` (for Docs `/a1b2c3d4_household_docs` or
  `/local_household_docs`), and `target` only as `/doc/<id>`, `/folder/<id>` or `/file/<id>` (ids of
  `A–Z a–z 0–9 _ -`, 1–64) — never a scheme, `//`, `..` or another app's page; anything else is `nack invalid`, and
  cards are checked again when shown (a stored card that doesn't pass shows "Open the … app from the sidebar"). A
  card from before a reinstall from another repository keeps its old path (the sidebar text if that page is gone).
- **How the link works in Chat** (inside Home Assistant's frame, same origin): an `<a href="{panel}{target}"
  target="_top">`. A click (1) asks Home Assistant to navigate — `window.parent.postMessage({type:
  "home-assistant/navigate", path}, origin)`, the frontend's own message for app pages; (2) if after 300 ms the top
  page's path hasn't changed (older Home Assistant), and the top page is reachable (same origin), it does what Home
  Assistant's own `navigate()` does: `top.history.pushState(…, path)` and a `location-changed` event on the top
  window; (3) otherwise the plain link loads that page. Both in-app ways keep the sidebar and don't reload Home
  Assistant. Outside Home Assistant's frame the link simply opens.
- **How Docs opens the item:** Home Assistant hands the rest of the path to the app's page — current versions in
  the `home-assistant/properties` message (`route.path`, after Docs posts `home-assistant/subscribe-properties`);
  in any version Docs can read `window.parent.location.pathname` (same origin) and take what follows `panel`. Docs
  opens that item (or shows its 🔒 No access page) and then replaces the address with the bare `panel` path, so a
  reload doesn't open it again. *(built, Docs 1.0.0)* All three ways are used: the parent's path at start-up, the
  properties message in the first seconds, and the app's own sub-path (`…/doc/<id>` answers a relative redirect to
  `../#/doc/<id>`; `/folder/<id>` and `/file/<id>` likewise). The 🔒 No access page says nothing about the item —
  not its title, its owner or even whether it exists — exactly as for a deleted one (Docs spec §17.15).
- **No `panel` known:** the card says "Open Household Docs from the sidebar to see it" instead of a link.

### 6.6 Household Assistant tools (apps → Household Assistant) — the apps' side built, the assistant not yet

A summary; `HOUSEHOLD_ASSISTANT_SPEC.md` §4–7 is the full spec. The assistant asks each app what it can answer and
then asks it questions on a person's behalf, like tools on an MCP server.

| Kind | Data | Answer |
|---|---|---|
| `assist.tools.list` | `{}` (to one app, or `*` at the assistant's start-up) | `ack {result: {tools: [{name, what, args, returns, acts, scope, examples}], on}}` |
| `assist.tool.call` | `{tool, args, requested_by, question, confirm?, confirmed_at?}` | `ack {result: {question, tool, text, items[], links[], more}}` |

- **Who sends, who answers.** Only Household Assistant sends these; an app answers if it lists `assist.tools.list`
  in its `hello` `can`. Tool names stay within the answering app's own area (`todo.tasks`, `docs.search`); an unknown
  tool is `nack not_found` with `detail: tool`, a bad argument `nack invalid` with the argument's name.
- **`requested_by` is the actor**, as for every request kind (§6): the app answers exactly what that person could see
  in it. Refusals are `nack not_allowed` with `detail` `no_access` (not an enabled user there), `off` (the app's
  admin hasn't turned on *Answer the Household Assistant*), `person_off` (the person turned off *Let the Household
  Assistant answer for me*), `child` or `confirm`.
- **Shared code**: `common/python/assist_tools.py` (the catalogue, checks, switches, fitting and links); each app's
  `tools.py` holds its own tools. Answering today: Chat, Arcade, Family Tree, Calorie Tracker, Splitpot, Todo,
  Docs, Receipt Price Intelligence, Finance Dashboard (HOUSEHOLD_ASSISTANT_SPEC §13).
- **Short-lived, never retried.** Both kinds go with `expires_in` ≈ 2 minutes (`assist.tools.list`) or **20 seconds**
  (`assist.tool.call`); the outbox doesn't retry them, and a lost call is shown as "didn't answer".
- **Actions only after a tap.** A tool marked `acts: true` (add a task, a shopping item, a note) is refused with
  `nack not_allowed` unless the call carries `confirm: true`, which the assistant sets only when the person tapped
  the proposed change.
- **Contents may travel** — the one exception to §3's "no contents" besides §6.4's checklist items: results carry
  amounts, task and document text, because the person asked for exactly that. A result is at most 6 KB, a
  catalogue at most 8 KB; never anything from Household Vault, Chat message text or Finance memo text. Every app
  that answers makes the recorder exclusion (§8) a must in its DOCS.
- **Links** in a result follow §6.5: `panel` is the answering app's own page, `target` one of its own routes, and
  the assistant checks both before showing them.

## 7. First use (to be specified and built later): planning a duel in Household Arcade

1. Arcade → *Play with someone* → **Plan for later**: pick the person (or people), the game, and 2–4 times.
2. If Chat is available for `poll.create`: **Ask in Chat** sends the poll; otherwise Arcade sends its own
   notification with the times as buttons (no Chat needed).
3. People vote in Chat. On `poll.closed` with a winner, Arcade books the duel; a `message.post` says "Snake Duel
   booked for 7:30 pm" in the same chat. No winner → Arcade tells the requester "No time agreed" with *Ask again*.
4. Five minutes before, everyone gets Arcade's usual race invite with **Join**.
5. Cancelling the plan in Arcade sends `poll.cancel`.

Later candidates, each its own spec: Chat → Todo "make this a task"; Receipt shopping-list items into a Docs
checklist; Family Tree birthdays into the household chat. (Docs → Chat and Docs → Todo: §6.3, §6.4, built.)

## 8. Trust and privacy

- **Who can send `household_apps` events:** the apps themselves (Supervisor token) and HA administrators (REST with a
  long-lived token, the developer tools, automations). Ordinary HA users can't. That's the same trust the apps already
  give HA admins — **and it extends to every installed app with `homeassistant_api: true`**, ours or anyone else's:
  any of them can fire a `household_apps` event claiming to be any app, and can read the bus's events (answers
  included). Receivers therefore accept such a message only as a request a real user could make (below), and
  senders never act on an answer beyond what their own user confirmed (Docs: member shares only for people the
  person ticked, out of those named for that chat — §6.3).
- **A message is a request, never a permission.** The receiver checks everything against its own data and rules (as
  §6.2 does), and acts only as a real user would be allowed to. People still decide inside the app (they vote in
  Chat).
- **Sender is a claim.** `from` isn't proven; receivers treat it as a label for display and for answers, not as
  authority.
- **Least revealing answers:** an answer says only what the asking dialog needs at that moment — the chat list
  names no members; a chat's member ids come only for the one chat the person picked, only when they asked to give
  its members access (§6.3).
- **History:** HA's recorder stores events. Envelopes hold only ids, names, times and choices (§3) — except the
  Household Assistant's tool results (§6.6), which is why every app that answers them makes this a must, not a tip.
  DOCS.md of every app that uses the bus shows how to keep them out of HA's history:
  ```yaml
  recorder:
    exclude:
      event_types:
        - household_apps
  ```
- **Automations can listen** (e.g. a household automation reacting to `poll.closed`). That's a feature; it's why
  envelopes stay free of private content.
- Nothing about the bus is logged with `data` contents; logs show id, from, to, kind, result.

## 9. The shared module: `common/python/app_bus.py`

Built in the shared-code work (phase 4b), copied to apps only when an app starts using it (with `ha_ws.py`, the
WebSocket client it uses) — Household Chat and Household Todo since 2.2.0 / 2.3.0. Standard library only; it
imports nothing from the app.

- **API for app code** (module-level functions on a default bus; `app_bus.AppBus()` makes more, e.g. in tests):
  ```python
  bus.start(app_slug, name, version, can=None, wants=(), handlers=None, db=db.get_conn, ws=None)  # in lifespan
  bus.stop()                                                       # on shutdown: closes the socket, ends both threads
  bus.available(app, kind) -> bool                                 # `can` said within 24 h
  bus.apps() -> [{slug, name, version, can, last_seen, active}]    # for Admin → Connected apps
  bus.send(to, kind, data, ref=None, expires_in=timedelta(hours=24), reply_to=None, kv=1) -> message_id
  @bus.handler("poll.create", versions=(1,))
  def on_poll_create(msg, conn) -> dict | None | Nack: ...         # runs inside the ack transaction
  bus.on_reply(kind_or_ref_prefix, callback)                       # callback(msg, conn)
  bus.run_outbox_once()                                            # what the outbox thread runs (housekeeping/tests)
  bus.migrate(conn)                                                # the three tables, for the app's own migrations
  ```
  - `db` is the app's connection factory: `db.get_conn` (context manager) or a function returning a sqlite3
    connection. `can` defaults to the kinds with handlers. Transport defaults from the environment
    (`SUPERVISOR_TOKEN`, `SUPERVISOR_CORE_API`, `SUPERVISOR_CORE_WS`); `start(…, api_base=, ws_url=, token=,
    post=, clock=, outbox_thread=, …)` overrides them (`post=` can be the app's `ha_client.request`). Without a
    token the bus stays off (nothing is available, nothing connects).
  - `ws` (optional) is the app's own `ha_ws.HAWebSocket`: the bus subscribes on it instead of opening a second
    connection, never starts or stops it (the app does), and on `stop()` removes only its own subscription and
    on-connect callback. Household Chat uses this: one connection carries its notification buttons
    (`ha_events.py`) and the bus. Apps without a WebSocket of their own (Todo) let the bus open one.
  - `msg` has `id, from_app, to_app, kind, kv, reply_to, ref, sent, expires, data`, and for answers `answers`
    (the kind of my message it answers), `reason`/`result` (nack/ack) and `local` (made up by my own outbox).
  - A handler returns a dict (→ `ack {result}`), `None` (→ `ack {}`) or returns/raises `bus.Nack(reason,
    detail=None)` (→ `nack`; **anything the handler wrote is rolled back**). Any other exception: nothing is
    recorded or answered, so the sender re-sends.
  - `on_reply` callbacks get `ack`/`nack` of my messages, the local `nack expired` when my outbox gives up, and
    answer kinds (e.g. `poll.closed`, with `reply_to`/`ref`) that have no handler of their own. A key matches
    `msg.kind`, `msg.answers` or a prefix of `msg.ref`. They run in the transaction that records the answer;
    raising undoes it and the answer comes again.
  - `send` raises `NotAvailable` (receiver not available for the kind; skipped for answers, i.e. with
    `reply_to`), `TooLarge` (whole event over 8 KB, nothing queued) or `ValueError` (bad slug/kind/data/ref,
    reserved kinds, `expires_in` not in (0, 7 days] — longer would outlive `bus_seen`).
- **Inside:** `ha_ws.HAWebSocket` — Chat's former `ha_events.py` client (stdlib, text frames, reconnect with
  back-off, ping) generalised to several subscriptions plus on-connect callbacks; Chat's `ha_events.py` already runs
  on it for its notification buttons, so Chat can later keep one connection for both those and the bus; REST sending (`POST {api}/events/household_apps`); a small outbox
  thread (woken at once by `send`; `outbox_thread=False` to drive `run_outbox_once()` from the app's housekeeping
  instead); the three tables created on `start` (`CREATE TABLE IF NOT EXISTS`) or by the app's migrations through
  `migrate`; ULIDs; size and field checks; rate limits: at most 30 sends a minute to each app (the rest wait for
  the next outbox run), 1 `hello` answer per app per hour, and at most 60 handled messages a minute from each app
  (more → `nack busy`). `nack busy` is never stored in `bus_seen` and leaves the sender's message pending, so it
  is retried on the normal schedule. On the first connection the bus says `hello` and `who` and re-sends every
  pending message; `run_outbox_once` also says `hello` every 6 hours and prunes `bus_seen` and finished outbox rows
  after 7 days. Answers (`ack`/`nack`), `hello` and `who` are sent directly, not through the outbox.
- **Tests:** `common/tests/test_app_bus.py` (run by the repository's `tests/test_app_bus.py`, and copied with
  `fake_ha.py` and `fake_ha_bus.py` into every app that uses the bus, where it tests the app's own copies) with the
  fake HA event bus `common/tests/fake_ha_bus.py` (extends `fake_ha.py` with `POST /api/events/<type>` and a WebSocket with
  auth, `subscribe_events` and ping), where two or three in-process apps exchange messages with an injected
  clock: delivery and ack; receiver down then up (outbox re-sends on schedule, one action only); restart
  re-send; duplicates and lost acks; expiry at sender and receiver; every nack reason; unknown kind/version;
  size limit; `hello`/`who`, availability and the hello-answer limit; ack inside the action's transaction (a
  failing handler acks nothing and is retried); `on_reply` routing; send/receive rate limits; `stop()` leaves no
  threads; the WebSocket client's several subscriptions and reconnect; a bus sharing the app's own connection
  (`ws=`), including one that is already connected. Each app also tests its own kinds (handlers, allowed and
  refused cases) and runs end-to-end tests: a fake Docs app on the fake bus sending to the real app in-process.
