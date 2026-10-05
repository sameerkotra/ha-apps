# Docs template for the household apps

How each app's **README.md**, **DOCS.md** and **CHANGELOG.md** are laid out (`SHARED_CODE_PLAN.md`
§6.2 #20). A pattern to follow, not a file that is copied: each app writes its own text. The repository
tests (`tests/test_repo.py`) and each app's packaging tests (`common/tests/packaging_core.py`) check the
parts marked **checked**. Existing docs move to this order when they are next rewritten; nothing changes
just because this template exists.

## Writing style (all three files)

- Written for the person who installs and uses the app, not for developers: what to do, what happens,
  in the words the app's own pages use (button and page names in **bold**, as they appear).
- Short sentences, present tense, second person ("you"). No marketing words.
- Home Assistant terms as Home Assistant uses them: *app* (not add-on) in user text, *Configuration
  tab*, *sidebar*, *Companion app*, *notify service*, *person*.
- Settings by their label on the page, with the stored key in `code` where an admin may need it
  (`admin_users`, `expose_schedule_sensors`).
- No personal details anywhere: invented names (Alice, Bob, Ann), example addresses ("12 Example Way,
  Springfield"), `example.com` (**checked**: the personal-details scan).

## README.md

Shown on the app's store page in Home Assistant, before installing. One screen.

```markdown
# <App name> — Home Assistant app

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

> **<Only when needed: a limit to know before installing, e.g. "64-bit only." and why.>**

<Two to four sentences: what the app is for and who uses it. Everyone opens it from the Home Assistant
sidebar and is recognised by their Home Assistant account — no separate login. What it sends where,
in one phrase, when it sends anything outside Home Assistant (e.g. "uses OpenStreetMap services when
turned on"). Ends with: See the Documentation tab (DOCS.md) for the full guide.>

- **<Feature>** — <one line>.
- **<Feature>** — <one line>.
- **Admin** — in-app settings, users, backup and restore.
```

- **checked:** the "Unofficial app." notice and the word "Claude" within the first 600 characters;
  README.md, DOCS.md, CHANGELOG.md, icon.png, logo.png, translations/en.yaml present.
- Feature bullets: five to eight, each one line, in the order a new user meets them.

## DOCS.md

Shown on the app's Documentation tab. Sections in this order; leave out a section the app has
nothing for (an app with no settings of its own still has the Configuration-tab part).

```markdown
# <App name>

<What the app is, in one paragraph: the same promise as the README, a little longer.>

## Getting started
1. Install the app, start it and open it from the sidebar (**<panel title>**).
2. Add your Home Assistant user name to `admin_users` on the app's Configuration tab, save and restart
   the app — until then every page shows "No admin yet" with the name to add.
3. <The first things an admin sets up, in order (e.g. the AI on Admin → App settings).>
4. <What everyone else does the first time.>
**How the app sees you** (<where it is>) shows who the app thinks you are and why you are (or aren't)
an admin.

## <The app's own pages and features>
<One section per page or feature, in the order of the app's menu. What it shows, what each button
does, what happens to the data. Optional features say "(optional, off by default)" in the heading.>

## Settings
### App settings (Admin → App settings)
<One line per group of settings, then each setting: its label, what it changes, its default and range.
Say when a change applies (at once / next restart).>
### The app option (Configuration tab)
`admin_users` — <the only option: who is an admin, by Home Assistant user name or user id>.
### <Home Assistant sensors / notifications, when the app has them>
<Entity ids, states and attributes; when they are published, refreshed and removed; the switch.>

## Who can see what, and where your data goes
- <Who in the household sees what (shared vs personal, admins).>
- **Home Assistant** gets <notifications / sensors: exactly which fields, never which>.
- **<Each outside service, only when turned on>** — what is sent (and what is never sent), to which
  server by default, how often, and how to use your own server instead.
- Nothing else leaves your Home Assistant. The app has no port on your network: it is reachable only
  through Home Assistant's sidebar (ingress), and it refuses requests that don't come through it.

## Backups
- **Admin → Storage → Download backup** — what the file contains (and what it doesn't, e.g. files on
  /share).
- **Import / Restore** — replaces everything, no merge, no undo; the file is checked first; older
  backups are upgraded.
- Home Assistant's own backups of the app include <the database / which folders>.

## Limits
<Sizes, counts and retention the app enforces; "Dates and "today" follow Home Assistant's time zone.">

## Troubleshooting
- **"No admin yet" on every page** — add the user name it shows to `admin_users` on the app's
  Configuration tab, save and restart the app.
- **I'm in `admin_users` but not an admin** — check **How the app sees you**: use the user name or user
  id shown there (not the display name), and restart the app after saving.
- <The app's own common problems: symptom in bold, then what to check, in order.>
- **Something else** — the app's **Log** tab in Home Assistant (Settings → Apps → <App name> → Log).
```

## CHANGELOG.md

Shown by Home Assistant when an update is offered.

```markdown
# Changelog

## <version>

- **<What changed, in the user's words.>** <What it means for them, where to find it.>
- Fixed: <what was wrong, as the user saw it>.
- Shared code: <a change that comes from common/, when users can notice it; otherwise one line
  "Shared code: no visible change." is enough>.

## <previous version>
…
```

- **checked:** starts with `# Changelog`; the newest `## <version>` is first and equals `version:` in
  config.yaml (every release bumps the version and adds its section, never edits an older one).
- Bullets, newest version first, user-facing changes before fixes and internal ones. No dates, no
  commit hashes, no developer names. The first public release says where the app is published.
