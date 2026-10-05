# Receipt Price Intelligence

## What you need

A vision-capable model on your own network, reachable from Home Assistant:

- **Ollama** (for example `qwen2.5vl` or another vision model), or
- any **OpenAI-compatible** server (vLLM, LM Studio, llama.cpp server, ...).

The app does not include a model.

## Setup

1. Install the app. On its **Configuration** tab, add your Home Assistant user name to **admin_users** (one name
   per line; the user id works too), save, and start the app.
2. Open **Shopping** in the sidebar, then **Admin → App settings**, and set **Model server URL**, **Model name** and
   **Model API format** under *Reading receipts*. Ollama addresses look like `http://192.0.2.10:11434`;
   OpenAI-compatible ones like `http://192.0.2.10:8000/v1`. Save: settings apply straight away, no restart.
3. Create a home (Admin → *Homes and people*, or the Receipts page offers it the first time).

Everyone who can open the sidebar panel can use the app. Administrators are the people on **admin_users**, and only
them: they see **Admin** in the menu (App settings, homes, backups and the web debug page). If you don't see it,
open *Signed in as* at the bottom of the menu (**How the app sees you**): it shows exactly the user name and id Home
Assistant sent and what to add. Changes to admin_users need a restart.

## Menu, themes and the back gesture

