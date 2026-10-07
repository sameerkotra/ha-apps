# Calorie Tracker

A calorie, macro, weight and goal tracker for everyone in your household, inside Home Assistant.
Everyone who opens it from the Home Assistant sidebar gets their own food log, weights and goals,
under their own Home Assistant login — there is no separate account or password. An optional AI
helper estimates the nutrition of a food from a short description and answers nutrition questions.
The app works fully without AI.

Nothing leaves your Home Assistant unless you choose an AI provider outside your home network
(see **Setting up the AI** and **Who can see what, and where data goes**).

## Getting started

1. In Home Assistant go to **Settings → Apps → Install app**, open the **⋮** menu (top right)
   → **Repositories**, paste `https://github.com/sameerkotra/ha-apps` and click **Add**.
2. In **Settings → Apps → Install app**, find **Calorie Tracker**, click **Install**, then
   **Start** (on the **Information** tab). The first install builds the app on your machine and
   can take a few minutes. Turn on **Show in sidebar** (also on the Information tab) if you like;
   the app opens from the sidebar (**Calorie Tracker**). There is no port to open.
3. Open the app. Because nobody is an admin yet, every page shows a **No admin yet** banner with
   your Home Assistant user name in it.
4. Go back to the app's **Configuration** tab and add that user name to **admin_users**
   (for example `jane.doe`). Several people can be admins. Save, then restart the app
   (**Information** tab → **Restart**).
5. Open the app again: the banner is gone and **🛡️ Admin** appears in the menu. If you want the AI
   helper, set it up in **Admin → App settings** (next section). If not, leave the provider on
   **Not set up** — everything else works.
6. Everyone in the household can now open the app from the sidebar. Each person starts with
   default goals (2000 kcal, 150 g protein, 200 g carbs, 65 g fat) — change them under **Goals**.

Not sure what to enter in `admin_users`? Click your name at the bottom of the menu (or the 👤
button on a phone): the **How the app sees you** page shows the exact values Home Assistant sends.

## Setting up the AI

The AI is used by **✨ Estimate with AI** (Food Log and Saved Foods) and the **AI Assistant** chat.
Set it up in **🛡️ Admin → App settings → AI**. Until a provider, address and model are set, those
features show "AI isn't set up — an admin can set it up in Admin → App settings", and the rest
of the app works as normal.

Choose one **Provider**:

- **Ollama (your own computer or server)** — runs open models on your own hardware, so nothing
  leaves your home network and nothing costs money per request. Install Ollama
  (<https://ollama.com>) on a computer with enough memory, pull a model (for example
  `ollama pull llama3.1:8b`), and make it reachable from Home Assistant (on that computer set
  `OLLAMA_HOST=0.0.0.0` so it listens on the network). Then enter:
  - **Address**: the computer's address and Ollama's port, e.g. `http://192.168.1.10:11434`
    (required for Ollama). If Ollama runs on the Home Assistant machine itself, try
    `http://host.containers.internal:11434` or the machine's own network address.
  - **Model**: the model name exactly as Ollama lists it, e.g. `llama3.1:8b`.
  - No access key.
  A large model on modest hardware can take a while for the first answer; the app sends Ollama a
  short "hi" to load the model when it hasn't been used for a few minutes (**Wake up model** on
  the AI Assistant page does the same).
- **OpenAI-compatible** — OpenAI itself or any service or local server that speaks the OpenAI
  chat API: OpenRouter, Groq, LM Studio, vLLM, and others.
  - **Address**: leave blank for OpenAI (`https://api.openai.com/v1`). Otherwise the service's
    API base, e.g. `https://openrouter.ai/api/v1`, or for **LM Studio** on your own computer
    `http://192.168.1.10:1234/v1` (start LM Studio's local server and allow network access).
  - **Model**: e.g. `gpt-4o-mini` for OpenAI, or the name your service or LM Studio shows.
  - **Access key**: from your OpenAI account (platform.openai.com → API keys) or the service's
    own dashboard. Local servers such as LM Studio need none — leave it empty.
- **Anthropic Claude** — Anthropic's API.
  - **Address**: leave blank (`https://api.anthropic.com`).
  - **Model**: e.g. `claude-sonnet-4-5` or a smaller, cheaper Claude model.
  - **Access key**: required; create one in the Anthropic Console (console.anthropic.com →
    API keys).
  - **Longest answer (tokens)**: the most Claude may write in one answer (default 4096). The app
    lowers it automatically if a model allows less. Other providers ignore it.

**Test connection** checks what is typed on the page before you save: it asks the provider which
models it offers and says whether your model is among them. It generates nothing, so it is free.
An empty key box means "use the saved key". Click **Save settings** when it looks right; the next
AI request uses the new settings — no restart.

**The access key** is stored only in the app's database. The page never shows it again (only
"Saved (…last 4 characters)"); leave the box empty to keep it, type a new one to replace it, or
select **Remove** next to the box and save. It is sent only to the provider, in a request header — never
in an address, a log line, an error message or a page — and it is left out of database downloads.

