# Household Phones

Household Phones tells you whether the household's phones are on and reachable. Each phone checks in with Home
Assistant through the Home Assistant **Companion app**, by itself; Home Assistant asks a phone to report only when it
is overdue, and **Check now** asks at once and shows the answer within seconds. Everyone opens the app from the
sidebar with their own Home Assistant account: in **Phone mode** each person sees their own phone(s), and exactly what
admins can see about them; in **Admin mode** admins see every phone and everything Home Assistant knows about it.
No phone is monitored until an admin adds it. Optionally, a phone's owner can forward its messages (a one-time code, a text) into **Household Chat** as
disappearing messages.

There is no network ping: everything goes through the Companion app.

## Getting started

1. Install the app from this repository: in Home Assistant go to **Settings → Apps → Install app**, open the **⋮**
   menu → **Repositories**, add `https://github.com/sameerkotra/ha-apps`, then install **Household Phones**. Start it
   and turn on **Show in sidebar** (the page is called **Phones**).
2. Add your Home Assistant user name to `admin_users` on the app's **Configuration** tab, save and restart the app —
   until then every page shows **No admin yet** with the name to add.
3. Each person installs the **Home Assistant Companion app** on their phone and signs in to this Home Assistant. The
   phone appears in **All phones** within a few minutes, under **Available devices** — not monitored yet: nothing
   is asked of it and no alert goes out about it.
4. An admin presses **Add to monitoring** on the phones to monitor and, in one dialog, checks the owner (the person
   whose Companion app registered it), name, kind (Phone or Tablet) and check-in interval.
5. Each person opens **Phones**, sees "Your phone is now monitored — set up forwarding if you want it" on **My
   phone**, and works through the **setup checklist**. A phone that isn't added shows only "Your phone isn't
   monitored — ask an admin".

**How the app sees you** (the menu under your name, top right) shows who the app thinks you are and why you are (or
aren't) an admin.

## How a phone checks in

- The Companion app sends its sensors to Home Assistant every 15 minutes on its own (**Sensor update frequency:
  Normal**, its default), plus whenever something changes. Any report from the phone counts as a check-in, even with
  unchanged values — but not Home Assistant's own updates: states restored when Home Assistant restarts or reloads,
  sensors going *unavailable* or *unknown*, and updates without a time. A phone that's switched off goes Late 2 minutes
  after its interval and Offline 12 minutes after it (17 and 27 minutes at the defaults).
- **Check now** is answered only by a report made after it was sent.
- When a phone hasn't checked in within the interval plus 2 minutes, the app sends it the Companion app's command
  `command_update_sensors` ("please report") through `notify.mobile_app_<phone>`. No reply within 5 minutes: it asks
  once more. A phone that reports on time is never sent anything. Commands don't count toward the Companion app's
  500 notifications a day.
- The request goes through the Companion app's push service, on Wi-Fi or mobile data, so Home Assistant must be
  reachable from outside (Home Assistant Cloud / Nabu Casa, or your own remote address).
- **iPhones** check in from their own reports only: iOS doesn't support the command, so they are never asked.

The interval is 15 minutes by default (or 10 or 5, in App settings; each phone can have its own on its page). At 10 or
5 minutes a phone left on *Normal* is asked once or twice between its own reports; a phone set to *Fast always*
reports every minute (more battery) and is never asked.

### Statuses

- **Online** — a check-in within the interval + 2 minutes.
- **Late** — overdue; the app is asking it to report. No alert yet.
- **Offline** — both requests unanswered (27 minutes without a check-in at the defaults). The offline alert goes out.
- **App not reporting** — the phone is probably on but the Companion app can't report: it seems signed out or
  uninstalled, a needed sensor (Battery level) is off on the phone, or it answers requests but never reports by
  itself (battery optimisation — set the Home Assistant app's battery use to *Unrestricted*). The page says which and
  how to fix it.
- **Can't check** — the request itself failed (Home Assistant couldn't reach the push service). Never counted as
  Offline: check remote access / Nabu Casa.
- **Paused** — alerts are paused, or the owner paused being watched.
- **Not monitored (until 07:00)** — the owner's do-not-monitor hours.
- **Unknown** — just added, or not monitored.

### Avoiding false alarms

- **After Home Assistant or this app starts**, no phone is marked Offline (and no offline alert goes out) until one
  interval plus 12 minutes has passed; the page says "Starting — waiting for phones to check in". The same grace
  applies to a phone just added to monitoring, and when its do-not-monitor hours end.
- **Lost contact:** when half or more of the monitored phones (and at least 2) go quiet within 10 minutes, the problem
  is almost certainly at home (Home Assistant lost the internet, or the push service is down). The admins get **one**
  alert — "Home Assistant has lost contact with the phones (5 of 6 silent since 14:02)" — as a Home Assistant
  notification and on their phones; the single offline alerts wait until contact returns, and then only the phones
  still silent are reported.
- **Pause alerts** for a phone (its owner or an admin): for 1, 4 or 12 hours, until a time, or until it's back online.
  Its status stays visible and Check now still works.
- **Do-not-monitor hours** (the owner, on the phone's page): see below.

### Do-not-monitor hours

The phone's owner can set times when it isn't monitored at all: one or more windows, each with days of the week and
a start and end time in Home Assistant's time zone — e.g. Mon–Fri 22:00–07:00 and Sat–Sun 23:00–09:00 (a window
can run past midnight). During a window:

- no "please report" is sent, and **Check now** says "Not monitored right now — Asha set do-not-monitor hours until
  07:00";
- no alert of any kind goes out, and none is kept for later;
- nothing is added to its history;
- admins see only **Not monitored (until 07:00)** — no battery, network or other details.

When a window ends, the phone gets the start-up grace (interval + 12 minutes) before it can be Offline. **Forwarding**
goes on during a window if it's on (your own choice), unless you tick **Also pause forwarding** for that window.
Admins see that you have do-not-monitor hours ("Not monitored weekdays 22:00–07:00") but can't change them. An admin
App setting allows them (on by default) and limits them to 12 hours a day; the editor shows the limit. Children's
phones can't have them. (They replace 1.0's daily quiet times, which were turned into a window on every day.)

