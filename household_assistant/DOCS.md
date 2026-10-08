# Household Assistant

Ask in plain words about what the household apps know, and get a short answer with links back to each app.
Source and issues: https://github.com/sameerkotra/ha-apps

## Getting started

1. Install the app, then on its **Configuration** tab add your Home Assistant user name to **admin_users**
   and start it. Until someone is listed, every page shows **No admin yet** with the exact name to add (select
   your name at the top right: "How the app sees you").
2. Open **Assistant** in the sidebar, then **🛡️ Admin → App settings** and set up the AI (below).
3. Keep the apps' answers out of Home Assistant's history (below), then tick **I have excluded household_apps
   from Home Assistant's recorder** in **App settings → Privacy**.
4. In each household app you want the assistant to ask, an admin turns on **Answer the Household Assistant**
   (that app's App settings). Household Todo, Household Docs, Household Chat, Household Arcade, Family Tree and
   Calorie Tracker have it on already; Finance Dashboard, Receipt Price Intelligence and Splitpot keep it off
   until you choose, because money is private.
5. **Admin → Apps** lists every app that offered its tools, with what each can answer. **Refresh** asks them
   again.

## Setting up the AI

**Admin → App settings → AI**: choose the provider, its address and the model, then **Test connection**.

- **Ollama** (your own computer or server): the address, e.g. `http://192.168.1.10:11434`, and a model such as
  `llama3.1:8b` or `qwen2.5:7b`. Nothing leaves your network.
- **OpenAI-compatible** (OpenAI, OpenRouter, Groq, LM Studio, …): the API base (blank = OpenAI's), the model,
  and an access key where the service needs one.
- **Anthropic** Claude: the model (e.g. `claude-sonnet-4-5`) and an access key.

**Tool calls**: the assistant lets the model pick the apps' tools with its own tool calling where the model has it
(Claude, GPT, Qwen 2.5, Llama 3.1 and newer), and asks other models for a short JSON plan instead — it notices which
on the first question. With tool calling, the answer usually comes in the same step as the last plan, one model call
fewer per question. If a model picks tools badly, set **Tool calls** to *Always ask the model for a JSON plan*.

The access key is stored in the app's database, never shown again, never logged, and left out of backups.
With a provider outside your network, the page says so above the ask box: the question, the last few questions
and answers, and what the apps returned are sent to it.

## Keeping answers out of Home Assistant's history

The apps send their answers over Home Assistant's event bus as `household_apps` events. Home Assistant's
recorder would otherwise keep them in its database, where anyone with access to Home Assistant's history could
read them. Add this to `configuration.yaml` and restart Home Assistant:

```yaml
recorder:
  exclude:
    event_types:
      - household_apps
```

The app can't check this for you; until you tick it in **App settings → Privacy**, admins see a reminder.

## Asking

- Type a question in the box at the top and press **Enter** (Shift+Enter for a new line), or tap a suggestion. On a phone, 🎤 uses
  the browser's own speech recognition where it has one; nothing is recorded by the app.
- While it works you see which apps it is asking, with **Stop**. 🔊 on an answer reads it aloud; a question asked with 🎤 is read aloud when it's answered (the browser's own voice). A question takes at most 5 minutes (Admin → App settings → Limits → *Longest a question may take*); if the model runs out of time after the apps answered, you see what the apps said instead; with Ollama, a model that hasn't been used for a few minutes is woken up first ("Waking up the model…"), which doesn't count.
- Answers appear under the box, the newest first (**Earlier questions** at the bottom). Each is short, with **Sources**: one link per app it used, straight to the right page in that app.
- **What was shared** (under the answer) lists each question the assistant asked an app and exactly what the
  app returned.
- **Changes need a tap.** When you ask for a change, the answer shows a button with the exact change. Nothing is
  sent until you tap it, and the app checks it as if you made the change yourself. After the tap the button shows
  what happened, with a link. What it can change, where those apps answer: add a task, tick one off or move it to
  another day (Todo); add an expense split equally (Splitpot); log a food, with the calories on the button
  (Calorie Tracker); make a note, tick or untick a checklist item, add an item (Docs); add to the shopping list
  (Receipt Price Intelligence).
- **What can I ask?** lists what each app can answer for you.
- Your questions are yours alone: nobody else, admins included, can see them. They are kept 30 days (App
  settings → Limits); **Clear my questions** deletes them now.

Each app answers only what you could see in it yourself, and only while both its admin's **Answer the Household
Assistant** and your own **Let the Household Assistant answer for me** (that app's settings, on unless you turn
it off) are on. Household Vault is never asked: passwords stay there.

## Morning and evening briefings

**🌅 Briefings** (under the ask box) sends you a short summary on your phone:

- **Morning**: 📋 today's tasks and what's on the schedule (Household Todo), 🎂 birthdays and anniversaries this
  week (Family Tree), 💳 bills due in the next 3 days (Finance Dashboard), 🤝 who owes whom (Splitpot).
- **🌙 Evening**: 📋 tomorrow's tasks and schedule, 🎂 birthdays in the next two days and 💳 bills due in the next
  two — to get ready the night before.
- Each only says what the apps that answer you have to say (the day's tasks are always there). Switch each one on,
  choose its time (evening: 20:00 unless you change it) and every day or weekdays. Each comes once a day, at that
  time or up to three hours later (if the app was stopped at that time), never later.
- It goes to the phones Home Assistant links to you (**Settings → People → you → Track device**, the Home Assistant
  app on your phone). Tapping it opens the assistant.
- **Read it aloud**: choose a **speaker** (any Home Assistant media player) and the briefing is also spoken there,
  starting "Good morning, Asha." It uses Home Assistant's text-to-speech (the first `tts.` entity, e.g. Google
  Translate or Piper — the dialog says when there is none). Leave it on *No speaker* for the phone only.
- It needs no AI model: it is what the apps said, as they said it, so it costs nothing. It also appears in your
  questions as "Morning briefing" or "Evening briefing", with Sources and **What was shared**.
- **Send a morning one now** / **Send an evening one now** make and send one at once, to try it.

## Follow-up questions

The assistant sees your last few questions in this conversation, its answers, and which apps and tools it used for
them — so "and next week?", "what about Meera?" or "only the overdue ones" carry on from the last answer, asking
the same app again with the change.

## Asking with your voice: Assist

The assistant can answer **Assist**, Home Assistant's own voice assistant ("What's on my list today?" to a phone,
a voice satellite or the Assist dialog), through a small companion **integration** from this repository:

