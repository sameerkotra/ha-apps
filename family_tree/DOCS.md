# Family Tree

A shared family tree for the household, inside Home Assistant. Everyone with a
Home Assistant login can view and edit it; every change is recorded with who
made it and when, and can be undone. There's no separate login: Family Tree
uses your Home Assistant user.

What it does, in short: people, families and life events; an interactive tree
around one person or the whole family; "This is me" with relationship names;
photos, documents, face tagging and stories; upcoming birthdays and
anniversaries with opt-in phone reminders; a family website export, a wall
chart and a family book; full history with undo; and backup and restore.
Several parts are optional and can be switched on or off by an admin (see
**Features you can turn on or off**).

## Getting started

1. In Home Assistant go to **Settings → Apps → Install app**, open the
   **⋮** menu (top right) → **Repositories**, paste
   `https://github.com/sameerkotra/ha-apps` and select **Add**.
2. In **Settings → Apps → Install app**, find **Family Tree**, **Install** it
   (the first install builds the app and takes a few minutes), then **Start**
   it on the **Information** tab. Turn on **Show in sidebar** there if you
   like.
3. Open **Family Tree** from the sidebar. While nobody is an admin yet, every
   page shows a banner: *"No admin yet — add your Home Assistant user name
   (**your name**) to `admin_users` …"*. The banner shows your exact user
   name; the **How the app sees you** page (your name at the bottom of the
   sidebar, 👤 **You** on a phone) shows it too, with your user id.
4. In the app's **Configuration** tab add that user name under
   **Admins** (`admin_users`), **Save**, and restart the app (**Information**
   tab → **Restart**). The
   banner goes away and you get the **🛡️ Admin** item in the sidebar. Nobody
   is ever made admin automatically.
5. As admin, open **Admin → App settings**: check the photo folder, and in
   **Features** switch on the optional parts your family wants (for example
   the places map, or Indian relationship names).
6. Back in the tree, add yourself on the welcome screen and tick **This is
   me**. The tree then opens centred on you and names everyone's relationship
   to you. Add relatives from the dashed **+ Add father / mother / partner /
   child** cards, or from anyone's page. **+ Family** adds a couple, their
   marriage and all their children in one form.

Everyone else in the household can start straight away: each person just
opens Family Tree and, in **Settings → This is me**, picks their own person.

## The tree

Tap a card to open that person; double-tap to centre the tree on them. Drag,
pinch or use the mouse wheel to move and zoom; **Me** jumps back to you. Above
the tree, choose:

- **Chart** — one person with their ancestors above (4 generations by
  default, up to 10) and descendants below (3 by default, up to 10). Tick
  **Siblings** to show brothers and sisters.
- **Everyone** — the whole family at once, one row per generation (up to 3 000
  people). Use *Find someone* to jump to a person; double-tap a card for their
  close-up chart. Families that aren't connected are shown side by side.
- **Cards** — a simple view (parents, the person, partners and children), the
  default on phones.

🖨 prints the chart or saves it as a PDF (always in the light Daylight theme).

**Father's and mother's side** (a switch in Features): tick **Colour by side**
to outline your father's side in one colour and your mother's side in
another; relatives on both sides (after a cousin marriage) are striped. **Both
sides / Father's side / Mother's side** fades the other side, which helps with
big trees. Your own line (you, your brothers and sisters, your children) is
never faded. Sides are worked out from "This is me" — on the Chart, from the
person in the middle if "This is me" isn't set. **People** and **Relatives**
can be filtered by side too.

## People and families

- **People** lists everyone, with search (names, maiden names, nicknames, and
  relationship words like "cousin"), filters (living or deceased, surname, has
  a photo, reminders on or off, side, a custom field) and sorting (surname,
  birth date, recently changed).
- A **person's page** has a header (photo, names, dates, age, "your first
  cousin" and **How are we related?**), and tabs: **Overview** (details, life
  events, parents, partners and children, siblings, biography), **Timeline**
  (their events in date order, plus relatives' births and deaths during their
  life), **Photos & documents**, **Stories** and **History**.
- **✎ Edit** changes names (birth/maiden surname, nickname, other names such as
  a married or religious name), gender, birth and death, the biography and, under
  *More details*, the name order, **Keep out of all exports** and **Send
  reminders for this person**.
