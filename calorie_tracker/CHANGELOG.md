# Changelog

## 2.5.0

- **One person's data — export, import, delete**: Admin → Users → **⋯ Data** on each person.
  - **Export their data** downloads one .zip with their food log, symptoms, weights, saved foods and goals (health
    data, not encrypted — keep it safe).
  - **Import a person's data…** (above the list, or **Import into this person**) checks the file, shows what's in it,
    and adds it to anyone here — **Add to what they have** (entries already there are skipped) or **Replace what they
    have** (type their name).
  - **Delete their data…** deletes it for good, after typing their name: keep them in the list, empty, or forget them
    too. Their daily-calories sensor is removed from Home Assistant. A copy is downloaded first unless you untick it.

## 2.4.2

- **Goals is just goals**: the "Publish my calories to Home Assistant" and "Let the Household Assistant answer for me"
  switches are gone from it. The household's switches in Admin → App settings decide for everyone: **Publish daily
  calories to Home Assistant** (everyone's sensor while it's on) and **Answer the Household Assistant**.

## 2.4.1

- **Tags are a dropdown**: open **🏷️ Tags**, tick the ones that fit, or add your own at the bottom.
- **Write out the ingredients**: the food box is bigger and takes up to 1000 characters, so you can list everything
  in a dish. Patterns counts each ingredient on its own (so ghee or onion can stand out across different dishes); a
  long list shows its first lines in the log — tap it for the rest. ✨ Estimate with AI no longer replaces what you
  typed.

## 2.4.0

**New**
- **Symptoms**: a new page to log what you feel — pick or type a symptom, the time, how bad (1–5) and a note — with
  the day's food and symptoms side by side in time order. Only you see your symptoms.
- **Patterns**: for each symptom, how often and how bad, and which foods and food tags often came in the hours
  before it (compared with days without it). It shows what came together, not what causes it. **⬇ Food & symptoms
  (CSV)** exports the lot, for a doctor or a spreadsheet.
- **Calories are optional**: log a food without them (a food diary without counting). Such entries show "—" and the
  Day Summary says how many weren't counted. Saved foods can be saved without calories too.
- **Food tags** (dairy, gluten, spicy, caffeine… or your own) on food entries and saved foods; a saved food's tags
  come with it.
