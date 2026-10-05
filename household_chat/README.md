# Household Chat

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

A private chat and file-sharing app for a household, inside Home Assistant. Everyone signs in with their own Home Assistant login, so there are no extra accounts, and nothing is reachable from outside Home Assistant. Files are kept as ordinary files in `/share` (or on network storage), and notifications go to the Home Assistant Companion app with Reply and Mark as read buttons. Nobody can chat until an admin enables them, and admins can't read chats they aren't in.

- Direct chats, groups, a Household group and a private **My room** for each person
- Files, photos (made smaller, location removed) and voice messages
- Optional **voice calls** in direct chats, phone to phone on the home network, with a ringing notification
- Replies, reactions, edits, @mentions, formatting, pins, polls and forwarding
- Search, starred messages and "remind me"
- Disappearing messages, announcements, send later and drafts that follow you
- Child accounts, home/away and photos from Home Assistant's People
- `/share` folders shared into chats, chat downloads, backups and storage clean-up
- Cards from **Household Docs**: a document sent to a chat shows up as a card with **Open in Docs** (Admin → App settings → Connected apps)

See the **Documentation** tab (DOCS.md) for setup and the full user guide.