- **Families** are two partners (or one) and their children. Each child is a
  birth, adopted, step or foster child. **Edit family** changes the kind
  (married, partners), whether it ended (divorced, separated, widowed), the
  marriage date and place, and the children's birth order (↑ ↓).
- **Life events**: birth, death, burial, baptism or naming, education,
  occupation, residence, immigration, emigration, military service,
  religious events, retirement and "other" (with your own title); for couples,
  marriage, engagement, divorce, residence and other events. With **Indian
  ceremonies** switched on, ceremony types are added (see Features).
- **Warnings** appear on a person's page (never blocking a save): a child born
  before a parent, a parent younger than 12 at a birth, death before birth, or
  someone over 110 not marked as deceased. An edit that would make someone
  their own ancestor is refused.
- **Name order**: first name first ("Asha Sharma") or surname first ("Sharma
  Asha"), for the household (App settings) or for one person (Edit → More
  details).
- A person is **living** unless they have a death date, are marked deceased,
  or were born more than 110 years ago. Ages show as "age 42" for the living
  and "aged 87" at death.

## Dates

Every date has **Day**, **Month** and **Year** boxes plus **About / Before /
After / Between**. The year is optional: a day and month alone means "the
birthday is known but not the year" — it shows as "12 March" and the age as
unknown, and reminders still arrive without the age. You can also type a whole
date into the Day box (`12/03/1950`, `1950-03-12`, `12 Mar 1950`); Settings →
Display chooses whether `12/03` means 12 March or 3 December.

Birth and death also have an optional **Time** (the local time where it
happened, shown in your own clock style). It needs the full, exact date.

## "This is me" and relationships

- Link yourself to your person in **Settings → This is me** (or **This is me**
  on your page). Each person can be claimed by one Home Assistant user.
- Everyone is then named by their relationship to you: parent, grandparent
  (great-×n), sibling and half-sibling, aunt/uncle, niece/nephew, cousins
  ("second cousin once removed"), in-laws, and relatives two marriages away
  ("wife's sister's husband"). Adopted, step and foster links say so.
- **How are we related?** shows the chain of people between you.
- **More → How are they related?** (or **🔗 Related to someone else?** on any
  page) shows what any two people are to each other, both ways, with the chain.
  The address (`#/relate/…`) can be bookmarked.
- **People → Relatives** finds people by relationship: pick a relationship
  (first cousins, uncles and aunts, descendants, in-laws…) of anyone (you if
  empty) and narrow it by side, living, gender or generation — or type "first
  cousins of me", "descendants of Grandpa", "everyone on my mother's side" and
  press Enter. **Export these people** opens Export with exactly that list.

## Photos, documents and stories

- **Photos** in the sidebar shows everything; each person has a **Photos &
  documents** tab. Upload several files at once with **Upload**, or drag them
  onto the page. JPEG, PNG, WebP and GIF photos and PDF documents are accepted
  (up to the *Largest upload* App setting each, 20 MB by default). The type is
  checked from the file itself; HEIC photos are refused with a hint (phones
  convert to JPEG when you share or pick them).
- Photos are re-saved on upload, which removes location (GPS) and camera
  details; the date taken is kept.
- Open a photo to add a title, date and notes, **tag the people in it**, and
  use ☆ to make it someone's profile photo. Arrow keys move between photos. The
  camera button on a person's photo uploads a new profile photo or picks one
  of their photos.
- **Tagging faces** (a switch in Features): open a photo and choose **▢ Tag a
  face**, drag a box around someone and type their name. Hover or tap a box to
  see who it is; select one to **Open** their page, **★ Use as profile photo**
  (their picture becomes that face; the photo itself isn't changed) or **Remove
  box**.
- **Photo fixes** (a switch in Features): **🛠 Fix photo** rotates,
  straightens (±15°), crops and auto-contrasts faded scans. The original is
  kept untouched, face boxes stay on the faces, and **Revert to original**
  undoes it.
- **Stories** on each person's page hold longer memories. They're in History
  and can be undone.
- Deleting a photo or document moves it to the trash (Undo it from the message
  or from History).

## Upcoming

Birthdays and wedding anniversaries coming up (the next 30 days to 12
months), with ages and years when the year is known — plus 🎉 milestones and
🪔 tithi days when those are switched on. **Close family** (people within three
steps of "This is me") or **Everyone**; tick **Remembrance days** to include
the birthdays and death dates of those who have died. A birthday needs at least
a day and a month. 29 February birthdays show on 28 February in other years.
With reminders on, each entry has a bell: 🔔 means that person is included in
reminders; tap it to switch.

## Reminders

A short daily message to your phone through Home Assistant: "🎂 Lakshmi (your
mother) turns 60 today", "💍 Ravi & Priya — 25th anniversary on Fri 3 Oct".
Two switches must be on, and both start **off**:

