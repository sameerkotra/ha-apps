# Household apps for Home Assistant

> ⚠️ **Unofficial apps.** These are not official Home Assistant apps (formerly called add-ons) and
> aren't affiliated with or endorsed by Home Assistant or Nabu Casa. They were built with Claude,
> Anthropic's AI model, and are provided as-is: try them out first, and keep your own backups.

Twelve apps for a household that already runs Home Assistant. Each one opens from the Home Assistant
sidebar, uses everyone's Home Assistant login (no extra accounts or passwords), and keeps its data on
your own Home Assistant.

| App | AI | What it does |
|---|---|---|
| [Family Tree](family_tree) | — | A shared family tree with photos, stories, relationship names, upcoming birthdays with phone reminders, exports and full change history. Optional parts (including Indian relationship names, Telugu/Hindi names and tithi dates) can be switched on or off. |
| [Household Chat](household_chat) | — | Private chat and file sharing: direct chats, groups, a personal room for each person, voice messages, polls, notifications with Reply on the phone. Optional **voice and video calls**, one-to-one or groups of up to four, phone to phone (at home, or away with a STUN server and a relay such as Cloudflare's), with a ringing notification and a Calls list. |
| [Household Todo](household_todo) | — | Shared and personal task lists, a calendar, a schedule of recurring things like trash day, house maintenance, and reminders through Home Assistant. |
| [Calorie Tracker](calorie_tracker) | 🤖 Optional | Food, macros, weight and goals for each person, with optional AI estimates through your own Ollama, an OpenAI-compatible service or Anthropic Claude. |
| [Finance Dashboard](finance) | 🤖 **Needed** | **64-bit only.** Bank and credit-card statements (PDF or CSV) read by the AI you choose, with categories and rules, transfers, recurring charges, a dashboard, saved reports and per-person data with optional sharing. Utility bills and toll statements are optional extras. |
| [Splitpot](splitpot) | — | Split shared expenses (equally, by amount or by percentage), see who owes whom and settle up; optional phone notifications for new charges, and balances can show up as sensors. |
| [Receipt Price Intelligence](receipt_price_intelligence) | 🤖 **Needed** | Scan receipts with your own vision model, track prices and spending, and find where to shop for less: a shopping list that knows where each item is cheapest, a trip planner, price alerts and sensors. |
| [Household Docs](household_docs) | 🤖 Optional | Notes (plain or Markdown), checklists and sheets in folders, shared with chosen people or everyone, with tags, links between documents, pins, quick notes, PDF, strong search, history and trash; templates, filing and clean-up rules, a Kids' space, and cards in Household Chat and checklists to Household Todo. Documents are plain files in Home Assistant's `/share` folder, readable and changeable by anyone with access to `/share` (Samba, the File editor, other apps, backups that include Share) — keep passwords in Household Vault. |
| [Household Vault](household_vault) | — | **Experimental.** A password manager with personal, household and shared vaults, each a standard KeePass file. It hasn't had an independent security review — keep your own KeePass copy of your passwords. |
| [Household Arcade](household_arcade) | 🤖 Optional | **Under development.** Classic games for the whole household, played from the sidebar or the phone: 42 games, from Snake and Falling Blocks to Gem Swap, Sudoku, Checkers, Ludo, Carrom and Chess. Race someone on the same game, play live on two phones (Snake Duel, Paddle Duel, Tank Battle, Carrom), or take turns over days (board games, Ludo, Chess) with your-move notifications. Personal bests, a household leaderboard, six looks, and optional time limits for children. |
| [Household Assistant](household_assistant) | 🤖 **Needed** | Ask in plain words about what the other apps know — today's tasks, this month's spending, the shopping list, a note — and get a short answer with links back to each app. Each app answers only what the person could see in it, once its admin allows it; changes are only proposed and need a tap. |
| [Household AI](household_ai) | — | **64-bit only.** An AI model on the Home Assistant machine's own CPU — no GPU, no second computer — for the apps above: download models with their size and memory in front of you, keep one loaded in the day, and share it fairly (the assistant first). The apps use it as an ordinary Ollama address. Slow on a CPU: seconds for a short answer, minutes for a receipt photo. |

**AI** — 🤖 **Needed**: the app's main job uses an AI model, so it needs one set up before it is useful (Finance
Dashboard reads statements with it; Receipt Price Intelligence reads receipt photos with a vision model; the
Household Assistant plans and writes its answers with it).
🤖 Optional: the app works fully without one, and an AI model adds extras (Calorie Tracker's estimates, Household
Arcade's extra levels, Household Docs' text read from scans, summaries and checklists made from text). — : no AI. Every app that uses AI lets you choose the model: your own Ollama on your
network, an OpenAI-compatible service, or Anthropic Claude (Receipt Price Intelligence: Ollama or an
OpenAI-compatible server). Nothing is sent to an outside service unless you choose one. With no other computer,
[Household AI](household_ai) runs the model on the Home Assistant machine itself (it gives AI to the other apps, so it
needs none: —).

### AI on Home Assistant itself: Household AI

