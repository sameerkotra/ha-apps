# Changelog

## 1.1.0

- **One row of tabs on phones**: the tabs scroll sideways instead of wrapping onto two or three rows, and *Use it in other apps* is now just **Use it**.
- **The page remembers its tab**: each tab has its own address, so a reload stays on it and Back returns to the tab you came from, then to Status.
- **Status at a glance**: a summary strip at the top shows the model server's state, the loaded model, the queue and free memory; *This machine* and *On my network* are folded below (opened by themselves when there is a warning).
- **Status no longer jumps**: the 5-second refresh updates values in place, so the log keeps its scroll position and a selection, and open sections stay open.
- **Downloads in progress at the top** of the Models tab; on phones the Recommended list shows one card per model.
- **Usage per app first**, then day by day with each day's totals.
- **Loading and errors**: every tab shows a spinner while it loads, and an error with **Retry** if it can't.
- **Restore is set apart** on the Storage tab, away from Download a backup.

## 1.0.6

- **Back goes to the page you came from.** The phone's back gesture (and the browser's Back) now steps back through the pages you opened in the app, instead of jumping straight to the start page; dialogs still close first, and Back on the start page still leaves the app.

## 1.0.5

### Security
- **The gateway answers only to this machine's names**: the app's host name, IP addresses, `localhost` and local names (`.local`, `.lan`, `.home.arpa`, `.internal`). A web page that points its own name at your machine is refused. Other names can be added under **App settings → Access → Other names for this machine** (new, empty by default).
- **A published port needs keys for other computers**: while port 11434 is published on your network and *Require an access key* is off, only apps inside Home Assistant are served; other computers are told to use a key. The Status tab says so.
- **A request whose body stalls** is answered "didn't arrive" after 30 seconds and frees its place, so slow senders can't block the gateway.
- **A right access key is always accepted**, even from an address that sent ten wrong ones (those still get "too many wrong keys" for a minute).
- Home Assistant's token is kept from the model server under both of its names.
- The settings page refuses requests over 1 MB, and backups over 256 MB.

### Fixed
- **An app that gives up while a model is loading** has its request stopped at once, instead of when the first word arrives; *Longest run* counts from the request's turn, model loading included.
- **The queue can't get stuck**: a request that fails in an unexpected way always frees its place.
- **Restarts and deletes wait their turn**: changing *Context length* or *Models loaded at once*, or deleting a model, waits for the answer running now instead of cutting it off.
- **A restored backup takes effect**: the model server is restarted (or turned on or off) when the restored settings say so.
- **Answer first works with access keys**: a key labelled *Household Assistant* counts as the assistant (on by default), and a new key can be made *Answer first* when it is created.
- Deleting a model has its own address, so a model whose name starts with `pull/` can't be mistaken for a download.

### Changed
- **Faster for many apps at once**: the Status page's checks and settings saves no longer hold up answers being streamed, a request naming a missing model looks the list up again at most every 5 seconds, and after an OpenAI-style answer the model's timer is reset only when it needs to be.
- OpenAI-style (`/v1`) requests are sent the *Threads* and *Context length* settings too (used by an Ollama that reads them).
- Backup copies a browser never finished downloading are cleared away within a few hours.
- Shared code: the page's files load faster (cached until the next version), API answers are never cached, and requests over the size limit are refused even without a declared size.

### Docker build (1.0.5-docker)
- **The settings page is on this machine only** by default (`127.0.0.1:8080` in docker-compose.yml). **ADMIN_PASSWORD** (new, optional) makes the page ask for a password, so it can be opened from other computers (README.md, "The settings page").
- **Model downloads only from Ollama's library and Hugging Face**, unless `ALLOW_ANY_REGISTRY=1`.
- docker-compose.yml sets `no-new-privileges` and a 30-second stop; `cap_drop` and a non-root `user` are documented (README.md, "Hardening").
- Without keys, the household apps (which all come from Home Assistant's address) share 6 waiting places instead of 2.
- The page's password never reaches the model server.

## 1.0.4

- **Web pages can't use the gateway through your browser**: a request from a browser must be sent as JSON, so a web page elsewhere can't quietly ask the model something (the household apps and Ollama's and OpenAI's clients already send JSON).
- **Steadier under load**: at most four requests are read at once (the next is told to try again in a few seconds), so many large requests at the same moment can't use up the memory.
- **Only the sampling options pass**: an app can set temperature, top_p, seed, stop and the like; settings that change how the model is loaded (graphics card layers, batch size, memory locking) are left out.
- **Models stay where the schedule puts them**: after an OpenAI-style answer the model's timer is reset before the next request starts, so a model just swapped out isn't loaded again.
- **Models from other registries**: `hf.co/…` and other registries' models download again (their size was looked up in Ollama's library, which wrongly said they don't exist).
- **Access keys**: a key's *Last used* is saved at most once a minute, off the gateway's main path.
- Shared code: a page can't set inline event handlers, safer settings saves and backup size messages.

## 1.0.3

- **Gemma 4 in the recommended models**: `gemma4:e2b-it-q4_K_M`, `gemma4:e4b-it-q4_K_M` and `gemma4:12b`. They answer text and read images, shown as **Text + vision**: one model can be every app's *Model* and *Vision model*, and it can be the model kept loaded in the day (a vision-only model can't).

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
