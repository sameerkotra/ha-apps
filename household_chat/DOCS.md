# Household Chat

Household Chat is a private chat and file-sharing app for a household, running inside Home Assistant. Everyone signs in with their own Home Assistant login: there are no separate accounts or passwords, and nobody outside Home Assistant can reach it. It has direct chats, groups, a Household group and a private room for each person; files are kept as ordinary files in Home Assistant's `/share` folder; and notifications go to phones through the Home Assistant Companion app.

It's built for a small household (around 4–6 people). Nobody can chat until an admin enables them, and **admins can't read chats they aren't in**.

## Getting started

1. **Add the repository.** In Home Assistant go to **Settings → Apps → Install app**, open the **⋮** menu (top right) → **Repositories**, paste `https://github.com/sameerkotra/ha-apps`, press **Add** and close the dialog.
2. **Install.** In **Settings → Apps → Install app**, find Household Chat and press **Install** (the first install builds the app and takes a few minutes), then **Start** it on the **Information** tab. Turn on **Show in sidebar** there too if you like.
3. **Open Chat** in the sidebar. While no admin is set, every page shows a **"No admin yet"** banner with your Home Assistant **user name**. Open **How the app sees you** (from the banner, or the ⋯ menu) to see your user name and user id.
4. **Make yourself an admin.** In the app's **Configuration** tab, add your user name (or user id — not your display name) under **Admin users**, **Save**, and restart the app (**Information** tab → **Restart**). The list is only read when the app starts. Upper and lower case don't matter.
5. **Enable yourself.** Open Chat again and press **Enable me**.
6. **Enable the others.** **⋯ → 🛡️ Admin → People** lists everyone with a Home Assistant login, and **nobody can chat until you enable them**. Enabling someone gives them their personal room (📌 My room) and adds them to the **Household** group (created with the first enabled person). Tick **Child** for children (see *Child accounts*).
7. **Set up phones, home/away and photos in Home Assistant**, once per person, in **Settings → People → (the person)**:
   - **Allow person to login** links the person to their Home Assistant login. Their home/away state and their picture then show in the chat.
   - **Track device** picks their phone (with the Home Assistant Companion app). New messages are sent to that phone, with Reply buttons.

   Household Chat picks these up within 5 minutes; **Check Home Assistant again** (Admin → People) does it now.
8. Optional: look through **Admin → App settings** (file size limits, who can create groups, where files are kept, …).

Nobody is ever made an admin automatically, not even the first person to open the app.

## Chats

- **📌 My room** is a chat with only yourself: notes, links, reminders and documents (a passport scan, a warranty). Nobody else can see it in the app, admins included — but like every chat it isn't encrypted (see *Who can see what*). It's always first in the chat list and shows your own picture (📌 without one). It has files, pins, stars, reminders, search, voice memos, a description, disappearing notes and downloads, but no members, polls, announcements, reactions or send later, and it never notifies you.
- **Direct messages** (＋ → 💬 Direct message): one chat per pair. The other person only sees it once it has a message.
- **Groups** (＋ → 👥 New group): a name, an emoji and people.
  - The creator is the **owner**, who can make others **group admins** and hand over ownership (**Make owner**).
  - Owners and group admins rename the group, change its emoji and description, and add or remove people.
  - Anyone can leave. When the owner leaves, ownership passes to the oldest group admin, else the oldest member.
  - The owner can delete the group for everyone by typing its name. Its files are moved to `_deleted` and removed after 30 days.
  - **New members see earlier messages** is on by default; turn it off in Group info.
  - Joins, leaves, renames and ownership changes appear as small notes in the chat.
- **The Household group** is made with the first enabled person, and everyone enabled joins it automatically. People can leave it.
- **Descriptions**: groups and direct chats can have a description (up to 500 characters, with formatting), shown when you open the chat and in its info (👥 Group info / ℹ Info).
- **The chat list** shows My room first, then chats you pinned (chat ⋯ → **Pin chat to the top**), then the newest activity, with unread counts, an @ badge when you're mentioned, and "Draft: …" when you have unsent text.
- **When someone loses access** (an admin disables them), they leave every group, their messages stay (shown as "Name (no longer here)"), and direct chats with them become read-only. If they're enabled again, their direct chats, their room and the Household group come back; other groups have to add them again.