1. **The person's 🔔 switch**, shared by the household: if Lakshmi's is on,
   her birthday is in the reminders of everyone who has reminders on. Change
   it with **🔔 Reminders on / 🔕 Reminders off** on her page, in Edit, or with
   the bell in Upcoming; it's an ordinary change in History. New people start
   with it off. A couple's anniversary is sent when either partner's 🔔 is on.
2. **Your own reminders** in **Settings → Reminders**: tick **Send me
   reminders** and choose the **time** (08:00 by default, in Home Assistant's
   time zone; the message arrives within that hour), **how many days ahead**
   (0–14), **which days** (birthdays, anniversaries, remembrance days, and
   milestones and tithi days when those are on) and **about whom** (everyone
   with 🔔 on, or only your close family). **Send me a test** checks it reaches
   you. **Turn on reminders for my close family** switches 🔔 on for everyone
   within three steps of you in one undoable change.

Messages contain names, relationships and ages only — never places, notes or
stories. Nothing is sent about people in the trash, and a user whose access
is turned off gets nothing.

**Your phone comes from Home Assistant.** In Home Assistant, **Settings →
People → (the person)**: **Allow person to login** links the person to their
login, and **Track device** picks their phone (with the Home Assistant
Companion app). Family Tree picks the phone up within 5 minutes (admins:
**Check Home Assistant again** in Admin → Users) and sends to its
`notify.mobile_app_…` action. An admin can add *extra* notify services for
someone (a speaker, a second service) under **Also** in Admin → Users.

## Milestones

🎉 Special birthdays and wedding anniversaries: by default the 1st, 60th, 70th
and 80th birthdays and the 25th, 50th and 60th anniversaries, for the living
and when the year is known. They're marked in Upcoming and, in reminders,
come 3 months, 1 month and 1 week ahead and on the day. Admins can switch any
off, remove them or add more (a birthday age, an anniversary, or — with the
tithi switch on — a number of full moons since birth) in **Settings →
Milestones**.

## Export a family website

**Export** in the sidebar makes a zip file of web pages to share with family.
Unzip it and open `index.html` in any browser — it works offline, on a phone
or from a USB stick, and loads nothing from the internet.

1. **Who** — Everyone; Ancestors / Descendants / Both of someone (with how
   many generations); **A branch, with cuts**; or **Pick people**. For a
   branch, choose who it starts from and which way to go, then tap people on
   the chart: **✂ Stop here** (included, but not their family beyond), **⛔
   Leave out** (they and anyone reached only through them are left out), or
   **🔒 Keep the link as "Private"** (a nameless card, so the people beyond
   still connect). Partners who married in come without their families unless
   you choose *Include their family too*.
2. **Living people** — *Names only* (the default: name and place in the family,
   nothing else), *Everything*, or *Leave out*.
3. **Details** — everything except each person's name and the family links can
   be left out. Start from **Names only**, **Standard** or **Everything**, then
   tick: gender, who has died, family details, maiden and other names, full
   dates / years / no dates, places, each kind of life event, photos (none,
   profile photos, all; web or original size), documents, biographies and
   stories, notes, relationship labels, and — when those modules are on —
   custom fields, sources and contact details (off unless you tick it, and
   asked again for a website).
4. **Website** — title, front-page text, home person, cover photo, theme and
   which pages to include (tree, everyone A–Z, surnames, places).

The bar at the bottom shows what the export will contain as you change things.
**Save choices…** keeps a set of choices for everyone to reuse. **Export
website** runs in the background; download the zip when it's ready (it's kept
for an hour, in the photo folder's `.exports`). Tick **Keep out of all
exports** on a person (Edit → More details) to leave them out of every export.
There is no password on a website: anyone with the zip can read it.

## Wall chart and family book

