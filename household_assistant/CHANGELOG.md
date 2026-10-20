# Changelog

## 1.6.6

- **Back goes to the page you came from.** The phone's back gesture (and the browser's Back) now steps back through the pages you opened in the app, instead of jumping straight to the start page; dialogs still close first, and Back on the start page still leaves the app.

## 1.6.5

- **Tests only:** the Finance Dashboard can now say how income breaks down by category (Finance 1.13.0); the tests check that its own phrases ("income breakdown", "sources of income", "interest earned", …) reach that answer, that "How much did I earn in September?" still gets the month's figures, and that every existing quick-answer phrase still reads as before.

## 1.6.4

- **Tests only:** the Finance Dashboard no longer answers balance history or net worth (Finance 1.12.0); the tests now check that "What's our net worth?" and the balance questions reach Finance's latest balances instead.

## 1.6.3

- **Tests only:** checks that the Finance Dashboard's new questions (statements, card payments, balances over time, subscription changes, finance dates and the finance overview) reach the right answer, and that every existing quick-answer phrase still does.

## 1.6.2

- **Tests only:** the tests no longer leave a question waiting on an address that, on GitHub's machines, hangs instead of refusing — which made the next tests fail with "Still answering the last question." After each test, a question still being answered is now reported.

## 1.6.1

- **Fixes to one person's data**: The Data dialogs, after a check in the browser: the ⋯ Data menu stays on screen (it opens the other way near an edge or a bottom bar), a wrong name is caught before the copy is downloaded, the copy uses the export's usual choices, the red button is readable in every theme, and the name box only shows when it's needed. On a phone, the People table keeps each button on one line.

## 1.6.0

### New

- **One person's data** — Admin → People → **⋯ Data** on each person: **Export their data** (their briefing settings, and their questions and answers only when you tick it — the log records it), **Import into this person** or **Import a person's data…** above the list (Add to what they have, or Replace it after typing their name), and **Delete their data…** (a question still being answered is stopped first; keep them, empty, or forget them too). See the documentation's *Admin → People → Data*.

## 1.5.2

### Fixed

- Choosing a *Did you mean* answer on someone else's question always says there's no such question (it could say "still answering the last question" while one of your own was being answered).

## 1.5.1

### Fixed

- **Assist sometimes never heard the answer**: a question answered very quickly (a quick answer takes milliseconds) could finish before the app had noted where to send the answer, so the voice assistant waited and gave up. The answer now always goes back.

## 1.5.0

### New

- **The short answer first**: "any bills due?" starts with **Yes — 2 bills due**, "how many tasks today?" with **3 tasks · today**, and a long answer from an app gets a one-line count above it.
- **Two months side by side**: "did we spend more than last month?", "groceries this month vs last month", "Costco spending September vs August" are answered quickly — both months, the difference and the percentage. Other comparisons still go to the AI.
- **More changes in plain words, still only as buttons**: "bought the milk" (ticks it off the shopping list), "move the dentist to Friday", "I spent 200 on fuel at Shell" (a hand-entered spend in Finance Dashboard), "make a note: the plumber comes Tuesday", "tick passport on the packing checklist" and "add sunscreen to the packing checklist".
- **Family by what you call them**: "when is mum's birthday", "how old is my wife", "Ravi's sister's birthday" — Family Tree works out who.
- **Hindi and Hinglish**: "aaj kya kaam hai", "kal kitna kharcha hua", "doodh khatam ho gaya", "kisko kitna dena hai", "is mahine ka bijli bill" and more.
- **Other names for the same thing**: dahi or yogurt finds the list's Curd, washing up liquid its Dish soap, "d mart" and "D-Mart" are Dmart, and the names people gave an item in Receipt Price Intelligence work too.
- **The same question again is instant**: asked again within a minute, the last answer is reused (marked *as of … ago*); any change made through the assistant starts afresh. App settings → *Reuse an answer for*.
- **Learning from rephrasing**: when a question isn't understood and you ask again in other words that are, the first way of asking is remembered — used once that has happened twice (App settings → *Learn from rephrasing*).
- **Suggestions that know your habits**: what you usually ask on this weekday or at this time of day comes first (⏰), for you only.
- **Admin → Quick answers** has more:
  - **Words**: teach a word for a kind of question ("rota" → tasks) or another name for something ("thayir" → Curd). Words from the questions that weren't answered are suggested, with how many they would answer.
  - **How fast the apps answer**: each app's calls, typical and slow times, refusals and timeouts over the last 7 days.
  - **Who asks**: how many questions each person asked and how many had a quick answer — counts only, never what anyone asked — and the household's most asked kinds of question.
  - **Replay**: the last 100 quick answers read again with today's rules, listing the ones now understood differently (as patterns, never with who asked).
  - **Move to another install**: download your phrases and words as a file and import them elsewhere.
