# Changelog

## 2.2.1

- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.

## 2.2.0

- **Shared from Household Docs**: *Send to chat* in the new Household Docs app puts a card in the chat — "Nisha shared **Trip 2026**", with the kind of document, whose it is and **Open in Docs**, which opens it in Household Docs inside Home Assistant. The card holds only the title, never what's in the document, and is posted as that person only where they could post themselves. Cards notify, show in search and can be replied to, pinned, starred and deleted like messages; they can't be edited or forwarded.
- **Admin → App settings → Connected apps**: the other household apps this one exchanges messages with, their version, when they were last heard from and what they can do. Read only.
- The household apps' messages travel over Home Assistant's own event bus, on the same connection Chat already keeps for the phone's Reply buttons — no new permissions. DOCS.md shows how to keep them out of Home Assistant's history.
- At the first start, a one-time database update adds the new kind of message; every message, file and setting is kept as it is.
- **Open in Docs** links: each card opens exactly the page it was sent with — only the sending app's own page and one of its items is accepted — so a card posted in one chat can never change where other cards' links go.
- When Docs asks for your chats, Chat answers with their names and sizes only; who is in a chat is told only for the one chat you pick to give its members access (these answers pass through Home Assistant's event history).

## 2.1.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device) — the same four in every household app. “Vault (dark)” is now Midnight; your saved choice carries over.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged. The files folder is now chosen with **Check folder**, then saved with the page's **Save** (the same checks and confirmation as before).
- **Admin → People**: one card per person instead of a table; the notify editor says **Send a test** and shows a result for each service.
- **How the app sees you** and the **No admin yet** banner use the same wording in every household app; the banner names the user name to add to `admin_users`.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

## 2.0.1

- Everyone can now see who can read their messages: a **Who can see your messages** card in Settings, and a line in each chat's info, in My room and on the start page. It says plainly that messages aren't encrypted, so anyone who can get into the Home Assistant machine or its backups — including an admin who downloads the app's backup — can read them outside the app.

## 2.0.0

First public release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- Private chat for the household, signed in with each person's own Home Assistant login; nobody can chat until an admin enables them, and admins can't read chats they aren't in
- Direct messages, groups, a Household group and a private 📌 My room for each person
- Files, photos (made smaller, location removed) and voice messages, kept as ordinary files in `/share`
- Replies, reactions, edits, @mentions, formatting, pins, polls and forwarding
- Search, starred messages and "remind me"
- Disappearing messages, announcements, send later and drafts that follow you
- Phone notifications through the Home Assistant Companion app, with Reply and Mark as read buttons
- Child accounts, home/away and photos from Home Assistant's People; chat downloads, backups and storage clean-up