**More → Wall chart**: ancestors, descendants or an hourglass from anyone, 2–10
generations, on A4–A1, Letter or Tabloid, portrait or landscape — or a
**poster** tiled across A4 sheets (cut on the dashed lines, overlap the shaded
1 cm edge). **Print or save as PDF**, or **Download SVG** for a print shop.

**More → Family book**: a person with generations of ancestors and/or
descendants, or everyone: a title page, contents, one numbered section per
person, a photo gallery and an index of names. **Preview**, then print or save
as PDF. Both follow the same privacy choices as Export.

## Details, sources and contacts

- **Custom fields** (admins: **Settings → Custom fields**) add details such as
  native village, clan, star sign or family deity — as text, a choice, a place
  or a date, for people or families. Quick-add templates are offered. They
  appear under **Details** on each person (or family) and everyone can fill
  them in; *on cards* shows a field on tree cards, *in exports* is its default
  in Export. Removing a field archives it — nothing typed in is lost.
- **Sources** (**More → Sources**): tap 📎 next to a fact or event to say where
  it came from — a certificate, document, book, website, photo or an interview
  with someone in the tree — with a page and how reliable it is.
- **Contact details** of living relatives (phone, WhatsApp, email, address,
  other) are on their page, with tap-to-call and WhatsApp links. **More →
  Address book** lists everyone's and downloads a vCard (`.vcf`) for your
  phone. Numbers without a country code get the household's *Default phone
  code*. Contacts of someone who has died are hidden.

## Duplicates

**More → Duplicates** lists pairs that may be the same person (similar
spellings such as Lakshmi/Laxmi, close birth years, the same parents), best
first, with the reasons. **Compare & merge** them, pick which value to keep
for each detail, and **Merge**: everything linked to the other person moves
over and they go to the trash. It's one change in History with Undo. **Not the
same person** hides the pair for good.

## Inbox folder

Copy photos or PDFs into the **`inbox`** folder inside the photo folder
(`/share/family_tree/inbox/` by default) — from a PC over Samba, a NAS, or a
phone sync app. Every two minutes, files that have finished copying are
imported and wait in **Photos → 📥 Unsorted**; a subfolder named after someone
(`inbox/Grandpa/wedding_1985.jpg`) suggests that person and becomes the title.
Tag several at once, then **✓ Done**. Duplicates and other files are moved to
`inbox/_rejected/` with a `.txt` note saying why. **Check now** scans at once.

## "Who is this?" quiz and kids mode

**More → Who is this?** Choose who is playing (you, or a child's person in the
tree), the game — *Who is this?*, *What do you call them?* or *Find them* —
close family or everyone, and whether to include those who have died. It uses
profile photos and tagged faces; people who are often missed come up more
often. Answers aren't tree changes and don't appear in History.

**Kids mode** lets a child play on your login: choose the games and an
optional time limit and tap **🔒 Start kids mode**. This browser then shows
only the quiz, even after a reload, and the app refuses everything else from
it. To unlock, hold the 🔒 for 3 seconds and enter your **kids-mode PIN** (set
in Settings, 4–6 digits) or, without a PIN, answer a sum. Five wrong tries
block unlocking for 5 minutes. Admins can end kids mode for any device in
Admin → Users. For a full lock of the device use **iOS Guided Access** or
**Android screen pinning**.

## Places map

With the **Places map** switched on (Features — it uses the internet),
**🗺️ Map** shows pins for births, marriages, homes and deaths: **Everyone /
Ancestors / Descendants / One person**, which kinds to show, **Moves** (a line
through each person's places in date order), a **year slider** with **▶
Play**, and a list of places not found yet. Tap a pin to see who was there;
if it's in the wrong place, **Move it** and click the right spot (every event
with that place text uses your pin). Each person's Timeline tab also shows a
small map. Admins can point the map at other servers (App settings).

## Indian relationship names, script names, tithi and ceremonies

These four are **off** for a new install; an admin switches them on in
Features.

