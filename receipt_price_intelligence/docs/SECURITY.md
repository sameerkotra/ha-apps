# Security

## Authentication

The application should use Home Assistant authentication/ingress where possible.
If the UI/API is directly exposed by port, require application authentication.

## Authorization

Every request must establish:
- current user
- role
- resource ownership

Admin routes require ADMIN or SUPER_ADMIN.

## Upload security

- Limit image size.
- Limit image dimensions.
- Verify MIME/content.
- Generate server-side filenames.
- Never use user filename directly as a filesystem path.
- Prevent `../` traversal.
- Store files outside executable paths.

## Model endpoint security

- Never expose model API keys to browser JavaScript.
- Store model configuration server-side.
- Redact credentials in logs.
- Consider TLS for remote model servers.
- Restrict model URL to configured destinations if appropriate.

## SQLite

- File permissions should restrict database access to the app.
- Do not expose SQLite over a network port.
- Use parameterized queries.

## Data deletion

Prefer soft deletion for approved financial/history records.
Admin deletions should be audited.

## Receipt photos

Photos (including pages rendered from PDFs) are stored only while a receipt is being reviewed. They are deleted when the receipt is approved, rejected or discarded, and when an unsaved receipt passes the retention period; the daily job also clears expired ones. Uploaded PDFs are never kept, only their rendered pages. PDFs are rendered by poppler in a subprocess with a time limit, a size limit (20 MB) and a page limit (8).

## Map services

The trip planner sends addresses (your home and the stores you shop at) to a Nominatim server and coordinates to an OSRM server. By default these are the free public OpenStreetMap servers, so those operators can see them; receipts, prices and people are never sent. The app sends an identifying User-Agent (and your contact email if you set one) and spaces searches a second apart, as their usage policy asks. Set `geocoder_url` and `routing_url` to your own servers to keep everything local. The route map on the page is drawn from coordinates with no tiles fetched.

With the `tavily` or `searxng` web search provider, the app opens store pages itself. Addresses come from search results, which anyone can influence, so a page is only opened when its host resolves entirely to public internet addresses, on port 80, 443, 8080 or 8443; every redirect is checked the same way, and at most 3 MB is read. Private, loopback, link-local and other non-public addresses (your router, Home Assistant, cloud metadata addresses) are refused. Only search words go to Tavily or Parallel, with your API key.

The nearby-store search (Stores page) sends an Overpass server the home's coordinates rounded to two decimals (about a kilometre) and a search radius widened to make up for the rounding; exact distances are worked out inside the app. Nothing else is sent. It is the public Overpass server by default (`overpass_url`; empty turns it off). Website addresses from OpenStreetMap are kept only when they are `http(s)`.

## Home Assistant access, mail and folders

The app asks for Home Assistant API access (`homeassistant_api`) to publish sensors and events, send notifications and read to-do lists. It uses the Supervisor's token, which is never stored or shown, and only calls the Core API paths for states, events and the notify, persistent notification and to-do services. The mailbox password is an App setting (write-only) used only to read the chosen mail folder over IMAP; attachments are treated like any upload (type, size and page limits) and nothing is saved until a person approves it. The import folder is under `/share`, which other apps can also read and write, so only put a folder there that you trust. Imported files are processed like uploads and never executed.

## CSV export

Any signed-in user can export their home's receipts and lines as CSV (the same data they can already see). Cells that begin with `=`, `+`, `-` or `@` are prefixed with an apostrophe so spreadsheets do not execute them as formulas. The file is generated on request and not stored.

## Web lookups

The search server (yours, on your network) receives search words: store names, streets and towns, and product names, and the web addresses of pages chosen from its own results. It never receives receipts, prices, totals or people. The bearer token, if set, is an App setting (write-only) and is only sent to that server. Page text is passed to your model as untrusted data (the prompts tell it to ignore instructions inside pages), and everything the model returns is validated before use: times must be real times, prices real numbers under a limit, dates real dates, and a deal only counts if it matches an item you buy. Store hours are only ever applied after you accept them, unless you turn on automatic application, which requires a confident match and only fills stores with no hours. Fetching is limited to http and https addresses.

## Export and import

Export and import of the whole database are administrator-only. An export contains every home's data and every person, so it is as sensitive as the database itself. Imports are limited to 500 MB, must be a SQLite file that passes an integrity check and contains this app's tables, and are never trusted beyond that: the file is only ever opened by SQLite, never executed. The current database is copied aside before an import and restored if it fails.

## Backups

Backups contain financial information (receipt details and prices) and photos of receipts still awaiting review, and must be protected accordingly.

## Users and homes

- Every request must come from Home Assistant's ingress proxy (`172.30.32.2`) or loopback; anything
  else gets 403, and API requests without a Home Assistant user get 401. The app publishes no port.
- Any signed-in user can view and add data in any home (by design). Administrators alone
  create/rename/delete homes, change App settings, back up and restore. Administrators are the
  people on the `admin_users` option (Configuration tab), nobody else; the in-app roles of earlier
  versions are gone.
- App settings secrets (model API key, mail password, web search key and token) are kept in the
  database, never sent to the browser (the page only learns whether one is set), left out of logs,
  and included in exports, which are therefore private.