**Privacy.** What the AI receives is only what you type for it: the food description you estimate
("✨ Estimate with AI") and the questions you ask on AI Assistant. Your food log, weights, goals and
names are never sent. When the configured address is outside your home network (anything other
than a private address such as `192.168.x.x` or `10.x.x.x`, `localhost`, or a local name such as
`something.local` or `something.lan`), the app shows everyone a notice on the pages that use AI
and on App settings, naming the service's host. That service's own privacy and data-retention
rules then apply.

**Cost.** Ollama and LM Studio cost nothing per request. Cloud services charge per token (a few
hundred tokens per estimate or short chat answer); check your provider's price list and consider
setting a spending limit in its dashboard. The AI Assistant page shows the tokens and time of the
last AI request, and every request is written to the app's **Log** tab with its token counts.

## What the app does

### Food Log (the start page)
- A **date bar** at the top: previous / next day, a date picker and **Today**. Everything on the
  Food Log follows the chosen day. "Today" follows Home Assistant's time zone, not the device's.
- **Add Food**: meal (breakfast, lunch, dinner, snack), food name, servings, calories, protein,
  carbs and fat. **Pick a saved food** fills the form in one click.
- **✨ Estimate with AI**: type a description in the food name box (or leave it empty to be
  asked) and the AI fills in calories and macros, with a short note about its assumptions.
  Always review the numbers before adding.
- **Log**: the day's entries as a table; ✕ deletes one.
- **Day Summary**: calories, protein, carbs and fat against your goals, plus your latest weight,
  and **Macros** progress bars.

### Saved Foods
- Build foods you eat often ("Protein shake", "Chicken and rice bowl") with serving size and
  unit, calories, macros and optional notes (recipe, brand, preparation). ✨ Estimate with AI
  works here too and also fills in the serving size and unit.
- Each saved food can be **＋ logged** in one click (as a snack, on the date shown in the date
  bar), edited (✎) or deleted (✕). Long notes open in a popup (⤢).

### Weight
- Log your weight (kg) for any date, with an optional note; delete entries you don't want.
- **Weight History** chart: daily (last 30 days), weekly (12 weeks) or monthly (12 months)
  averages, with a dashed line at your target weight when one is set.

### Goals
- Daily calorie, protein, carbohydrate and fat goals, plus starting and target weight.

### Dashboard
- **Summary**: a calorie chart against your goal — daily (last 14 days), weekly (8 weeks) or
  monthly (6 months) averages; bars above the goal turn red. Hover or use the arrow keys for
  exact values.
- **Monthly Breakdown**: for any month, the average calories and macros per day against your
  goals, and your average weight and change over the month.

