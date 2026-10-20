# Changelog

## 2.6.3

- **Back goes to the page you came from.** The phone's back gesture (and the browser's Back) now steps back through the pages you opened in the app, instead of jumping straight to the start page; dialogs still close first, and Back on the start page still leaves the app.

## 2.6.2

- **Emptying the trash works again**: the daily clean-up (and **Empty trash**) failed as a whole when someone in the trash had told a source's story, had other people merged into them, or a trashed photo was a source's scan. Those links are now cleared first, each item is removed on its own (one that still can't be is left in the trash and logged, the rest go), and deleted sources show in **Admin → Trash** and are cleaned up too.
- **Security**: only admins can send a backup to **Restore** — anyone else is turned away before the upload is read, and the upload limit (*Largest upload* + 1 MB) now also counts uploads sent without a size up front.
- **Security**: place names and people's names on the map's labels are shown as text, never read as page code.
- **Security**: photos are checked before they're decoded: at most 40 million pixels (60 million for a JPEG, which is decoded at a reduced size) and at most two processed at a time, so a crafted or huge picture can't use up the memory. The inbox looks at a file's size before reading it.
- **Security**: kids mode is held by a cookie the page can't read or remove, so leaving out the device header no longer gets round it; while it's on, a change that doesn't say which device it comes from is refused for that login.
- **Security**: someone marked *Keep out of all exports* is left out of the Household Assistant's answers too, and only an admin can take that mark off. A contact's label can't break the lines of a downloaded vCard; unanswered quiz questions are capped at 50 per person; only places the tree uses can be pinned on the map; long GEDCOM notes are read in one pass and huge files are refused early (GEDCOM up to 50 MB, tree data up to 64 MB).
- **Optional App-messages signing key** (App settings → Household Assistant): the same key in every household app makes them refuse messages that aren't signed with it. Off until you set one; backups never contain it.
- **Big imports are much faster**: the import is read and planned in the background, the family tree isn't reloaded for every child added, and photos are processed before the database is locked; a broken data file is a clear message instead of an error.
- **Faster on big trees**: relationship names for everyone (search by "babai", the Everyone chart, exports, the quiz) reuse their work; the photo gallery reads only the page it shows (also fixing very large galleries); the Export preview no longer works out labels, contacts or photo fixes just to count; the duplicate finder keeps its results until a name, date or family link changes, and its groups are finer so big families aren't cut short; tithi dates are worked out in memory instead of written while a page is read; exports no longer make every cached answer stale; the Admin → Storage file count is reused for ten minutes.
- **The chart loads photos as you move to them**: only the cards on screen (and near it) fetch their photo, none while zoomed far out; printing loads them all first. A face cut from a tagged photo is made once and kept. Maps are closed when you leave the page.
- **Uploading the same file again** gives back the photo or document already there (linked as you asked) instead of a second copy, as the inbox already did.
- **Colours kept**: a photo with its own colour profile (Display P3, Adobe RGB) is converted to sRGB when it's stored, so it no longer looks washed out.
- **Fixes**: a network error while looking up a place no longer counts as one of its three tries; a crop pushed against the edge can't leave an empty picture, and a photo whose fixes can't be applied says so; two photo fixes saved at once can't mix their files; people with the same name get their own pages in an exported website; tithi and milestone entries hide script names while that feature is off; a restored backup's photos are moved into place only after the database is swapped; saving an edit that changes nothing adds no History entry.
- **New App setting *Keep history for*** (default 0 = keep everything, as before): History older than that many days is removed by the daily clean-up.
- Under the hood: new indexes and a stored sort key for photo dates are added to the database at the first start (nothing to do); abandoned backup downloads are swept after six hours; the base image is pinned to one Alpine release.
- **Shared code**: an optional message signing key for the household apps; API answers are never stored by the browser, while the app's own files (which carry the version) are cached for good, so pages load faster after an update's first visit; uploads without a stated size are counted; database writes need one disk sync fewer.

## 2.6.1

- **Safer import of another tree's website zip**: each photo in the zip is now checked against the upload limit (Admin → App settings) before it's unpacked, so a file that would unpack to something huge is skipped with a note instead of filling the memory.
- **Shared code**: the Household Assistant tools answer only calls that come from the Household Assistant itself; pages no longer use inline event handlers; saving App settings is safer, and backup restores say clearly when a file is too large.

## 2.6.0

- **GEDCOM**: Export → **⬇ GEDCOM file** gives the same people, with the same choices as the website (living people, places, stories …), as a `.ged` file other genealogy programs open (GEDCOM 5.5.1, no photos). Admin → Import now also takes a `.ged` (or `.gdz`) from another program: names (with maiden and other names), sex, births, deaths, marriages and other events with dates and places, notes, parents and children (adopted, step, foster) come in after the usual preview, as one step History can undo; importing a newer copy of the same file later only adds what's new.
- **Relationship words further out**: someone three or more marriages away ("husband's brother's wife's father") is now named step by step in English, and gets a Telugu or Hindi word built from its parts, instead of just "related by marriage".
- **Undo puts "This is me" back**: undoing a merge that had moved someone's "This is me" to the kept person now moves it back too.

## 2.5.0

- **Ask the Household Assistant about one person**: "How old is Lakshmi?", "Who are Ravi's children?", "When did Sita get married?" — born (when, where, age), died, parents, partners with the wedding date and years married, children, brothers and sisters, and what they are to you. Names needn't be exact, as for "How are they related?".

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