- Needs Receipt Price Intelligence 1.3.4 (ticking items off) and Finance Dashboard 1.4.4 (a spend added by hand) for those two changes.

## 1.4.0

### New

- **Quick answers, in a second or two, without the AI**: the common questions — today's tasks, the shopping list, prices, bills, spending, balances, who owes whom, birthdays, calories, unread messages, a document, scores — are matched by rules and answered with the app's own words and links, no model involved. A question it can't place goes to the AI as before (App settings → Quick answers → *When a question isn't understood*).
- **Many ways of asking the same thing**: typos, text-speak ("wat do i hav 2day"), no punctuation, number words ("in the next two weeks"), filler ("hey, can you tell me…") and the household's own names — Todo lists, Splitpot groups, the shopping list's items, Finance's categories, Arcade's games, the people — are understood. Over 850 real phrasings are checked on every build.
- **What it understood** shows above a quick answer (*Your tasks · today · Household Todo*), with **Not what I asked**, which asks the AI instead (or shows the other meanings without it).
- **Did you mean**: a close call shows two or three choices; the one you tap answers, and that way of asking is remembered for the household. By voice (Assist), answer "the first one" or "tasks".
- **Follow-ups and several questions at once**: "and tomorrow?", "what about next week" change only the time; "tasks and birthdays this week" answers both.
- **Changes are still buttons**: "add milk and bread to the shopping list", "remind me to call mum at 5pm", "I paid 40 for dinner", "log 2 rotis for lunch" become buttons — nothing changes until you tap.
- **Use AI** (Admin → App settings → AI): off, the assistant never calls a model — not for questions, learning or a connection test — and answers what it can quickly. Its settings stay saved. The assistant now also works with no model at all.
- **Admin → Quick answers**: *Test* how a question is understood, add your own phrases ("trash day" → the schedule), and see the questions that weren't answered quickly (words and counts only, never who asked).
- **Learning**: phrases are also learned from the AI's answers (used once seen twice; *Use what the AI learned straight away* to change that). Children's choices aren't learned; unused phrases go after 180 days.
- The household apps can teach their own words for their tools (Household Todo 2.7.3, Receipt Price Intelligence 1.3.3 and Finance Dashboard 1.4.3 do).

### Fixed

- Asking again straight after an answer could say "Still answering the last question" for a moment.

## 1.3.2

### Security

