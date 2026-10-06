# Changelog

## 2.5.4

- **Calls**: ⚙ now has a **Camera** list with every camera the browser sees — the built-in one, a USB webcam, a phone's front and back. Choosing one turns the camera on or switches it during the call; the choice is remembered on that device.

## 2.5.3

- **Relay usage** in App settings → Voice calls: how much of your calls went through the relay this month and before, against Cloudflare's free allowance when that relay is set. The phones measure it during each call, so it's always current; **Refresh** re-reads it.

## 2.5.2

- **Docs**: a warning box on calls — not a phone line, ringing is a notification, keep the app on screen, what leaves the house away from home and Cloudflare's charges, video's data use, iPhones untested — in the README and at the top of the calls section.
- **Docs**: the *Voice and video calls* section rewritten in one piece, with a part on the Home Assistant phone app — the Microphone list under ⚙ is how you switch the call to the speakerphone, earpiece or headset — and a troubleshooting line for it.

## 2.5.1

- **Video calls fill the screen**: the pictures take the whole screen, the big photo is gone, and the name and time are a small line at the top.
- **Tap a picture to make it big**: the others shrink to a row of small ones above the buttons; tap it again to go back to the grid. In a one-to-one video call the other person starts big.

## 2.5.0

- **Video calls**: 📹 next to 📞 at the top of a chat starts a call with the camera on. In any call, the camera button turns your picture on or off, and 🔄 switches between the front and back camera. The picture goes straight between the phones like the sound; nothing is recorded.
- **Group calls** of up to **four people**: 📞 or 📹 at the top of a group rings everyone in it; whoever answers joins, and the call goes on while two or more are in it. Each person's tile shows their picture or photo, and who's muted. A fifth person is told the call is full.
- The chat note and the 📞 Calls list say whether it was a video or group call and who was in it; a missed group call is unread and notified for the people who missed it.
- Under the hood, every call now works the same way for two people or four: each phone connects to each other, and the app only passes the connection details between them.

## 2.4.2

- **Cloudflare relay**: fetching the credentials failed with "HTTP 403 … error code: 1010" — Cloudflare's bot filter refusing the app's default web client name. The app now identifies itself as Household Chat.

## 2.4.1

- **Calls**: ⚙ during a call shows the Microphone list (on a phone also where you switch to the speakerphone, earpiece or Bluetooth headset) and the output list where the browser has one.
- **Test calling** says what Cloudflare answered when the relay credentials can't be fetched (its HTTP status and message), and the Log tab too.

## 2.4.0

- **Calls away from home.** Admin → App settings → Voice calls gets an **address lookup (STUN) server** (Cloudflare's is free and needs no account) and a **call relay**: Cloudflare Realtime TURN (key id and API token) or your own TURN server (address and shared secret, with a short-lived password made for each call). **Test calling** checks the microphone, the lookup and the relay from your browser. The token and secret are write-only and never included in a backup; a restore keeps this install's.
- **📞 Calls** (⋯ menu): your recent calls, missed ones on top, with **Call back** and a button to the chat.
- **Notifications open the exact chat**: tapping a message notification opens that chat, and a call's notification (or its **Answer** button) opens straight onto the ringing screen.

## 2.3.8

- **Calls**: ⚙ is back to the list from 2.3.4, which worked, with just the microphone choice taken out. (2.3.5–2.3.7 tried buttons for Speakerphone, Earpiece and headset; they didn't work on the phone.)

## 2.3.7

- **Calls**: the ⚙ buttons (Speakerphone, Earpiece, headset) are shown whenever the phone lists any outputs, even when its browser claims an app can't choose — the Home Assistant app on Android says that and still switches. Tapping one tries it and says if it doesn't work.

## 2.3.6

- **Calls**: ⚙ is always shown. Where the phone lets the app choose where the call plays, the buttons are there; where it doesn't, ⚙ says so and shows what the browser reports (the outputs it lists and the browser), so the problem can be described.

## 2.3.5

- **Calls**: ⚙ now shows **where the call plays** as buttons — 🔊 Speakerphone, 📱 Earpiece, 🎧 Bluetooth headset — instead of lists, and no longer offers a microphone choice. Shown where the browser lets the app choose.

## 2.3.4

- **Calls**: the Speaker button is gone — on phones a web page can't switch to the loudspeaker, and making the call louder didn't help. Use the phone's volume buttons, or its sound or Bluetooth menu.
- **Calls**: the mute button now shows a microphone, red with a line across it while you're muted (it used to show a speaker).

## 2.3.3

- **Tapping a notification really opens Household Chat now.** 2.3.2 still sent phones to `/hassio/ingress/…`, an address current Home Assistant no longer has, so the tap still showed "404 not found". Notifications (and **Answer** on a call) now open the app's sidebar page, whose exact address the app asks Home Assistant for when it starts (for example `/a1b2c3d4_household_chat`). The app's **Log** tab shows it: "Notifications open … in Home Assistant".
- Notifications sent before this update still carry the old address; new ones work.

## 2.3.2

- **Tapping a notification opens Household Chat again** instead of "404 not found". Notifications pointed at the app's short name, but an app installed from a repository has the repository's id in its address in Home Assistant (for example `a1b2c3d4_household_chat`); they now use that full address. This also fixes **Answer** on a call notification.

## 2.3.1

- **Calls: both people are heard.** On phones and in the Home Assistant app, the other person's sound could be held back on one side, so only one voice came through. The sound is now started by your tap on 📞 or Answer, and if a phone still holds it back, a 🔈 button appears to hear them.
- **🔊 Speaker** during a call: switches to the loudspeaker where the browser lets a web page choose it (and back); where it can't, the call is made louder instead.
- **⚙ Microphone and sound output** during a call: pick the microphone, and where the browser allows it the speaker or headset the call plays through. Your choice is remembered on that device.
- **Is sound getting through?** Two small level bars show your microphone and the other person's sound, and a line says what's wrong: no sound arriving from them, their microphone seems silent, they've muted, or your own microphone seems silent.
- Muting tells the other person ("Nisha has muted their microphone").

## 2.3.0

- **Voice calls** (optional, off until an admin turns them on in **App settings → Voice calls**): 📞 at the top of a direct chat calls that person. Every open Household Chat page rings, and the phone gets "📞 Nisha is calling" with **Answer** (opens the app, which shows the ringing screen) and **Decline** (works without opening it). Mute and hang up during the call; the screen stays on.
- The sound goes straight between the two phones or computers (WebRTC, encrypted); the app only introduces them and never handles or records it. This version works when both are on the home network; calling from outside the home is planned.
- Each call leaves a note in the chat (Outgoing call · 4 min, Missed call, No answer, Declined) with **Call back**. Missed calls count as unread and are notified like messages; quiet hours and muted chats stop the phone ringing.
- One call at a time: calling someone already on a call says so, and they see a missed call. Children can call the people they can message.
- At the first start, a one-time database update adds call notes and the call history; every message, file and setting is kept as it is.

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
