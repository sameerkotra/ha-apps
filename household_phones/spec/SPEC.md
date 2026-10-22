# Household Phones — spec

The household's phones through the Home Assistant Companion app, with **two modes**:

Nothing is monitored by default: **an admin adds the phones to monitor** (§2); a device that isn't added is left
alone entirely.

1. **Phone mode** (everyone). Each monitored phone checks in with Home Assistant every **15 minutes** by default (5 or
   10 as a setting). It does this on its own through the Companion app; Home Assistant only asks a phone to report
   when it hasn't. A person sees only their own phone(s), and exactly what admins can see about them (§9).
2. **Admin mode** (admins). One page with all phones: status, battery and every other detail Home Assistant has about
   each mobile device.

Plus **Check now** (§4): a "please respond" sent on demand, e.g. when you're expecting a forwarded message. It says
within seconds whether the phone is online and when it last forwarded something.

Plus **phone actions** (§15): Find my phone, Lost mode, Screen on and the Companion app's other commands — each one
the owner lets others use —, children's bedtime, storage and battery-health alerts, an outdated-app check, one-tap
phone setup with Test everything, and one view per person.

Plus **message forwarding** (§6): a phone's last notification (a text, an OTP…) is forwarded as a message into a
**Household Chat** chat. By default it **disappears after 1 hour**.

It follows the repository's app pattern (HA_ADDON_PATTERNS.md, shared code in `common/`). This file is the app's own
spec, written from the project's draft 6 and updated to what was built; **§14 lists the decisions taken while
building, and the phase-1 questions answered by fallbacks.**

## 1. Packaging

- `household_phones/`, ingress-only (port 8106), `panel_title: Phones`, `panel_icon: mdi:cellphone-check`,
  `panel_admin: false`: everyone opens the panel; Phone mode for everyone, Admin mode for admins.
- `config.yaml` options: `admin_users` only. Every setting is in **Admin → App settings**.
- `homeassistant_api: true`, used for: the device and entity registries (WebSocket commands) and states (REST);
  `notify.mobile_app_*` (the report request); `persistent_notification.create` (alerts); publishing sensors (optional);
  events (check-ins, Check now from scripts); the household app bus (Chat, Assistant). `hassio_api`, `auth_api`,
  `docker_api`, `full_access` false; no `NET_RAW`, no host network, no ports.
- Shared code (common/manifest.json): Python auth_core, db_core, settings_core, web_security, backup_core,
  sensor_publisher, ha_client, ha_ws, ha_time, ha_people, ha_notify, people_admin, deeplinks, housekeeping, app_bus,
  assist_tools, whoami; static backnav, ui, settings (+css), themes, theme-boot, whoami, people, deeplink; the test
  helpers (fake_ha, fake_ha_bus, packaging_core, …).

## 2. Phones (and devices that aren't phones)

- **Discovery** (`phones.py`): Home Assistant's mobile_app devices — a device whose config entry is a `mobile_app`
  entry (`config_entries/get` with `domain: mobile_app`), or whose identifiers include `mobile_app`
  (`config/device_registry/list`), with its `mobile_app` entities (`config/entity_registry/list`). Read at start-up,
  every 5 minutes, and a few seconds after `device_registry_updated` / `entity_registry_updated`. A device appears
  once the Companion app is installed and signed in. A device gone from Home Assistant stays listed
  (`in_registry = 0`) until removed.
