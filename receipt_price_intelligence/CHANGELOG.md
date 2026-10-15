# Changelog

## 1.3.3

- **The Household Assistant's quick answers know the Best prices and Budgets words** ("special offer", "price drops", "grocery budget", "spending limit", …), so those questions are answered in a second without the AI (Household Assistant 1.4.0).

## 1.3.2

### Security

- **Notifications go to notify services only**: *Send test* on the Notify page now accepts only a notify service Home Assistant offers; before, it could be made to call any Home Assistant service.
- **Notify page settings are the household's**: only an administrator can save them now (everyone can still see them and send a test). The List page's test of "cheapest here" says how far away the tracked phone is only to an administrator or to the person being tracked.
- **Size limits on everything sent to the app**: 1 MB for a change, 25 MB for a photo or PDF, 256 MB for a bulk upload, also when the size isn't announced. Uploads are measured before they are read.
- **Saved receipts stay as saved**: a saved receipt can no longer be changed (store, lines, photos) without *Edit* first, and the model's own reading can no longer be overwritten from the page.
- **Web pages built to stall the app are read quickly**: the price and hours reader now reads a page in one pass; a hostile page used to keep it busy for about a minute.
- **Oversized photos are refused before they are opened**: a photo may have up to 40 million pixels (a tiny file can claim billions, which used gigabytes of memory), at most two are decoded at once, and only real JPEG, PNG, GIF or WebP photos are kept, whatever the file says it is.
- **Safer mail**: the mail server's certificate is now checked. With *Use SSL for mail* off, the connection is encrypted with STARTTLS; a server that offers no encryption at all is only used when the new *Allow mail login without encryption* is turned on (off by default — if your mail server is on your own network without encryption, turn it on).
- **Mail import limits**: the new *Only import mail from* (empty: everyone, as before) skips emails from other senders; an attachment already imported is not imported again; and once a home has *Receipts waiting at most* (new, 200 by default) receipts being read or reviewed, new scans are refused and the folder and mailbox wait (emails stay unread) until some are reviewed.
- **The import folder and the receipt archive never follow links**: a link placed in the shared folder is left alone instead of being read, written through or moved.
- **Paid services can have a monthly limit**: *Web searches a month at most* and *Model questions a month at most* (App settings; 0, no limit, by default). This month's counts are on the Web debug page. Answers from the search service and the model are capped at 5 MB, and a page must arrive within 30 seconds.
- Web addresses that lead back into the home network through IPv6 (NAT64, 6to4) are no longer opened.
- Store hours found online are applied by themselves only on evidence (the store's own page data, OpenStreetMap, or two websites giving the same hours), never on the model's say-so.
- Members no longer see where files are kept on the server, the import folder, the mail server or the map servers; error messages no longer repeat what the model server said; the log no longer shows a model address's password; the Web debug page hides keys and tokens in addresses.
- The API description pages (/docs, /openapi.json) are no longer served.

### Fixed

- Receipts that were being read when the app stopped are read again at start-up (before, they stayed at "Reading…" and were deleted after a few days). A receipt the model hasn't read yet is never deleted for being old.
- **Restoring a backup waits for everything else**: requests and background checks already running finish first, and the rest wait until the restore is done; before, a check running at the time could write to the replaced database and its changes were lost.
- *Model timeout* is used as set (it could not be lower than 300 seconds); an Ollama server's OpenAI-compatible address (`…:11434/v1`) is now used as such.
- The daily check keeps its hour after a daylight-saving change.
- Two clicks (or a click and the daily check) no longer start the same web lookup twice; a check of online prices that found nothing is not repeated every day.
- Mail folders with spaces in their names work; an odd answer from the routing service no longer causes an error; huge or deeply nested numbers from the model are ignored; negative quantities and weights are not kept.
- Left-over export and upload files are deleted after six hours; discarded scans are deleted once their time is up.
- Keyboard: the car choice in the trip planner works with the arrow keys; clearing the trip list asks in the app's own dialog. HEIC photos are no longer offered for bulk import (they can't be read).

### Changed

- **Faster Receipts page**: it loads one line per receipt (saved ones 100 at a time, with *Show older receipts*) instead of every receipt in full, and while a receipt is being read only its status is checked.
- **Faster everywhere**: pages no longer hold up each other while the database works; scripts and styles are kept by the browser until the next update; answers are compressed; the shopping list updates at once when you tick an item and fetches only the list again; saving a model's reading looks up past purchases once instead of once per line; the trip planner skips trips that can't win; new database indexes and fewer duplicate ones; page thumbnails are small copies.
- **Shared code**: an optional *App-messages signing key* (Admin → App settings → Advanced: put the same key in every household app); API answers are never stored by the browser; scripts and styles load faster.

## 1.3.1