## My phone

Your monitored phone's status ("Online · last check-in 6 min ago"), battery and charging, network and last restart,
with **Check now**. A phone of yours that no admin has added shows only "Your phone isn't monitored — ask an admin"
(or, if it replaces an older monitored phone of yours, **Replaces <old phone>**). Right after an admin adds your
phone, a card says "Your phone is now monitored — set up forwarding if you want it", with the checklist.
**Settings, checklist and log** opens the phone's own page:

- **Pause** — alerts (above), or **pause being watched** for up to 24 hours (adults only; App setting): no requests,
  no alerts, and admins see only "Paused by <you> until 18:00".
- **Setup checklist** — the Companion app signed in; remote access working; the sensors this app uses switched on
  (Battery level, Battery state, Charging, Last reboot, and Last notification for forwarding); a sensor that changes
  with every report (**Battery power** or **Last update trigger**: the command only sends sensors whose value changed,
  so a phone where nothing changed might not answer otherwise — ticked once the phone has answered a request);
  battery use *Unrestricted*; and for forwarding on Android 15+, letting the Home Assistant app read one-time codes.
- **Forward my messages** — see below.
- **Do-not-monitor hours** — see above.
- **What admins see** — the exact list, **Share my last place** (off by default: the zone is shown to admins and in
  offline alerts only when on), **Who checked your phone** and **Alerts about your phone**.
- **A new phone?** — when you have an older phone too: **Replaces <old phone>** moves its name, settings, forwarding
  and history here and retires the old one.
- **Stop monitoring my phone** — sends the admins a request.

Below your phones: **Phones that forward into your chats** — a **Check <owner>'s phone** button for each phone that
forwards into a chat you're in (you see only the Check-now result), and your own **quiet hours for alerts you
receive**.

## Check now

For when you're expecting a message (a code, a text forwarded from someone's phone) and it hasn't arrived. It sends
the phone "please report" at once and shows a live card, waiting up to 60 seconds:

- **✅ Online** — "replied in 4 s", battery, Wi-Fi or mobile data, last restart, and for a phone that forwards, the
  last forward ("last message forwarded 14:02 into Phone codes") — the time and chat only, never the text.