The menu is a sidebar on a computer (it can be collapsed to icons) and a bar along the bottom on a phone: List,
Receipts, Insights, Best prices, Trip, Stores, Notify and, for administrators, Admin. The theme (Midnight, Slate,
Daylight, or Auto, which follows the device's light or dark setting) is chosen at the bottom of the sidebar and
remembered on each device. In the Home Assistant app, the back
gesture closes an open dialog first, then returns to the List, and only then leaves the app.

## Using it

1. **Add the receipt**: tap *Scan receipt*, then **Choose photos or PDF** (photos from your library, or a PDF such as an emailed receipt; on a computer you can also drop files onto the page), or tap **Use camera** to take the photos there. Long receipts can take several photos, top to bottom. Each page of a PDF is turned into an image and read like a photo (up to 8 pages).
2. **Review**: check the store, date, lines and totals. The screen flags anything that does not add up. Check the date (the review screen shows it as printed, and offers the other reading if it could be month-first or day-first). Fix lines by hand, add a common name for products ("Milk, 2%"), or use *Read again with a note* to tell the model what it got wrong. If it cannot read the receipt at all, you can type it in.
3. **Save**: approving records the prices. The receipt photos are then deleted; only the details are kept. A saved receipt's date can still be changed later with *Change date* on its review screen; spending and price history follow the new date.

Other pages:

- **Insights**: spending by month, store and item, prices across stores, and how often you buy things.
- **Best prices**: a daily check that finds where each item is cheapest, and what would save money.
- **Stores**: rename chains, fix addresses, merge duplicates (merging is for administrators). Merging two stores combines them into a single store, with all their receipts under one location, unless you untick that option.

Homes let separate households or places (a house and a cabin) keep their receipts apart. Stores are shared between homes.

## App settings

Everything except admin_users is set in the app, under **Admin → App settings**, by an administrator. Settings are
kept in the app's database (so they are part of its backups), apply as soon as they are saved, and are grouped:

| Group | What is in it |
| --- | --- |
| Reading receipts | Model server URL, name, API format and key; timeout; temperature; context window (Ollama); maximum reply length; let the model think first; receipts read at the same time |
| Receipts | Default currency; date order on receipts (`auto` uses the currency); receipt archive folder; how long unsaved receipts are kept |
| Importing receipts | Folder to import from (under `/share` or `/media`); home for imported receipts; mail server (IMAP) settings |
| Maps and trips | Units (miles or kilometres); address search, routing and nearby-store servers (empty turns the nearby search off); contact email for map requests; how long trip plans are kept |
| Web search | Provider, address, API key or token, timeout, requests at once, how long results are kept, seconds between searches, field names, and how often hours, deals and online prices are looked up |
| Best prices | Price history window, minimum saving to suggest, smallest saving worth an alert, daily check hour |
| Home Assistant and notifications | Sensors, alerts, notification services, restock and price-drop notifications, overcharge notices, the to-do list to sync, the person or phone for "cheapest here" and its distance |
| Advanced | Log level |

Each group is a card, with a line under each setting saying what it does, its range and its default. Changes are
kept until you select **Save** at the bottom (it shows how many unsaved changes there are); **Discard changes** puts
everything back. API keys and the mail password are write-only: the page shows whether one is set, never what it
is, and **Remove** clears one. Each value is checked before anything is saved (a number out of range is flagged at
the field), and a bad one is named in the message.

The Notify page lets each person choose their own notifications; App settings are the defaults until they save
there.

### Saved trip plans

A trip plan is kept for **Keep trip plans (hours)** (default 12). Planning the same list with the same options again
shows the kept plan at once, with no stores to find, no online prices to check and nothing to work out again, as long
as nothing it uses has changed since: receipts, online prices and deals, stores and their hours, your cars and your
home address. Any such change means the next plan is worked out afresh. Reopening the Trip page shows your last plan
(marked when something has changed since). **Plan again** always works it out afresh (and checks prices online if
that is ticked); **Clear** removes the last plan. A plan for leaving "now" had store hours checked for the time it was
made, which the page says.

## Notifications

The **Notify** page has every notification setting in one place:

- **Where to send**: tick the phones and tablets to get **push notifications** on. Every device signed in to the Home
  Assistant companion app appears here (its `notify.mobile_app_…` service), and other notify services (Telegram,
  e-mail…) too. *Send test* checks each one. Tapping a push notification opens the app. You can also, or instead,
  get them as Home Assistant notifications (the bell); with no phone chosen, the bell is used.
- **Which to send**: price alerts and targets; *Cheaper than usual* (with the percentage); *Due to restock* (and
  whether to add those items to the shopping list); *Paid more than posted*; *Cheapest here* (with the distance).
- ***Cheapest here*** follows every phone you send to, each by its own location from the Home Assistant app
  (`device_tracker.<phone>`, or the tracker with the phone's name); the page shows where each phone's location comes
  from. When a phone is at a store where items on your **shopping list** (not yet ticked off) are cheapest, only that
  phone is told, about only those items, once per visit. With no phone chosen, a person or tracker you pick is followed
  instead.
- **Quiet hours**: nothing is sent between the times you choose (for example 22:00 to 07:00), except *Cheapest here*,
  which only comes when you are at a store.

Until you save on this page, the notification defaults in Admin → App settings are used; once saved, the
page's settings take precedence.

## Best prices page

A short header says when prices were last checked (*Check now* checks again) and shows what you could save a month,
how many items are cheaper elsewhere, and, when there is any, how much you paid over the stores' posted prices. Below
it, four tabs: **For you** (the best things to act on, from everything below, biggest saving first), **All items** (the
cheapest store for every item, searchable), **Online** (deals and prices found online, and the buttons to look for more)
and **Alerts** (alerts, prices that moved and price targets).

## Looking out for you

- **Due to restock** (List page, `sensor.receipt_restock_due`): items you buy on a fairly steady schedule (at least
  three purchases), with when you are next due to buy them: "Large Eggs: 1 day overdue · you buy it about every 10
  days, usually at Kroger". *Add* or *Add all* puts them on the shopping list. Options: *Notify what is due to restock*
  (a daily notification, on by default) and *Add due items to the shopping list* (off by default). Fees, restaurant
  dishes and clothing are left out, and so is anything you seem to have stopped buying.
- **Cheaper than usual now** (List page, and a notification every hour at most): items on your shopping list that are
  at least *Price drop alert (percent)* (10%) cheaper somewhere than the median you paid over six months, online prices,
  deals and store discounts included: "2% Milk: $2.79/gal at Kroger (online), usually $3.79 (26% less)". The same item,
  store and price is notified once a week at most; a further drop is notified again. 0 turns it off.
- **Paid more than the store posted** (Best prices page, and a notification when you approve a receipt): receipt lines
  where you paid noticeably more (over 3% and 5¢) than the same store's price found online within a week of the purchase,
  or a deal it was running that day: "2% Milk, Kroger, Sep 18: paid $3.79; the store posted $3.29 on Sep 16". Many stores
  refund the difference if you ask. Option: *Notify possible overcharges*. Online prices you chose to ignore are not used.
- **Ways to save** (Best prices page): per unit, from your receipts and online prices over six months, another store at
  least 10% cheaper than where you usually buy something, or another pack size at least 10% cheaper than the one you
  usually buy ("Olive Oil: usually 17 oz at Kroger $0.529/oz; Costco (68 oz) $0.323/oz, 39% less").

## Sold per piece at one store, by weight at another

Avocados at $1.29 each at one store and $2.79/lb at another are compared by the weight of one piece, without you
weighing anything: the weight printed on the receipt line when a pack is sold each (a *10 lb bag* of onions), else the
typical weight of one (a built-in list of common produce: an avocado about 0.44 lb, a banana 0.35 lb, an onion 0.44 lb,
a lemon 0.24 lb, a bell pepper 0.37 lb…). Each item is compared in the unit you mostly buy it in; prices in the other
kind of unit are converted, on each date, and shown with **≈** and how they were worked out (*≈ $1.23 each, from
$2.79/lb; a typical avocado is about 0.44 lb*). Before, such prices were left out of comparisons. Items with no typical
weight (prepared foods, for example) are still only compared in the same kind of unit.

## Weights and quantities not printed on the receipt

Many receipts print only a price (*BANANAS 1.86*, *GAS UNLEADED 35.60*). Since things are usually bought in much the
same amount at the same store, right after a receipt is read the app fills in what the line leaves out from the last
time you bought the same thing (same printed name, or same item) **at the same store**:

- **Weight, for things sold by weight**: from last time's price per unit (1.86 at last time's 0.59/lb is about
  3.15 lb), so it stays right when you bought more or less; last time's weight if that comes out far off.
- **Pack size, for things priced per pack** (a 3 lb bag): last time's size.
- **Quantity**: last time's, only when the total matches it (6.98 when you last bought 2 @ 3.49).

What the receipt prints is never changed. On the Review page a filled-in line says so (*about 3.15 lb, from last
time's price here on Sep 3*); change it like anything else if it is wrong.

## Receipt archive folder

Set **Receipt archive folder** in Admin → App settings (for example `/share/receipts` or `/media/receipts`) to keep every
scanned receipt's original file there: the photo, or the whole PDF as uploaded, also for receipts imported from the
import folder. While a receipt is being reviewed its files are in `Being reviewed/`; once approved they move to
`<year>/<date> <store> <total>/`, for example `2026/2026-09-18 Kroger $54.23/`, so the folder is easy to browse from
Samba, the Media browser or a file manager. A scan that is rejected or deleted before approval is removed from the
folder; deleting a saved receipt in the app leaves its archived files alone. The folder must be under `/share` or
`/media`; Home Assistant's full backups include both. The Scan page says where scans are kept, or why the folder cannot
be used. Leave the setting empty to keep no copies (the app itself only keeps photos while a receipt is reviewed).

## Store addresses on receipts

A receipt's store address is read whether it is printed on several lines or one (*123 E MAINSTREET SPRINGFIELD IL
62701*), with full state names (*Springfield, Illinois 62701*), a suite, a store number or a phone number around it. A
receipt is filed under a store you already have when it is the same place, however the address is written: *18901 E
MAINSTREET*, *18901 East Main Street* and *18901 E. Main St.* are one address, and a misread letter or two in the
street name still matches when the street number and ZIP agree. Two different suites, or two different printed store
numbers, stay two stores. When a receipt prints a store name the app does not know yet (*KINGSOOPERS*, *KING SOOPERS
FUEL*) at the address of a store you have with a similar name, it is filed under that store, and the printed name is
remembered for it; a different business at the same address (a coffee shop inside a supermarket) stays separate. The
Stores page's duplicate finder compares addresses the same way, so duplicates made before are offered for merging.

## Store discounts (cards, memberships)

On the **Stores** page, a store's menu has **Discounts…**: what you save there on top of its prices, from a store credit
card or a membership. Each discount has a name, a percent off, what it applies to (*everything*, *fuel only*, or
*everything except fuel*) and, for fuel, optional cents off per gallon. For example: Sam's Club Mastercard, 5%,
everything (in the club and at its fuel station); Costco, 2%, everything except fuel, plus a fuel-only discount.
Several discounts at one store add up.

Discounts are taken off prices wherever the app works out where things are cheapest: the trip planner (route, totals,
*Prices at each store*) and the shopping list (*Cheapest at*). They apply to online and receipt prices alike, since a
card's cashback is not on the receipt. The plan shows each discounted item's price before the discount and what you pay
(*You pay $3.039/gal −5% Sam's Club Mastercard*), and the panel under the totals says how much discounts take off.
The check that an online price was not misread compares prices before discounts.

## Shopping list

The **List** page keeps a shopping list. Add items by typing or tick several under *Pick from items you buy*. As you
type, every item whose name contains the text (any case) is suggested, those starting with it first: pick one to add
it. Otherwise, *Add "…" as a new item* (or Enter, or *Add*) creates a **new item** with that name, unless an item has
exactly that name. Items added on the Home Assistant to-do list, where nobody picks a suggestion, become the item they
clearly are when the main word is the same ("milk" is your *2% Milk*, but "almond" is not your *Almond milk*), or else a
**new item**
(for example *Birthday candles*, with its category guessed), so the same thing typed again, or added on the Home
Assistant to-do list, is the same item. A new item has no price yet: when you buy it, give its receipt line that name on
the Review page (or pick it), and from then on the list shows where it is cheapest. Each item shows where it is **cheapest**
right now, with the price, whether it is the store's online price or what you paid (the same prices the trip planner
uses), and the next cheapest stores. *By cheapest store* groups the list by store with a rough total. Tick items off
as you buy them; *Plan a trip with these* opens the trip planner with the list.

### On your Home Assistant dashboard

- **To-do list card**: set **Shopping list to-do entity** in Admin → App settings (for example `todo.shopping_list`, the
  built-in shopping list). The two lists are kept in step every couple of minutes: items added, ticked off or deleted
  on either side show on the other, and each item's description says where it is cheapest. Add a *To-do list* card
  for that entity to your dashboard.
- **Sensor**: `sensor.receipt_shopping_list` (items left to buy; attributes `items` with each item's cheapest store
  and price, and `by_store`). For example, a Markdown card:

  ```yaml
  type: markdown
  title: Shopping list by store
  content: >
    {% for g in state_attr('sensor.receipt_shopping_list', 'by_store') or [] %}
    **{{ g.store }}**{% if g.total %} (about ${{ g.total }}){% endif %}
    {% for i in g['items'] %}- {{ i }}
    {% endfor %}{% endfor %}
  ```

### "Cheapest here" notifications

Set **Track this person or phone** (a `person.` or `device_tracker.` entity kept up to date by the Home Assistant
companion app) and **Notify service for "cheapest here"** (for example `notify.mobile_app_pixel_8`; empty uses the
alerts' notify service). Every minute its position is checked against the stores where items on the list are cheapest;
within **Distance that counts as at a store** (default 250 m) you get one notification, for example *"Sam's Club: 2
items on your list are cheapest here — Gas $3.199/gal (online) · 2% Milk $3.19/gal (online)"*, and a
`receipt_price_intelligence_nearby_cheapest` event is fired for your own automations. It is sent once per visit (not
again until you have been well away) and at most once every four hours per store; a position less accurate than 500 m
is not used. *Send a test notification* on the List page checks the set-up.

## Stores near home

On the **Stores** page, *Stores near home* → *Find stores near home* searches OpenStreetMap for grocery stores
around your home address (set on the Trip page), within a distance you choose, including stores you have never
shopped at. For each one it shows the distance, kind of store, address and opening hours, when OpenStreetMap has them.

- A store you **already shop at** is recognised (same store name within about 250 m, or the same street address)
  and marked *you shop here*. If OpenStreetMap knows its opening hours, they are offered as a suggestion on that
  store, the same way hours found on the web are: nothing changes until you accept. The hours lookup also uses
  these before searching the web.
- **Add** makes any other store one of your stores, with its location and opening hours. From then on:
  *Check prices online* and the trip planner's *Check current prices online before planning* also look up
  your items on that store's website, *Look for deals* includes it, and the trip planner can send you there.
  A store you have no receipt from is only ever used for an item once a price for it is found online (or,
  for another branch of a chain you already buy from, the chain's price), and an online price there is
  checked against what you pay for the item elsewhere: one far off is taken as misread and not used.
  **Remove** undoes it.
- **Hide** a store you are not interested in; it stays hidden when you search again.

Searches are kept, so the list is there next time without asking OpenStreetMap again; search again whenever you
like. **Privacy:** only your home's location, rounded to about a kilometre, is sent to the OpenStreetMap store
search (the *Nearby store search server* option, the free public Overpass server by default). Leave that
setting empty to turn the search off.

## Data and backups

The database is stored in the app's data folder, which is included in Home Assistant backups and survives updates. Receipt photos are only kept while you review a receipt and are deleted when you save or discard it (or when an unsaved receipt expires), so backups hold your receipt details and prices, not the photos. Treat backups as personal financial data.

## Store hours and deals from the web

The app can search the web to find **opening hours** for your stores, **current prices** and **deals** on the items you buy. Choose how with **Web search provider**:

- **tavily** (easiest, free): sign up at tavily.com (no card needed; the free plan gives 1,000 searches a month, reset on the 1st), and put the key (starts with `tvly-`) in **Web search API key**. Each search uses one credit. As a rough guide, *Check prices online* for 10 items at 2 stores uses about 20; *Look for deals* about 2 per store; an hours lookup 1 per store. Pages are opened by the app itself, so reading them is free; when a store's site refuses that, the page text Tavily sent with the result is used. If the month's credits run out, lookups say so until they reset.
- **parallel** (free credit, not blocked): Parallel's Search API, which uses its own index, so it is not blocked the way
  engines scraped by SearXNG are. Every account gets $5 of free credit each month; searches use the Fast mode ($1 per
  1,000), so about 5,000 searches a month cost nothing. Sign up at platform.parallel.ai, create an API key and put it in
  **Web search API key**. Each result comes with text from its page, which is used when a store's site refuses to be
  opened. When the free credit is used up (or you add no card), searches pause and say so.
- **searxng** (free, self-hosted): set **Search server address** to your SearXNG instance. `json` must be listed under `search: formats` in its `settings.yml`, or searches are refused (403). Pages are opened by the app itself.
- **custom**: your own search server with the two endpoints below.

### Web debug page

Administrators can open **Admin → Web debug** (also **Stores → Debug lookups**) to see exactly what the web lookups send and get
back, with any of the three providers (tavily, searxng, custom):

- The provider in use and its settings (keys and tokens are only shown as *set* or *missing*).
- **Try it**: *Search* (one search as lookups send it, optionally limited to one website, with the hits read from the
  answer); *Open a page* (what is read from it: its text, prices and hours from its structured data, and the part the
  model would be given for hours); and full dry runs of an *Hours lookup* for a location, a *Price check* of an item at
  a store, and a *Deals lookup* at a store, showing each page tried, what was read from it, whether the model was asked
  and what it answered. Nothing is saved.
- **Presets** (top of *Try it*): *Show search queries* lists what every lookup would open and search for, store by
  store and item by item, without sending anything (each search has *Try this search*). *Find hours only*, *Check
  prices* and *Look for deals* run that one kind of lookup for your locations, most bought items (at the stores you
  buy them at) or stores, up to *How many*, one after another in the background, as a dry run: each result shows what
  was found and, when opened, every request it made. Nothing is saved.
- **Recent requests**: every request any lookup made (searches, pages opened, questions to the model), newest first,
  each with what was sent and what came back, the status and the time taken. Kept in memory only (the last 150,
  cleared on restart); authorization headers and keys are always replaced by *(hidden)*.

### Store web pages (check stores directly)

On the **Stores** page, each store's menu has **Website and pages…**:

- **Website** (e.g. `kroger.com`): searches look on this site first, before the whole web.
- **Product search page**: the address of the store's search results with `{query}` where the product name goes.
  Search the store's site for anything, copy the results page's address, and replace your search words with
  `{query}` (for example `https://www.kroger.com/search?query={query}`). *Check prices online* opens it directly
  for each item, with no web search at all.
- **Weekly ad or deals page**: *Look for deals* reads it first.

Each location's menu has **Store web page…**: that store's own page (with its address and hours). Hours lookups
read it first; most store pages publish their hours as structured data, which is read without the model. Stores
found or recognised by *Stores near home* get this page filled in from OpenStreetMap when it lists one.

**Fill in websites** (a card at the top of the Stores page while some stores have no website) does the Website field
for you. First it takes each store's website from its store pages (and from the websites OpenStreetMap lists for its
branches, after *Stores near home*); those are set straight away. Then, with web search set up, it searches each
remaining store's name and suggests the site that comes up most, preferably one carrying the store's name. Check each
suggestion (*open*) and tap *Use*, or *Use all* for the ones carrying the store's name. Social networks, maps, review,
delivery and coupon sites are never suggested. Product search and weekly ad pages stay for you to add, since every
site builds those differently.

Each lookup uses these first and only falls back to searching when they give nothing. Some big chains build their
pages with scripts or block automated visits; for those, the direct page may give nothing and the search is used as before.

**Fewer searches.** Search engines block bursts of automated searches (see Troubleshooting), so the app keeps
the number down: search results are kept and reused for **Keep search results (days)** (default 3; pages 1 day) in
`websearch_cache.db` next to the database (not part of backups, safe to delete); searches to SearXNG or a custom
server are at least **Seconds between searches** apart (default 4); and when a search on a store's own website
found pages that opened, the extra search of the whole web is left out. The Web debug page shows answers from
saved results as *Saved*, and can clear them.

When the app opens pages itself (tavily and searxng), it only opens public web addresses on the usual ports: a search result or redirect pointing into your home network (your router, Home Assistant) is never followed.

A **custom** server is set with **Search server address** (an address Home Assistant can reach, such as `http://192.0.2.10:8080`, not `localhost`) and must answer two POST requests with JSON:

- `POST /search` with `{"query": "..."}` returns a list of results (each with a title, address and text; they may be under `results`, `items`, `data` or `hits`, and use `url`/`link`/`href`, `snippet`/`content`/`text`).
- `POST /fetch` with `{"url": "..."}` returns the page as text (in `text`, `content` or `markdown`, or as plain text).

If your server calls those fields something else, change **Search request field name** and **Fetch request field name**. On the Stores page an administrator can press **Test connection**: it searches, fetches the first result, asks the model one question, and shows what came back, so you can see exactly where a mismatch is.

- **Opening hours** (Stores page): *Find hours for all stores* (or ⋯ → *Find hours online* for one). For each store the server is searched, the best pages are read by your model, and the answer is offered as a **suggestion** with its source and how sure it is: *Use these hours* or *No thanks*. Nothing changes until you accept, unless you turn on *Apply store hours found online automatically* (only for stores with no hours, and only for confident matches). Accepted hours are used by the trip planner.
- **Deals** (Best prices page): *Look for deals* searches each store's weekly ad and sale pages, has the model list what is on sale, and keeps the products that are items you buy. Each shows the sale price against what you usually pay at that store, when it ends, and where it was found. Deals well below your usual price raise an alert, and the trip planner uses them ("Use deals found online" in its options). They are looked up in the background every few days (an App setting).

- **Current prices** (Best prices page → *Latest prices online*): *Check prices online* looks up what your most bought items cost now on the websites of the stores you bought them at (through the search server, several item-at-a-store checks at once — see *Requests at once* below). A price is only kept when a listing clearly is that item and its price can be compared in the item's own unit (a 1 gal listing against a per-gallon history, a per-lb price against per-lb receipts). Each shows the price against what you last paid there. Prices are read from the page itself where possible: first the product data store sites publish for search engines, then a price printed next to the product's name; the model reads the page only when neither works, and each price is labelled with how it was read. Every check is listed on the Best prices page under *Online price checks by date* (kept 30 days; the last 7 days count). An item-at-store pair checked within **Reuse an online price for** (default 6 hours) is left as it was rather than checked again; if everything you asked for was already fresh, a *Check again anyway* option appears. The trip planner can use them: a current online price replaces the price from your last receipt when it is newer, unless it is wildly different from what you paid (then it is assumed to be misread). The trip result has an *Online prices behind this plan* card listing each online price found for the trip's items, by the day it was checked, with which ones decided the route and why any were not used. **Ignoring online prices.** In the trip planner's options, *Prices to use* can be *My receipt prices only*: online
prices are then ignored for that plan and no online check runs. To ignore just one online price (a misread page, a
product that is not the one you buy), tap **Use receipt price instead** under that item in the plan or in *Prices at
each store*: from then on that item at that store uses its receipt price everywhere (trip planner, shopping list, Home
Assistant, notifications) and price checks skip it; **Use online price again** undoes it. The shopping list has its own
*Use online prices* switch.

**Which price the trip planner uses.** For each item at each store, the store's current **online price** is used whenever one was found; the **receipt price** (what you last paid there, or at another branch of the chain) is used only when there is no online price, or when the online price is so far from what you pay (under 0.4× or over 2.5×) that it was almost certainly misread. Every item in the plan says *Used: online price* or *Used: receipt price*, shows both prices side by side (the one used is ticked), and says why: "today's price on the store's website", "no online price found", or "online price $0.35 looked misread". A panel under the totals counts how many items use an online price and how the online prices compare with your receipts, and *Check online prices now* looks again. *Prices at each store* lists every item at every store with both prices and where the plan buys it; *All online prices found* lists every online price with what happened to it.

**Searching by ZIP code.** Searches name the store's ZIP code (from its address, else your home address): *Kroger 2% Milk price 80134*, *Costco Wholesale gas prices 80134*, *Kroger weekly ad sale this week 80134*. A price check first makes **one area search per item** (*2% Milk price 80134*, *gas prices 80134*) and files each result under the store it is about: the store whose website it is on, or the one store it names (*Sam's Club* also matches *Sams Club*; *Costco Wholesale* matches *Costco*). For fuel, a page listing many stations (*Costco … $3.29 · Sam's Club … $3.19*) gives each of your stations the price after its name, preferring the entry with your station's street number when the chain has several; area and statewide averages are not taken for a station's price. Only the stores still without a price are then searched one by one, so one search can price several stores. Area searches are made for fuel at two or more stations and for other items at three or more stores; *Show search queries* on the Web debug page lists them.

**How an item's online price is chosen.** Every listing on the store's pages that matches the item is collected (up to three pages from one search, no extra searches), only the closest matches are kept, prices under half or over twice the middle one are left out when there are three or more (a per-ounce price read as the pack price, a multipack), and the cheapest remaining is taken. The plan shows how it was chosen ("lowest of 3 matching listings (1 left out as unlikely)") and links to the page.

In the trip planner, *Check current prices online before planning* checks every item on your list at **every one of your stores** (not only where you bought it, since the planner compares each item at all of them), up to 60 item-at-store checks, skipping anything already fresh; a kept plan is only reused after that check, when the prices have not changed. In the plan, each item priced from an online price is marked **online** (linked to the page it came from), and a line under the plan says how many items are priced online and how many were just checked. **What is searched for.** Item names are tidied for searching: receipt noise is removed (*PRODUCE Large Hass Avocados* → *Large Hass Avocados*, *1 Chocolate Mousse CC*, *(GF, DF, V)*), clear misspellings are fixed (*Avacado* → *Avocado*, *Pomogranate* → *Pomegranate*; an unclear one is settled by your other items, so *peanut chikli* becomes *chikki* because you also buy *Peanut chikki*), and the brand printed on the item's receipts is added at the stores you bought it at (*1 FS LAXMI SOOJI ROASTED* → *Laxmi Sooji roasted*; *SILK ORG.ALM* → *Silk Almond milk*; *NM VIT D* → *Nature Made Vitamin d*). Other stores are searched with the plain name, since they may not carry that brand. Fees (parking), restaurant dishes and clothing are not looked up online at all, and restaurants and stores where only such things were bought (a car park) are left out of price, deal, hours and website lookups.

**Fuel** is priced on its own path, at the stations where you bought it (Costco, Sam's Club, a gas station): the grade comes from your receipts (*Regular*, *Pump# 14 UNLEAD* → regular; *Premium*, *Diesel*), and the lookup reads the prices the station posts, per gallon with three decimals (*Regular $3.899*, *$3.19⁹*, *Unleaded 3.79 9/10*): first the location's own page, then search results on the store's site and on the web (with the street and town, since each station has its own price), reading each result's snippet before opening its page. The trip planner then uses it like any online price: a fill-up of your usual amount at the cheapest station on the way. The Web debug page's *Show search queries* lists every name as searched and everything left out.

A listing matches an item when every word of the item's name is in it: words that only describe it (brand, *reduced fat*, *organic*, *boneless*) are fine, words that make it a different product (*chocolate*, *almond*, *egg whites*) rule it out, and fat percentages must agree (*2%* milk is not *whole* milk). The listing's main noun counts most: *Member's Mark Unsalted Butter, 4 x 1 lb* is butter whatever else it says, *Tomato Sauce* and *Water Chestnuts* are not tomato or water. Different words for the same thing are understood (*toilet paper* / bath tissue, *hand soap* / handwash, *semolina*, *suji* / sooji, *soy chunks* / soya chunks, *bhindi* / okra, *coriander leaves* / cilantro, *chili* / chilli, *Almondmilk* / almond milk), and a word one or two letters off still matches (*Avacado*). A package with a weight range (*2.25–3.5 lb tray*) is priced per pound on the middle of the range. Prices are dropped a week after they were checked either way. An App setting (*Check online prices every*) can do this in the background for your most bought items; it asks the model many questions, so it is off by default.

Web pages can be wrong, out of date or read wrongly by the model, so deals and prices are marked as coming from the web and the planner says to check the store's own ad. Your receipts and prices are never sent to the search server: only store names, towns, addresses and product names go in search queries.

## Editing and deleting saved receipts

Open a saved receipt and use the menu (⋯): **Edit receipt** reopens it so you can correct the store, date, lines, totals and names. The saved version stays in your history until you save your changes, and **Cancel editing** puts it back exactly as it was. **Delete receipt** removes it and its prices from spending and price trends for good. If a receipt you are saving matches one you already saved (same store, day and total or receipt number), you are asked before it is saved again.

## Categories, budgets and other insights

- **Categories** (Insights → Categories): spending by kind of item. New items get a category guessed from their name; *Fill in automatically* does it for existing ones, and you can set any item's category from its detail. Add a **budget** per category or for all spending; you are told once at 80% used and once when it is exceeded.
- **Sales and coupons** (Insights → Overview): what discounts saved you, by store and item.
- **Your price index** (Insights → Prices): how the things you keep buying cost now compared with a year ago.
- **Learning**: when you correct how a line was read, the same misreading from that store is fixed automatically next time.
- **Tags and export**: tag receipts (business, warranty, ...) on the review screen, filter the saved list by tag, and export receipts or every line as CSV from Insights → Export.
- **Upload several receipts**: the upload icon on the receipts page adds many photos and PDFs at once, one receipt each.

See `docs/INSIGHTS.md` for how each one is calculated.

## Alerts and price targets

The **Best prices** page shows alerts and lets you set **price targets**: "tell me when eggs are at or below $3" or "tell me about a new low". Alerts are also raised for a much cheaper store, a big price drop, items overdue for restock and budgets nearly used up or exceeded, each only once. They appear on that page and are sent to Home Assistant (see below). The smallest saving worth an alert is an App setting.

## Home Assistant

The app talks to Home Assistant through the Supervisor, so no token or setup is needed.

- **Sensors** (updated after every saved receipt and every day), per home, named `sensor.receipts_<home>_...`: `spend_this_month` (with trips, average basket and the change from last month), `potential_savings`, `items_due`, `price_alerts`, `budgets_over`, `basket_change` (your 12-month price index) and `last_receipt`. Sensors made this way disappear when Home Assistant restarts and come back at the next update.
- **Events**: every new alert fires the event `receipt_price_intelligence_alert` with `home`, `kind` (`PRICE_TARGET`, `NEW_LOW`, `SAVING`, `PRICE_DROP` or `RESTOCK`), `title`, `message` and `data`, for automations.
- **Notifications**: alerts go to the notification service in App settings (for example `notify.mobile_app_your_phone`), or appear as persistent notifications if it is empty.
- **Shopping list**: on the Trip page, *Import from a Home Assistant list* reads a to-do list (for example the Shopping List), matches its lines to items you have bought, lets you confirm them, and adds them to the trip. Needs a recent Home Assistant.

Example automation:

```yaml
trigger:
  - platform: event
    event_type: receipt_price_intelligence_alert
    event_data:
      kind: PRICE_TARGET
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "{{ trigger.event.data.title }}"
      message: "{{ trigger.event.data.message }}"
```

## Importing receipts from a folder or email

Set **Folder to import receipts from** (a folder under `/share`, for example `/share/receipts`) and photos and PDFs dropped there become drafts to review; handled files move to a `processed` folder (or `failed`) and are deleted after two weeks. Set the **Mail server (IMAP)** settings and PDF and image attachments of unread emails are imported the same way (the emails are marked as read; only attachments are used). If you have more than one home, name the home imported receipts go to. Imported receipts are read by the model in the background and wait under *To review*. Nothing reaches your price history until you approve it. The receipts page has a *Check now* button.

## Trip planner

Choose items from those you have bought and the planner works out where to buy them and the route to drive, for the lowest **item prices plus driving cost**.

1. **Home and car**: open *Trip*, enter your home address, and add a car. A fuel car needs its economy (mpg) and fuel price. An electric car needs its consumption (kWh per 100 miles) and electricity price. The planner shows what each mile costs to drive.
2. **Items**: search and add items (or import a Home Assistant list) (each once) and set how many you want. It starts at 1. For items sold by weight, 1 means your usual purchase.
3. **Options**: whether to return home, whether to use sale and member prices seen on receipts, and (under *More options*) what an hour of your time is worth, how long you spend in each store, when you leave, and an optional preference for how many stores to visit. You never have to pick a number of stores: the planner works out how many are worth visiting. If you do ask for at most a certain number and your list needs more, you still get the trip, with a short note. Set opening hours on the **Stores** page (⋯ → *Opening hours*) and the planner only visits a store when it will be open.
4. **Plan my trip**: the app finds your stores on the map (each address is looked up once), then shows the best plan: which stores, in what order, what to buy where, the distance and time, and the total. It also shows what you save against shopping at one store, other options, and anything it had to leave out.

It considers the stores you have bought from, using the price you last paid there, plus any store you added under *Stores near home* (see below), so keep addresses right on the **Stores** page. Prices exclude tax and may have changed. See `docs/TRIP_PLANNER.md` for how it works.

**Privacy:** addresses are looked up on the free OpenStreetMap services (Nominatim for addresses, OSRM for driving distances). Your home address and your stores' addresses are sent to them; receipts and prices are not. Point the *Address search server* and *Routing server* settings at your own servers to keep them private. The map on the page is drawn locally, with no map tiles.

## Backup and restore

Administrators open **Admin → Backup**.

- **Export** downloads everything (all homes' receipts, prices, items, stores, people and the App settings) as one
  file. Backups leave out access keys and passwords; after restoring on a new install, enter them again. (Restoring
  keeps the ones this install already has.) Receipt photos are not included because they are deleted once a receipt is
  saved. The file is personal financial data, so keep it private.
- **Restore** replaces everything in the app with a chosen export. The file is checked first; a copy of the current
  data is kept (the newest three are listed on the page and can be downloaded, also without access keys and passwords)
  and put back automatically if the restore fails. Exports from older versions are upgraded. App settings come from
  the file when it has them; otherwise the current ones are kept. Administrators always come from admin_users.

Home Assistant's own backups of the app also include this data. Use export/restore to move data between installs or
to keep a copy outside Home Assistant.

## Troubleshooting

- **Tavily says the API key was refused, or the search limit was reached**: check the key in Admin → App settings; the free plan's 1,000 monthly searches reset on the 1st of the month.
- **Using LLocalSearch**: it is an AI chat app, not a search API, so its address cannot be used. It runs SearXNG
  inside it, though: publish SearXNG's port in LLocalSearch's `docker-compose.yaml` (under `searxng:` add
  `ports: ["8081:8080"]`), restart it, then choose the `searxng` provider with the address
  `http://<that machine's IP>:8081`.
- **SearXNG returns no results, and its answer lists `unresponsive_engines` with "CAPTCHA" or "too many requests"**
  (the app then says web searches are paused): the search engines SearXNG uses have blocked your home address for
  sending too many automated searches. SearXNG itself stops using a blocked engine for a while (by default around an
  hour for too many requests, up to a day for a CAPTCHA; see `suspended_times` in its `settings.yml`). The app
  pauses all web searches for 20 minutes when every engine refuses, instead of adding to the blocks. To avoid it: keep
  **Keep search results** on and **Seconds between searches** at 4 or more, set **Search requests at once** to 1, run presets with small numbers, keep scheduled lookups modest, and enable more
  engines in SearXNG (for example Bing, Mojeek, Qwant) so one blocked engine does not leave it with none. Store
  pages and product search pages (Stores → *Website and pages…*) need no search at all, so filling those in helps most.
- **SearXNG refuses searches (403)**: add `json` under `search: formats` in its `settings.yml` and restart it.
- **The web lookups say the search server could not be reached, or return nothing**: use *Test connection* on the Stores page. Use the server's IP address rather than `localhost`, check the port, and check the field names match what your server expects. If the test shows results but no addresses or text, your server uses different names for them: open an issue with a sample answer so they can be added.

- **A new tab, screen or feature is missing, or you see an error like "... is not a function"**: the app is showing an old cached copy of a script underneath a current page. From 0.9.4 on, each page checks this itself and reloads automatically the moment it notices, so this should only ever be momentary. If it persists: update to the latest version, then in the Home Assistant app go to *Settings → Companion app → Troubleshooting → Reset frontend cache* (or clear the app's cache in your phone's settings) and reopen the panel — this is only needed once, to get a version new enough to carry out that check itself; from then on it should not require manual clearing again.

- **"Could not connect to the model server"**: check the URL is reachable from Home Assistant (use the server's IP address rather than `localhost`).
- **Reading is very slow or times out**: raise the timeout; large models on home hardware can take a few minutes per receipt. The first read after the model loads is the slowest.
- **Text is cut off or the model returns nothing usable**: raise the context window (Ollama) or the maximum reply length.
- **Camera does not open**: browsers only allow the live camera over HTTPS. Use Home Assistant through its secure address, use *Take photo* (your phone's own camera) when offered, or *Choose photos or PDF* instead.
- **A PDF won't upload**: it must be under 20 MB and not password protected. Only the first 8 pages are used.
- **An item shows a price with a small "other unit" tag**: that purchase was priced in a different unit from most of the item's purchases (for example each instead of per lb). Its amount is counted in spending, but it is left out of the store comparison and price chart.
- **The trip planner leaves a store out**: its address is missing or could not be found. Fix it on the Stores page. Stores are looked up on OpenStreetMap, so a full street address with city and postal code works best.
- **Distances are marked as estimates**: the routing server could not be reached. Check the app can reach the internet, or set your own routing server.
- **Prices look wrong for an item**: open it in Insights and rename it or merge it with the right item.
- **Something else**: set the log level to DEBUG, reproduce the problem, and read the app log.