- **Indian relationship names**: in **Settings → Names and relationships**
  choose **Telugu** or **Hindi** (or the household default, set in App
  settings). Relatives are then named the family's way — "**Babai** — your
  father's younger brother", "Pinni", "Atta", "Bava", "Tauji", "Bua" — on
  person pages, tree cards, Upcoming, reminders and the quiz. Elder and younger
  come from birth dates, or the order of children in a family. Parallel and
  cross cousins get their own words. **Edit the Telugu (or Hindi) words**
  changes any word for everyone (with Undo), and a relationship with no single
  word gets one built from two. Searching People for a relationship word
  ("babai") finds everyone who is that to you. Ceremony and milestone names
  are shown in the chosen language too.
- **Names in Telugu/Hindi script**: each person gets a second spelling of
  their name in script, typed with a phone keyboard or **Suggest from
  English**; **Show names in** (Settings) chooses English, script or both.
  Search matches either spelling.
- **Tithi (Hindu lunar calendar)**: a death can carry its **tithi**
  (shraddha) — worked out from the full date (and time, when known) or chosen
  as month, paksha and tithi — and a living person a **janma tithi**. Each
  year's date is worked out on the app itself (no internet) for Home
  Assistant's home location and time zone (Hyderabad until that's known), on
  the amanta calendar, by the *Tithi day* rule (App settings). **Correct the
  date** for one year if your priest says otherwise. **More → Tithi dates**
  lists a year's dates, ready to print. Tithi days appear in Upcoming and
  reminders (on the day and up to 30 days before), and the **Sahasra Chandra
  Darshanam** milestone (the 1000th full moon after birth) is added.
- **Indian ceremonies**: event types Barasala / Namakaranam, Annaprasana,
  Aksharabhyasam, Upanayanam, Seemantham, Shashtipoorthi, Sahasra Chandra
  Darshanam, Nischitartham, Gruhapravesham and *Other ceremony*, exported under
  *Religious events & ceremonies*.

## Features you can turn on or off

Admins switch these in **Admin → App settings → Features**. A change applies
at once, for everyone, with no restart. **Turning a feature off only hides
it — nothing is deleted.** Its menu items, buttons, form fields and badges
disappear, its background work stops, its pages say "… is turned off" (with a
link to App settings for admins), and its data is left out of exports.
Turning it back on shows everything again, exactly as it was.

For a new install, general features are on, and region-specific features and
anything that uses the internet are off. When you restore a backup (or update
an older install), a feature that already holds data — for example tithis,
Telugu names or map pins — is switched on for you, so nothing you use
disappears; after that, the switch is yours.

| Feature | New install | What it covers (and hides when off) | Internet |
|---|---|---|---|
| Reminders | on | 🔔 switches and bells, Settings → Reminders, phones and notify services in Admin → Users, the daily reminder run | Only through your Home Assistant's own notifications |
| Milestones | on | 🎉 marks and extra notice in Upcoming and reminders, Settings → Milestones | no |
| Father's and mother's side | on | Colour by side and the side filters in the tree, People and Relatives | no |
| Face tagging | on | Boxes around faces, using a box as a profile photo, tagged faces in the quiz (profile photos already cropped to a face keep their crop) | no |
| Photo fixes | on | 🛠 Fix photo (photos already fixed keep their fixed look) | no |
| Inbox folder | on | Importing files from the photo folder's `inbox/` every 2 minutes, and Photos → Unsorted | no |
| Custom fields | on | The Details block, the People filter, fields on tree cards and in exports, Settings → Custom fields | no |
| Sources | on | 📎 buttons, More → Sources, sources in exports | no |
| Contact details | on | Contact block on person pages, More → Address book and vCard, contacts in exports, *Default phone code* | no |
| Duplicate finder | on | More → Duplicates and merging | no |
| Photo quiz | on | More → Who is this?, kids mode, the kids-mode PIN (a device in kids mode is released) | no |
| Wall chart and family book | on | More → Wall chart and Family book | no |
| Website export | on | The Export page, saved choices, *Export these people* | no |
| Places map | **off** | 🗺️ Map, the Timeline mini-map, looking places up in the background | **yes** — each place name (only the place, no names or dates) goes to the place search server (OpenStreetMap Nominatim by default), one a second; browsers download map pictures from the tile server (OpenStreetMap by default) |
| Indian relationship names | **off** | Telugu/Hindi relationship names, the language choices, the family's own words, Telugu/Hindi labels for ceremonies and milestones (everything shows in English) | no |
| Names in Telugu/Hindi script | **off** | Script name fields, Suggest from English, *Show names in*, script names on cards, pages, search and vCards | no |
| Tithi (Hindu lunar calendar) | **off** | Tithis on person pages, More → Tithi dates, tithi days in Upcoming and reminders, the full-moon milestone, *Tithi day* | no |
| Indian ceremonies | **off** | The ceremony event types, and events of those types (on pages, timelines, the map and in exports) | no |

