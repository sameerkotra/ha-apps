# Changelog

## 2.4.1

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.
- An exported website's "made on" date and the export's file name use Home Assistant's day, not the container's clock.
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 2.4.0

- **Ask the Household Assistant how two people are related**: "How is Ravi related to Sita?", "What is Lakshmi to me?" or "How am I related to Venkat?" now get the same answer as **How are they related?** — both ways, in your relationship language (for example *Menalludu (younger brother's son)*), with the chain of parents, children and marriages that links them, and a link that opens that page with the two people chosen.
- **Names don't have to be exact**: misspellings and spelling variants (Laxmi / Lakshmi, Seeta / Sita, Kiren / Kiran), part of a name ("Venkat" for Venkateswara), an initial ("Ravi P"), nicknames and other names all find the right person; "me", "my husband" or "Ravi's son" work too. When the name was taken loosely the answer says who it took; when two people share a name it lists them (with birth year and parents) so the assistant can ask which one.

## 2.3.2

- **Links open the right page**: tapping a reminder notification opens Upcoming, and so does the Household Assistant's answer. A link to `…/person/<id>` opens that person.

## 2.3.1

- The places map's Leaflet stylesheet is again exactly the released file (it had been stored with different line endings, so its checksum didn't match).

## 2.3.0

- **Answers the Household Assistant**: the new Household Assistant app can ask Family Tree "Whose birthday is coming up?" and gets what Upcoming would show that person — names, dates, ages and relationships, never photos, contacts or notes — with a link back to Family Tree. On by default; an admin can turn it off in **App settings → Household Assistant**, and each person in **Settings → Household Assistant**.
- Family Tree now joins the household apps' message bus (Home Assistant's event bus). The **DOCS** show how to keep those messages out of Home Assistant's history.

## 2.2.2

- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back, so the next Back goes to the page you came from instead of closing the app.

## 2.2.1

- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.

## 2.2.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device) — the same four in every household app. Heritage is now Midnight (a cool navy instead of warm brown; the amber accent stays) and Parchment is now Daylight; your saved choice carries over. The exported website keeps its own themes.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged.
- **Admin → People**: one card per person; the notify editor says **Send a test** and shows a result for each service.
- **How the app sees you** and the **No admin yet** banner use the same wording in every household app; the banner names the user name to add to `admin_users`.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

## 2.1.0

- **Import part of another family tree.** A relative with their own Family Tree exports the branch to share (Export → Website); you import that zip under **Admin → Import**.
  - You see what will happen before anything changes: new people and families, people already here who gain details, and new photos.
  - On a first import, someone with the same name and birth year as one person already here is matched to them; untick any match that isn't the same person.
  - **Importing only adds.** Nothing already here is changed: empty details are filled in, and missing people, events, stories, photos and parent, partner and child links are added.
  - Import a newer zip from the same tree later and only what's new comes in.
  - Anything imported before that isn't in the new zip is listed: choose **Keep here** or **Remove here** for each (people, families and photos go to the trash). Kept ones aren't asked about again unless they come back.
  - The whole import is one entry in History, so **Undo** takes it back.
- The website export now includes the tree data (`family-tree.json`) used for importing, with the same people and details as the pages. Untick **Tree data for importing** to leave it out.

## 2.0.0

First public release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- A shared family tree for everyone with a Home Assistant login: people, families (adopted, step and foster too) and life events, with approximate dates
- An interactive tree around one person or the whole family, with a phone view
- "This is me": relationship names for everyone and relationship search
- Photos and documents on the `/share` folder, with face tagging, stories, sources and custom fields
- Upcoming birthdays and anniversaries, milestones, and opt-in phone reminders through Home Assistant
- A family website export, a wall chart and a family book
- Full history with undo, a trash, and backup and restore for admins
- Optional parts an admin can switch on: a places map, Indian relationship names, names in Telugu/Hindi script, tithi dates and Indian ceremonies
