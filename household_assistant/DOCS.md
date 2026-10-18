# Household Assistant

Ask in plain words about what the household apps know, and get a short answer with links back to each app.
Source and issues: https://github.com/sameerkotra/ha-apps

## Getting started

1. Install the app, then on its **Configuration** tab add your Home Assistant user name to **admin_users**
   and start it. Until someone is listed, every page shows **No admin yet** with the exact name to add (select
   your name at the top right: "How the app sees you").
2. Open **Assistant** in the sidebar, then **🛡️ Admin → App settings** and set up the AI (below). It's optional:
   without it the common questions are still answered (**Quick answers**, below).
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

**Use AI** (on unless you turn it off): off, the assistant never calls a model — not for questions, learning or a
connection test — and answers only what it can answer quickly; everything else gets *That needs the AI, which an
admin has turned off* and the questions it can answer. The model's settings stay saved for when you turn it back on.
The page says *Quick answers only* under the ask box while it's off (or no model is set up). Turning it off doesn't
change Household AI or the other apps.

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

The app can't check this for you; until you tick it in **App settings → Privacy**, admins see a reminder. The
companion Assist integration (below) checks it from Home Assistant's side: while the recorder keeps these events,
it raises an issue in **Settings → Repairs**.

## Signing the apps' messages

By default any app on Home Assistant's network could send a message that claims to come from one of the household
apps. To stop that, choose a long random key and paste the same one into **App settings → Household apps →
App-messages signing key** of every household app — and into the Assist integration's **Configure**, if you use
it. From then on every message is signed and unsigned ones are refused. Set it in all of them within a few
minutes: an app with the key refuses the apps that don't have it yet. The key is never shown again and is left out
of backups; leave the box empty to keep it, or remove it to go back to unsigned messages.

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

## Quick answers

The common questions are answered in a second or two by rules, without the AI: the question is matched to one
app's tool, that app answers as it would for you, and its own words are the answer (with Sources, as always).

