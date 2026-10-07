# Changelog

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