- **⏳ No reply yet** — "no reply; last seen 13:48 on mobile data, battery 9%", with **Try again**. The phone is then
  watched as overdue: asked again, and Offline if it stays silent.
- **⚠️ Can't ask** — the request failed, or it's an iPhone ("Can't ask an iPhone — last check-in 13:48").
- A forward that arrives while the card is open is noted on it.

Who can: the owner, admins, and people in the chat the phone forwards into (App setting). One check per phone per 30
seconds (more presses join the one running) and at most 20 per phone per hour. Every check is logged: who, when, and
the result. It has its own address (`#/check/<phone>`), which **Household Chat** (📱 Check phone in a chat's ⋯ menu)
and the **Household Assistant** ("check Asha's phone") open.

## All phones (admins)

A summary strip of the monitored phones (monitored, online, offline, late, paused), **Not used lately** (no check-in
for 30 days: **Keep** or **Remove** — its history goes with it; nothing is removed by itself), then the monitored
phones, problems first: status and last check-in, owner and model, battery, network and app version, the owner's
do-not-monitor hours, with ⋯ (Check now, Pause alerts, Edit, Stop monitoring). Tablets come after. Then **Available
devices**: every Companion-app device not monitored, with a guess of its kind (phone, tablet, or watch and the like),
and **Add to monitoring** (owner, name, kind, interval and when to alert that it's offline, in one dialog). **Stop monitoring** asks whether to keep or
delete the phone's history, switches forwarding off, and puts it back under Available devices. **Read Home Assistant
again** reads the devices now (it also happens every few minutes and whenever Home Assistant's devices change).

A **phone's page** shows the device (manufacturer, model, Android/iOS version, Companion app version, owner, kind),
its settings (name, kind, its own interval, *Alert when offline for at least*, owner, and the notify service if Home Assistant names it
differently from `notify.mobile_app_<device name>`), **every enabled sensor** grouped (Battery, Network, Activity,
Storage, Location zone, Last reboot and updates, Other — notification texts are never shown), the sensors that are
**off on the phone**, and its **history** (status changes, restarts, Check now, pauses and alerts, with the battery
at each change). While an offline alert is waiting, a line under the status says so: "⏳ Offline 2 h 14 min — alert
after 10 h". A phone whose owner paused being watched shows only "Paused by …"; one in its do-not-monitor hours only "Not
monitored (until …)".

**Check-in details** (collapsed, on the phone's page): under the status, "Last check-in 14:02 — from Battery level
(reported, unchanged)"; inside, the last 20 updates that counted as check-ins and the last 20 that were ignored, with
the reason ("HA restart", "unavailable", "added or restored by Home Assistant", "before the request"…). Only sensor
names, times and reasons — never a sensor's value, and the Last notification sensor is just named. The history's Late
and Offline rows say the same about the last check-in. Kept in memory since the app started.

## Forwarding messages into Household Chat

The phone's **Last notification** sensor (Android) changes with every new notification: each one from an allowed app
is forwarded at once as one Chat message, e.g. "📱 Asha's Pixel · Messages · 14:02", then the sender and the text.

- **Only the owner** switches **Forward my messages** on, on their phone's page, once an admin has added the phone to
  monitoring; an admin can't do it for someone else (and the admin's App setting can switch forwarding off for the
  whole household). It keeps working during your do-not-monitor hours unless a window pauses it.
- **Where:** your own 📌 My room by default, or a group or direct chat you belong to (an admin can limit this). For a
  group the page shows how many people will see your forwards; a small group just for this ("Phone codes") is best.
- **Disappears after** 1 hour by default (1 day, 7 days or 30 days).
- **What:** *Codes only* (a 4–8 digit number next to a word like code, OTP, passcode, PIN or verification) — the
  starting filter for a group —, *Containing words* of your choice, or *Everything* (you confirm it first).
- **Which apps:** Google Messages and Samsung Messages by default; you can add WhatsApp, Signal… The Companion app's
  own notifications (Household Chat's among them) are never forwarded, and the same text from the same phone within 2
  minutes goes once.
- **Quiet hours:** forwards are held until the end (kept in memory only — lost if the app restarts meanwhile) or
  dropped.
- A summary ("3 new messages") goes as "📱 3 new messages on Asha's phone". A text that looks cut is marked
  "(shortened — see the phone)".

**On the phone:** Companion app → Settings → Companion app → Manage sensors → **Last notification**: switch it on,
grant notification access, and add your messaging apps to its **Allow list**.

**Android 15 and later hide one-time codes** from apps that read notifications: the Companion app gets "Sensitive
notification content hidden". The app then forwards "📱 A code arrived on Asha's phone (14:02, Messages, from
AX-BANK) — Android hid it." and the checklist shows the fix: from a computer with ADB, once:
`adb shell cmd appops set --user 0 io.homeassistant.companion.android RECEIVE_SENSITIVE_NOTIFICATIONS allow` (it may
need repeating after an Android or app update), or turn off *Enhanced notifications* in Android's notification
settings (which also removes suggested replies).

**The text is never stored by this app** — not in its database, logs, backups or sensors. It goes straight into Chat
(Household Chat's *Phones may post forwarded messages* App setting must be on). Only the time and the chat of each
forward are kept. **Home Assistant itself** records the Last notification sensor in its history; to keep the texts
out of it, add to `configuration.yaml`:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.*_last_notification
      - sensor.*_last_removed_notification
```

## Alerts

In App settings, each alert on or off with its recipients (admins, the phone's owner, or both):

- **Offline**, with the battery at the last check-in (and optionally the last zone, for phones whose owner shares it).
  **Alert when offline for at least** chooses when: as soon as it's Offline (the default), or after 30 minutes, 1, 2,
  4, 6, 10, 12 or 24 hours. That's the household's default; an admin can give a phone its own on its page (or when
  adding it). The phone is still shown Offline straight away — only the alert waits, and none goes out if it comes back
  sooner (then no Back online either). Its owner's do-not-monitor hours and pauses don't count towards the time, and a
  restart doesn't start the count again. The alert says how long: "Asha's Pixel has been offline for 10 hours (since
  05:02, battery was 4%)". *Example:* for a phone that's often off at night, set its Alert when offline to 10 hours.
- **App not reporting** — once it has lasted longer than the Offline time, and the phone's *Alert when offline* time
  if one is set ("Asha's Pixel: the HA app stopped reporting — signed out?").
- **Lost contact with all phones** — to admins.
- **Back online** (off by default) — when a phone that was Offline or App not reporting reports again; with an
  *Alert when offline* time, only if its alert had gone out.
- **Restarted** — with a guess: "battery was 3%, probably ran out" or "probably an update".
- **Battery low** — below 15%, not charging, once per discharge.
- **Charging reminder at night** (off by default) — at 22:00, each phone below 30% and not charging; optionally to
  the phone itself too.

Alerts are only about monitored phones, and never during their do-not-monitor hours. Each alert goes once per phone
per 30 minutes (cooldown), waits for the recipient's quiet hours (theirs from My
phone, or the household's), and never goes to the phone it is about (except the charging reminder, if chosen). It is
delivered to the recipients' phones (Home Assistant → Settings → People → Track device, plus any extra notify service
added under Admin → People), as a Home Assistant notification, and/or into each recipient's 📌 My room in Household
Chat. There is no escalation to other people. **Admin → Tests** sends a sample of each alert to you, and a test
forward to your own My room.

## Who can see what, and where your data goes

- Only the phones an admin added are monitored. Everyone sees their own phones and exactly what admins see about them
  (including that admins see your do-not-monitor hours but can't change them); admins see every monitored phone
  except while its owner pauses being watched or is in their do-not-monitor hours. People in a phone's forwarding chat
  see only Check now's result.
- **Children** (marked under Admin → People) can't pause being watched or set do-not-monitor hours; they still see
  what admins see and the log.
- The zone is shown only when the owner turns on **Share my last place**; coordinates never.
- Forwarded texts go only into Household Chat; this app keeps the time and the chat. Nothing leaves your Home
  Assistant except the Companion app's push for "please report" (through its push service) and what you post into
  Chat.

## Settings

### App settings (Admin → App settings)

- **Phones:** guess the kind of devices (on); "Not used lately" after 30 days.
- **Check-in:** interval (15, 10 or 5 minutes), ask after (2 minutes overdue), wait for a reply (5 minutes), requests
  before Offline (2), start-up grace (12 minutes after one interval), recognise lost contact (on: half the phones,
  at least 2, within 10 minutes).
- **Check now:** wait for a reply (60 s), at most 20 per phone an hour, people in a phone's forwarding chat may check
  it (on).
- **Alerts:** each on/off with recipients, alert when offline for at least (as soon as it's Offline), battery threshold (15%), charging reminder time and level (22:00, 30%),
  last place in offline alerts (off), cooldown (30 minutes), delivery, quiet hours.
- **Forwarding:** allowed (on), default disappear time (1 hour), which chats owners may choose, the default messaging
  apps, the starting filter for a group (codes only).
- **Transparency:** adults may pause being watched (on), longest pause (24 hours), owners may set do-not-monitor
  hours (on), at most 12 hours a day.
- **History:** kept 7 days.
- **Home Assistant:** sensors in Home Assistant (off), answer the Household Assistant (on), the App-messages signing
  key (the same in every household app).

Changes apply at once.

### The app option (Configuration tab)

`admin_users` — the admins, by Home Assistant user name (login name) or user id. Display names are not accepted.
Restart the app after changing it.

### Sensors in Home Assistant (optional, off by default)

- `binary_sensor.phones_<phone>_online` (connectivity, monitored phones): on while the phone is Online; attributes
  status, battery, last check-in. Unavailable while its owner pauses being watched or is in do-not-monitor hours.
- `sensor.phones_online_count`: monitored phones online (attribute `total`).
- **Check now from Home Assistant:** Home Assistant doesn't let an app create a button entity through its API, so
  instead fire the event `household_phones_check_now` with the phone's id (from its address, `#/phone/<id>`) or the
  `<phone>` part of its sensor's entity id. The result comes back as the event `household_phones_check_result`
  (`phone`, `check`, `result`: online, no_reply or cant_ask). A script to put on a dashboard as a button:

```yaml
script:
  check_ashas_phone:
    alias: Check Asha's phone
    sequence:
      - event: household_phones_check_now
        event_data:
          phone: asha_s_pixel_8
```

The states are posted again every 5 minutes (Home Assistant forgets them when it restarts) and removed when the
switch is turned off.

### The Household Assistant

"Is my phone online?", "which phones are offline?", "what's Asha's phone battery?" and "check Asha's phone" (runs
Check now and links to the result). Admins can ask about any monitored phone, everyone else about their own and the
phones that forward into their chats; a paused phone answers "paused by <owner>", one in do-not-monitor hours "not
monitored until 07:00". Forwarded messages are never answered.

## Backups

**Admin → Storage → Download a backup** saves the whole database (phones, settings, history, people, App settings;
the signing key is left out). **Restore a backup** (in the red box) replaces everything with a backup's. Home
Assistant's own backups include the app's data too.

## Troubleshooting

- **A phone doesn't appear:** the Companion app must be installed and signed in to this Home Assistant (Settings →
  Devices & services → Mobile App lists it). **Read Home Assistant again** on All phones.
- **"Your phone isn't monitored":** an admin adds it under All phones → Available devices → Add to monitoring.
- **Can't check:** Home Assistant couldn't send the request — check remote access (Home Assistant Cloud / Nabu Casa,
  or your own remote address) and that the Companion app can reach Home Assistant away from home.
- **App not reporting (signed out):** open the Companion app and sign in again. If Home Assistant names the phone's
  notify service differently, set it on the phone's page.
- **Late every 15 minutes, then Online:** the phone reports only when asked — set the Home Assistant app's battery
  use to *Unrestricted*.
- **A phone shows Online although it's switched off:** open its page → **Check-in details**. The last check-in says
  which sensor it came from; if something other than the phone keeps counting, the Ignored list shows what is being
  filtered out. Turn on **Last update trigger** in Manage sensors so the phone says when it shuts down.
- **Check now says No reply although the phone is on:** turn on **Battery power** or **Last update trigger** in
  Manage sensors (the command only sends sensors that changed).
- **Nothing is forwarded:** check the switch, the household's App setting, the Last notification sensor (on, with
  notification access and your messaging apps allowed), and Household Chat's *Phones may post forwarded messages*.
  A refusal from Chat shows on the phone's page.
- **Codes arrive as "Android hid it":** see Android 15 above.