## App settings

**Admin → App settings** (admins only). Changes apply straight away.

| Setting | Default | What it does |
|---|---|---|
| Days in trash | `90` | How long deleted people, families and photos can be restored (7–3650 days) before the daily clean-up removes them for good. |
| Largest upload (MB) | `20` | The biggest photo or document anyone can upload (1–100 MB). |
| Name order | first name first | How full names are written for everyone; each person can have their own. |
| Photo folder | `/share/family_tree` | Where photos and documents are kept: a folder inside `/share` (not `/share` itself), e.g. `/share/nas/family_tree`. **Changing it doesn't move any files** — see *Moving the photo folder*. **Check folder** shows what a folder holds before you save. |
| Default phone code | `+1` | Contact details: used when a phone number is typed without a country code. Shown while Contact details is on. |
| Tithi day | aparahna | Tithi: which day a tithi is kept on when it spans two days — *aparahna* (covers the afternoon, usual for shraddha) or *sunrise*. Shown while Tithi is on. |
| Relationship names | English | The household's language for relationship names: English, Telugu or Hindi; each person can choose their own. Shown while Indian relationship names is on. |
| Map tiles address | OpenStreetMap's tile server | Places map: where map pictures come from (`https://…/{z}/{x}/{y}.png`). Shown while the map is on. |
| Place search address | `https://nominatim.openstreetmap.org` | Places map: the Nominatim server place names are looked up on. Shown while the map is on. |
| Features | see above | The switches for every optional part of the app. |

The app's **Configuration** tab has only one option:

| Option | Default | What it does |
|---|---|---|
| `admin_users` | `[]` | Home Assistant **user names or user ids** of admins (never display names). Admins get the **Admin** area. It stays here because someone has to be an admin before App settings can be opened. Restart the app after changing it. |

## Admin

Admins have a **🛡️ Admin** item in the sidebar; nobody else sees it, and its
pages say "Only admins can open this page" to others. Tabs:

- **App settings** — see above.
- **Users** — everyone who has opened Family Tree or has a Home Assistant
  person: turn someone's access off (they then see nothing and get no
  reminders), set their "This is me", see their phones from Home Assistant
  and add extra notify services (**Send test** checks them), and end kids mode
  on a device.
- **Storage** — photo storage status, backup and restore (see *Backups*).
- **Trash** — deleted people, families and photos, with **Restore** and
  **Empty trash**.

## Who can see what, and where data goes

- Family Tree is reachable only through Home Assistant (Ingress); no port is
  opened on your network, and everyone is identified by their Home Assistant
  login.
- Everyone who can use Family Tree can see and edit the whole tree, including
  contact details. An admin can turn anyone's access off.
- All records (people, history, settings) are in the app's own database.
  Photos and documents are in the photo folder on `/share`, which anything
  else with access to that folder can read (for example the Samba app or
  people on your NAS share). Location and camera details are removed on upload.
- **Nothing goes to the internet** unless the **Places map** is on: then
  place names (only those) go to the place search server, and each browser
  downloads map pictures from the tile server. Everything else — including
  tithi calculations and the transliteration helper — runs on the app itself.
- Reminders go to the phones linked in Home Assistant and any extra notify
  services an admin added, however your Home Assistant delivers them; they carry
  names, relationships and ages only.
- Exports are downloaded only by the person who made them. A website zip, once
  shared, can be read by anyone who has it — keep living people as *Names
  only* unless it stays within the family.

## Backups

The app's backup (and "Family Tree" in a partial Home Assistant backup)
contains only the database — a few megabytes, however many photos you have.

Home Assistant's **full and automatic backups include the Share folder**
unless you untick it. To keep photos out of those backups:

- **Recommended — use a NAS.** In Home Assistant go to *Settings → System →
  Storage → Add network storage*, choose usage **Share**, and name it (for
  example `nas`); it appears as `/share/nas`. Move the photos there (see below)
  and set the *Photo folder* to `/share/nas/family_tree`. Home Assistant
  doesn't copy network storage into its backups; your NAS's own backups
  protect the photos.
