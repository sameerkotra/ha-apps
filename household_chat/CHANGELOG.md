# Changelog

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
