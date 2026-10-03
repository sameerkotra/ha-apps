# Receipt Price Intelligence

> **Unofficial app.** Not made or supported by Home Assistant or Nabu Casa. Built with Claude (Anthropic's AI); review
> it before you rely on it.

Photograph receipts, read them with your own vision model, and keep track of prices, spending and where to shop for
less: a shopping list that knows where each item is cheapest, a trip planner, price alerts and Home Assistant sensors.
Everything stays on your network.

- Everyone signs in with their Home Assistant account; administrators come from the app's `admin_users` option.
- Every other setting (the model, imports, maps, web search, notifications) is in **Admin → App settings** and applies
  without a restart.
- Sidebar menu on a computer, bottom bar on a phone, three themes (Midnight, Slate, Daylight).

You need a vision-capable model on your own network (Ollama, or an OpenAI-compatible server such as vLLM or LM
Studio). The app doesn't include one.

Setup, settings and troubleshooting: [DOCS.md](DOCS.md) (also on the app's Documentation tab). What changed:
[CHANGELOG.md](CHANGELOG.md). How it is built: [spec/SPEC.md](spec/SPEC.md) and `docs/`.

Supports `amd64` and `aarch64`.
