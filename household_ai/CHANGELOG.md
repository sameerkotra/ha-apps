# Changelog

## 1.0.2

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.

## 1.0.1

- **Answers work**: the app's image now includes Ollama's `llama-server`, the program that runs a model (before,
  every answer failed with "llama-server binary not found"), with the libraries it needs. A missing `llama-server`
  is shown on the Status tab instead of failing on the first request.
- **Starts with Home Assistant's time zone**: the app no longer stopped at start-up while reading it.

## 1.0.0

- **First release**: Ollama on the Home Assistant machine's CPU, behind a gateway on port 11434 that the other
  household apps use as an Ollama (or OpenAI-compatible) address. An admin page to turn the model server on and
  off, download and delete models (a recommended list with sizes and memory needed, or any model from Ollama's
  library), choose the model kept loaded in the day, see the machine's memory, disk and temperature, what is
  loaded and the queue.
- **A fair queue**: one request at a time; the Household Assistant goes first; at most 4 waiting (a setting) and 2
  per app; a request whose app gave up is dropped, or stopped if it was running; a run longer than 15 minutes (a
  setting) is stopped.
- **Limits for every request**: context length, longest answer and threads, whatever the app asks for.
- **Day and night**: the kept model is loaded at 05:00 and stays loaded until 22:00 (settings); other models, and
  every model at night, unload 10 minutes after their last use.
- **Access**: apps inside Home Assistant need no key; *Require an access key* turns keys on. Usage per app per day,
  without prompts or answers.
- Source: https://github.com/sameerkotra/ha-apps
