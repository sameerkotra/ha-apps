# Household AI

An AI model on your Home Assistant machine's own CPU, for the other household apps. Source and issues:
https://github.com/sameerkotra/ha-apps

## Getting started

1. Install the app, then on its **Configuration** tab add your Home Assistant user name to **admin_users** and
   start it. Until someone is listed, the page shows **No admin yet** with the exact name to add (select your name
   at the top right: "How the app sees you").
2. Open **Household AI** in the sidebar (only Home Assistant administrators see it). On **Status**, turn the model
   server **On**.
3. On **Models**, download one model. `qwen2.5:3b` is a good first choice (1.9 GB to download, about 2.5 GB of
   memory). The page warns when a model needs more memory than the machine has free.
4. On **Use it in other apps**, copy the values into each app's own **Admin → App settings → AI**, then use that
   app's **Test connection**.

## Pointing an app at it

| Field | Value |
|---|---|
| Provider | **Ollama** (recommended; *OpenAI-compatible* works too — then the address ends in `/v1`) |
| Address | `http://<host name>:11434` — the page shows the real one, e.g. `http://a1b2c3d4-household-ai:11434` |
| Access key | empty (or a key from **Access keys**, when *Require an access key* is on) |
| Model / Vision model | one of the downloaded models (*Test connection* lists them) |

Ollama is recommended because its requests carry the schedule and the limits exactly; an OpenAI-compatible
request runs with the model server's own thread count.

**Timeouts**: an app's timeout covers the time its request waits in the queue as well as the answer, because the
apps wait for the whole answer. On a CPU:

- **Household Assistant**: *Longest a question may take* 300 seconds or more.
- **Receipt Price Intelligence**: *timeout* at least 300 seconds and *receipts read at the same time* 1, with a
  vision model.
- **Finance Dashboard**: a vision model for statements; allow several minutes per page.
- An app that shares the model with Receipt or Finance should allow longer than one of their jobs.

## Models

The recommended list:

| Model | Kind | Memory | Suits |
|---|---|---|---|
| `qwen2.5:1.5b` | Text | ~1.5 GB | the assistant on a Raspberry Pi; Docs, Calorie Tracker, Arcade extras |
| `qwen2.5:3b` | Text | ~2.5 GB | the assistant; Docs, Calorie Tracker, Arcade extras; Finance's optional text model |
| `llama3.2:3b` | Text | ~2.5 GB | the same, an alternative |
| `qwen2.5vl:3b` | Vision | ~4 GB | Receipt Price Intelligence, Docs' text from scans (slow on a CPU) |
| `qwen2.5vl:7b` | Vision | ~7 GB | Finance Dashboard statements, Receipt (better reading); 16 GB machines |

*Download other model…* takes any name from Ollama's library. One text model for everything text and one vision
model is usually enough: every switch between models loads the other one (seconds from an SSD, up to a minute
from an SD card). A download is refused when it would leave less than 2 GB free.

Models live in `/data/models` and are **left out of Home Assistant's backups** (they are gigabytes each). After a
restore, the Models tab lists the kept model if it is missing, with **Download again**.

## When models stay loaded

**Keep loaded** (on the Models tab) is the text model kept in memory during the day — by default the first text
model you downloaded. **App settings → When models stay loaded**:

- **Daytime** 05:00–22:00, in Home Assistant's time zone (a window passing midnight is allowed).
- **During the day, unload other models after** 10 minutes; the kept model stays loaded.
- **At night, unload after** 10 minutes, for every model.
- **Load at the start of the day**: the kept model is loaded before anyone asks.
- **Models loaded at once** 1 or 2. With 2, a vision model is loaded next to the kept text model instead of
  replacing it — only when the memory allows both.

**Unload now** (Status) frees the memory until the next request or the next day's start.

## The queue and the limits

One model answers one request at a time. **App settings → Limits**:

- **Queue length** (4): requests that may wait while one runs; each app may have at most 2 waiting. More are told
  to try again shortly (the apps retry by themselves). There is no limit on how long a request waits: an app that
  gives up has its request dropped.
- **Answer first** (on **Use it in other apps**): apps whose waiting requests go ahead of the others; the assistant
  by default.
- **Longest run** (15 minutes): a request still running is stopped.
- **Longest answer** (2048 tokens), **Context length** (8192 tokens) and **Threads** (all cores but one): whatever
  an app asks for, it gets at most these. Changing *Context length* or *Models loaded at once* restarts the model
  server.

The model server runs at a lower priority than Home Assistant, so the home stays responsive while it works.

## Access

- By default the model is reachable only by apps inside Home Assistant (its internal network), with no key. They
  can ask questions; they can never download, delete or replace models — that is only on this page.
- **Require an access key** (App settings → Access): every app then needs a key from **Access keys** in its own
  *Access key* field. A key is shown once; the app keeps only a hash of it. Revoking a key refuses that app at once.
  Turn keys on if you install an app you don't trust.
- **On my network**: to use the model from other computers, enter a host port for 11434 on the app's **Network**
  tab in Home Assistant (empty = not published). The Status tab shows whether it is published, and warns while it
  is published without keys. The connection is plain HTTP.

## Usage and privacy

The **Usage** tab shows, per app, per model and per day, the requests, tokens, seconds and time spent waiting
(kept 30 days). Prompts, images and answers are never stored. Nothing is sent outside your home except the model
downloads you start (from Ollama's library).

The app reads Home Assistant's time zone and, once a minute at most, its own network settings from the Supervisor
(to show whether the port is published). It asks for nothing else.

## Backups

**Storage** downloads or restores the app's database: the App settings, the access keys (as hashes, so a backup
holds no usable key) and the usage. Models are not in it.

## Troubleshooting

- **"unreachable" in another app**: the model server is off, or still starting (Status). Check the address
  exactly as the page shows it.
- **"Download … in Household AI first"**: the app asks for a model that isn't downloaded; download it or choose
  one that is.
- **"The model server was stopped for lack of memory"**: the model is too big for the free memory. Choose a
  smaller model, set *Models loaded at once* to 1, or unload sooner.
- **Answers time out in another app**: raise that app's timeout (see *Timeouts*), or set its requests to
  *Answer first*.
- **The model server log** (Status) shows the last 50 lines of the model server's own output.