### Messages

- **Sending**: Enter sends, Shift+Enter starts a new line (on a phone, use the send button).
- **Formatting**: `*bold*`, `_italic_`, `~strike~`, `` `code` ``, ```` ```block``` ````, lists (`- ` or `1. `) and quotes (`> `). ＋ → Aa Formatting shows them. Links become clickable.
- **@mentions**: type `@` and pick a name, or `@everyone` in a group.
- **Message actions**: hover a message and press ⋯ (on a computer), or long-press it (on a phone):
  - ↩ reply, react with an emoji, 📋 copy text;
  - ✏ edit (for 15 minutes by default; shown as "edited") and 🗑 delete (leaves "message deleted" and deletes its files). You can delete your own messages; a group's owner and group admins can delete anyone's in that group;
  - 📌 pin (up to 10 per chat; the newest pin shows in a bar under the chat's header, and ⋯ → Pinned messages lists them all);
  - ☆ star (private to you) and ⏰ remind me (private to you);
  - ↪ forward to one or more chats you're in, or ☑ select several messages to forward together (up to 20). Files are copied; polls are forwarded as text.
- **Polls** (＋ → 📊 Poll): a question and 2–10 answers, one or several answers allowed, and an optional closing time. Results show live with who voted for what. The creator or a group admin can close a poll early. The creator is told when everyone has voted.
- **Seen by** appears under your latest message. There's also a typing indicator and online status with "last seen" (hide yours in Settings).
- **Live updates**: new messages, edits, reactions and read markers appear at once. The dot next to your name at the bottom of the chat list is green while live; if the connection drops, the page checks for news every few seconds instead.

### Files, photos and voice

- **Adding files**: ＋ → 📎 File or document, drag and drop, or paste. On a phone, ＋ → 📷 Photo or camera. Each file shows its upload progress with a cancel button; up to 10 files per message.
- **Photos are made smaller** (2000 pixels on the longest side, as JPEG) and their location and other metadata are removed, unless you tick *Send photos at original size* (then the file is kept exactly as it was, location included). The Files view says "made smaller from …".
- **Viewing**: photos open in a viewer (arrow keys or swipe for the next one), PDFs and text files open in the app, everything else downloads.
- **Files view**: each chat's ⋯ → 📁 Files lists what was shared in it, filterable by photos, PDFs, voice and other files. ⋯ → 📁 My files lists what you've shared.
- **Voice messages** (🎤, up to 5 minutes by default):
  - On a computer, click to start, then ➤ to send. On a phone, hold to record, release to send, and slide left to cancel.
  - Playback at 1×, 1.5× or 2×.
  - The microphone needs a secure (https) connection. The Home Assistant phone app may block it; the button explains why and suggests using the browser.
- **Blocked file types**: `.exe`, `.bat`, `.js`, `.html`, `.svg` and similar are refused (the list is an App setting).
- **Where files are kept**: as ordinary files in the chat files folder (`/share/household_chat` unless an admin changed it), in `<chat name (id)>/<year-month>/`. Personal rooms are in `<name> - personal (id)`. You can reach them over Samba or the File editor too, but please don't rename or move them there, or the chat shows them as "no longer available".

### Voice calls

An admin turns calls on in **App settings → Voice calls** (off until then).

- **📞** at the top of a direct chat calls that person. Calls are one-to-one, in direct chats only: not in groups or My room, and not in a direct chat that's read-only. Children can call the people they can message.
- **When someone calls you**, every Household Chat page you have open shows a ringing screen with **Answer** and **Decline**, and your phone gets a notification "📞 Nisha is calling" with the same two buttons. **Answer** opens Household Chat, which shows the ringing screen; **Decline** works without opening anything. It rings for 30 seconds (an App setting), then it's a missed call.
- **During a call**:
  - The microphone button mutes and unmutes you (muted, it's red with a line across the microphone); the other person sees that you've muted.
  - ⚙ on a phone picks where the call plays — **Speakerphone, Earpiece, Bluetooth headset** (Android lists them as sound routes) — and on a computer the microphone and, where the browser allows it, the speaker or headset. The choice is remembered on that device.
  - Two small bars show your microphone and the other person's sound. If something's wrong a line says what: no sound arriving from them, their microphone seems silent, they've muted, or yours seems silent.
  - 🔈 appears if your phone held back their sound: tap it to hear them.
  - The red button hangs up. The screen stays on while the call is on.
  - There's no speaker button: a web page can't switch a phone to the loudspeaker. Use the phone's volume buttons, or its sound or Bluetooth menu.
- **In the chat**, each call leaves a note: "📞 Outgoing call · 4 min", "📞 Missed call", "📞 No answer", "📞 Declined", with **Call back**. A missed call counts as unread and is notified like a message.
- **Busy**: one call at a time. Calling someone who's already on a call tells you so, and they see a missed call.
- **Quiet hours and muted chats**: your phone isn't rung, but open pages still ring. In quiet hours a missed call waits for the summary afterwards, like messages; in a muted chat it isn't notified.
- **In the chat**, each call leaves a note. **⋯ → 📞 Calls** lists your recent calls, missed ones on top, with **Call back**.
- **Away from home**: at home nothing needs setting up. For calls when someone's out, an admin adds an **address lookup (STUN) server** in App settings → Voice calls (Cloudflare's `stun:stun.cloudflare.com:3478` is free and needs no account), and for networks that block direct connections — common on mobile data — a **call relay**: either **Cloudflare Realtime TURN** (make a TURN key under Realtime → TURN in your Cloudflare account, free up to a large monthly allowance, and enter its key id and API token) or **your own TURN server** (for example a coturn app with a router port forwarded; enter its address and shared secret). A Cloudflare Tunnel doesn't carry call sound, so the relay is separate. **Test calling** under those settings checks the microphone, the lookup and the relay from your browser. The token and secret are never included in a backup.
- **The microphone needs https** and permission, as for voice messages. The Home Assistant phone app may block it; then Answer says so, the call ends as "couldn't connect", and a browser works instead.
- **Ringing is a notification, not a real phone call**: it can take a few seconds, and your phone's own Do Not Disturb silences it. On some phones the call needs Household Chat to stay open on screen; locking the phone or switching apps may end it.

### Search, starred and reminders

- **Search**: the box at the top of the chat list searches message text and file names in the chats you're in; ⋯ → 🔍 Search in chat searches one chat.
- **⋯ → ☆ Starred** lists your starred messages across all chats, with a link to each one.
- **⏰ Remind me** (on a message): in 1 hour, this evening (18:00), tomorrow 09:00, next week, or a date and time you pick. When it's due, only you get a notification, and ⋯ → ⏰ Reminders lists what's coming up and what's done. Up to 50 open reminders per person. A reminder is dropped if you leave the chat or the message is deleted. Times use Home Assistant's time zone.

### Disappearing messages

- **For a whole chat**: chat ⋯ → ⏱ Disappearing messages sets 1 hour, 1 day, 7 days or 30 days. In a group this is for the owner and group admins; in a direct chat either person can set it. It covers new messages only, and a note in the chat says who turned it on.
- **For one message**: ＋ → ⏱ Disappear after… (in a disappearing chat you can pick a different time, but not "never").
- Each disappearing message shows ⏱ and the time left.
- **When the time is up**, the message, its reactions, poll, pin, stars, reminders and files are deleted completely, for everyone: nothing is kept in `_deleted`, and replies to it say "Original message no longer available".
- Notifications only say "Name sent a disappearing message". Disappearing messages can't be forwarded, and they're left out of chat downloads and of the app's own backup. Restoring any backup never brings back one that's past its time.
- **Honest limits**: before it goes, anyone in the chat can copy or screenshot it or save its files, and Home Assistant's own backups taken meanwhile keep it until those backups are deleted. It isn't a safe way to send passwords.

### Announcements

- Admins can send an announcement (＋ → 📢 Send as announcement), usually in the Household group; group admins can too in their groups if App settings allow it.
- It's highlighted and stays at the top of the chat until everyone has tapped **Got it**, and shows who has and hasn't.
- It reaches people even if they muted the chat or chose mentions only (not people who turned notifications off, and it waits for their quiet hours).
- The sender can send one reminder to those who haven't tapped Got it, or take it down.

### Send later and drafts

- **Send later**: right-click or hold ➤, or use ＋ → 🕓 Send later, and pick a time (at least a minute ahead, at most a year). Waiting messages show in a 🕓 bar in the chat and under ⋯ → 🕓 Scheduled, where you can change the text or time, send now or delete. Only you see them. Files are uploaded straight away. If you're no longer in the chat when it's time, it isn't sent and you're told. Up to 20 per person; not in My room.
- **Drafts follow you**: what you've typed but not sent is saved and appears on your other devices (text only).

### Child accounts

An admin ticks **Child** in Admin → People. A child can read and write in the Household group and in chats an adult adds them to, and start direct chats with adults. A child can't create groups, add or remove people, rename chats, pin, announce, be a group admin, delete other people's messages, or send disappearing messages or change the disappearing setting. Children can only message each other directly if App settings allow it. Admins can't read a child's chats unless they're in them, like everyone else.

### Home / away and photos

- **Home / away** shows under people's names (🏠 Home, Away, 📍 a zone name, with "since …"), taken from the Home Assistant person linked to their login. Admins can hide it for everyone in App settings.
- **Photos** are the picture of that same Home Assistant person. To change yours, change the picture in Home Assistant (Settings → People); it shows in the chat within 10 minutes, and removing it there shows initials. A copy without location data is kept in the app's `/data`. Groups show their emoji (or initials).
- If the automatic person is wrong (for example a child tracked by another Person), an admin can pick a different `person.*` in **Admin → People → 🏠**, shown as "chosen here", or go back to **Automatic**.

### Downloading a chat

Chat ⋯ → ⬇ Download chat gives a zip with one readable page of the messages (names, dates, pins, polls, replies) and a `files/` folder, optionally for a date range. Disappearing messages are left out, and so are files over the size limit (App setting; the page says what was left out). Keep the zip safe: anyone with it can read the chat.

### Shared folders

- An admin can share an existing folder from Home Assistant's `/share` (for example `Documents/House`) into **any chats** — groups, direct chats and personal rooms, their own My room too — in **Admin → Shared folders**. Tick several chats at once, each with its own access: **read-only**, or **read and write** (upload files and make folders). At most 10 per chat.
- Members open it with 📂 in the chat's ⋯ menu. The **search box** at the top finds files and folders anywhere inside it as you type. With read and write access, **⬆ Upload files** takes several files at once (or drop them onto it on a computer), with a progress bar and a summary of anything refused.
- New files are announced in the chat every few minutes if that's on (not in personal rooms).
- **Edit…** changes the name, the announcements and the chats. **Stop sharing** removes it from every chat and leaves the folder untouched.
- You don't need shared folders to send files: 📎 works in every chat.

### Shared from Household Docs

If the household also uses the **Household Docs** app, its **⋯ → Send to chat** puts a card in a chat you pick: "**Nisha shared Trip 2026**", with an icon for the kind of document (📝 note, ✅ checklist, 🧮 sheet, 📁 folder, 📄 file), whose it is, and **Open in Docs**.

- **Open in Docs** opens the document in Household Docs' page in Home Assistant, without leaving Home Assistant. Each card opens only the page it was sent with — only Docs' own page and one of its items are accepted. If Docs doesn't say where its page is (for example it isn't shown in the sidebar), the card says to open Household Docs from the sidebar instead.
- The card holds only the **title, type and owner** — never what's in the document. Whether you can open it is up to Household Docs: if it wasn't shared with you, Docs says so and who to ask. Docs can share it with the chat's members at the same time (the card then says *Shared with this chat's members*).
- It's posted **as the person who sent it**, only where they could post themselves (they must be in the chat; a direct chat with someone who no longer has access is read-only), and it counts toward their 30 messages a minute.
- Cards notify like messages ("Nisha shared a checklist “Trip 2026”"), show in search by title, and can be replied to, reacted to, pinned, starred, reminded and deleted. They can't be edited or forwarded — send it again from Docs instead. A chat download lists them by title, without a link.

