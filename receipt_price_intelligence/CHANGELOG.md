# Changelog

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
