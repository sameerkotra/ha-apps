# Changelog

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