- **Signed messages between the apps (optional)**: App settings → Household apps → *App-messages signing key*. With the same key in every household app (and in the Assist integration's options), every message is signed and messages that aren't are refused, so no other app on Home Assistant's network can send questions or answers as one of them. Empty (the default): as before.
- **A new app on the bus starts switched off** on Admin → Apps until an admin turns it on; the household's own apps start on as before. A tool named for one of the household's apps (`todo.…`) is taken only from that app, and a tool name two apps offer is offered by neither.
- **A tool that doesn't say it is about the person counts as household-wide**: children never get it.
- **Clearing your questions no longer resets the hourly limits** (questions per hour, briefings per hour); two "Send me one now" taps at once can't both slip under the limit.
- **Speakers and quiet hours for briefings**: App settings → Briefings → *Speakers briefings may use* (empty: every speaker, as before) and *Quiet hours* (empty: never quiet), when a briefing isn't read aloud — it still goes to the phone.
- **The AI access key goes only where it was saved for**: after changing the provider or address, type the key again (saving and Test connection both ask for it).
- An app's answer (or an earlier answer) containing `</data>` can no longer close the block the model is told to treat as data; earlier questions and answers are marked as data too.
- The values the model asks a tool for are checked against the tool's own description (type, choices, range) before they are sent.
- **New people are marked New** on Admin → People (everyone new may ask and counts as an adult until you change it), with **Looks right** to dismiss.
- **Assist on voice satellites**: the companion *Household Assistant* integration 1.0.2 can turn off household-wide tools for voice satellites (*Voice: household-scope tools off*), and its options recommend a dedicated low-privilege Home Assistant user for them.

### Fixed

- **"Longest a question may take" above 300 seconds works again**: the model's retries were cut off at the shared 300 s.
- After **Stop**, a new question waits until the model has let go of the old one, so one person can't keep several model calls running.
- A question just asked could be marked "The app restarted while answering" by the hourly housekeeping; and an answer that arrived after that kept the error.
- A briefing at 23:00 missed while the app restarted now still goes until 02:00, as that day's.
- Live answers no longer re-read the question every second: they wake when it changes (at most 6 open per person).
- Faster pages: the person's details are written only when they change, history reads its tool calls in one go, Home Assistant's speakers are asked outside the database's transaction, and the page's files are cached by the browser until the next version.
- The companion integration 1.0.2: a question back ("Which month?") keeps Assist listening for the reply (Home Assistant's chat log); a busy app is asked once more; it says hello again when the app restarts (Connected apps); Settings → Repairs says when the recorder keeps the household apps' messages.

### Changed

- Request bodies are limited to 1 MB (a backup to restore: 1 GB), counted as they arrive.
- Download copies of the database left behind by a broken download are removed after 6 hours.
- **Shared code**: an optional signing key for the apps' messages (above), `no-store` on every API answer, the page's files cached until the next version, and faster database commits.

## 1.3.1

- **The button says exactly what changes**: a proposed change's button now reads "Household Todo · todo.items.add (list: Shopping, text: Milk)" — the app, the action and its details — and the model's own words are only a note under it.
- **A change is made once**: tapping its button twice (or in two windows) sends it once.
- **Only someone who may ask can confirm**: when an admin turns the assistant off for a person, or for children, their earlier proposed changes can't be confirmed any more.
- **At most three proposed changes per question.**
- **Assist asks only for the household's people**: questions come only from the companion integration, and only for someone who has opened the assistant or is one of Home Assistant's people — no one else is added. The companion *Household Assistant* integration 1.0.1 doesn't ask for a Home Assistant user that no longer exists or is turned off.
- **Send me one now** is limited to 5 briefings an hour, and a briefing is read aloud only on one of Home Assistant's own speakers.
- A name of only spaces no longer stopped the spoken greeting ("Good morning, there").
- **Shared code**: the apps take tool calls only from the assistant; AI requests never follow a redirect; no inline handlers in the pages; safer settings saves.

## 1.3.0

- **🌙 Evening briefing**: next to the morning one — tomorrow's tasks and schedule, birthdays in the next two days and bills due soon, at the time you choose.
- **Read it aloud**: choose a speaker (a Home Assistant media player) and the briefing is spoken there too, with Home Assistant's text-to-speech.
- **Follow-up questions** know which apps and tools answered before: "and next week?", "what about Meera?" carry on from the last answer.
- **More to ask** (with each app's update): "Remind me at 5 pm to call the plumber" (Todo), "Where's milk cheapest?" and "How are my budgets?" (Receipt Price Intelligence).

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