- **What it answers quickly**: your tasks (today, tomorrow, this week, overdue, a list, someone's), the schedule
  (bin day), your lists; the shopping list, prices, best prices, budgets, spending at a store; spending, a month's
  summary, bills due, recurring charges, account balances, spending at a shop or company; who owes whom, recent
  shared expenses; birthdays, someone's details, how two people are related; calories; unread messages; a document
  or checklist; Arcade scores; this month's spending against last month's ("did we spend more than last month?",
  "groceries September vs August"). Changes become buttons as always: add to or tick off the shopping list ("bought
  the milk"), add, tick off or move a task ("move the dentist to Friday"), a reminder, a shared expense, a spend
  you made ("I spent 200 on fuel at Shell" — on your own account in Finance Dashboard), a food to log, a new note,
  an item ticked or added on a checklist ("tick passport on the packing checklist").
- **The short answer first**: "any bills due?" starts with **Yes — 2 bills due**, "how many tasks today?" with
  **3 tasks · today**; a long answer from an app gets that one line above it.
- **However it's asked**: "what's on my list today", "wat do i hav 2day", "anything I need to do today?", "today"
  and dozens of other ways mean the same; typos are put right, number words read as numbers ("in the next two
  weeks"), and your household's own names are known — your Todo lists ("the Costco list"), Splitpot groups, the
  shopping list's items, Finance's categories and your accounts, Arcade's games, the people. Family by what you call
  them ("mum's birthday", "how old is my wife", "Ravi's sister" — Family Tree works out who), other names for the
  same thing (dahi or yogurt for the list's Curd, "d mart" for D-Mart, the names given to an item in Receipt Price
  Intelligence), and common Hindi and Hinglish ("aaj kya kaam hai", "kal kitna kharcha hua", "doodh khatam ho
  gaya").
- **What it understood** is shown above the answer (*Your tasks · today · Household Todo*). **Not what I asked**
  asks the AI instead, or, without the AI, shows the other meanings.
- **Did you mean**: when two meanings are close ("balances": who owes whom, or the bank accounts?), you get the
  choices. The one you tap answers, and that way of asking is remembered for the household. By voice, say "the
  first one" or the words of a choice.
- **Several at once**: "tasks and birthdays this week", "bills and balances" answer each part. **Follow-ups**:
  "and tomorrow?", "what about next week?" change only the time of the last quick answer.
- **The same question again** within a minute is answered at once from the last answer (*as of … ago* on the
  understood line); any change made through the assistant starts afresh (App settings → *Reuse an answer for*, 0 to
  always ask the apps).
- **Suggestions** under the ask box are what you ask most — and first, marked ⏰, what you usually ask on this day
  of the week or at this time of day. They're yours only.
- Questions that need thinking — *why*, *should I*, comparisons other than two months' spending, averages — go to
  the AI (App settings → Quick answers → *When a question isn't understood*: *Ask the AI*, or *Show what I can
  answer*).
- **The household teaches it**: a choice you tap (not a child's), a question the AI answered with one app's answer
  twice, and a question asked again in other words straight after it wasn't understood (twice), become phrases that
  are answered quickly from then on. Admins can add their own phrases and words (**Admin → Quick answers**).
  Phrases nobody has used for 180 days are dropped.

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
- **Read it aloud**: choose a **speaker** (one of Home Assistant's media players) and the briefing is also spoken there,
  starting "Good morning, Asha." It uses Home Assistant's text-to-speech (the first `tts.` entity, e.g. Google
  Translate or Piper — the dialog says when there is none). Leave it on *No speaker* for the phone only. An admin
  can limit which speakers may be chosen (**App settings → Briefings → Speakers briefings may use**; empty: all of
  them) and set **Quiet hours** (e.g. 22:00 to 07:00) when nothing is read aloud — the phone still gets it.
- It needs no AI model: it is what the apps said, as they said it, so it costs nothing. It also appears in your
  questions as "Morning briefing" or "Evening briefing", with Sources and **What was shared**.
- **Send a morning one now** / **Send an evening one now** make and send one at once, to try it. Each person gets
  at most 5 briefings an hour, these and the day's own together; clearing your questions doesn't reset that.

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

Each question is asked as the Home Assistant user speaking — someone who has opened this app, or one of Home
Assistant's people (Settings → People); a removed or turned-off user isn't asked for — with everything above (their own apps, switches and
limits; their question appears in their history here). A voice satellite doesn't say who is speaking: in the
integration's **Configure**, choose the person whose questions it asks, or leave it at *Nobody* and it says so.
Anyone in the room can talk to a satellite, so:

- Choose a **dedicated Home Assistant user with few rights** for satellites (not an admin), and mark it as a
  **Child** on this app's **Admin → People**: then it asks only about that user's own things.
- Or turn on **Voice: household-scope tools off** in the integration's **Configure**: questions from satellites
  then use only the tools about that person (their tasks, their food log, …), never household-wide ones
  (spending, balances, everyone's birthdays). Questions asked as yourself (the phone app, the Assist dialog) aren't
  limited.

The answer is spoken without its links; **Sources** and **What was shared** are on this page. When the answer is a
question back ("Which month?"), Assist keeps listening for your reply. Quick answers make voice quick too: the
common questions come back in a second or two, and a *Did you mean* is asked aloud ("Did you mean your tasks today,
or the shopping list?") — answer "the first one", "tasks" or "the shopping list". A change ("add milk to the
shopping list") still needs a tap on this page. Questions and answers travel over Home
Assistant's event bus like the apps' answers (keep `household_apps` out of the recorder, above; with an
app-messages signing key, enter it in the integration's **Configure** too). Assist waits up to 5 minutes for an
answer (the integration's **Configure**); if the app says it is busy, it asks once more a moment later.

## Admin

- **Apps**: each app that offered tools, its switch here (both this one and the app's own must be on), whether
  it was heard from in the last day, any problem with its tool list, and **Connected apps**. The household's own
  apps start switched on; any other app that offers tools starts **off** until you turn it on. A tool named for one
  of the household's apps (`todo.…` is Household Todo's) is taken only from that app, and a tool name two apps
  offer is offered by neither (the Apps tab says so).
- **App settings**: AI (with **Use AI**); **Quick answers** (on; *When a question isn't understood*; learn from
  choices, from the AI and from rephrasing; *Reuse an answer for*; *Quick call timeout*, after which the page says
  which app is slow); **People → Children may ask** (off: people marked as children on the People tab can't
  ask; on: they ask only about their own things, from apps that let children ask); **Limits** (30 questions per
  person per hour, 200 per day for the household, keep questions 30 days — clearing questions doesn't reset the
  hourly limit); **Privacy** (the recorder tick; *Show "What was shared" open*); **Briefings** (the speakers they
  may use, quiet hours); **Household apps** (the app-messages signing key, above). The AI access key is sent only
  to the provider and address it was saved for: after changing either, type the key again.
- **People**: everyone who has opened the assistant, **May ask** and **Child**. Someone new — they opened the app,
  or Assist asked for them — is marked **New** (everyone new may ask and counts as an adult): check them, then tap
  **Looks right**. Each person has a **⋯ Data** button (below).
- **Quick answers**: **Test** a question to see how it's understood — its words after clean-up, what they mean,
  the household names in it, and which answer wins (nothing is asked). **Phrases**: your household's own ways of
  asking ("trash day" → the schedule; times and names in a phrase work for any time or name), with where each came
  from; add or remove them. **Asked but not answered quickly**: the questions it couldn't place, in their cleaned-up
  words and how often — never who asked — each with **Test** and **Add as a phrase**. **Words**: a word your household
  uses for a kind of question ("rota" → tasks) or another name for something ("thayir" → Curd); words from the
  questions that weren't answered are suggested, with how many they'd answer. **How fast the apps answer**: each
  app's calls, its typical and slow times, refusals and timeouts over the last 7 days. **Who asks**: how many
  questions each person asked and how many had a quick answer — counts only, never what anyone asked — and the
  household's most asked kinds of question. **Replay**: the last 100 quick answers read again with today's rules,
  listing those now understood differently (as patterns, never with who asked). **Move to another install**:
  download the phrases and words as a file, and import one.
- **Usage**: questions, tool calls and tokens per day for the last 30 days — counts only, never who asked what.
- **Storage**: backups (below).

### Admin → People → Data

One person's data, for moving them to another install, giving them a copy, or starting them afresh. Only admins
see it.

- **⋯ Data → Export their data** downloads one `.zip`: their briefing settings (morning and evening, times, days,
  the speaker) and their name. **Include their questions and answers** (off) adds the questions they still have
  here and what the apps answered for each. It is off because it is the one way an admin could read someone's
  questions: leave it off to move someone's settings, tick it to give a person their own history. The app's log
  says who exported whom and whether it was ticked. The file isn't encrypted: keep it somewhere safe.
- **Import a person's data…** (above the list), or **⋯ Data → Import into this person**: choose the file, check
  what's in it, pick who it goes to and how.
  - **Add to what they have**: their own briefing settings are kept; a question asked at the same time with the
    same words is already there and skipped.
  - **Replace what they have**: their questions and briefings here are deleted first, then the file's come in.
    Type their name to confirm.
  - Questions older than **Keep questions for** aren't brought in (the preview says how many). Imported questions
    don't count toward anyone's hourly limit or the usage totals, and a change that was proposed but never tapped
    comes back as a record, not a button. **May ask** and **Child** are never taken from the file: you set them here.
    A speaker this Home Assistant doesn't have is kept and shown as not found.
- **⋯ Data → Delete their data…**: a question of theirs still being answered is stopped, then their questions,
  answers, briefings and hourly-limit marks go for good. **Keep them, empty** leaves them in the list with the same
  **May ask** and **Child**; **Forget them too** removes them as well — the next time they open the assistant or ask
  through Assist they are someone new, marked **New**. Type their name to confirm. Learned phrases and words stay
  (they store no person), and so do the usage totals. It can't reach backups made before, app messages already
  sent to other apps (gone within 7 days), or Home Assistant's own event history (see the recorder above).

## Who can see what, and where data goes

- The app has no port on your network: only Home Assistant's sidebar (ingress) reaches it, and it uses each
  person's Home Assistant login. Admins can't ask "as" someone else, and never see anyone else's questions —
  except in a person's export (Admin → People → Data) with *Include their questions and answers* ticked, which the
  app's log records.
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
(after that it "didn't answer"); one question at a time per person (after **Stop**, until the model has let go of
it); at most 6 answers followed live at once per person (more tabs fall back to asking every second); requests up
to 1 MB (a backup to restore up to 1 GB, a person's export file up to 64 MB); and the limits in App settings.

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
- **An app's tools don't show up**: an app that isn't one of the household's own starts switched off — turn it on
  in **Admin → Apps**.
- **Nothing answers after setting a signing key**: every household app (and the Assist integration) needs the same
  key; until then they refuse each other.
- **Strange answers**: small local models sometimes misread the plan format. A larger model, or a clearer
  question ("spending on groceries in September"), helps; **What was shared** shows what the apps actually said.