## Notifications

- **Settings → Notifications**:
  - **Tell me about**: every message; direct messages, mentions and replies to me (the default); or nothing.
  - **What the notification shows**: sender and message, sender only, or nothing ("New message"). An admin can set a stricter limit for everyone.
  - **Quiet hours**: notifications wait and come as one summary afterwards (Home Assistant's time zone).
  - Whether a phone is linked to you, and the chats that have their own setting.
- **Per chat** (chat ⋯ → 🔔 Notifications): all messages, mentions only or off, and mute for 1 hour, 8 hours, a week or until you turn it back on.
- **Fewer pushes**: several messages from one chat within a minute arrive as one ("Nisha: 3 new messages"). Nothing is sent while you're looking at that chat, or for your own room. Reading the chat cancels anything still waiting.
- **Reply and Mark as read** buttons appear on notifications sent to a phone with the Companion app. The reply is posted as you. Admins can turn the buttons off in App settings.
- Tapping a notification opens the chat it's about (or the ringing screen for a call).
- **Extra notify services**: an admin can add others for a person (a speaker, a second service) in Admin → People → 🔔 → **Also**, and **Send a test** to all of them.
- People without access never get notifications.

## Admin

Only people listed under **Admin users** in the app's **Configuration** tab are admins. Admin is under **⋯ → 🛡️ Admin**:

- **People**: enable or disable people, tick Child, see each person's phones from Home Assistant (⚠ means Home Assistant has no notify action for that phone yet — open the Companion app on it once), 🔔 extras and test, 🏠 home/away and photo, and **Check Home Assistant again**.
- **Chats**: every chat's name, kind, members, last activity, message count and storage used — never messages or file names of chats you aren't in. A group with no enabled members left can be deleted here (by typing its name).
- **Shared folders**: see above.
- **App settings**: see below. Under the settings, a read-only **Connected apps** card lists the other household apps this one exchanges messages with (for example Household Docs): name, version, when it was last heard from and what it can do. An app not heard from for a day shows as *not seen lately*.
- **Storage**: see *Storage and backups*.

## App settings (Admin → App settings)

The settings are in cards (Files, Chats and messages, Old messages, Notifications, Voice calls), with a line under each setting saying what it does, its range and its default. Changes are kept until you select **Save** at the bottom (it shows how many unsaved changes there are); **Discard changes** puts everything back. A number out of range is flagged at the field before anything is saved. Saved changes apply straight away, without a restart.

| Setting | Default | What it does |
|---|---|---|
| Chat files folder | `/share/household_chat` | Where chat files are kept (see *The chat files folder*). |
| Largest file (MB) | 50 | The biggest single file anyone can send (1–1024). |
| Limit for the chat folder (GB) | 0 (no limit) | Uploads are refused once the chat folder would pass it; Storage warns at 80 %. |
| Who can create groups | Everyone | Or admins only. |
| Messages can be edited for (minutes) | 15 | 0 = never, up to a day. |
| Delete messages older than (days) | 0 (keep forever) | Old messages are deleted nightly (pinned ones are kept). |
| Keep the files of messages deleted by age | On | Off also deletes their files. |
| Also delete old notes in personal rooms | Off | Whether the age limit applies to My room too. |
| Notification preview | Sender and text | The most any phone shows: sender and text, sender only, or nothing. |
| Refused file types | exe, bat, cmd, com, scr, msi, ps1, vbs, js, jar, apk, html, htm, svg | Extensions that can't be uploaded. |
| Make photos smaller than (pixels) | 2000 | 0 = never. |
| Longest voice message (seconds) | 300 | 10–900. |
| Show home / away | On | Off hides it for everyone (photos still show). |
| Reply and Mark as read on notifications | On | Off sends plain notifications. |
| Who can post announcements | Admins | Or admins and group admins in their groups. |
| Children can start direct chats with each other | Off | |
| Largest chat download (MB) | 500 | Files beyond this are left out of a chat download. |
| Voice calls | Off | Shows 📞 in direct chats (see *Voice calls*). |
| Ring for (seconds) | 30 | 15–60; shown while Voice calls is on. |
| Address lookup (STUN) server | empty | For calls away from home, e.g. `stun:stun.cloudflare.com:3478`. Empty = home network only. |
| Call relay (TURN) | None | None, Cloudflare Realtime TURN (key id + API token) or your own TURN server (address + shared secret). |

## The chat files folder

- **Choosing it** (Admin → App settings → Files → Chat files folder): type a folder inside `/share` and press **Check folder**; it tells you what's there. Then **Save** at the bottom of the page (with any other changes): saving checks the folder again, asks first if needed, and creates the folder and what the app needs inside it (`_thumbs`, `_deleted`, `README.txt` and a small `.household_chat_store` file that marks it as this install's). Folders that belong to another install, are a shared folder, are read-only, or whose parent doesn't exist are refused.
- **Network storage**: in Home Assistant, add it under Settings → System → Storage → *Add network storage* with usage *Share*. It appears as `/share/<name>`, so use `/share/<name>/household_chat`. Home Assistant's backups don't copy network storage, so give it its own backup.
- **Changing it never moves files.** Copy the whole old folder, **including `.household_chat_store`**, to the new place first, then change the setting. Otherwise the files already shared show as "no longer available" until you do (you're asked first).
- **Connected?** Admin → Storage → *File storage* shows **● Connected** or **● Not connected** and why, with the free space. It's checked at start-up, every 5 minutes and after a change; **Check again** checks now.
- **When it isn't connected** (the NAS is off, the mount failed, the marker file was deleted): everyone sees "File storage isn't connected". Messages keep working; files can't be sent or opened. Nothing is written to the folder, so nothing lands on Home Assistant's own disk by mistake, and files deleted meanwhile are removed once it's back. If the marker file was lost but these are this chat's files, **Use this folder** (Storage) sets it up again without moving or deleting anything.

## Storage and backups (Admin → Storage)

- **Space used**: the database size, space per chat (sizes only) and by type, missing files, and **Check files**.
- **Clean up**: the largest files, filterable by type and age. For chats you aren't in, you see only the type and size. Deleting files moves them to `_deleted` and shows "File deleted by admin" in their chat. You can also empty `_deleted` now (otherwise it's emptied after 30 days) and delete previews (they're rebuilt when needed).
- **Old messages**: how many messages are older than 30 days, 90 days and a year, and what the age setting would remove.
- **Download backup**: the database, optionally with every file (can be large). Disappearing messages are left out. **The backup contains every other message readable — keep it safe.**
- **Restore**: replaces every message with the backup (and the files, if it includes them). The chat files folder setting of this install is kept. Open pages reload.
- **Home Assistant's own backups** include the app's `/data` always, and `/share` when "Share" is ticked.
- **Automatic clean-up**: every night (03:30 Home Assistant time) old messages if an age limit is set, `_deleted` entries older than 30 days, missing files and old previews; every hour, files uploaded but never sent within a day.

## Who can see what, and where data goes

Everyone can read this in the app too: **⚙ Settings → 🔓 Who can see your messages**. Each chat's info, My room and the start page say it in one line and link there.

| Where | Who can read it |
|---|---|
| Messages in the app | The chat's current members only. **Admins can't read chats they aren't in**: Admin → Chats shows names, members, dates and sizes, never messages or file names. |
| The database (`/data/chat.db`) | Anyone with access to the Home Assistant machine or its backups. **Messages are not encrypted.** An admin who downloads the app's backup can read every chat outside the app. |
| Files in the chat files folder | Anyone who can reach `/share` (and the NAS, if it's there): Samba, the File editor, other apps with `share` access, and Home Assistant backups that include Share. Folder names show chat names. |
| Phone notifications | What the preview setting allows shows on lock screens and in the Companion app's history. |
| Notification replies | They pass through Home Assistant's event bus, where Home Assistant admins and automations can see them. |
| Messages between the household apps | Household Docs and Chat talk over Home Assistant's event bus (`household_apps` events): ids, names, document titles and types, never document contents or chat messages. Your chat list goes without who is in each chat; the members of a chat are told only when you ask Docs to give that one chat's members access. Home Assistant admins, automations and every installed app with Home Assistant API access can see these events (and could send one), and Home Assistant's history keeps them unless you leave them out (below). Chat treats such a message only as a request the person could make themselves. |
| Voice calls | The sound goes straight between the two phones or computers, encrypted; the app never handles it and calls are never recorded. With an address lookup (STUN) server set, the phones' public network addresses go to it; with a relay, the encrypted sound passes through it (Cloudflare or your own server) — names and messages never do. The app keeps who called whom, when and for how long (the notes in the chat and the Calls list), like messages. |
| Home / away | Home Assistant already shows every user these states. |
| Shared folders | Ordinary `/share` folders: anyone who can reach `/share` can read them. In the app, the members of each chat they're shared into. |
| Chat downloads and backups | Anyone who has the file. |

**What leaves your home network:** the app itself only talks to Home Assistant. Phone notifications are delivered by Home Assistant's Companion app push service (through Google's or Apple's push systems), so their title and preview text travel over the internet like any Home Assistant notification — choose "Sender only" or "Nothing" in Settings or App settings if you'd rather they didn't. People's pictures are copied only from Home Assistant itself, never from other sites.

So it's fine for household chat and documents, but not for secrets such as passwords or card numbers.

**Keeping app messages out of Home Assistant's history.** Home Assistant's recorder stores events, including the household apps' messages to each other. They hold no message text or document contents, but if you'd rather not keep them, add this to Home Assistant's `configuration.yaml` and restart Home Assistant:

```yaml
recorder:
  exclude:
    event_types:
      - household_apps
```

## Limits

- Built for a household: up to 20 people per group and 100 groups.
- Messages up to 8000 characters, 10 files per message; 30 messages and 20 uploads a minute per person.
- 10 pinned messages and 10 shared folders per chat; 50 open reminders and 20 scheduled messages per person; polls with 2–10 answers.
- Voice calls are one-to-one; no video calls. No end-to-end encryption for messages, no link previews, and no access from outside Home Assistant or for people without a Home Assistant login.
- Home Assistant can't show an unread count on the sidebar.
- PDFs have no preview picture in the Files view.
- Notifications still waiting (batching, quiet hours) are dropped if the app restarts.

## Troubleshooting

- **"No admin yet" banner**: add your user name (shown in the banner and on *How the app sees you*) to **Admin users** in the app's **Configuration** tab, save, and restart the app (**Information** tab → **Restart**).
- **I added my name but I'm not an admin**: restart the app after changing the list, and use your **user name or user id** exactly as *How the app sees you* shows them — display names don't count (the page tells you if yours is only matched by display name).
- **"Ask an admin to give you access"**: an admin enables you in Admin → People.
- **No notifications**:
  - Settings shows whether a phone is linked to you. In Home Assistant: **Settings → People → you → Track device**, picking your phone with the Companion app; the person must also be linked to your login (*Allow person to login*).
  - Admin → People shows each person's phones from Home Assistant; *Check Home Assistant again* reads them now, and 🔔 → *Send a test* shows any error.
  - The app's **Log** tab names notify services Home Assistant doesn't know.
- **No Reply button**: buttons need the Companion app's `notify.mobile_app_…` action; notify entities (for example `notify.pixel_7`) can't carry them.
- **"File storage isn't connected"**: see *The chat files folder*. Admin → Storage says why.
- **A file shows "no longer available"**: it was renamed, moved or deleted outside the app (for example over Samba).
- **Live updates stop**: the dot next to your name turns amber when the page falls back to checking every few seconds; it reconnects by itself.
- **The microphone doesn't work**: it needs https and permission; in the Home Assistant phone app, try a browser instead.
- **Only one person can be heard**: look at the bars and the line on the call screen. "No sound is arriving" means the connection lets sound through only one way (try again, both on the same Wi-Fi); "their microphone seems silent" means their phone isn't giving the call its microphone (muted, used by another app, or blocked — try a browser instead of the Home Assistant app, or another choice in ⚙). If 🔈 shows, tap it.
- **A call says "Couldn't connect"**: away from home it needs the address lookup and, on mobile data, a relay (App settings → Voice calls → Test calling says what's missing); and the microphone must be allowed. A call also ends if one side loses its connection to Home Assistant for a minute.
- **The back gesture**: on Android (Home Assistant app or browser), Back closes an open menu or dialog first, then goes from a chat or page back to the chat list; only Back from the list leaves the app.
- **"Open in Docs" does nothing, or Docs isn't in Connected apps**: Household Docs and Chat find each other when they start and every few hours; restart Household Docs, then look at Admin → App settings → Connected apps. Both apps must run inside Home Assistant (messages between apps are off outside it).
- **Themes**: Settings → Look → Theme (Midnight, Slate, Daylight or Auto, which follows your device's light or dark setting). If you had picked Vault before, you now get Midnight.