### AI Assistant
- Ask nutrition and fitness questions in a simple chat. Each question is answered on its own
  (the AI doesn't see your log or earlier questions).
- Shows which provider and model are in use, the last error if any, and the tokens and time of the
  last request. With Ollama, **Wake up model** loads the model in advance.

### Admin (admins only)
- **App settings**: the AI setup (above), **Publish daily calories to Home Assistant** and **Answer the Household Assistant**.
  Changes apply immediately.
- **Users**: everyone who has opened the app. A switch hides someone from the user switcher (an
  old account, a guest); it doesn't delete their data or lock them out.
- **Storage**: download the whole database as a backup, or restore one (see **Backups**).
- **User switcher** (top right, admins only): view and edit another household member's log,
  saved foods, weight and goals — for example to help a child or partner.

### How the app sees you
Click your name at the bottom of the menu (👤 on a phone) to see the user name and user id Home
Assistant sends, whether you are an admin, and what to change if you expected to be one.

### Look and feel
Four themes at the bottom of the menu: Midnight (the default), Slate, Daylight and Auto (Daylight
when your device is set to light, Midnight when it is dark); the menu collapses to icons.
On a phone the menu becomes a bar at the bottom. In the Home Assistant app, the Back gesture
closes a popup first, then returns to the Food Log.

## Home Assistant sensor

While **Publish daily calories to Home Assistant** is on (the default), each person who has used
the app gets a sensor such as `sensor.calorie_tracker_jane_doe_daily_calories` whose state is
today's total calories (kcal) — nothing else. Use it in dashboards or automations, e.g. a
notification when someone is over their goal in the evening. The name comes from the person's
Home Assistant display name. The value updates whenever that day's log changes and every 15
minutes, so it returns to 0 after midnight. Turning the setting off removes these sensors from
Home Assistant; turning it on publishes everyone's again at once.

## The Household Assistant

If the household also uses the **Household Assistant** app, you can ask it "How many calories do I
have left today?". Calorie Tracker tells it **only your own day** — calories, protein, carbs and fat
against your goals, and what you logged at each meal — never anyone else's, and never your weight.
Its answer links back to that day in Calorie Tracker. An admin can turn this off for everyone with **Answer
the Household Assistant** in App settings, and you can turn it off for yourself on **Goals → Let the
Household Assistant answer for me**.

The answers travel through Home Assistant's event bus, which Home Assistant's recorder keeps in its
history unless told not to. Add this to Home Assistant's `configuration.yaml` and restart Home
Assistant:

```yaml
recorder:
  exclude:
    event_types:
      - household_apps
```

## App settings

All in **🛡️ Admin → App settings**; changes apply immediately, no restart. Each group of settings
is a card, with a line under each setting saying what it does, its range and its default. Your
changes are kept until you select **Save settings** at the bottom (it shows how many unsaved changes
there are); **Discard changes** puts everything back. A number out of range is flagged at the field
before anything is saved.

| Setting | Default | What it does |
|---|---|---|
| Provider | Not set up | Ollama, OpenAI-compatible or Anthropic Claude; "Not set up" turns the AI features off |
| Address | blank | The provider's address. Required for Ollama; blank = `https://api.openai.com/v1` (OpenAI-compatible) or `https://api.anthropic.com` (Claude) |
| Model | blank | The model's exact name |
| Access key | none | Needed for Claude and most cloud services; never shown again, left out of backups |
| Longest answer (tokens) | 4096 | Output limit per answer (Claude requires one; others ignore it) |
| Publish daily calories to Home Assistant | on | The per-person daily-calories sensor |
| Answer the Household Assistant | on | Lets the Household Assistant app tell each person their own day (see *The Household Assistant*) |

## Configuration (the app's Configuration tab)

- **admin_users** — Home Assistant user names (login names) or user ids of the app's admins.
  Display names are not accepted: they aren't unique, so anyone could pick one. Empty by default,
  which shows the **No admin yet** banner. The list is read when the app starts: restart the app
  (**Information** tab → **Restart**) after changing it.

Everything else is set inside the app.

## Who can see what, and where data goes

- Everyone sees and changes only their own data. Admins can switch to another person with the
  user switcher; this is checked by the app on every request, not just hidden in the page.
- The app is reached only through Home Assistant (ingress). It has no port of its own, and it
  refuses requests that don't come from Home Assistant's own proxy.
- To Home Assistant it sends only each person's calorie total for today (the sensor), and only
  while that setting is on — never macros, weights or food names.
- To the AI provider it sends only the food descriptions you estimate and the chat questions you
  ask, and only when you use those features. With a provider outside your home network, a notice
  on the AI pages and App settings says so.
- The database is plain SQLite in the app's own storage (`/data/calorie.db`); it is not
  encrypted, so anyone with access to your Home Assistant files or backups can read it.
- Home Assistant decides who is an admin of Home Assistant; `admin_users` is this app's own list.
  Someone removed from Home Assistant's administrators stays an app admin until you edit the list.

## Backups

- Home Assistant's own backups include the app and its data.
- **Admin → Storage → Download database** saves a complete copy of the app's data (every
  person's) as a `.db` file. The AI access key is left out of it.
- **Import database** replaces **all** current data with a downloaded file — there is no merge
  and no undo, so use it only to recover (e.g. after reinstalling). The file is checked before
  anything is replaced, older backups are brought up to date automatically, and the App settings
  in the file take effect at once — except the AI access key: the install keeps its own.
- Backups leave out access keys and passwords; after restoring on a new install, enter them again.

## Limits

- One Home Assistant user is one person; there are no separate profiles inside the app.
- The AI's numbers are estimates — check them, especially for mixed dishes and restaurant food.
- The chat has no memory of earlier questions and no access to your log.
- There is no rate limit on AI requests; with a paid provider, set a spending limit there.
- The sensor name follows the display name: renaming someone in Home Assistant creates a new
  sensor, and two people with the same display name share one.
- Weights are in kilograms.
- The time zone is read from Home Assistant when the app starts (UTC if it can't be read);
  restart the app after changing Home Assistant's time zone.

## Troubleshooting

- **"No admin yet" won't go away** — the user name in `admin_users` must match exactly what
  **How the app sees you** shows (upper/lower case doesn't matter), and the app must be
  restarted after saving. If Home Assistant doesn't send a user name for you, use your user id.
- **"Can't identify a Home Assistant user"** — open the app from the Home Assistant sidebar, not
  by a direct address.
- **"AI isn't set up"** — an admin chooses a provider and enters the address, model and (for
  Claude and most cloud services) the access key in Admin → App settings.
- **"Couldn't reach Ollama at …"** — check the address and port, that Ollama is running, and that
  it listens on the network (`OLLAMA_HOST=0.0.0.0`), not only on its own computer.
- **"… refused the access key"** — the key is wrong, expired, or has no credit; paste a new one.
- **"… answered 404"** — usually a wrong model name or a wrong address (OpenAI-compatible
  addresses normally end in `/v1`). **Test connection** lists the models the provider offers.
- **"… is rate-limiting requests" / "overloaded"** — the app already retried a few times; wait a
  minute and try again, or check your plan's limits.
- **The first AI answer is slow with Ollama** — the model is being loaded; later answers are
  faster. **Wake up model** loads it ahead of time.
- **The daily-calories sensor doesn't appear** — check that the setting is on, and look in the
  app's **Log** tab for messages about Home Assistant.
