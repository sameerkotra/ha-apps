# API Reference

## Base URL
```
http://localhost:8099
```

## Authentication and access

Every request must come from Home Assistant's ingress proxy (`172.30.32.2`) or loopback (`403`
otherwise), and every `/api/` request needs `X-Remote-User-Id` (`401` otherwise). The proxy also adds
`X-Remote-User-Name` and `X-Remote-User-Display-Name`.

**Every signed-in user can view and add data in any home.** Only administrators can create,
rename or delete homes, change App settings, back up and restore, and use the web debug page
(`403` otherwise). Administrators are the people on the app's `admin_users` option, by user id or
login name (any case), and nobody else.

## Endpoints

### Health

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | `{status: "ok"}` (the Supervisor's watchdog) |
| `GET /health/model` | `{configured, model_name}`: whether a model is set in App settings |
| `GET /health/database` | `{status, connected}` |

---

### App settings (Admin)

| Endpoint | Who | Purpose |
| --- | --- | --- |
| `GET /api/admin/settings` | admin | `{values, defaults, meta, groups, secretsSet}`; secrets are sent as `""` |
| `PUT /api/admin/settings` `{key: value, ...}` | admin | Change only the keys sent; `422` names the bad setting and nothing is saved. A secret sent as `""` is cleared |

See `spec/SPEC.md` §3 for the fields of `meta`.

### Current user, homes, stores and prices

| Endpoint | Who | Purpose |
| --- | --- | --- |
| `GET /api/v1/me` | any | `{id, display_name, username, is_admin, anonymous, noAdmin}` |
| `GET /api/v1/whoami` | any | The shared "How the app sees you" contract (`WHOAMI_PAGE_SPEC.md`): what Home Assistant sent (`haUserId`, `haUsername`, `haDisplayName`, `nameSent`), `isAdmin`, `displayNameOnly`, `adminEntries` (a count), `noAdmin`, `viaIngress`, `extras` |
| `GET /api/v1/homes` | any | Homes with `receipt_count` and `pending_count` |
| `POST /api/v1/homes` `{name}` | admin | Create a home (`409` if the name exists) |
| `PATCH /api/v1/homes/{id}` `{name}` | admin | Rename |
| `DELETE /api/v1/homes/{id}` | admin | Delete an empty home (`409` if it holds receipts) |
| `GET /api/v1/users` | admin | Users who have opened the app, with `is_admin` |
| `GET /api/v1/stores/chains` | any | Known chain names (autocomplete) |
| `GET /api/v1/homes/{id}/stores` | any | Stores this home has bought from |
| `GET /api/v1/homes/{id}/prices?q=` | any | Latest price per item per store (the Insights page uses `/analysis/prices`, which adds trends) |

Creating a draft (`POST /api/v1/receipt-drafts`) takes an optional `home_id`. It may be omitted only when exactly one home exists (`409` otherwise). `GET /api/v1/receipt-drafts` accepts `home_id` and `status_filter`.

### Stores

Stores are shared by every home. Any signed-in user can correct names and addresses; merging and deleting are administrator-only. Conflicts return `409` with `{detail, conflict_id}`, where `conflict_id` is the row in the way.

| Endpoint | Who | Purpose |
| --- | --- | --- |
| `GET /api/v1/homes/{id}/store-directory` | any | `{chains, similar_chains, duplicate_locations}`. Each chain has `locations` (store number, address parts, receipt count and spend **for this home**, `deletable`) and `aliases`. `similar_chains` and `duplicate_locations` are suggestions only |
| `PATCH /api/v1/store-chains/{id}` `{name}` | any | Rename. The old name becomes an alias, so receipts that still print it land on this chain. `409` if the name belongs to another chain |
| `POST /api/v1/store-chains/{id}/merge` `{into_chain_id, combine_locations}` | admin | Move all locations, receipts and prices to another chain; both names keep resolving to it. `combine_locations` (default `true`) also merges every location into one, so the merged store is a single store holding all receipts (the location with the most receipts is kept and inherits a missing store number or address); `false` keeps each location separate. Returns `locations_moved`, `locations_merged`, `locations_combined` |
| `DELETE /api/v1/store-chains/{id}` | admin | Only when no receipt uses it (`409` otherwise) |
| `POST /api/v1/store-chains/{id}/locations` | any | Add a location: `store_number`, `name`, `street`, `city`, `state`, `postal_code` (or `raw`) |
| `PATCH /api/v1/store-locations/{id}` | any | Edit those fields (only sent fields change) or move it with `store_chain_id`. `409` if the chain already has that store number |
| `POST /api/v1/store-locations/{id}/merge` `{into_location_id}` | admin | Receipts, prices and tax observations move to the other location |
| `DELETE /api/v1/store-locations/{id}` | admin | Only when no receipt uses it |

### Common item names

A *common name* is what you call a product ("Milk, 2%"); printed descriptions are aliases of it. In a draft, set `suggested_common_item_name` (and `suggested_common_item_id` when picking an existing one) on a line via `PUT /receipt-drafts/{id}/items`. On extraction, lines whose printed description already has a confirmed common name are filled in automatically. On approval a named line joins the existing item with that name (case-insensitive) or creates it, and links the printed description to it for next time.

| Endpoint | Who | Purpose |
| --- | --- | --- |
| `GET /api/v1/homes/{id}/common-items?q=&limit=` | any | Items with `name_confirmed`, `alias_count`, `purchase_count`. Feeds the autocomplete |
| `GET /api/v1/common-items/{id}` | any | The item and its printed descriptions |
| `PATCH /api/v1/common-items/{id}` `{name}` | any | Rename (marks it confirmed). `409` + `conflict_id` if another item has that name |
| `POST /api/v1/common-items/{id}/merge` `{into_item_id}` | any | Move purchases, prices and printed names to another item in the same home |

### Analysis

All under `/api/v1/homes/{id}/analysis`, readable by any user, computed live from approved receipts. Items appear under their common name. `range` is `30d`, `90d`, `12m` or `all`.

| Endpoint | Returns |
| --- | --- |
| `GET /overview?range=` | `totals` (spend, trips, avg basket, stores, distinct items, `previous_spend`, `change_pct`), `by_month` (gaps filled), `top_stores`, `top_items` |
| `GET /stores?range=` | Spend, trips, average basket, share and last visit per chain, with a breakdown by location |
| `GET /items?range=&q=&sort=` | Per item: spend, purchases, `avg_interval_days`, `next_expected_date`, `overdue_days`, top store, latest/average price, trend. `sort`: `spend`, `frequency`, `recent`, `name` |
| `GET /prices?range=&q=` | Per item: latest price at each store location (cheapest flagged, `premium_pct`) and the price trend series |
| `GET /items/{item_id}?range=` | One item: aliases, stats, store comparison, price series per location, recent purchases (each with its price, unit, `comparable` and `total`), and `other_unit_purchases` (purchases priced in a unit the item is not compared in) |
| `GET /recommendations?days=` | `switch_store`, `price_alerts`, `restock`, `store_scorecard`, `est_monthly_savings`. Defaults to `RECOMMENDATION_WINDOW_DAYS` and `MINIMUM_SAVINGS_PERCENT` |

Prices are only compared like for like: mass units (lb, oz, kg, g) are converted to one unit per item; anything else (for example "each") is compared only with itself. See [RECOMMENDATIONS.md](RECOMMENDATIONS.md) for how each suggestion is calculated.

### Best prices (daily check)

A background job checks every home once a day (at `RECOMMENDATION_RUN_HOUR`, server local time) and stores the result, so the page opens instantly. At start-up it also checks any home whose result is missing or older than 23 hours.

| Endpoint | Who | Purpose |
| --- | --- | --- |
| `GET /api/v1/homes/{id}/best-prices` | any | The latest check: `status` (`PENDING`, `OK`, or `FAILED` with `error`, in which case the previous results are still returned), `generated_at`, `next_run`, `summary`, `best_prices` (every checked item: cheapest store and price, your usual store, `saving_pct`, latest price per store), `switch_store`, `price_alerts`, `restock`, `store_scorecard` |
| `POST /api/v1/homes/{id}/best-prices/refresh` | any | Run the check for this home now |

### Trip planner

Any signed-in user. Units follow the `UNIT_SYSTEM` setting (`imperial`: miles, gallons, mpg, kWh/100 mi; `metric`: km, litres, L/100 km, kWh/100 km). See [TRIP_PLANNER.md](TRIP_PLANNER.md).

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/planner/config` | Unit labels, currency, and the map servers in use |
| `GET /api/v1/homes/{id}/planner/setup` | `{home: {address, latitude, longitude, located}, vehicles, config}` |
| `PUT /api/v1/homes/{id}/planner/address` `{address}` | Save the home address and look it up. An empty address clears it. Returns `located` and a `message` if it could not be found or the map service was unreachable |
| `GET /api/v1/homes/{id}/vehicles` | Cars with `economy`, `energy_price`, `other_cost_per_distance`, `range`, `cost_per_distance` |
| `POST /api/v1/homes/{id}/vehicles` | Add a car: `name`, `kind` (`GAS`, `DIESEL`, `HYBRID`, `EV`), `economy`, `energy_price`, optional `other_cost_per_distance`, `range`. `400` with a message for invalid values |
| `PATCH /api/v1/vehicles/{id}` / `DELETE /api/v1/vehicles/{id}` | Change (send all fields) or delete a car |
| `GET /api/v1/homes/{id}/planner/items?q=` | Items with a price on record: `unit`, `amount` (usual purchase for weighed items, else 1), `purchases`, `stores` |
| `POST /api/v1/homes/{id}/planner/locate` `{item_ids}` | Find map coordinates for stores that need them (a few per call, one search a second, remembered). Call again while `pending` > 0. `unreachable` is true if the map service could not be reached |
| `POST /api/v1/homes/{id}/planner/plan` | `{items: [{item_id, qty}], vehicle_id | cost_per_distance, max_stops (optional preference, 1-6), round_trip}`. The number of stores is worked out by the planner; `max_stops` only expresses a preference. Also returns `online_prices` (each online price and deal considered for the trip's items: `price`, `unit`, `fetched_at`, `method`, `source_url`, `status` — `used` or why not — and `in_route`), `stops_needed`, `stops_message` (set when more stores are needed than the preference), `hours_ignored`, and stops may carry `closed_on_arrival`. Returns `best` (stops in order with leg distance and time, items with unit price, cost and whether the price is estimated or old, subtotals, `items_total`, `travel_cost`, `total`), `alternatives` (best plan for other stop counts), `single_store`, `savings_vs_single_store`, `ignoring_driving`, `unavailable`, `warnings`, `map` (home, stops and the route line) and `distances_estimated` |

### Insights, categories, budgets and export

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/homes/{id}/analysis/categories?range=` | Spend per category with share, top items and change from the previous period; `uncategorized_share` |
| `GET /api/v1/homes/{id}/analysis/discounts?range=` | What sales and coupons saved: total, rate, by store, month and item |
| `GET /api/v1/homes/{id}/analysis/inflation?months=` | Personal price index: `series` (starts at 100), `change_pct`, `risers`, `fallers`; `available: false` with a `reason` until there is enough history |
| `GET /api/v1/homes/{id}/categories` | Categories to choose from (standard plus your own) |
| `POST /api/v1/homes/{id}/common-items/categorize` | Fill in every empty category from the item's name |
| `PATCH /api/v1/common-items/{id}` `{name?, category?}` | Rename and/or set the category (only the fields sent change; an empty category clears it) |
| `GET /api/v1/homes/{id}/budgets` | Budgets with this month's `spent`, `remaining`, `percent`, `projected`, `status` (`ok`, `warn`, `over`) |
| `PUT /api/v1/homes/{id}/budgets` `{category, monthly_amount}` | Create or change a budget (empty `category` = all spending) |
| `DELETE /api/v1/budgets/{id}` | Remove a budget |
| `PATCH /api/v1/receipt-drafts/{id}/tags` `{tags}` | Change the tags of a saved receipt. For a draft in review send `tags` with `PATCH /receipt-drafts/{id}` |
| `GET /api/v1/homes/{id}/export/receipts.csv` | One row per saved receipt; optional `start`, `end` (inclusive) and `tag` |
| `GET /api/v1/homes/{id}/export/items.csv` | One row per receipt line, with common name and category |
| `POST /api/v1/import/upload` | Add several receipts at once (`multipart`: `home_id` and up to 30 `files`); one draft per photo or PDF; returns a result per file |

### Saved receipts

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/receipt-drafts/{id}/reopen` | Reopen a saved receipt for full editing. It stays in the history until the edited version is approved, which replaces it. `409` if it is not saved |
| `POST /api/v1/receipt-drafts/{id}/discard-edit` | Abandon the edit: the draft goes back to exactly what was saved |
| `DELETE /api/v1/receipt-drafts/{id}/saved` | Delete a saved receipt, its prices and tax records, and its review copy |
| `GET /api/v1/receipt-drafts/{id}/duplicates` | Saved receipts that look like this one (same home, store and day, and the same total or receipt number) |
| `POST /api/v1/receipt-drafts/{id}/approve` | Answers `409` with `duplicates` when a match exists; send `allow_duplicate: true` to save anyway. Drafts have `editing_saved` while a saved receipt is being edited |

### Alerts and price targets

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/homes/{id}/alerts?include_dismissed=` | Recent alerts, newest first: `kind` (`PRICE_TARGET`, `NEW_LOW`, `SAVING`, `PRICE_DROP`, `RESTOCK`), `title`, `message`, `data` |
| `POST /api/v1/alerts/{id}/dismiss` | Dismiss an alert |
| `GET /api/v1/homes/{id}/price-targets` | Targets with the item's best price seen in the last 30 days |
| `POST /api/v1/homes/{id}/price-targets` `{item_id, mode, target_price}` | `mode` is `BELOW` (needs `target_price`, in the item's price unit) or `NEW_LOW`. Checked at once, after each daily check and after each saved receipt |
| `DELETE /api/v1/price-targets/{id}` | Remove a target |

### Home Assistant and importing

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/planner/todo-lists` | To-do and shopping lists in Home Assistant |
| `POST /api/v1/homes/{id}/planner/todo-import` `{entity_id}` | Read a list and match each line to your items: `lines: [{text, name, qty, match, candidates}]` |
| `GET /api/v1/import/status` | Whether the watched folder and the mailbox are set up |
| `POST /api/v1/import/scan` | Check the folder and mailbox now; returns the new draft ids |

The trip planner's `plan` request also accepts `depart_at`, `time_cost_per_hour`, `stop_minutes` and `include_sales`, and returns `time_cost`, `drive_minutes`, `shopping_minutes`, per-stop `arrive_at`, and `on_sale` / `regular_price` per line. Store locations accept `opening_hours` (`{"all": [["08:00","22:00"]], "sun": []}`; a day set to `true` is open 24 hours, an empty list is closed, a missing day is unknown) on `PATCH /api/v1/store-locations/{id}`.

### Web lookups (search server)

Needs web search set up in App settings. Lookups run in the background: the start endpoints return a job to follow.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/webinfo/status` | `{configured, server, auto_hours, deals_days}` |
| `POST /api/v1/webinfo/test` (admin) | Searches once, fetches the first result and asks the model a question; reports what came back and which step failed |
| `GET /api/v1/webinfo/jobs/{id}` | `{done, total, message, results, error, finished}`; a prices job also carries `skipped` |
| `GET /api/v1/homes/{id}/webinfo/hours` | Suggested opening hours: `hours`, `source_url`, `confidence`, `note`, `status` (`SUGGESTED`, `APPLIED`, `DISMISSED`) |
| `POST /api/v1/homes/{id}/webinfo/hours` `{location_ids?, force?}` | Look up hours for stores that have none (or the given locations) |
| `POST /api/v1/webinfo/hours/{location_id}/apply` / `/dismiss` | Use or turn down a suggestion |
| `GET /api/v1/homes/{id}/webinfo/deals?kind=` | What was found online for your items: `kind` (`DEAL` a promotion, `PRICE` the current shelf price on the store's website), `price`, `unit`, `usual_price` (what you last paid there, in the item's unit), `saving_pct` (negative when it costs more), `valid_to`, `conditions`, `source_url` |
| `GET /api/v1/homes/{id}/webinfo/prices/history?days=` | Every online price check of the last `days` days (up to 30), grouped by date, newest first: `[{date, checks: [{item_name, chain, product, price, unit, method, source_url, time, latest}]}]`. `method` is `structured` (the page's product data), `text` (a price printed on the page) or `model`; `latest` marks the check currently used |
| `POST /api/v1/homes/{id}/webinfo/prices/refresh` `{item_ids?, chain_ids?, force?}` | Check the current online price of items (default: the ones you buy most) at the stores you bought them from. At most 30 item-at-store lookups per run. A pair already checked within `WEB_PRICE_FRESHNESS_HOURS` is skipped and its stored price reused, unless `force` is set; `400` if every pair asked for was skipped this way. The returned job (see below) carries `skipped`, how many were left out as already fresh |
| `POST /api/v1/homes/{id}/webinfo/deals/refresh` | Look for deals at the stores you shop at |

The trip planner's `plan` request accepts `include_web_deals` (default true); lines that use one carry `web_deal`, `deal_until` and `source_url`.

### Notifications

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/notifications` | `{settings, kinds, ha: {available, devices: [{service, name, kind (phone/other)}], trackers, error}}` |
| `PUT /api/v1/notifications` `{devices, bell, kinds, price_drop_percent, restock_auto_add, tracker, nearby_meters, quiet}` | Save; `kinds` has `alerts`, `price_drop`, `restock`, `overcharge`, `nearby`; `quiet` `{start, end}` or null |
| `POST /api/v1/notifications/test` `{service?}` | Send a test now, to one service or every device chosen |

Every notification goes through `app/services/notifier.py`, which applies these settings (saved in the `app_settings` table, key `notifications`; the App settings are the defaults).

### Looking out for you

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/homes/{id}/restock` | `[{item_id, name, every_days, last_bought, due, days_left, status (due/soon/later), regular, purchases, usual_store, on_list}]` |
| `GET /api/v1/homes/{id}/price-drops` | Shopping list items at least `price_drop_percent` cheaper than usual: `[{item_id, name, store, price, unit, usual, percent, price_source, source_url}]` |
| `GET /api/v1/homes/{id}/overcharges?receipt_id=&days=` | Receipt lines costing more than the store posted around the purchase: `[{receipt_id, date, store, item, paid, posted, unit, extra, posted_on, kind, product, source_url}]` |
| `GET /api/v1/homes/{id}/lookout-summary` | `{savings, overcharges}` together from one read of the receipts (the Best prices page) |
| `GET /api/v1/homes/{id}/savings` | `[{kind (store/pack), item, unit, percent, now, better}]` |

### Ignoring online prices

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/homes/{id}/online-price-ignores` | `[[item id, chain id]]` whose online prices the home chose not to use |
| `POST /api/v1/homes/{id}/online-price-ignores` `{item_id, chain_id, ignored}` | Stop (`true`) or start again (`false`) using an item's online price at a store; while ignored it uses its receipt price everywhere and price checks skip it |

`GET /homes/{id}/list?online=false` works out the shopping list's cheapest stores from receipt prices only. Plans list the ignored ones among their items as `online_ignored`.

### Store discounts

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/homes/{id}/store-discounts` | `{chain id: [{id, name, percent, cents_per_unit, applies_to, applies_to_text, active}]}` |
| `PUT /api/v1/homes/{id}/store-chains/{chain_id}/discounts` `{discounts: [{name?, percent, cents_per_unit?, applies_to, active?}]}` | Replace the home's discounts at a store (`applies_to`: `ALL`, `FUEL`, `NON_FUEL`; percent 0–50, cents 0–100, fuel only); an empty list removes them |

Plan lines, `price_table` rows and shopping list prices carry `discount` (`{names, percent, cents, saved}`) and `before_discount` when a store discount applies; `online_summary` has `discounted` and `discount_saved`.

### Shopping list

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/homes/{id}/list` | The list: `items: [{id, text, item_id, qty, checked, cheapest: {store, price, unit, price_source, …}, also}]`, `by_store`, `left`, `currency`, `ha` (sync and notification set-up) |
| `GET /api/v1/homes/{id}/list/browse?q=` | Items bought before, to pick from (`on_list`) |
| `POST /api/v1/homes/{id}/list` `{item_id? , text?, qty?}` | Add a known item or typed text: the item it clearly is, else a new item (no price until a receipt line is given its name); "2 milk" adds two; an item already on the list gets its quantity raised |
| `POST /api/v1/homes/{id}/list/many` `{item_ids}` | Add several known items |
| `PATCH /api/v1/homes/{id}/list/{row_id}` `{checked?, qty?, text?}` / `DELETE` | Tick off, change, remove |
| `POST /api/v1/homes/{id}/list/clear-checked` | Remove bought items |
| `POST /api/v1/homes/{id}/list/sync` | Sync with the Home Assistant to-do list and republish `sensor.receipt_shopping_list` now |
| `POST /api/v1/homes/{id}/list/test-nearby` | Send the "cheapest here" notification now, for the store where most items are cheapest; says how far the tracked person or phone is from it |

### Saved trip plans

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/homes/{id}/planner/plan/saved` (same body as `/planner/plan`) | The kept plan for exactly this request if nothing it uses has changed: `{found: true, ...plan, saved: {at, hours_left}}`, else `{found: false}`. Nothing is looked up |
| `GET /api/v1/homes/{id}/planner/last` / `DELETE` | The home's newest plan `{request, result, saved_at, current, hours_left}` or `null`; forget it |

Plan lines carry `price_source` (`online`, `deal` or `receipt`), `online` (`{price, url, product, fetched_at, method, note}` when an online price was found), `online_rejected` (`{price, url, product, reason}` when it looked misread), `receipt_price`, `receipt_date` and `receipt_from_other_store`. A plan also has `price_table` (each item at each store: `stores: [{store, price, price_source, online, receipt_price, receipt_date, chosen, …}]`) and `online_summary` (`{items, priced_online, priced_receipt, online_rejected, compared_with_receipt, online_vs_receipts}`, the last being how much more (+) or less (−) online prices come to than your receipts for items bought at an online price).

`POST /webinfo/prices/refresh` takes `all_stores` (the Trip page sends it): every item is also tried at every other store of the home, up to 60 checks, stores the item was bought at first.

`POST /planner/plan` keeps each plan it makes (`trip_plan_keep_hours`) and now also takes `include_web_deals` (it was ignored before).

### Web debug (administrators only)

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/webdebug/setup` | Provider in use and its settings (no secrets) |
| `GET /api/v1/webdebug/options?home_id=` | Locations, stores and items to try lookups with |
| `GET /api/v1/webdebug/log?limit=&service=` / `DELETE` | Recent requests of all lookups, newest first (`service`: tavily, searxng, custom, page, model); clear them |
| `POST /api/v1/webdebug/search` `{query, site?, limit?}` | One search; `hits`, `query_sent`, `trace` |
| `POST /api/v1/webdebug/fetch` `{url}` | One page; its text, structured prices and hours, the hours excerpt, `trace` |
| `POST /api/v1/webdebug/hours` `{home_id, location_id}` | Hours lookup dry run |
| `POST /api/v1/webdebug/price` `{home_id, item_id, chain_id}` | Price check dry run: each page with its listings, the model's answer and the pick |
| `POST /api/v1/webdebug/deals` `{home_id, chain_id}` | Deals lookup dry run (first 3 pages) |
| `POST /api/v1/webdebug/preset` `{home_id, preset, limit?}` | `preset`: `queries` (returns `queries`: what each lookup would open and search for, nothing sent), or `hours`, `prices`, `deals` (a background dry run for every location, item-at-store pair or store, up to `limit` (≤25); returns `job`) |
| `GET /api/v1/webdebug/preset/{job_id}` | That run's `results`: `[{label, ok, summary, error, requests, detail}]`, `detail.trace` holding its requests |

Each `trace` entry: `{service, action, method, url, step, sent, status, content_type, received, received_chars, error, ms, note}`; bodies are cut to 20,000 characters and secrets replaced by `(hidden)`.

### Store web pages

| Endpoint | Purpose |
| --- | --- |
| `PUT /api/v1/store-chains/{id}/web` `{website?, search_url?, deals_url?}` | A chain's website (searches look there first), product search page (must contain `{query}`) and weekly ad page. Only the fields sent change; an empty string clears one. `400` if one is not a web address |
| `PATCH /api/v1/store-locations/{id}` `{page_url}` | A location's own page (hours are read from it first) |

| `POST /api/v1/homes/{id}/store-websites/fill` `{search?}` | Fill in the website of the home's chains that have none. From the stores' own pages (and OpenStreetMap's listings of their branches): set at once (`applied`). For the rest, with `search` (default true) and web search set up: a background lookup (`job`); the finished job's `suggestions` (`[{chain_id, name, website, domain, source_url, matches_name}]`) are saved only when accepted with `PUT /store-chains/{id}/web`. `missing` counts chains still without one |

The store directory includes `website`, `search_url`, `deals_url` per chain and `page_url` per location.

### Stores near home (OpenStreetMap)

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/homes/{id}/nearby-stores` | What the last search found, nearest first: `{enabled, searched_at, distance_unit, default_radius, max_radius, stores: [{id, name, brand, kind, distance, address, lat, lng, status, chain, opening_hours, opening_hours_raw, website, osm_url, store_location_id, matched_label}]}`. `status` is `NEW`, `ADDED` (one of the home's stores, `store_location_id`), `KNOWN` (a store you already shop at, `matched_label`) or `HIDDEN` |
| `POST /api/v1/homes/{id}/nearby-stores/search` `{radius?, force?}` | Search around the home (`radius` in the app's distance unit; default 5 mi / 8 km, at most 30 km). The same or a smaller search within 10 minutes reuses the last answer unless `force`. Returns the listing plus `hours_suggested`, how many of your stores got opening-hours suggestions. `400` with a message if the home has no location or the search server fails |
| `POST /api/v1/homes/{id}/nearby-stores/{nearby_id}/add` | Make it one of the home's stores: `{store_location_id, chain}` |
| `POST /api/v1/homes/{id}/nearby-stores/{nearby_id}/remove` | Undo *add* (the location is deleted unless a receipt or another home uses it) |
| `POST /api/v1/homes/{id}/nearby-stores/{nearby_id}/hide` / `/unhide` | Hide a store, or show it again |

Stores added this way are included in `webinfo/prices/refresh` for every item (after the stores you bought the
item at), in `webinfo/deals/refresh`, and in the trip planner. A planner line priced at such a store is an
estimate from an online price; a plan warning (`nearby_unpriced`) names added stores left out for having no
price for the list.

### Backup (administrators only)

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/admin/backup/info` | Row counts, database size, and the safety copies kept from earlier imports |
| `GET /api/v1/admin/backup/export` | Download the whole database as one SQLite file (a consistent snapshot made with SQLite's online backup, safe while the app is in use) |
| `POST /api/v1/admin/backup/import` | Replace the database with an uploaded export (`multipart/form-data`, field `file`, up to 500 MB). Validates that it is an intact database from this app, keeps a copy of the current one (`before-import-*.db`, newest three kept), swaps it in, adds tables and columns missing from older exports, keeps the caller an administrator, drops photo references to files that are not present, and refreshes best-price results. Restores the previous database if anything fails. `400` with a message for an invalid file |
| `GET /api/v1/admin/backup/safety-copies/{name}` | Download one of those safety copies |

### Receipt Drafts

A receipt the model could not read (`EXTRACTION_FAILED`) can be typed in by hand: `POST /api/v1/receipt-drafts/{id}/manual` moves it to `NEEDS_REVIEW` with an empty header and no lines (`409` for any other status). Fill it in with the usual `PATCH /receipt-drafts/{id}` and `PUT /receipt-drafts/{id}/items`. Lines can also be added, edited and removed on any draft in `NEEDS_REVIEW`.

#### POST /api/v1/receipt-drafts
Create a new receipt draft.

**Request Body:**
```json
{
  "source": "camera"
}
```

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "user_id": "user_default_001",
  "status": "PROCESSING",
  "source": "camera",
  "created_at": "2026-01-01T00:00:00",
  "expires_at": "2026-01-04T00:00:00",
  "items": [],
  "taxes": [],
  "images": []
}
```

#### GET /api/v1/receipt-drafts
List all drafts for the current user.

**Query Parameters:**
- `status` (optional): Filter by status (PROCESSING, NEEDS_REVIEW, APPROVED, REJECTED)

**Response:** `200 OK`
```json
[
  {
    "id": "uuid",
    "status": "NEEDS_REVIEW",
    ...
  }
]
```

#### GET /api/v1/receipt-drafts/{id}
Get a specific draft by ID.

**Response:** `200 OK`
```json
{
  "id": "uuid",
  "user_id": "user_default_001",
  "status": "NEEDS_REVIEW",
  "source": "camera",
  "raw_ocr_text": "...",
  "raw_llm_output": "{...}",
  "created_at": "...",
  "expires_at": "...",
  "items": [...],
  "taxes": [...],
  "images": [...]
}
```

**Response:** `404 Not Found`
```json
{
  "detail": "Draft not found"
}
```

#### PATCH /api/v1/receipt-drafts/{id}
Correct the receipt header before approval. Only the fields sent are changed; `null` or an empty string clears a field. Header values are stored in the draft's extraction JSON (`raw_llm_output`) and copied onto the receipt at approval; the names of edited fields are recorded in `user_edited_fields`.

**Request Body (all optional):**
```json
{
  "store_name": "Walmart",
  "store_address": "123 Main St, Springfield, IL 62701",
  "store_number": "412",
  "receipt_number": "00481",
  "purchase_date": "2026-01-01",
  "purchase_time": "14:05",
  "currency": "USD",
  "subtotal": 50.00,
  "discount_total": null,
  "tax_total": 4.25,
  "fee_total": null,
  "grand_total": 54.25
}
```
Returns `422` for an unparseable date, time, currency, or amount.

#### PUT /api/v1/receipt-drafts/{id}/items
Replace every item on the draft. Used by the review screen to save its edited list. Each item requires `receipt_description`; the other fields (`item_type`, `quantity`, `weight_value`, `weight_unit`, `unit_price`, `unit_price_unit`, `line_subtotal`, `discount_amount`, `line_total`, `tax_amount`, `extraction_confidence`) are optional. Returns the full draft.

#### PATCH /api/v1/receipt-drafts/{id}/purchase-date
Correct the date of a receipt that is already saved (`APPROVED`).

**Request Body:** `{"purchase_date": "YYYY-MM-DD"}`

Updates the receipt, the price observations and tax observations recorded from it (so spending, price
trends and best prices follow the new date), and the review copy, and refreshes the home's best-price
results. `400` for an invalid, pre-2000 or future date; `409` if the receipt is not saved yet.

#### POST /api/v1/receipt-drafts/{id}/images
Add a photo or a PDF to a draft (`409` once the receipt is saved).

**Content-Type:** `multipart/form-data`

**Form Data:**
- `file`: a photo (JPEG, PNG, GIF, WebP, max 10 MB) or a PDF (max 20 MB). PDFs are recognised by their signature as well as their content type.

A PDF is converted on the server with poppler: each page becomes a JPEG (longest side 2200 px) stored on the draft, and the PDF itself is not kept. A receipt holds at most 8 pages in total; pages beyond that are left out and `truncated` is true. `400` if the file is not a photo or PDF, is too large, is password protected, cannot be read, or the receipt already has 8 pages.

**Response:** `200 OK`: the first stored image's fields, plus all of them
```json
{
  "id": "uuid",
  "image_type": "original",
  "mime_type": "image/jpeg",
  "file_size": 123456,
  "width": 943,
  "height": 2200,
  "created_at": "...",
  "images": [ { "id": "uuid", "...": "one entry per stored image" } ],
  "converted_from_pdf": true,
  "pdf_pages": 3,
  "truncated": false
}
```

Photos are only kept while a receipt is being reviewed. They are deleted when the receipt is approved, rejected or deleted, and when an unsaved receipt expires (`DRAFT_RETENTION_HOURS`). An approved receipt keeps its details, not its photos.

#### POST /api/v1/receipt-drafts/{id}/items
Add an item to a draft.

**Request Body:**
```json
{
  "receipt_description": "Milk 2% gallon",
  "item_type": "UNIT",
  "quantity": 1,
  "line_total": 4.99
}
```

#### POST /api/v1/receipt-drafts/{id}/taxes
Add a tax record to a draft.

**Request Body:**
```json
{
  "tax_type": "SALES",
  "tax_name": "State Sales Tax",
  "tax_rate": 0.0625,
  "taxable_amount": 80.00,
  "tax_amount": 5.00
}
```

#### POST /api/v1/receipt-drafts/{id}/approve
Approve a draft. This is the only path that creates permanent records, all in one transaction:

- the receipt (with its `home_id`), items and tax lines
- the **store**: chain matched on normalized name ("KROGER #412" and "Kroger" are one chain), then location by store number, then by street address
- **items**: matched within the home by normalized printed description; an unmatched description creates a new item named exactly as printed (never renamed or merged automatically)
- one **price observation** per priced line (coupons, unreadable and unpriced lines are skipped; a per-unit price is worked out from the line when none is printed)
- **store tax observations** for tax lines with a rate or amount

Requires items, a store name and a purchase date (`422` otherwise). Returns the draft plus:
```json
{
  "approval": {
    "receipt_id": "uuid", "store_name": "Kroger", "store_location_id": "uuid",
    "price_observations": 5, "items_matched": 3, "items_created": 2, "tax_observations": 1
  }
}
```

#### POST /api/v1/receipt-drafts/{id}/reject
Reject a draft (discard all data).

**Request Body:**
```json
{
  "reason": "Invalid receipt"
}
```

**Response:** `200 OK`
```json
{
  "status": "rejected",
  "draft_id": "uuid"
}
```

#### DELETE /api/v1/receipt-drafts/{id}
Delete a draft.

**Response:** `200 OK`
```json
{
  "status": "deleted",
  "draft_id": "uuid"
}
```

---

### Images

#### GET /api/v1/receipt-drafts/{draft_id}/images/{image_id}
Get an image file.

**Response:** `200 OK`
- Content-Type: `image/jpeg` (or appropriate type)
- Binary image data

#### DELETE /api/v1/receipt-drafts/{draft_id}/images/{image_id}
Delete an image from a draft.

**Response:** `200 OK`
```json
{
  "status": "deleted",
  "image_id": "uuid"
}
```

---

## Extraction

#### POST /api/v1/extraction/extract
Start extraction for a draft (all of its images are read together, in upload order). Returns immediately. Allowed while the draft is `PROCESSING`, `NEEDS_REVIEW` (re-read; replaces edits) or `EXTRACTION_FAILED`. `409` if an extraction is already running.

Optional `correction` (string, up to 1000 characters): a note from the reviewer that is appended to the standard prompt for this read (see [LLM_EXTRACTION.md](LLM_EXTRACTION.md)). It is stored in the extraction's `meta.correction`. If a re-read of a draft that already had a result fails, the earlier result is kept and the reason is returned as `error` by the status endpoint.

#### GET /api/v1/extraction/status/{draft_id}
Progress for polling: `status`, `is_extracting`, `active` (running in this server process), `elapsed_seconds`, `last_event`, and `error` when the draft is `EXTRACTION_FAILED` (or when the last re-read failed and the earlier result was kept).

#### GET /api/v1/extraction/stream/{draft_id}
Server-Sent Events progress. Observes the running extraction; it never starts a second one.

Draft statuses: `PROCESSING`, `NEEDS_REVIEW`, `EXTRACTION_FAILED`, `APPROVED`, `REJECTED`.

---

## Frontend Routes

The pages `analysis.html` (Insights) and `stores.html` (Stores) sit alongside `index.html`, `capture.html` and `review.html`.


All pages use relative URLs so they work under a Home Assistant ingress prefix.

- `/` (or `/index.html`): receipts list
- `/capture.html`: scan a receipt (camera, or photo picker when the live camera is unavailable)
- `/review.html?id={draft_id}`: review, edit and approve a draft. `/draft/{draft_id}` redirects here.
- `/api/info`: API information

---

## Error Responses

### 400 Bad Request
```json
{
  "detail": "Error message"
}
```

### 404 Not Found
```json
{
  "detail": "Resource not found"
}
```

### 403 Forbidden
```json
{
  "detail": "Not authorized"
}
```

### 500 Internal Server Error
```json
{
  "status": "error",
  "message": "Internal server error"
}
```

---

## Status Codes

| Status | Description |
|--------|-------------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request |
| 403 | Forbidden |
| 404 | Not Found |
| 500 | Internal Server Error |

---

## Data Models

### ReceiptDraft
```json
{
  "id": "string (uuid)",
  "user_id": "string",
  "status": "PROCESSING | NEEDS_REVIEW | APPROVED | REJECTED",
  "source": "string",
  "raw_ocr_text": "string | null",
  "raw_llm_output": "string | null",
  "extraction_model": "string | null",
  "created_at": "datetime",
  "expires_at": "datetime | null"
}
```

### DraftItem
```json
{
  "id": "string",
  "receipt_description": "string",
  "suggested_common_item_id": "string | null",
  "item_type": "UNIT | WEIGHT | QUANTITY | UNKNOWN",
  "quantity": "number | null",
  "weight_value": "number | null",
  "weight_unit": "string | null",
  "unit_price": "number | null",
  "line_total": "number",
  "extraction_confidence": "number | null"
}
```

### Image
```json
{
  "id": "string",
  "image_type": "original | processed",
  "file_path": "string",
  "mime_type": "string",
  "file_size": "number",
  "width": "number",
  "height": "number",
  "created_at": "datetime"
}
```
