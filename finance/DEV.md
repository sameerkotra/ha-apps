# Developing the app

The full spec (what the app does, the rules it follows, layout and release steps) is
[`spec/SPEC.md`](spec/SPEC.md); the schema is `spec/finance_schema.sql`.

## Tests

```bash
cd finance
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
```

Each test gets a fresh database and app (`tests/conftest.py`). Nothing talks to
Ollama: pipeline tests replace the vision call with canned results
(`fake_vision`), and the LLM categorization fallback is disabled.

## Running locally

The app trusts Home Assistant's ingress headers and, normally, only connections
from Supervisor's ingress proxy. Locally, allow any client and send the headers
yourself (curl, or a header-injecting browser extension):

```bash
cd finance
DB_PATH=/tmp/finance-dev.db PENDING_DIR=/tmp/finance-pending \
  ADMIN_USERS=dev ALLOW_ANY_CLIENT=1 \
  OLLAMA_URL=http://192.168.1.10:11434 OLLAMA_MODEL=qwen2.5vl:7b \
  python3 -m uvicorn app.main:app --port 8321 --reload

curl -H "X-Ingress-Path: /dev" -H "X-Remote-User-Id: dev" -H "X-Remote-User-Name: dev" \
  http://localhost:8321/accounts
```

`bootstrap.py` (the Docker entrypoint) is skipped when running uvicorn directly,
so the variables come from your shell rather than `/data/options.json`.

## Installing on Home Assistant

**From the app repository (for anyone):**

1. In Home Assistant: Settings → Apps → Install app → ⋮ (top right) → Repositories →
   paste `https://github.com/sameerkotra/ha-apps`, Add. **Finance Dashboard**
   appears in the app store. (A fork: set its address in `repository.yaml` and
   `finance/config.yaml` (`url`), and its owner as `maintainer`.)
2. Install (Home Assistant builds the image from the `Dockerfile`, a few minutes
   the first time), then Start on the Information tab. There is no port to open — ingress is the only way in.
3. Follow the first-run notice: add your HA user name to `admin_users` on the
   Configuration tab, restart, then set up the AI on Admin → App settings.
   The app's Documentation tab (`finance/DOCS.md`) walks through it.

**As a local app (development):** copy this `finance/` folder to `/addons/finance` on
the Home Assistant host (Samba or the SSH app), then Settings → Apps → Install app → ⋮ →
Check for updates; it appears under Local apps.

Bump `version` in `config.yaml` for every release, or Supervisor won't offer the
update. The repository layout Supervisor reads: `repository.yaml` at the root,
the app in `finance/` (`config.yaml`, `Dockerfile`, `README.md` for the
store page, `DOCS.md` for the Documentation tab, `icon.png` 128×128, `logo.png`
250×100, `translations/en.yaml` for the option labels) and `CHANGELOG.md` (shown in
the update dialog; add a `## <version>` section for each release).

## Database changes

Add a new `migrations/NNNN_description.sql` (it must insert its own
`schema_version` row). Never edit a migration that has already run.
`app/db.py` applies pending ones at startup.
