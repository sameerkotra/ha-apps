# Household AI on a Docker host

The same model server as the **Household AI** Home Assistant app, for a separate machine — a desktop or a small
server, with or without an NVIDIA graphics card. Home Assistant's household apps (Household Assistant, Finance,
Receipt Price Intelligence, Docs, Calorie Tracker, Arcade) use it exactly as they would any Ollama.

- **Port 11434 — the gateway.** What the household apps talk to. Only the answering routes of Ollama's API are
  open (no downloading or deleting models from outside), with a fair queue, the day/night schedule, limits, usage
  counts and optional access keys.
- **Port 8080 — the settings page.** Everything the Home Assistant app's page has: status, models (download,
  delete, *Keep loaded*), *Use it in other apps*, access keys, App settings, usage, backups. **No login**: anyone who
  can open the page can change it, so keep port 8080 to your own network (never forward it on your router).

## Start it

You need Docker with Compose on the machine (Linux, or Windows / macOS with Docker Desktop; 64-bit, amd64 or arm64).
Podman works too: see [With Podman instead of Docker](#with-podman-instead-of-docker).
Copy this folder there, then in it:

```bash
docker compose up -d --build
```

1. Set your time zone in `docker-compose.yml` (`TZ`) first: the daytime window follows it.
2. Open `http://<this machine>:8080`. The model server starts on its own.
3. **Models** tab: download a model — `qwen2.5:3b` for text (the assistant, Docs, Arcade, Calorie) and
   `qwen2.5vl:7b` for reading receipts and statements (Receipt, Finance). With a graphics card, bigger models fit.
4. **App settings → Access**: turn on **Require an access key** (recommended — the port is on your network), then
   **Access keys** tab: make one key per household app, labelled with the app's name.

Updating: copy the new files over this folder and run `docker compose up -d --build` again. Settings, keys, usage
and models stay in `./data`.

### Without Compose

```bash
docker build -t household-ai:local .
docker run -d --name household-ai --restart unless-stopped \
  -p 8080:8080 -p 11434:11434 -e TZ=Europe/London -v "$PWD/data:/data" household-ai:local
```

Add `--gpus all` for an NVIDIA graphics card, and `--build-arg OLLAMA_LIBS=cpu` to the build for a much smaller
image on a machine without one.

## With Podman instead of Docker

Podman runs the same image, rootless or as root. Linux needs Podman 4.4 or newer (5.x recommended); on Windows and
macOS use Podman Desktop (or `podman machine init && podman machine start` once). The differences from the Docker
steps above:

- **Build with `--format docker`.** Podman builds OCI images by default, and those drop the `HEALTHCHECK` line.
  `export BUILDAH_FORMAT=docker` does the same for every build in that shell (including `podman compose`).
- **Create `./data` first.** Docker makes a missing bind-mount folder for you; Podman stops with *no such file or
  directory*.
- **SELinux (Fedora, RHEL, CentOS, Alma, Rocky):** add `:Z` to the volume (`-v "$PWD/data:/data:Z"`), or the
  container can't write its settings and models.
- **Firewall:** Podman doesn't open ports in firewalld the way Docker does. If the household apps can't reach the
  machine: `sudo firewall-cmd --permanent --add-port=11434/tcp && sudo firewall-cmd --reload` (and `8080/tcp` if
  you open the page from another computer).

### Start it (Podman)

```bash
mkdir -p data
podman build --format docker -t household-ai:local .
podman run -d --name household-ai --restart unless-stopped \
  -p 8080:8080 -p 11434:11434 -e TZ=Europe/London -v "$PWD/data:/data:Z" localhost/household-ai:local
```

Drop the `:Z` where there's no SELinux (Debian, Ubuntu, Windows, macOS) — it's harmless but unnecessary. Add
`--build-arg OLLAMA_LIBS=cpu` to the build for the smaller CPU-only image.

`podman compose up -d --build` also works with `docker-compose.yml` (it needs `podman-compose` or Docker Compose
installed; `export BUILDAH_FORMAT=docker` first, `mkdir -p data`, and change the volume line to `./data:/data:Z` on an
SELinux machine). For a graphics card, use `podman run` or the Quadlet file below rather than the compose file's
`deploy:` block.

Updating: copy the new files over this folder, then build again, `podman rm -f household-ai` and run the
`podman run` line again (or `podman compose up -d --build`). Everything in `./data` stays.

### NVIDIA graphics card (Podman)

Podman uses the NVIDIA Container Toolkit through CDI. Once, after installing the toolkit (and again after a driver
update):

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list            # should list nvidia.com/gpu=all
```

Then add `--device nvidia.com/gpu=all` to the `podman run` line (with SELinux enforcing, also
`--security-opt=label=disable`). Build with the default `OLLAMA_LIBS=all`.

### Start on boot (Podman)

Podman has no always-running daemon, so `--restart unless-stopped` doesn't bring the container back after a reboot.
Let systemd run it with a Quadlet file instead. Rootless: save this as
`~/.config/containers/systemd/household-ai.container` (as root: `/etc/containers/systemd/household-ai.container`),
with `/path/to/household_ai_docker` replaced by this folder's full path:

```ini
[Unit]
Description=Household AI

[Container]
ContainerName=household-ai
Image=localhost/household-ai:local
PublishPort=8080:8080
PublishPort=11434:11434
Environment=TZ=Europe/London
Volume=/path/to/household_ai_docker/data:/data:Z
# NVIDIA graphics card: remove the # on the next two lines.
# AddDevice=nvidia.com/gpu=all
# SecurityLabelDisable=true

[Service]
Restart=always

[Install]
WantedBy=default.target
```

Then (build the image first, as above; remove a container started by hand with `podman rm -f household-ai`):

```bash
systemctl --user daemon-reload
systemctl --user start household-ai
loginctl enable-linger "$USER"     # rootless only: keep it running when you're logged out, and start it at boot
```

As root, use `systemctl` without `--user` and skip `loginctl`. Logs: `journalctl --user -u household-ai`. After an
update, build the image again and `systemctl --user restart household-ai`.

### Caller addresses (rootless Podman 4)

The page tells callers apart by their address. Podman 5's rootless networking (`pasta`) keeps the real address;
Podman 4's default (`slirp4netns`) shows every caller as one address. On Podman 4 rootless, add
`--network slirp4netns:port_handler=slirp4netns` to `podman run` (`Network=slirp4netns:port_handler=slirp4netns` in
the Quadlet file) — or simply use one access key per app, which works either way.

## Point the household apps at it

In each app in Home Assistant: **Admin → App settings → AI** (the page's *Use it in other apps* tab shows the exact
values):

| Field | Value |
|---|---|
| Provider | **Ollama** |
| Address | `http://<this machine's IP address>:11434` |
| Access key | that app's key from the *Access keys* tab (empty while *Require an access key* is off) |
| Model / Vision model | one of the downloaded models (*Test connection* lists them) |

Then press **Test connection**. Use the machine's IP address (e.g. `192.168.1.20`) unless Home Assistant can
resolve its name; give the machine a fixed address in your router so it doesn't change.

**Why one key per app:** every household app reaches this machine from Home Assistant's own address, so without keys
they all look like one computer on the page. With a key each, the page shows each app's usage, and you can tick
*Answer first* for the Household Assistant (its questions go ahead of a long receipt or statement).

## Settings from the environment

| Variable | Default | What it is |
|---|---|---|
| `TZ` | `UTC` | Time zone of the daytime window (e.g. `Europe/London`, `Asia/Tokyo`) |
| `WEB_PORT` | `8080` | The settings page's port inside the container |
| `PUBLIC_GATEWAY_PORT` | `11434` | The host port 11434 is published on (`-p <this>:11434`), shown on the page as the address |
| `DATA_DIR` | `/data` | Where settings and models live (mount a volume there) |

Everything else is on the settings page.

## Differences from the Home Assistant app

- No login and no Home Assistant identity: whoever opens the page is its admin. Writes from another website are
  still refused (a page elsewhere can't change settings through your browser).
- The time zone comes from `TZ`, not from Home Assistant.
- The household apps can't be recognised by their Home Assistant host names here; use one access key per app.
- The model server is on from the first start (it's a machine of its own).
- Backups on the *Storage* tab hold the database only; the models are in `./data/models`.
- Graphics cards: NVIDIA through the NVIDIA Container Toolkit. AMD (ROCm) needs Ollama's `rocm` image and isn't
  covered by this Dockerfile.

The code in `app/` is the Home Assistant app's (`household_ai/app/`) with these changes (`config.py`, `auth.py`,
`main.py`, `network.py`, `routers/api.py`, `settings.py` and the page's texts); `app/common/` is a copy of the
shared files at the time it was made. Tests: `python3 -m unittest discover -s tests` in this folder.