- **When you ate it**: the Food Log keeps the time (now, or the time you pick; on another day, the meal's usual time).
- **A shorter menu**: Food Log, Symptoms, Dashboard and Weight first; Saved Foods, Goals, AI Assistant and Admin below
  a line — on a phone the bottom bar is the first four and **⋯ More** (it no longer scrolls sideways).
- App settings → **Symptoms and patterns**: the symptoms and tags to pick from, how many hours Patterns looks back,
  and how often a symptom must be logged before it's compared.

## 2.3.2

**Security**
- **AI limits** (Admin → App settings → AI limits): each person can ask for 20 AI estimates or chat messages a minute
  and 200 a day (0 = no limit), and at most two AI requests run at a time for the whole household; past a limit the
  page says so plainly. The longest answer is now 2000 tokens by default, for every provider (an admin can raise it).
- **The access key stays with its address**: changing the AI address or provider without typing the key again clears
  the saved key, and **Test connection** asks for the key again instead of sending the saved one to the new address.
- **AI details for admins only**: the AI's address, model, last error and last request are shown to admins; everyone
  else sees whether it is set up, which provider, and the privacy notice. Errors no longer show the server's address to
  people who aren't admins.
- **Your calories sensor is your choice**: **Goals → Publish my calories to Home Assistant**. Everyone who used the app
  before keeps it as it was (on while the household switch was on); people new from now on start with it off. The
  household switch in App settings stays the master switch.
- An AI estimate is checked like a food entry before the page sees it (no negative, huge or missing calories).
- A food entry can only point to one of the person's own saved foods.
- Changes an admin makes while acting as someone else are written to the app's log.
- Requests are capped at 1 MB (a restore at 512 MB), counted even without a Content-Length.
- The app's image is pinned to one Alpine release (`python:3.12-alpine3.22`, like the other household apps).

**Fixed**
- **Errors are shown**: a refused entry, goal, weight or saved food (for example a number out of range) now says what is
  wrong instead of doing nothing; the form fields have the same limits as the server.
- **"+ log" from a saved food** logs it on the day you're looking at and says which day, and the Food Log list updates.
- A month that doesn't exist on the Dashboard answers a clear error instead of a server error.
- Names of only spaces are refused; entries made in the same second keep their order; the Household Assistant
  recognises foods with non-English letters ("Crème brûlée") whatever their case.
- Deleting an entry asks first, and the calorie chart updates.
- The calories sensor goes back to 0 just after midnight instead of up to 15 minutes later, and is published again as
  soon as the app starts.

**Changed**
- **Faster pages**: an ordinary page load no longer writes to the database, the app's database work no longer holds up
  other requests, and the sensor is updated after the answer is sent. Restoring a backup waits for everything else
  using the database and holds new requests for those few seconds.
- The Weight page loads your newest 100 weigh-ins, with **Show all** for the rest; month summaries and the calorie chart
  are added up by the database.
- Picking a different day no longer reloads your saved foods; adding food refreshes the page's parts together; the
  charts redraw when the window is resized.
- **Shared code**: an optional **App-messages signing key** (Admin → App settings → Household Assistant; the same key
  in every household app) signs the messages between the apps; API answers are never cached by the browser, while the
  page's scripts and styles are cached until the next version, so it loads faster; database writes are lighter.

## 2.3.1

- **Servings count**: on the Food Log, the calories, protein, carbs and fat in **Add Food** are for one serving, and the entry adds them up for the servings you type — picking a saved food and typing 2 servings now logs twice its calories (before, it logged one serving's calories marked as 2 servings, and the Household Assistant then counted that food at half its calories). Entries you already added are unchanged.
- **Latest Weight on a past day**: the Food Log of an earlier day shows your weight as of that day, not a later weigh-in; with two on one day, the last one entered.
- **One calories sensor per person**: people whose names give the same sensor name ("Asha K" and "Asha-K", or names without Latin letters) shared one sensor in Home Assistant, each overwriting the other. Each now gets their own (the later ones with a short ending); everyone else's sensor keeps its name.
- **Checks on what you enter**: calories, macros, servings, goals and weights can't be negative or impossibly large, the meal is one of the four, and names have a sensible length. Before, a value like "1e309" was saved as infinity and the Food Log and Dashboard of that day stopped loading.
- **Shared code**: the Household Assistant tools answer only calls that come from the Household Assistant itself; the AI connection no longer follows redirects; pages no longer use inline event handlers; saving App settings is safer, and backup restores say clearly when a file is too large.

## 2.3.0

- **More for the Household Assistant**: "How did I do this week?" gives your last 7 days against your goal, and "I had a banana" logs it — only after you tap the exact entry it proposes, with the calories on the button (the assistant's estimate, or for a food you saved or logged before, its own calories times the servings).

## 2.2.4

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 2.2.3

- **Shared AI code**: the AI connection can offer tools to a model in its own way (used by the Household Assistant); nothing changes in this app.

## 2.2.2

- **Your own Household Assistant switch**: **Goals → Let the Household Assistant answer for me** (on) — turn it off and the Household Assistant gets nothing about your day, whatever the admin's setting.

## 2.2.1

- **Links open the right page**: the Household Assistant's answer opens that day's Food Log. Links ending in `/foodlog/<date>`, `/dashboard`, `/weight` or `/goals` open that page.

## 2.2.0

- **Answers the Household Assistant**: the new Household Assistant app can ask Calorie Tracker "How many calories do I have left today?" and gets only that person's own day — calories and macros against their goals, and what they logged at each meal; never anyone else's, never weight — with a link back to the app. On by default; an admin can turn it off with **Answer the Household Assistant** in App settings.
- Calorie Tracker now joins the household apps' message bus (Home Assistant's event bus). The **DOCS** show how to keep those messages out of Home Assistant's history.

## 2.1.2

- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back, so the next Back goes to the page you came from instead of closing the app.

## 2.1.1

- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.
- Backups still leave out the AI access key; restoring keeps the key this install has (now the same shared code in every app that has access keys).

## 2.1.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device) — the same four in every household app. Your saved choice carries over.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged.
- **Admin → People**: one card per person; the notify editor says **Send a test** and shows a result for each service.
- **How the app sees you** and the **No admin yet** banner use the same wording in every household app; the banner names the user name to add to `admin_users`.
- **Security**: pages now send a Content-Security-Policy and `nosniff` header; scripts moved out of the pages into files. No visible change.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

## 2.0.0

First public release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- Food log by meal for everyone in the household, each under their own Home Assistant login, with a day summary and macro bars against personal goals
- Saved foods for one-click logging
- Weight log with daily, weekly and monthly charts and a target-weight line
- Dashboard with a calorie history chart and a monthly breakdown of calories, macros and weight
- Optional AI calorie and macro estimates and a nutrition chat through your own Ollama, an OpenAI-compatible service or Anthropic Claude; everything else works without AI
- A daily-calories sensor per person in Home Assistant (can be switched off)
- Admin page: App settings, users, and database backup and restore
- Reached only through Home Assistant (ingress): no ports, no extra logins