- **Safer web pages**: a page fetched for prices that arrives packed (gzip) can no longer unpack to more than the 3 MB a page may have; a bigger one is skipped instead of filling the memory.
- **Safer mailbox import**: emails over 30 MB are skipped (and marked as read) without being downloaded, and at most 10 attachments are read from one email. A photo attached without a usual file name (such as "Receipt" or a `.jfif` file) is now imported by the type the email gives it, instead of being turned away.
- **Imported receipts are always read**: if checking the mailbox failed, receipts just picked up from the watched folder could stay at "Reading…"; each source is now checked on its own and new receipts are always queued for reading.
- A receipt photo whose file is missing now answers "not found" instead of a server error, and upload errors no longer show internal details or where files are kept on the server.
- **Shared code**: the Household Assistant tools answer only calls that come from the Household Assistant itself; pages no longer use inline event handlers; saving App settings is safer, and backup restores say clearly when a file is too large.

## 1.3.0

- **More for the Household Assistant**: "Where are the best prices?" (savings by store and pack size, and recent price drops) and "How are my budgets this month?" (each budget, how much is left and where it's heading).

## 1.2.3

- **Home Assistant's time zone, always**: "today" (prices, budgets, alerts, file names) now follows Home Assistant's zone, read at start-up, asked again until Home Assistant answers and every six hours after; until then the zone the Supervisor gives every app (Home Assistant's own), never UTC.
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 1.2.2

- **Your own Household Assistant switch**: while an administrator lets the Household Assistant ask, the **Who am I** page has **Let the Household Assistant answer for me**. Turn it off and the assistant won't answer your questions from this app's shopping list, prices or spending.

## 1.2.1

- **Links open the right page**: the Household Assistant's answers open the shopping list or Insights. Links ending in `/list`, `/receipts`, `/insights`, `/deals`, `/trip` or `/stores` open that page.

## 1.2.0

- **Can answer the Household Assistant**: once an administrator turns on **Answer the Household Assistant** (App settings; off by default, because spending is private), the new Household Assistant app can ask for the shopping list with the cheapest store for each item, an item's prices by store, and spending by store for a month — and add to the shopping list, but only after the person taps the exact change it proposes. Each answer links back to this app.
- The app now joins the household apps' message bus (Home Assistant's event bus), keeping the bus's own small tables in a separate file (`app_bus.db`). The **DOCS** show how to keep those messages out of Home Assistant's history.

## 1.1.2

- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back, so the next Back goes to the page you came from instead of closing the app.

## 1.1.1

- **Security**: backups (Backup → Export) no longer include the access keys and passwords from App settings (the model's access key, the mailbox password, the web search key and token), and importing a backup keeps the ones this install already has. After restoring on a new install, enter them again in Admin → App settings.
- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.
- **Security**: PDFs are now read by the PDF tools (poppler) as a separate user without any rights, with memory, time and file-size limits, on a copy of the file — they can't reach the app's database or other files.
- **Security**: pages fetched for store hours and prices go to exactly the address that was checked as being on the public internet (a site can no longer answer the check with one address and the connection with another); redirects are checked the same way.
- The `/health/model` and `/health/database` checks no longer show the model's name or error details (the details go to the log).

## 1.1.0

- **Themes**: Midnight, Slate, Daylight and a new **Auto** (follows your device), as in the other household apps.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged.
- **How the app sees you** has copy buttons and the same wording as the other household apps; the **No admin yet** banner names the user name to add.
- **Security**: pages now send a Content-Security-Policy and `nosniff` header; scripts moved out of the pages into files. No visible change.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

## 1.0.1

- **The menu shows on phones in the Home Assistant app.** On a phone the page is now a column the height of the
  screen: the content scrolls and the menu sits under it as part of the page, instead of being pinned to the bottom of
  the screen, which some Home Assistant app views put out of sight. Save bars (Admin → App settings, Notify, Scan)
  stay just above it.

## 1.0.0

Made the same as the other household apps.

- **All settings moved to Admin → App settings.** The model, receipts, imports, maps and trips, web search, best
  prices, Home Assistant and notification settings and the log level are set in the app by an administrator, grouped,
  checked before they are saved, and applied straight away (no restart). API keys and the mail password are
  write-only. They are kept in the app's database, so exports include them; restoring an export without them keeps
  the current ones.
- **`admin_users` is the only app option**, now a list (one Home Assistant user name or user id per line).
  Administrators are those people and only them: nobody becomes one by opening the app first, and the role menu is
  gone. **After updating, open the Configuration tab, enter admin_users again as a list, save and start the app**;
  then set the model and anything else you had changed in Admin → App settings (earlier option values are not
  carried over).
- **Admin area** (for administrators): App settings, Homes and people (moved from the Receipts page), Backup, Web
  debug.
- **New menu**: a sidebar on a computer (collapsible) and a bottom bar on a phone, on every page.
- **Themes**: Midnight, Slate and Daylight, chosen in the sidebar and applied before the page draws.
- **Back gesture** in the Home Assistant app: closes an open dialog, then returns to the List, then leaves the app.
- **How the app sees you** (Signed in as, at the bottom of the menu): the user name and id Home Assistant sent and
  whether they match admin_users. A banner says so on every page while admin_users is empty.
- **Only reachable through Home Assistant**: requests that don't come through ingress are refused; signing in without
  a Home Assistant user is no longer possible.
- Smaller, pinned image (Python 3.13); scanning without a model set shows how to set one.

Versions before 1.0.0 were released on their own; this is the first release in this repository.
