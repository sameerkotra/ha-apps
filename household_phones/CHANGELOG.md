# Changelog

All notable changes to Household Phones. See the [repository](https://github.com/sameerkotra/ha-apps).

## 1.3.0

- **Fixed:** after Home Assistant restarted, the last message on a phone could be forwarded into Chat again. A restored Last notification sensor is no longer treated as a new message.
- **Fixed:** a switched-off phone could still show Online. Only reports the phone itself makes count as check-ins now — not Home Assistant restoring states after a restart, sensors going unavailable or unknown, updates without a time, or anything after the phone says it's shutting down. A phone that's off goes Late after 17 minutes and Offline after 27 (at the defaults).
- **Fixed:** Check now could say Online for a phone that's off. Only a report made after you pressed it counts as the answer.
- **Check-in details:** a phone's page (admins) shows where its last check-in came from — "Last check-in 14:02 — from Battery level" — and, folded away, the last 20 updates that counted and the last 20 that were ignored, with the reason. Never a message's text.

## 1.2.0

- **Alert when offline for at least…:** choose how long a phone may be offline before its alert goes out — as soon as it's Offline (as before), or after 30 minutes up to 24 hours. Set the household's default in Admin → App settings → Alerts, and give a phone its own on its page or when adding it to monitoring. For a phone that's often off at night, choose 10 hours.
- **Only the alert waits:** the phone is still shown Offline straight away. Nothing is sent if it comes back sooner, its owner's do-not-monitor hours and pauses don't count, and a restart picks the count up where it left off. The alert says how long: "Asha's Pixel has been offline for 10 hours (since 05:02, battery was 4%)". App not reporting alerts wait the same time.

## 1.1.0

- **Phones aren't monitored until an admin adds them.** All phones lists every Companion-app device under **Available devices** with a guess of its kind; **Add to monitoring** sets the owner, name, kind and check-in interval in one step. Until then a device is never asked to report, gets no alerts or history, and its owner sees only "Your phone isn't monitored — ask an admin".
- **Stop monitoring** puts a phone back under Available devices and asks whether to keep or delete its history.
- **Forwarding after you're added:** once an admin adds your phone, My phone says "Your phone is now monitored — set up forwarding if you want it", with the checklist. Forwarding stays your own choice.
- **Do-not-monitor hours** replace daily quiet times: set the days and hours your phone isn't monitored (e.g. weekdays 22:00–07:00). During them nothing is asked, no alert is sent or kept for later, nothing goes into its history, and admins see only "Not monitored (until 07:00)" and that the hours exist — they can't change them. Forwarding keeps working unless you pause it for that window.
- **New App settings:** owners may set do-not-monitor hours (on), at most 12 hours a day.
- **Updating from 1.0.0:** devices that were watched only because they were guessed to be phones are no longer monitored; phones an admin had confirmed or edited stay monitored. Your saved quiet times become do-not-monitor hours on every day.

## 1.0.0

- **First release.** The household's phones through the Home Assistant Companion app: each phone checks in on its own, and Home Assistant asks only a phone that is overdue.
- **My phone** for everyone: status, battery, last restart, a setup checklist, pauses and quiet times, and exactly what admins can see, with the log of who checked your phone.
- **Check now:** a "please respond" on demand with a live result — online, battery, network and the last forward — from My phone, All phones, Household Chat's 📱 Check phone, the Household Assistant, or a Home Assistant script.
- **All phones** for admins: problems first, tablets apart, new devices to confirm, devices not used lately, and a page per phone with every sensor and its history.
- **Alerts** for offline phones, the app not reporting, restarts, low battery and an optional charging reminder at night, with cooldowns, quiet hours, pauses and one alert when Home Assistant loses contact with all phones.
- **Forward my messages** (the owner's choice): codes or texts into Household Chat as disappearing messages; Android 15's hidden codes are recognised. The text is never stored by this app.
- **Sensors in Home Assistant** (optional): each phone's online sensor, the number online, and Check now from an automation.
