# Changelog

## 1.2.0

- **🌅 Morning briefing**: each morning, at the time you choose (every day or weekdays), a short summary of your day on your phone — today's tasks and schedule, birthdays this week, bills due in the next 3 days, who owes whom — from the apps that answer you. It needs no AI model, appears in your questions too, and **Send me one now** tries it.
- **More the assistant can do** (with each app's update): tick off a task or move it to another day, add a Splitpot expense, log a food, tick or add a checklist item — all only after you tap; and answer more: who has which tasks, spending at one shop, account balances, someone in the family tree, your last 7 days of food.
- Admin → Usage counts each day by Home Assistant's day (the header said UTC).

## 1.1.3

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.
- **Usage counts by your day**: the questions-per-day limit and *Usage* counted UTC days, so a new day started in the evening. They now follow Home Assistant's zone.
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 1.1.2

- **How long each answer took**: under every answer, "⏱ Took 42 s" — from asking to the answer (also for a question
  that failed). Useful to see how a model on the Home Assistant machine's CPU is doing.

## 1.1.1

- **The ask box is at the top**, with the answers under it, the newest first; **Earlier questions** is at the
  bottom.

## 1.1.0

- **Answers read aloud**: 🔊 on an answer reads it out with the browser's own voice; a question asked with 🎤 is
  read aloud by itself when it's answered. Nothing is sent anywhere for this.
- **Answers appear as they happen**: the page follows a question live instead of asking every second, so each step
  ("Asking Household Todo…") and the answer show up at once; where a proxy doesn't allow that, it still asks every
  second.
- **The model picks tools its own way**: models with tool calling (Claude, GPT, Qwen 2.5, Llama 3.1 and newer)
  choose the apps' tools natively, which they do more reliably than writing a JSON plan, and usually answer in the
  same step — one model call fewer per question, which matters most on a CPU. Other models are noticed on their
  first question and get the JSON plan as before. App settings → AI → **Tool calls** can force the JSON plan.
- **Ask with Assist, Home Assistant's voice assistant**: with the companion *Household Assistant* integration from
  this repository (HACS or `custom_components`), Assist on a phone, a voice satellite or the Assist dialog asks
  here as the person speaking and says the answer. An admin turns it on: App settings → People → **Answer Assist**
  (off until then). See *Asking with your voice* in the Documentation.

## 1.0.2

- **The apps' answers aren't lost**: when the model runs out of time or fails after the apps have answered, the
  answer is what the apps said, in their own words, with a line saying the model didn't finish (before, the whole
  question failed although **What was shared** showed the apps' answers).
- **How long a question may take is a setting**: Admin → App settings → Limits → *Longest a question may take*,
  300 seconds by default (was a fixed 180), 60–900. A model on the Home Assistant machine's CPU needs 300 or more.

## 1.0.1

- **More time for an answer**: a question may take up to 3 minutes end to end (was 1), so a model on the Home
  Assistant machine's own CPU isn't cut off after its first step.
- **The model is woken up first**: when an Ollama model hasn't answered anything for a few minutes, the assistant
  sends it a short "hi" before the question so it is loaded; the page shows "Waking up the model…" meanwhile, and
  that wait isn't counted against the question's 3 minutes.

## 1.0.0

- **First release.** Ask about the household in plain words and get a short answer from what the household apps
  know, with links back to each app. Works with Household Todo, Household Docs, Household Chat, Household
  Arcade, Family Tree, Calorie Tracker, Splitpot, Receipt Price Intelligence and Finance Dashboard once each
  one's admin turns on **Answer the Household Assistant**. Changes are only proposed and need a tap; **What was
  shared** shows what each app returned. Questions are kept 30 days and only their owner sees them. Source:
  https://github.com/sameerkotra/ha-apps
