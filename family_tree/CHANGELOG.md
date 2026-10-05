# Changelog

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