No second computer for a model? Install **Household AI**: it runs an Ollama model on the Home Assistant machine's own
CPU (64-bit only; 8 GB of memory is enough for the small text models), and the other apps use it as an ordinary
Ollama address — its page shows the exact values to paste into each app's AI settings. Choose and download models
there, keep one loaded during the day, and let the apps share it fairly. It is slow on a CPU (seconds for a short
answer, minutes for a receipt photo), keeps everything at home, and is reachable only by apps inside Home Assistant
unless you choose otherwise. See its [documentation](household_ai/DOCS.md).

## Installing

[![Add this repository to your Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsameerkotra%2Fha-apps)

Or by hand:

1. In Home Assistant go to **Settings → Apps → Install app**, open the **⋮** menu (top right) →
   **Repositories**, paste `https://github.com/sameerkotra/ha-apps` and select **Add**.
2. The apps appear in the app store. Install the ones you want, then **Start** them on the
   **Information** tab (where **Show in sidebar** is too). The first install builds the app on your
   machine, which can take a few minutes (longer on a Raspberry Pi).
3. Open the app from the sidebar. Until someone is an admin, every page shows a **No admin yet**
   notice with your Home Assistant user name. Add that name to `admin_users` on the app's
   **Configuration** tab, save, and restart the app (Information tab → **Restart**).
4. Everything else is set up inside the app, under **🛡️ Admin → App settings**. Each app's
   **Documentation** tab is its full user guide.

**Which systems:** every app runs on 64-bit Home Assistant systems (amd64 and aarch64, e.g. a
Raspberry Pi 4 or 5 with the 64-bit Home Assistant OS). **Finance Dashboard is 64-bit only**; the
others also run on some 32-bit systems — each app's store page lists the ones it supports.

**Moving from a copy you installed as a local app?** Home Assistant treats the version from this
repository as a different app. Download a backup in the old one (Admin → Storage), install the new
one, restore the backup there, check it, then uninstall the old one. Don't run both at once: Family
Tree and Household Chat keep their files in the same `/share` folder either way.

### The Household Assistant in Assist (an integration)

The Household Assistant can also answer **Assist**, Home Assistant's voice assistant, through a small companion
integration in this repository's `custom_components/household_assistant` folder: in HACS add this repository as a
custom repository of type *Integration* and install **Household Assistant**, or copy the folder into your
configuration's `custom_components`. Then add the integration, turn on **Answer Assist** in the app's App
settings, and choose *Household Assistant* as a voice assistant's conversation agent. The app's Documentation
tab has the details.

## What they have in common

- **Home Assistant logins only.** The apps are reachable only through Home Assistant's own
  sidebar (ingress); no network port is opened. The admin list is the only option you set on the
  Configuration tab.
- **Settings live in the app** (Admin → App settings) and apply without a restart.
- **Backups:** each app's data is in Home Assistant's backups, and Admin → Storage (Household Docs and
  Receipt Price Intelligence: Admin → Backup) can download and restore a copy. Downloaded backups leave out
  access keys and passwords (enter them again after restoring on a new install). Files that live in `/share`
  (Family Tree photos, Household Chat files, Household Docs documents — the apps that map `/share`) need their
  own backup if they are outside Home Assistant's backups.
- **Where data goes:** nothing leaves your home network unless you turn on a feature that needs it
  (for example an AI provider outside your network, drive times through OpenStreetMap, or the
  Family Tree map). Each app's Documentation tab says exactly what is sent and where.
- **What's new:** each app's `CHANGELOG.md` (shown in Home Assistant's update dialog) starts at this
  first public release: 2.0.0, and 1.0.0 for Finance Dashboard, Household Arcade and Household Docs.

## Reporting a problem

Bugs and ideas: open an issue on GitHub. Security problems (especially in Household Vault): please
report them privately — see [SECURITY.md](SECURITY.md). Support is best effort.

## For developers

Each app folder has `spec/SPEC.md` (how it works), its own tests
(`python3 -m unittest discover -s tests` inside the folder, or `pytest` for Finance Dashboard, with its
`requirements-dev.txt`), and
`DOCS.md` (the user guide). `HA_ADDON_PATTERNS.md` and `WHOAMI_PAGE_SPEC.md` describe the
conventions they share; `APP_MESSAGES_SPEC.md` is how the apps talk to each other, and
`HOUSEHOLD_ASSISTANT_SPEC.md` is a draft of an assistant that answers questions from what they know. The tests in `tests/` check the repository as a whole:
`python3 -m unittest discover -s tests` from the repository root.

**Shared code.** Code the apps have in common (the ingress and admin checks, "How the app sees you",
database and backup helpers, App settings and People pages, themes, security headers, Home Assistant
client, sensors, AI client, maps, CSV export, test helpers) lives once in `common/`. Home Assistant builds
each app from its own folder, so `python tools/sync_common.py` copies these files into every app that uses
them (`app/common/`, `app/static/common/`, `tests/common_tests/`, as listed in `common/manifest.json`);
the copies are committed with the apps. Edit `common/`, never a copy, then run the script
(`--check` only reports differences; the repository tests run it). `python tools/check_build.py` checks
every app's Dockerfile and pinned requirements against `common/build/`. See `common/README.md` and
`SHARED_CODE_PLAN.md`.

## License

MIT — see [LICENSE](LICENSE).