- **Or** keep the default local folder and untick **Share folder** in the
  automatic backup settings. That leaves out everything in `/share` — back the
  photos up another way.

**Admin → Storage:**

- **Download database** — a small zip of the whole tree (people, families,
  events, history, trash, App settings) without photo files.
- **Download database + photos** — everything, for a complete copy or a move
  to another Home Assistant.
- **Restore** — replaces the whole tree with a backup zip; it's checked
  before anything changes. The backup's App settings come back with it, except
  the *Photo folder*, which stays as it is on this Home Assistant. Features
  that hold data in the backup are switched on (see Features).
- **Check photos** — lists photos whose files are missing and files the
  database doesn't know (which can be moved to an `orphans/` folder).
- **Use this folder** — appears when the photo folder holds another install's
  photos (for example after restoring onto a new Home Assistant).

A website export is not a backup: it holds only the details you chose, and no
history.

### Moving the photo folder

Changing the *Photo folder* setting never moves or copies files. To move the
photos (for example onto a NAS):

1. Copy the **whole** old folder — including the hidden `.family_tree_store`
   file — to the new place (Samba, the NAS's own tools, or a terminal).
2. In **Admin → App settings**, type the new folder and press **Check folder**.
   It should say *"This folder holds this tree's photos"*.
3. **Save.** It applies straight away. Delete the old copy once you've checked
   the photos show.

If the photo folder isn't reachable (the NAS is off), Family Tree notices — it
keeps a small marker file, `.family_tree_store`, in the folder — and **never
writes into the empty folder**. The tree keeps working, photos show initials,
uploads wait, and a banner says so.

## History, undo and trash

Every change is listed in **History** (and on each person's History tab) with
who made it and when. **Undo** works as long as nothing it touched was changed
again afterwards — otherwise undo the later change first. Undoing is itself a
change, so it can be undone too. History is kept for good.

Anyone can delete a person, family or photo; it goes to the **Trash** and can
be got back with **Undo**. The trash itself is in **Admin → Trash** (admins
only), for *Days in trash* days.

## Themes and settings

Pick a theme in the sidebar (or Settings on a phone): **Heritage**, **Slate**,
**Daylight**, **Parchment** or **Auto** (follows your device); it's remembered
in each browser. **Settings** also has This is me, names and relationships,
reminders, milestones, the kids-mode PIN and how typed dates are read.

## Limits

- Up to 20 000 people; up to 50 events and 200 photo links per person.
- The **Everyone** view shows up to 3 000 people (relationship labels up to
  800); the Chart shows up to 2 000 cards.
- Uploads: 1–100 MB each (App setting), one file per upload request; HEIC isn't
  accepted.
- Only the website export exists; there's no GEDCOM import or export.
- Tithi dates can be worked out for dates from 1800 on; they follow the
  amanta calendar — check them against your family's panchangam.
- Kids mode can't lock the Home Assistant app or the phone itself.

## Troubleshooting

- **"No admin yet" banner** — add the user name it shows to `admin_users` in
  the app's Configuration tab, save, and restart the app.
- **I'm not admin although I added myself** — open *How the app sees you*
  (your name at the bottom of the sidebar). Use the **user name** or **user
  id** shown there, not your display name, and restart the app after
  changing the option.
- **"Your access is turned off"** — an admin turned it off in Admin → Users.
- **Photos show initials and a banner says photo storage isn't reachable** —
  the photo folder is missing or not this tree's (a NAS that's off, or a
  changed folder). Check **Admin → Storage** and **Admin → App settings →
  Check folder**.
- **"… is turned off"** — that part of the app is switched off in Admin → App
  settings → Features.
- **No reminders arrive** — in Settings → Reminders, check a phone is listed
  and **Send me a test**; make sure the person's 🔔 is on and the Reminders
  feature is on. Phones are set in Home Assistant: Settings → People → you →
  Track device.
- **Map pins missing** — places are looked up one a second; use **Place on
  map** for those that can't be found, or **Try the missing ones again** in
  App settings.
- **Tithi date differs from our panchangam** — correct that year's date, or
  try the other *Tithi day* rule; make sure Home Assistant's home location and
  time zone are set.