1. **Install the integration.** With HACS: HACS → ⋮ → **Custom repositories**, add
   `https://github.com/sameerkotra/ha-apps` as an *Integration*, then install **Household Assistant**. Without
   HACS: copy the repository's `custom_components/household_assistant` folder into your Home Assistant
   configuration folder's `custom_components`. Restart Home Assistant.
2. **Add it**: Settings → Devices & services → **Add integration** → *Household Assistant*.
3. **Turn on Answer Assist** in this app: Admin → App settings → People.
4. **Choose it in a voice assistant**: Settings → Voice assistants → a voice assistant → *Conversation agent*:
   **Household Assistant**.

Each question is asked as the Home Assistant user speaking, with everything above (their own apps, switches and
limits; their question appears in their history here). A voice satellite doesn't say who is speaking: in the
integration's **Configure**, choose the person whose questions it asks, or leave it at *Nobody* and it says so.
The answer is spoken without its links; **Sources** and **What was shared** are on this page. Questions and
answers travel over Home Assistant's event bus like the apps' answers (keep `household_apps` out of the recorder,
above). Assist waits up to 5 minutes for an answer (the integration's **Configure**).

## Admin

- **Apps**: each app that offered tools, its switch here (both this one and the app's own must be on), whether
  it was heard from in the last day, any problem with its tool list, and **Connected apps**.
- **App settings**: AI; **People → Children may ask** (off: people marked as children on the People tab can't
  ask; on: they ask only about their own things, from apps that let children ask); **Limits** (30 questions per
  person per hour, 200 per day for the household, keep questions 30 days); **Privacy** (the recorder tick; *Show
  "What was shared" open*).
- **People**: everyone who has opened the assistant, **May ask** and **Child**.
- **Usage**: questions, tool calls and tokens per day for the last 30 days — counts only, never who asked what.
- **Storage**: backups (below).

## Who can see what, and where data goes

- The app has no port on your network: only Home Assistant's sidebar (ingress) reaches it, and it uses each
  person's Home Assistant login. Admins can't ask "as" someone else.
- To answer, it asks the other apps over Home Assistant's event bus, as you. Their answers come back the same
  way (see the recorder above) and go only to your own page and to the AI model.
- The AI model gets the question, your last six questions and answers, the list of what the apps can answer and
  what they returned. With Ollama that stays on your network; with a cloud provider it goes to that provider.
- The apps' answers are treated as data: the model is told never to follow instructions inside them, it can
  only propose changes (each needs your tap), and links come only from the apps themselves, checked to open that
  app's own page.

## Backups

**Admin → Storage → Download a backup** saves the whole database (everyone's questions, the apps' tool lists,
the App settings) without the AI access key. **Restore a backup** replaces all of it; this install keeps its own
access key. Home Assistant's own backups include the app's data too.

## Limits

At most 3 rounds of asking the apps and 8 questions to apps per question; each app has 20 seconds to answer
(after that it "didn't answer"); one question at a time per person; and the limits in App settings.

## Configuration (the app's Configuration tab)

- **admin_users**: Home Assistant user names or user ids of the app's admins (not display names). Restart the
  app after changing it.

## Troubleshooting

- **"The assistant's AI isn't set up"**: an admin sets it up in Admin → App settings → AI.
- **"None of the household apps answer the assistant yet"**: turn on **Answer the Household Assistant** in the
  apps (their App settings), make sure they run a version that can answer, then **Admin → Apps → Refresh**.
- **An app is greyed out**: it hasn't said hello on the event bus for a day — is it running?
- **"X didn't answer"**: the app took longer than 20 seconds or isn't running. Ask again.
- **"You've turned off answers from X"**: turn **Let the Household Assistant answer for me** back on in that
  app.
- **Strange answers**: small local models sometimes misread the plan format. A larger model, or a clearer
  question ("spending on groceries in September"), helps; **What was shared** shows what the apps actually said.
