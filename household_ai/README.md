# Household AI

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

An AI model that runs on your Home Assistant machine's own CPU — no GPU, no second computer, nothing sent to an
outside service — for the other household apps: the Household Assistant, Household Docs, Calorie Tracker,
Household Arcade, Receipt Price Intelligence and Finance Dashboard. Each of them uses it as an ordinary Ollama
address in its own AI settings.

- **Models chosen with the numbers in front of you**: a short recommended list with download size and memory
  needed, any other model from Ollama's library, downloads with progress. Nothing is downloaded on its own.
- **One model in memory for every app**, kept loaded in the day and unloaded sooner at night.
- **A fair queue**: one request at a time, the assistant first (a person is waiting), at most two waiting per
  app.
- **Safe by default**: the other apps can ask the model questions, never download, delete or replace models;
  only apps inside Home Assistant can reach it unless you publish it, and access keys are a switch away.

🐢 **Not fast**: on a CPU a short text answer takes seconds, a receipt photo or a statement page from about 30
seconds to several minutes. 64-bit machines only (amd64, aarch64 — a Raspberry Pi 4 or 5 with 8 GB works for
the small text models).

See the [documentation](DOCS.md) to set it up.