- **Monitored only when an admin adds it.** Discovery lists every mobile_app device; in Admin mode All phones shows
  the ones not added under **Available devices** (with the kind guess) and **Add to monitoring** on each: one dialog
  sets the owner (pre-filled from discovery), name, kind (Phone or Tablet), interval and *Alert when offline for at
  least* (§5). A device that isn't added
  (`monitored = 0`) is never asked to report, its reports aren't written, it gets no alerts and no history, isn't
  counted in the summary strip or the lost-contact rule, and doesn't appear in Check now, the bus answers
  (`phones.forwarding_into`), the Assistant or the Home Assistant sensors. It has no My phone page: its owner sees
  only "Your phone isn't monitored — ask an admin" (its name, nothing else). Once added, interval + the start-up
  grace (12 minutes) pass before it can be Offline. **Stop monitoring** (admins; ⋯ and the phone's danger zone)
  asks whether to keep or delete its history (history, Check-now log, forward times); forwarding is switched off
  and the device goes back to Available devices.
- **Sensors by key:** the entity's unique id without the registration's webhook prefix (`<webhook>_battery_level` →
  `battery_level`); the tracker is `device_tracker`. Read: battery_level, battery_state, is_charging, last_reboot,
  last_update, wifi_connection / ssid, network_type / connection_type, last_notification, …
- **Kind:** Phone or Tablet (own sections in All phones); the guess "Watch or other device" (`ignored`) lists it last
  under Available devices. Guessed from the model and name (watches → other; iPad, "Tab", SM-T/SM-X, Fire HD… →
  Tablet; App setting), chosen by the admin when adding it. The kind never decides whether a device is monitored.
- **Owner:** the Home Assistant user whose Companion app registered the device: the registration's `user_id` when Home
  Assistant hands it over, else the person whose **Track device** list holds the device's tracker (ha_people). An
  admin can correct it (then discovery leaves it alone); changing the owner switches forwarding off.
- **Notify service:** `mobile_app_<slug of the device name>` as Home Assistant builds it (`ha_people._notify_name`,
  numbered `_2` when HA had to), overridable per phone on its page.
- **Replaced phones:** the admin or the owner (from My phone) marks a new device **Replaces <old phone>**: name, kind,
  owner, monitoring, settings, do-not-monitor hours, forwarding rules, history, checks and forwards move to the new
  device; the old one is retired. A new device that isn't monitored offers this on its "isn't monitored" card when
  its owner has an older monitored phone.
- **Not used lately:** a monitored phone with no check-in for 30 days (setting) → listed with **Remove** (history
  deleted) or **Keep**. Never removed automatically.
- **Per phone:** display name (default "<owner>'s <model>"), kind, monitored, its own interval, owner, notify service,
  its offline alert delay (§5; admins only); the owner's do-not-monitor hours.

## 3. Phone mode: checking in

**Check-in interval** (App settings; per-phone override): 15 minutes by default, or 10 or 5.

**The phone reports by itself.** A **check-in** is a genuine report from the phone: a `state_reported` or
`state_changed` event on one of its sensors (`sensor.` / `binary_sensor.`) or its location (`device_tracker.`: a new
position, a re-send or a change of zone — the phone updates it whenever it moves or wakes), even with an unchanged value
(`monitor.on_state_event` → `monitor.classify`, at the event's own `last_reported`, else `last_updated`). Bursts are
written at most every 20 s per phone, except while a request or Check now waits.

**What doesn't count** — Home Assistant writes the Companion app's states with an empty context (no user, no parent),
exactly like its own rewrites, so the event's context can't tell them apart; these rules do:
- a `state_changed` with no old state (an entity added, or restored after a restart or a reload), and the next value
  after `unavailable` / `unknown`;
- a new state `unavailable` or `unknown`;
- anything while Home Assistant (re)starts: from `homeassistant_start` / `homeassistant_stop` until 2 minutes after
  `homeassistant_started`, and for 2 minutes after the event WebSocket reconnects (usually a restart; on the app's first
  connection the grace ends at once when `GET /api/config` says Home Assistant is already RUNNING);
- an event without `last_reported` / `last_updated` — never "now";
- after the phone said it is **shutting down** (the Last update trigger sensor's state contains "shutdown", e.g.
  `android.intent.action.ACTION_SHUTDOWN`; kept in `phones.shutdown_at`): reports within 60 s still count (its last
  words); the next genuine report after that — changed or not, since a phone left locked mostly re-sends unchanged
  values — means it's back on and ends it. A powered-off phone sends nothing, so it goes Late at I + 2 and Offline at
  I + 12 after its last genuine report;
- a report older than the last check-in.

**Check-in details** (the admin's phone page, collapsed): "Last check-in 14:02 — from Battery level (reported,
unchanged)" (`phones.checkin_entity` / `checkin_kind`; also on the history's Late / Offline rows), and the last 20
counted check-ins and the last 20 ignored updates with the reason ("HA restart", "unavailable", "added or restored by
Home Assistant", "before the request"…; consecutive ones with the same reason fold into one with a count), in memory.
Entity names, event kinds, times and reasons only: never a state — the Last notification sensor is just "Last
notification sensor".

**Home Assistant asks only when a phone is overdue.** Silent for interval + **2 minutes** → `command_update_sensors`
through `notify.mobile_app_<phone>` (`{"message": "command_update_sensors"}`). No reply within **5 minutes** → asked
once more. Commands don't count toward the 500 pushes/day. A phone that reports on time is never sent anything. A
check-in within the wait after a request is an *answer* (learnt per phone: `answers_requests`); any other is
*spontaneous*. iPhones (§11 phase 5) are never asked.

**Status** (I = the interval; `monitor.compute`, a pure function):
- **Online:** a check-in within I + 2 minutes.
- **Late:** overdue; the app is asking.
- **Offline:** no check-in for I + 2 + requests × wait (I + 12 minutes; 27 at the default). The offline alert goes out
  (at once, or after the phone's offline alert delay, §5).
- **App not reporting**, with the reason and the fix: *signed out or uninstalled* (the notify service is missing, or
  the request is refused as a registration error); *a needed sensor is off* (Battery level disabled, when it would
  otherwise be Offline). A phone that only reports when asked is **not** flagged (since 1.3.3): the Companion app sends
  only values that changed, so a phone lying still sends little on its own even with Unrestricted battery use; answering
  a request means it's fine. Its alert goes out only once it has
  lasted longer than the Offline time ("Asha's Pixel: the HA app stopped reporting — signed out?").
- **Can't check:** the request itself failed (§3.1); never Offline; retried every wait period.
- **Paused** (alerts or watching, §3.1, §9), **Not monitored (until 07:00)** (the owner's do-not-monitor hours,
  §3.1), **Unknown** (just added, or not monitored).
- The wait before asking (2), the wait for a reply (5) and the number of requests (2) are App settings.

### 3.1 Avoiding false alarms

- **Start-up grace:** after this app starts (Home Assistant restarting restarts it too), no phone is marked Offline
  and no offline alert sent until one interval + 12 minutes (setting) has passed; the pages say "Starting — waiting
  for phones to check in". The same grace applies to one phone after an admin adds it and after each of its
  do-not-monitor windows ends.
- **Lost contact:** when half or more of the monitored phones (not in do-not-monitor hours or the grace after them) (and at least 2; settings) went Late within the same 10
  minutes, one alert "Home Assistant has lost contact with the phones (5 of 6 silent since 14:02)" goes to the admins
  as a Home Assistant notification plus their phones; individual offline alerts are held until contact returns, then
  only the phones still Offline are reported.
- **The push service can't be reached:** a failed request (an HA error, a relay error, a rate limit) → **Can't
  check**, with "Home Assistant couldn't send the request — check remote access / Nabu Casa".
- **Pause alerts** (owner or admin): 1, 4 or 12 hours, until a time, or until back online. Status stays visible,
  check-ins continue, Check now still works.
- **Do-not-monitor hours** (the owner, per phone; `dnm.py`): one or more windows of days of the week plus a start
  and an end in Home Assistant's time zone (ha_time), crossing midnight allowed — Mon–Fri 22:00–07:00 covers Monday
  22:00 to Tuesday 07:00; back-to-back windows join. During a window: no "please report" (automatic or Check now:
  "Not monitored right now — Asha set do-not-monitor hours until 07:00"), no alerts of any kind sent, held or queued
  for later (queued ones for that phone are dropped; a restart is noted silently), no history rows for status
  changes, check-ins aren't written; the status is **Not monitored (until 07:00)** and admins see only that line (no
  battery, network or live details, as for a watching pause). Forwarding goes on (the owner's own choice) unless the
  window has **Also pause forwarding** (off by default; forwards then aren't sent). Admins see that the hours exist
  ("Not monitored weekdays 22:00–07:00") on All phones and the phone's page, and can't change them. App settings:
  *Owners may set do-not-monitor hours* (on; off: no window applies) and at most N hours a day (12; counted per
  calendar day, overlaps once), shown in the owner's editor. Children's phones can't have them (marking a child
  clears them). These replace 1.0's daily quiet times (held offline and battery alerts), which were migrated into a
  window on every day with the same times.
- **Phase-1 check — `state_reported`:** see §14.

**My phone page** (monitored phones only): status ("Online · last check-in 6 min ago"), battery and charging, network,
last restart, **Check now**; once an admin adds a phone, a **"Your phone is now monitored — set up forwarding if you
want it"** card with the checklist and Set up forwarding (closed with Got it, `welcome`); then the phone's own page:
pauses, **do-not-monitor hours**, the **setup checklist** (companion app signed in; remote access;
the sensors used; a sensor that changes with every report; battery optimisation *Unrestricted*; Android 15+ codes,
notification access and the allow list when forwarding; Chat refusing forwards), **What admins see** with the log of
who checked it and the alerts about it, **Share my last place**, **Forward my messages**, **Replaces <old phone>**,
**Stop monitoring my phone**. Plus **Check <owner>'s phone** cards (§4) and the person's own alert quiet hours.

## 4. Check now

1. Sends `command_update_sensors` at once (`checknow.start`). Only a genuine check-in (§3) made strictly after the
   request was sent answers it: a report from before it (even one arriving later) or older than the last check-in
   never completes a check.
2. A live result card (`#/check/<phone>`, polled every 1.5 s), waiting up to **60 seconds** (setting):
   - **✅ Online** — "replied in 4 s"; battery, charging; Wi-Fi *Home* / mobile data; last restart; and, for phones that
     forward, the **last forward** ("last message forwarded 14:02 into *Phone codes*" / "nothing forwarded in the
     last 24 h") — time and chat only.
   - **⏳ No reply yet** — "no reply; last seen 13:48 on mobile data, battery 9%", **Try again**. The phone is then
     watched as overdue (`overdue_from`): asked again after the wait, Offline if it stays silent.
   - **⚠️ Can't ask** — the request failed (which, in words), or an iPhone ("Can't ask an iPhone — last check-in hh:mm").
3. A forward that arrives while the card is open is noted ("a message was just forwarded into *Phone codes*").

**Where:** My phone (own phones), Admin mode (⋯ menu and phone page), **Check <owner>'s phone** cards for people in a
phone's forwarding chat (result card only), Household Chat's **📱 Check phone** (it asks `phones.forwarding_into`),
the Household Assistant (`phones.check`), and Home Assistant (with Sensors in Home Assistant on): the event
`household_phones_check_now` `{phone}` and the result event `household_phones_check_result` (§7).

**Limits:** one per phone per 30 seconds (extra presses join), at most 20 per phone per hour (setting; 429). Every
check is in the phone's log: who, when, via, result; the owner sees it.

## 5. Admin mode: all phones

- **Summary strip** (monitored only): monitored · online · offline · late · paused (pauses and do-not-monitor hours).
- **Not used lately** when there are any; **Available devices** (not monitored) after the monitored ones.
- **Phone list**, problems first (Offline, App not reporting, Can't check, Late): status dot and last check-in; name,
  owner and model; battery with charging; Wi-Fi network or mobile data; companion app / OS version; the owner's
  do-not-monitor hours; ⋯ (Check now, Pause alerts, Edit, Stop monitoring). Tablets in their own section.
- **Phone page:** the device (manufacturer, model, OS, companion app version, owner, kind); its settings; **every
  enabled sensor** grouped (Battery, Network, Activity, Storage, Location zone — only if the owner shares it —, Last
  reboot and updates, Notifications — never their text —, Other); disabled sensors by name ("off on the phone");
  while an offline alert delay is counting, a hint under the status ("⏳ Offline 2 h 14 min — alert after 10 h", "not
  counting now" during do-not-monitor hours or a pause);
  **history** (kept 7 days) with the battery at each change, grouped by day; Check now at the top; a danger zone
  (Stop monitoring — keep or delete the history —, Remove). A device not monitored shows only what discovery knows
  and Add to monitoring.

**Alerts** (App settings), each on/off with recipients (admins / the owner / both):
- **Offline**, with the battery at the last check-in (+ last zone with *Last place in the offline alert*, for owners
  who share it).
- **Alert when offline for at least** (`offline_alert_after_min`; shown when the Offline alert is on): as soon as it's
  Offline (the default), 30 minutes, 1, 2, 4, 6, 10, 12 or 24 hours. Each phone can use the household's default or
  its own choice (`phones.offline_alert_after_min`, NULL = default), set by admins on the phone's page or in Add to
  monitoring — not by owners. The phone is still marked Offline (status, list, history unchanged); only the alert
  waits, until the phone has been offline for that long, counted from when it became Offline. Time in the owner's
  do-not-monitor hours and while alerts or watching are paused doesn't count (the count pauses and goes on after);
  the start-up grace and Late / Can't check in between neither reset nor end it. The spell is kept on the phone's
  row (`offline_since`, `offline_counted_s`, `offline_counted_at` — NULL while not counting —, `offline_alerted`,
  `app_alerted`), so an app or Home Assistant restart resumes it; time while the app was stopped counts except the
  do-not-monitor hours and pauses in it. The spell ends when the phone is back online: no Offline alert if it comes
  back sooner (also when it comes back during a do-not-monitor window), and **Back online** only if the spell's
  Offline or App-not-reporting alert went out (at once, Back online follows an Offline or App not reporting status, as
  before). The text says how long: "Asha's Pixel has been offline for 10 hours (since 05:02, battery was 4%)". Cooldown,
  quiet hours (queued) and lost contact (held) apply when it fires. **App not reporting** waits the same delay ("…
  Unreachable for 10 hours (since 05:02)"); **Lost contact** is never delayed.
- **App not reporting**; **Lost contact** (admins); **Back online** (off by default); **Restarted** with a guess
  ("battery was 3%, probably ran out" when ≤ 5%; "probably an update" after an OS or app version change in the last 6
  hours); **Battery low** (below 15%, not charging, once per discharge); **Charging reminder at night** (off; 22:00,
  30%; optionally to the phone itself).

For every alert: only about monitored phones, never during their do-not-monitor hours; cooldown 30 min per phone per
alert; quiet hours per recipient (their own, or the household's), queued until they end (dropped if the phone is in
do-not-monitor hours by then); the phone's pause; never sent to the phone it is about (except the charging
reminder, optional); delivered to the recipients' phones (ha_notify: Home Assistant's people + extra services), a Home
Assistant notification, and/or Household Chat (`chat.message.post` as the recipient into their 📌 My room). No
escalation.

## 6. Forwarding messages into Household Chat

**Source:** the phone's **Last notification** sensor (Android). Each `state_changed` event is handled on its own from
the event's new state, on a worker thread, never by reading the sensor again (`forwarding.py`). Needs notification
access and the messaging apps in the sensor's allow list (checklist).

**Who:** only the owner switches **Forward my messages** on (and off), and only on a monitored phone — after an admin
adds it, the owner decides; all forwarding settings appear only to the owner of a monitored phone. Admins can't switch
it on for them; the admin's **Forwarding allowed** switches it off for everyone; Stop monitoring switches it off.
During do-not-monitor hours it goes on unless that window also pauses forwarding.

**Where:** default the owner's **📌 My room**; or a group or direct chat they belong to (`chat.chats.list`); an admin
may allow only the My room, or listed groups. Picking a group shows "N people will see your forwarded messages" and
suggests a small group.

**What is sent:** one Chat message: label "📱 Asha's Pixel · Messages · 14:02", text "<title>\n<text>". Disappears
after 1 hour by default (1 day, 7 days, 30 days; the admin sets the default). **The text is never stored by this app**
— database, logs, backups, sensors: the envelope is posted to the bus directly, held in memory until Chat answers, and
only the time and target chat of each forward are kept (`forwards`). Deleted rows are overwritten (`secure_delete`).

**Filtering** (owner): which apps; **codes only** (a 4–8 digit number within 40 characters of code / OTP / one-time /
passcode / PIN / verification / 2FA…), **containing…** words, or **everything** (confirmed first); a group starts on
codes only; quiet hours **hold** (memory only) or **drop**.

**Loop guard:** never notifications from `io.homeassistant.companion.android*` (which carries Household Chat's and
this app's notifications); an identical text from the same phone within 2 minutes is dropped (a keyed hash in memory).

**Home Assistant history:** HA records the sensor's text itself; the setup notes (All phones) and DOCS show the
`recorder: exclude: entity_globs: sensor.*_last_notification` YAML.

### 6.1 Android 15+: one-time codes hidden

Recognised by `forwarding.REDACTED_TEXTS` (a constant: "sensitive notification content hidden" and similar, anywhere
in the text, any case) or an empty text under a title. Forwarded as "📱 A code arrived on Asha's phone (14:02,
Messages, from AX-BANK) — Android hid it."; the checklist shows ⚠️ with the ADB grant
(`adb shell cmd appops set --user 0 io.homeassistant.companion.android RECEIVE_SENSITIVE_NOTIFICATIONS allow`) or
turning off *Enhanced notifications*, until a normal message is forwarded after it.

### 6.2 Quirks

- **Long texts:** `android.bigText` when present, else `android.text`; "(shortened — see the phone)" when it ends with
  an ellipsis, comes only from the 255-character state, or is over the 4000-character limit.
- **Grouped notifications:** a summary ("3 new messages", or `android.summaryText` without text) → "📱 3 new messages
  on Asha's phone".
- **Several in quick succession:** each event handled from its own new state.
- **Other chat apps:** not in the default list; added by the owner; title and text shown as they come.
- **Duplicates:** the same text from two apps within 2 minutes goes once.

## 7. Settings (Admin → App settings)

| Group | Keys (default) |
|---|---|
| Phones | `guess_kind` (on), `min_app_version` ("" — optional, §15.6), `unused_days` (30) — nothing monitored until an admin adds it (§2) |
| Check-in | `interval_min` (15; 10, 5), `ask_after_min` (2), `wait_reply_min` (5), `requests` (2), `startup_grace_min` (12), `lost_contact_on` (on), `lost_contact_window_min` (10), `lost_contact_min_phones` (2) |
| Check now | `check_wait_s` (60), `check_per_hour` (20), `check_by_chat_members` (on) |
| Alerts | `alert_offline` (on) + `_to` (admins), `offline_alert_after_min` (0 = at once; 30, 60, 120, 240, 360, 600, 720, 1440 minutes), `alert_app` (on) + `_to` (both), `alert_lost_contact` (on), `alert_back` (off) + `_to`, `alert_restart` (on) + `_to`, `alert_battery` (on) + `_to` (owner), `battery_low_pct` (15), `alert_charge` (off) + `_to` (owner), `charge_time` (22:00), `charge_pct` (30), `charge_to_phone` (off), `alert_storage` (on) + `_to` (owner), `storage_low_pct` (10), `alert_battery_health` (on) + `_to` (owner), `alert_last_place` (off), `alert_cooldown_min` (30), `alert_delivery` ([phones]; phones / ha / chat), `quiet_start` / `quiet_end` ("") |
| Forwarding | `forwarding_allowed` (on), `forward_disappear` (1h), `forward_chats` (member; own_room / member / listed), `forward_groups` ([]), `forward_apps` (Google Messages, Samsung Messages), `forward_group_filter` (codes) |
| Phone actions | `actions_allowed` (on), `find_per_hour` (20), `actions_per_hour` (30), `find_restore_min` (5), `lost_ring_min` (10), `lost_location_min` (5) |
| Children's bedtime | `bedtime_after_min` (5) |
| Transparency | `adults_may_pause` (on), `pause_max_hours` (24), `dnm_allowed` (on), `dnm_max_hours` (12) |
| History | `history_days` (7) |
| Home Assistant | `ha_sensors` (off), `ha_find_event` (off), `assistant_answers` (on), `bus_signing_key` (secret) |

**Sensors in Home Assistant** (off by default): `binary_sensor.phones_<phone>_online` (monitored phones; connectivity;
unavailable while watching is paused or in do-not-monitor hours), `sensor.phones_online_count`. **Check-now buttons:** a `button.` entity can't be created
through the REST API (a posted state has no press action), so this is the **event alternative**:
`household_phones_check_now` `{phone: <id or the <phone> part of the entity id>}` (listened for while the switch is
on, rate limits apply), answered by `household_phones_check_result` `{phone, check, result}`; DOCS has the script.

## 8. Pages and navigation

Single-page app (`app/static/app.js`), `#/` addresses with the shared back trail (BackNav: `isTab` for `#/`,
`#/phones`, `#/admin`; `parentOf` for sub-pages; `replace` for admin tabs):

| Address | Page |
|---|---|
| `#/` | My phone (Phone mode) |
| `#/mine/<id>` | one of my (monitored) phones: welcome card, pause, checklist, forwarding, do-not-monitor hours, what admins see, log, Replaces, stop request |
| `#/check/<id>` | Check now's live card (starts or joins a check) |
| `#/phones` | All phones (admins): monitored phones by status, Available devices (Add to monitoring dialog) |
| `#/phones/person` | All phones grouped by person (the toggle uses `BackNav.replace`) |
| `#/phone/<id>` | a phone's page (admins; Stop monitoring dialog: keep or delete history) |
| `#/admin/<tab>` | App settings, People (child flag, notify services), Tests (each alert, a test forward), Storage (backup; restore in a danger zone) |

Shell: tabs in the header on a computer, a bottom bar (My phone, All phones, Admin — at most 5) under 760 px with the
safe area; the user menu (How the app sees you, Theme, Admin); UI.loading / UI.empty / UI.errorBox; `.summary-strip`,
`.ui-group-head`, `.danger-zone`, `aria-current`. Deep links: `/<page>/check/<id>`, `/phone/<id>`, `/mine/<id>`,
`/phones` (deeplinks.py + deeplink.js).

## 9. Trust and transparency

- **Only what an admin added is monitored**; the owner of a device that isn't sees only that it isn't.
- **What admins see** (My phone): the exact list, the log of who checked the phone, and the alerts sent about it;
  it states the owner's do-not-monitor hours ("Not monitored weekdays 22:00–07:00"), that admins see that they exist
  but can't change them, and that during them they see only "Not monitored (until …)".
- **Adults can pause being watched** for up to 24 hours (settings): no requests, no alerts, admins see only "Paused by
  Asha until 18:00" (no status, battery, network, sensors; the API leaves them out); only the owner ends it.
- **Do-not-monitor hours** (§3.1): the owner's schedule of windows with nothing asked, alerted or recorded, up to the
  household's daily limit.
- **Children** (Admin → People → Child; never an admin): can't pause being watched or set do-not-monitor hours; still
  see what admins see and the log.
- **Location:** the zone only with **Share my last place** (off by default); coordinates are never kept. Lost mode posts
  the place only with Share my last place, coordinates only with the owner's *Exact location in Lost mode* (off).
- **Phone actions** (§15): the owner chooses which actions others (admins) may use — Find my phone and Screen on by
  default, nothing else, and Lost mode only if allowed; admins can't change it (on a child's phone they decide, and the
  child sees it). Every use is in the phone's log, which the owner sees. **What admins see** lists which actions others
  may use, whether Home Assistant automations may ring the phone, and what Lost mode would post.
- **Forwarding** opt-in by the owner; admins see that a phone forwards and into which chat, never the messages.
- **Leaving:** "Stop monitoring my phone" sends the admins a request (an alert to admins); an admin can stop
  monitoring any phone.

## 10. Household Assistant and Household Chat (app bus)

- `phones.status` (`person`, `which: all | problems`): "Is my phone online?", "which phones are offline?", "what's
  Asha's phone battery?". `phones.check` (`person`): runs Check now after the answer is committed and answers with
  the result of a check finished in the last minute, else "Asked … to report now", with a link to `/check/<id>`.
  Admins any monitored phone; others their own and the phones forwarding into their chats; paused → "paused by
  <owner>"; do-not-monitor hours → "not monitored until 07:00"; devices not monitored aren't known.
  Forwarded messages are never answered. Link targets: `/`, `/phones`, `/phone/<id>`, `/check/<id>`.
- **The contract with Household Chat** (shared file, both builds): Phones answers **`phones.forwarding_into`**
  `{requested_by, chat_id, my_room}` → `{phones: [{id, name, owner_name}], page: "#/check/"}` from the database only
  (Chat waits 4 s and caches 60 s): the phones forwarding into that chat (or the person's My room) that the person
  may Check now (owner, admin, or a member of that chat as Chat last told Phones). Only from `household_chat`.
  Phones uses **`chat.chats.list`** (the owner's chats; one chat's `member_ids`, asked when the target changes and
  hourly) and **`chat.message.post`** (`requested_by` = the owner, `chat_id` or `my_room`, `label`, `text`,
  `disappear_after`). A nack is kept as the phone's `fwd_error` and shown on My phone; `not_allowed app` = Chat's
  *Phones may post forwarded messages* is off (not retried).

## 11. Phases (all built in this release)

1. Check-in and Phone mode. 2. Admin mode, alerts, replaced and unused devices, transparency. 3. Chat `message.post`,
📱 Check phone (Chat), forwarding. 4. App-bus answers, optional HA sensors and the Check-now event. 5. **iPhone:**
check-in only from its own reports (no Last notification or Last reboot sensors, no `command_update_sensors`): never
asked, Check now "Can't ask an iPhone — last check-in hh:mm", no forwarding.

## 12. API (ingress; JSON)

Everyone: `GET /api/me`, `GET /api/whoami`, `GET /api/my` (with `unmonitored`: name only), `PUT /api/me/quiet`,
`GET /api/phones/{id}` (owner page, admins also `admin`; 409 "isn't monitored" for the owner of a device not added),
`PUT /api/phones/{id}/mine` (sharePlace, welcomeSeen), `PUT /api/phones/{id}/dnm` (owner only: `windows`), `POST|DELETE /api/phones/{id}/pause`,
`PUT /api/phones/{id}/forwarding`, `POST /api/phones/{id}/forwarding/chats` → `GET /api/requests/{rid}`,
`POST /api/phones/{id}/stop-request`, `POST /api/phones/{id}/replaces`, `POST /api/phones/{id}/check`,
`GET /api/checks/{cid}`, `GET /api/check/{id}/latest`, `POST /api/phones/{id}/actions/{action}` (owner, or admins
as allowed), `PUT /api/phones/{id}/consent` (owner; admins on a child's phone: `allow`, `fromHa`, `lostExact`,
`lostContact`, `lostMessage`), `POST|DELETE /api/phones/{id}/lost`, `POST /api/phones/{id}/lost/chats`,
`POST /api/phones/{id}/test-everything` (owner). Admins: `PUT /api/admin/phones/{id}/bedtime`; `GET /api/admin/phones`, `POST /api/admin/refresh`,
`PUT|DELETE /api/admin/phones/{id}`, `POST /api/admin/phones/{id}/monitor` (ownerId, name, kind, intervalMin, offlineAlertAfterMin; also on `PUT`),
`POST /api/admin/phones/{id}/unmonitor` (keepHistory), `POST /api/admin/phones/{id}/keep`, `GET|PUT /api/admin/settings`,
`GET /api/admin/people`, `PUT /api/admin/people/{uid}` (`isChild`), the notify routes, `POST /api/admin/test-alert`,
`POST /api/admin/test-forward`, `GET /api/admin/connected`, `GET /api/admin-storage-download-db`,
`POST /api/admin-storage-import-db`.

## 13. Data (`/data/phones.db`)

`users` (id, name, username, is_child, own alert quiet hours), `user_notify`, `phones` (the device, kind, owner,
settings, monitored and since when, the welcome card, check-in and request state, status, pauses, do-not-monitor
windows (JSON), share place, alert memory and the offline alert delay with its current spell, forwarding settings and
its last error — no message text; the owner's action choices, Lost mode's state, Find my phone's restore values,
bedtime, the storage and battery-health memory), `history`, `checks`, `actions` (who used which action, when, how and
the result), `forwards` (phone, time, chat name, kind), `alert_state`
(cooldowns, held), `alert_queue` (alerts waiting for a recipient's quiet hours), `bus_requests`, `app_state`
(lost contact), `app_settings`, and the bus tables. In memory only: Home Assistant's devices, entities and latest
states (the Last notification state blanked), held forwards, unanswered forward envelopes, the duplicate hashes.

## 14. As built: decisions and phase-1 fallbacks (2026-10-21)

**Phase-1 unknowns, handled by fallbacks (no real phone could be tested):**
- **`state_reported` events** (§3.1): subscribed to; if none has arrived in the last 30 minutes, the app looks at
  every phone sensor's `last_reported` (else `last_updated`) once a minute (`monitor.poll_states`) — self-detecting,
  shown on All phones → Setup notes ("events" / "polling"); it also runs while the event WebSocket is down. It skips
  `unavailable` / `unknown` and Home Assistant's start-up grace (when a restart restores the sensors). All of a phone's sensors moving to the same instant is a normal report: the Companion
  app sends them in one batch.
- **Does `command_update_sensors` get a reply when nothing changed?** Self-detecting per phone (`answers_requests`
  once it has answered one); until then the checklist asks for an always-changing sensor (**Battery power** or **Last
  update trigger**) and ticks it when one is on. Check now's "No reply" says what to check (Troubleshooting).
- **Android 15's redaction text:** a constant list (`forwarding.REDACTED_TEXTS`) matched anywhere in the text, plus
  the empty-text pattern; the checklist re-checks after each hidden code.

**Deviations and choices:**
- **Owner:** Home Assistant's WebSocket API doesn't return a config entry's `data`, so the registration's `user_id` is
  used when present, else the person tracking the device (Settings → People → Track device); admins correct it.
- **Companion app version** comes from the registration data when Home Assistant gives it; otherwise "—" (the device
  registry's `sw_version` is the OS version).
- **Check-now buttons** are an event, not `button.` entities (§7).
- **Forwards bypass the bus outbox** (which stores bodies for 7 days) to keep the "never stored" promise; they are
  re-posted from memory at most 3 times with the same id. A shared `app_bus.send(..., store=False)` would make this
  cleaner.
- **Alerts in Chat** go into each recipient's own My room, posted as them (the contract has no app identity).
- **Held forwards** (quiet hours, hold) live in memory and are lost if the app restarts during the quiet time.
- Status is worked out every 30 seconds (sooner after a check-in a request or check was waiting for).
- **1.1 (do-not-monitor hours, monitored only when added):** databases from 1.0.0 are migrated once
  (`db.migrate_1_1`): a device stays monitored only if it was watched *and* an admin had taken it on (confirmed under
  New devices, edited its name, owner, interval or notify service, or marked it as a replacement); the rest become
  not monitored. Saved daily quiet times become a do-not-monitor window on every day (not on children's phones).
  Do-not-monitor hours are counted per calendar day; forwards during a window that pauses forwarding are not sent
  (not held).

## 15. Phone actions, Lost mode, bedtime, health, setup (1.4)

Shared rules (`actions.py`): **who** — the owner always; admins only for the actions the owner allows others
(`act_allow`; default Find my phone and Screen on; for a child's phone admins choose, the child can't); nobody else.
**Never** on a device not monitored, while the owner pauses being watched (others), during the owner's do-not-monitor
hours (except Find my phone, and Lost mode, started by the owner), with *Phone actions* off, or on an iPhone except
Find my phone (critical alert) and Lost mode (ring and location only). **Rate limits** per phone: Find my phone once per
30 s and `find_per_hour`; other actions once per 5 s each and `actions_per_hour`. Commands don't count toward the
Companion app's 500 notifications a day; Find my phone's alarm notification and spoken message, Lost mode's notification
and rings, and Test everything's notification do. **Logged**: every use in `actions` and the phone's history (who, what,
via, result), shown to the owner (and admins) as *Actions on this phone*. A refusal says why in words (403/409/429).

### 15.1 The actions (notify payloads, from the Companion app's documentation)

| Action | Payload(s) | One-time phone permission |
|---|---|---|
| Find my phone (Android) | `command_dnd` `off` (only when the Do Not Disturb sensor says it's on); `command_volume_level` `{media_stream: alarm_stream, command: <max>}`; a notification `{ttl: 0, priority: high, channel: alarm_stream, importance: max, tag, persistent, sticky, visibility: public, actions: [Found it]}`; `TTS` `{tts_text, media_stream: alarm_stream_max}` | Do Not Disturb access |
| Find my phone (iPhone) | a notification with `push.sound {name: default, critical: 1, volume: 1.0}` and Found it | Critical Alerts allowed |
| Screen on | `command_screen_on` | — |
| Flashlight | `command_flashlight` `turn_on` / `turn_off` | — |
| Ringer | `command_ringer_mode` `normal` / `vibrate` / `silent` | Do Not Disturb access |
| Do Not Disturb | `command_dnd` `priority_only` / `alarms_only` / `total_silence` / `off` | Do Not Disturb access |
| Volume | `command_volume_level` `{media_stream, command: level}` (alarm, ring, notification, music, call, system) | Do Not Disturb access |
| Brightness | `command_screen_brightness_level` 0–255 | (may ask: modify system settings) |
| Screen timeout | `command_screen_off_timeout` ms | (may ask: modify system settings) |
| Open an app | `command_launch_app` `{package_name}` | display over other apps |
| Open a dashboard | `command_webview` `{command: /path}` | display over other apps |

The notify call's answer only says Home Assistant sent it; the result therefore adds "If nothing happens, the phone
needs … — open the HA app on the phone". **Find my phone** keeps what to put back (`find_restore`: the alarm volume from
the Volume level sensor, Do Not Disturb's mode); **Found it** (`mobile_app_notification_action` with the
`HP_FOUND_<token>` action) or `find_restore_min` later: `clear_notification` for its tag, the old alarm volume and Do Not
Disturb mode sent back. Without a Volume level sensor the volume stays at its maximum (said in the result); on Android 15+
a Do Not Disturb the user turned on can't be turned off by Home Assistant — the alarm still sounds when Do Not Disturb
lets alarms through (said in the result). The ringer mode isn't touched: the alarm stream sounds on silent and vibrate.

### 15.2 Lost mode (`lost.py`)

Started by the owner, an admin when the owner allows *Lost mode* for others, admins on a child's phone; optional end time
(≤ 7 days), ring every N min (`lost_ring_min`), location every N min (`lost_location_min`; the UI warns about battery),
a chat for the place (the starter's 📌 My room or a chat they choose from `chat.chats.list`; posted as them, disappearing
after a day unless *keep*). Each round (every 30 s): the first time a persistent, public notification "If found, please
call <contact> — <message>" (owner-editable; `authenticationRequired` on its **I found it** action) — then every ring
interval the alarm notification, the alarm volume and `command_screen_on` (no spoken message, to save notifications),
and every location interval `request_location_update`. The place goes into the chat (directly, never through the
outbox) only with Share my last place: the zone name; with *Exact location in Lost mode* also the address and
coordinates, read from Home Assistant at that moment and never stored; only when it changed. **Ends** when turned off,
at the end time, with I found it, or when the phone is unlocked: the Device locked sensor going off after it started;
without it, the Interactive sensor going on after it started and more than 2 minutes after the app last woke the screen
(a finder pressing the power button then ends it too — the card recommends the Device locked sensor). Ending clears the
notification and puts the volume / Do Not Disturb back. Shown as a red banner on My phone, the phone's page and All phones,
and a chip on the row. During do-not-monitor hours it runs only if the owner started it.

### 15.3 Ring from the Household Assistant and Home Assistant

`phones.find` (`acts`: the assistant confirms) with the button's rights. The event `household_phones_find` `{phone}`
(listened for with `ha_find_event`): the person is the event's `context.user_id` (who pressed the dashboard button or
ran the script) — as if they pressed it here; without one, only phones whose owner allows *from Home Assistant
automations*. Answered with `household_phones_find_result` `{phone, result: sent | failed | refused}`. DOCS has the
script for a wall tablet.

### 15.4 Children's bedtime (`bedtime.py`)

Admins set windows (as do-not-monitor hours), the parents to tell and *Do Not Disturb at bedtime* on a child's phone.
In bedtime, the Interactive sensor on for more than `bedtime_after_min` without a break → one quiet alert
(`importance: low`, iOS `interruption-level: passive`) to the parents per night (the night = the date 12 hours before)
per phone. DND: `command_dnd priority_only` at the start, `off` at the end (and when the owner stops being a child).
The child sees it on My phone, in What admins see and in the checklist (Interactive sensor on).

### 15.5 Storage and battery health (`health.py`)

Storage (Internal storage: the state is the % free) below `storage_low_pct` → once, again after recovering by 2
points; Battery health worse than good → once per change. The usual alert rules and a Health card on the phone pages.

### 15.6 Out-of-date HA app

The Companion app's version (Current version / App version sensor, else the registration's) against the newest on the
household's monitored phones with the same system, and against `min_app_version` (Android). "HA app outdated" chips on
All phones and My phone, the Health card, and a checklist item. (The morning summary was dropped.)

### 15.7 Set up this phone, Test everything (`phonesetup.py`)

A QR code (drawn by `static/qr.js`, copied from Household Vault with its checks) and links: `homeassistant://navigate/<the
app's sidebar page>` (the Companion app's deep link, Android and iOS; opens My phone) and
`https://my.home-assistant.io/redirect/supervisor_ingress/?addon=<slug>`. The Companion app has no deep links to its own
settings or sensor pages, so the steps are written out. **Test everything** (owner, once per 30 s): the checklist, a
Check now (its result follows on the page), a test forward into the owner's My room when forwarding is on, a test
notification → a pass / fail list, logged.

### 15.8 One view per person

My phone shows each of the person's monitored devices (phones and tablets) as stacked cards with status, battery,
Check now and the actions (⋯). All phones has a *By status / By person* toggle (`#/phones/person`; API `people`).
Watches are listed under Available devices and can be monitored as a Phone or Tablet.

