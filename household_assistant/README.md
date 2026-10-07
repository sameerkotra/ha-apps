# Household Assistant

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

Ask in plain words about what the other household apps know — "What's on my list today?", "How much did we
spend on food in September?", "Is milk on the shopping list, and where is it cheapest?", "Find the note about
the boiler" — and get a short answer with a link back to each app it came from.

The apps stay in charge of their own data: each one answers only what the person asking could see in it, and
only once its admin has turned on **Answer the Household Assistant**. Anything that changes data (add a task,
add to the shopping list, make a note) is only proposed: nothing happens until the person taps the exact change.
Under every answer, **What was shared** shows word for word what each app returned.

🤖 **Needed**: the assistant uses the AI model you choose — your own Ollama on your network, an
OpenAI-compatible service, or Anthropic Claude. Nothing is sent to an outside service unless you choose one.

It talks to the other apps over Home Assistant's own event bus, so it needs no extra permissions. See the
[documentation](DOCS.md) to set it up, including the one line that keeps the apps' answers out of Home
Assistant's history.
