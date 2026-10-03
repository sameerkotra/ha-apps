# SQLite Database

Use SQLite as the single application database.

## Core settings

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
```

## Draft tables

```sql
CREATE TABLE receipt_drafts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROCESSING',
    source TEXT NOT NULL,
    raw_ocr_text TEXT,
    raw_llm_output TEXT,
    extraction_model TEXT,
    extraction_prompt_version TEXT,
    extraction_schema_version TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT
);

CREATE TABLE receipt_draft_items (
    id TEXT PRIMARY KEY,
    receipt_draft_id TEXT NOT NULL,
    receipt_description TEXT NOT NULL,
    suggested_common_item_id TEXT,
    suggested_common_item_name TEXT,
    item_type TEXT,
    quantity REAL,
    weight_value REAL,
    weight_unit TEXT,
    unit_price REAL,
    unit_price_unit TEXT,
    line_subtotal REAL,
    discount_amount REAL,
    line_total REAL,
    tax_amount REAL,
    extraction_confidence REAL,
    normalization_confidence REAL,
    raw_text TEXT,
    FOREIGN KEY(receipt_draft_id) REFERENCES receipt_drafts(id) ON DELETE CASCADE
);

CREATE TABLE receipt_draft_taxes (
    id TEXT PRIMARY KEY,
    receipt_draft_id TEXT NOT NULL,
    tax_type TEXT,
    tax_name TEXT,
    tax_rate REAL,
    taxable_amount REAL,
    tax_amount REAL,
    FOREIGN KEY(receipt_draft_id) REFERENCES receipt_drafts(id) ON DELETE CASCADE
);
```

## Permanent tables

The permanent tables (receipts, items, stores, price observations, homes and so on) are defined by the ORM models in `app/db/models.py`. The tables are created from those models at start-up, and columns added in later versions are added to existing databases automatically.

## Data lifecycle

```text
Draft:
  temporary
  editable
  expires
  no analytics

Approved:
  immutable historical receipt facts
  normalized references
  analytics
  recommendation evidence
```

## Deletion

Prefer:
- `deleted_at`
- `deleted_by`
- status flags

over hard-deleting financial history.

## Backup

The app should support a safe backup mechanism that:
1. flushes writes.
2. uses SQLite backup API or a safe online copy.
3. includes image storage if configured.


## Homes

- `homes(id, name, created_by_user_id, created_at, updated_at)`
- `home_id` added to `receipt_drafts`, `receipts`, `common_items` and `price_observations`.
  Stores (`store_chains`, `store_locations`, `addresses`) are shared across homes; items and
  price history are per home.
- Existing databases are upgraded at startup (`ensure_columns` in `app/db/models.py`), and
  rows without a home are attached to a home called "Home".
- Approving a receipt writes `store_chains`/`store_locations`/`addresses`, `common_items` +
  `common_item_aliases` (source `EXTRACTION`), `price_observations`, and
  `store_tax_observations`.

## Stores and common names

- `store_chain_aliases(id, store_chain_id, normalized_alias UNIQUE)`: names a chain used to have.
  Renaming or merging a chain records the old name here, and receipts are matched against
  both `store_chains.normalized_name` and this table, so an old printed name never creates a
  duplicate chain.
- `common_items.name_confirmed` (0/1): 1 once a person has chosen or confirmed the name. Items
  created automatically carry their printed text and stay 0. Only confirmed names are
  pre-filled on later receipts.
- Merged common items keep their row with `status = 'MERGED'` and `merged_into_id`; their
  aliases, receipt lines and price observations move to the target.
- New indexes on `(home_id, purchase_date)` for receipts and price observations, which the
  analysis queries filter by.
- Recommendations are computed on request and not stored (the `recommendations` table is unused).

## Daily best-price check

- `recommendation_snapshots(home_id PK, generated_at, duration_ms, status, error, payload)`: one row
  per home holding the latest run as JSON, replaced each run. A failed run keeps the previous
  `payload` and sets `status = 'FAILED'` with the error. Deleted with its home.

## Draft to receipt link

- `receipt_drafts.approved_receipt_id`: set when a draft is approved. Used to find the permanent receipt when its date is corrected. Receipts saved before this column existed are matched by their copy of the original reading the first time they are edited.

## Export and import

The whole database is one SQLite file (`/data/receipt_price_intelligence.db`; WAL mode while running). Export writes a consistent single-file snapshot with SQLite's online backup API; import validates an upload, replaces the file, then runs `init_models()` so older exports gain any newer tables and columns. `price_observations.line_total` is derived from the unit price and quantity or weight when a line has no printed total; rows saved without one are repaired when read.

## Trip planner

- `homes.address`, `homes.latitude`, `homes.longitude`: where trips start and end.
- `vehicles(home_id, name, kind, consumption, energy_price, other_cost_per_km, range_km, is_default)`: stored in metric (`consumption` is L/100 km for fuel cars, kWh/100 km for electric; `energy_price` per litre or per kWh). Deleted with their home.
- Store coordinates are cached in the existing `addresses.latitude/longitude/geocoding_confidence` (`NULL` = not looked up yet, `0` = looked up and not found). Editing a store's address clears them.

## Alerts, targets and opening hours

- `alerts(home_id, kind, dedupe_key, title, message, data, dismissed)`: each event once per home (`UNIQUE(home_id, dedupe_key)`).
- `price_targets(home_id, common_item_id, mode, target_price, unit, active, last_triggered_at)`.
- `store_locations.opening_hours`: JSON weekly hours used by the trip planner.
- Reopening a saved receipt keeps `receipt_drafts.approved_receipt_id` set; approving the edited draft deletes the old receipt records and writes new ones in one transaction. Drafts with that link never expire.

## Budgets, corrections and tags

- `budgets(home_id, category, monthly_amount)`: `category` NULL means all spending.
- `line_corrections(store_chain_id, original_key, corrected_text, times)`: `UNIQUE(store_chain_id, original_key)`; applied when a receipt from that chain is read.
- `receipts.tags`: JSON list. `receipt_draft_items.original_description`: the line as first read, kept while it is edited so the fix can be learned. Item categories use the existing `common_items.category`.

## Web lookups

- `web_hours_suggestions(store_location_id PK, hours JSON, source_url, confidence, note, status, fetched_at)`: opening hours found online; `status` is `SUGGESTED`, `APPLIED` or `DISMISSED`. Applying copies `hours` to `store_locations.opening_hours`.
- `web_deals(home_id, store_chain_id, common_item_id, kind, product, price, unit, valid_from, valid_to, conditions, source_url, method, fetched_at)`: what was found for items you buy. `kind` is `DEAL` (a promotion) or `PRICE` (the current shelf price on the store's website); `method` is how it was read (`structured`, `text` or `model`). Every `PRICE` check is kept for 30 days; only the latest per item and chain is used, and only while it is under 7 days old. Source addresses are only ever `http(s)`. Deals that have ended (or, without an end date, are 21 days old) are removed. A chain's deals are replaced each time it is looked up.

## Ignored online prices

- `online_price_ignores(home_id, common_item_id, store_chain_id)`: online prices a home chose not to use; that item at that store uses its receipt price, and price checks skip it.

## Store discounts

- `store_discounts(home_id, store_chain_id, name, percent, cents_per_unit, applies_to, active)`: what a home saves at a store chain (cards, memberships). `applies_to` is `ALL`, `FUEL` or `NON_FUEL`; `cents_per_unit` is off per gallon (or litre) of fuel.

## Shopping list

- `shopping_list_items(home_id, common_item_id, text, qty, checked, ha_uid, ha_status, added_at, checked_at)`: the home's shopping list. `common_item_id` links an item to a known item (so its cheapest store can be worked out); `ha_uid` is the paired item of the Home Assistant to-do list, and `ha_status` its status at the last sync (which tells which side changed an item since).

## Store web pages

- `store_chains.website`, `store_chains.search_url` (with `{query}`), `store_chains.deals_url` and `store_locations.page_url`: pages online lookups open directly before searching. Added to existing databases at startup through `_ADDED_COLUMNS`.

## Stores near home

- `nearby_stores(home_id, osm_id, name, brand, shop, latitude, longitude, distance_km, street, city, state, postal_code, raw_address, opening_hours_raw, opening_hours, website, store_chain_id, matched_location_id, store_location_id, status, fetched_at)`, unique on `(home_id, osm_id)`: grocery stores OpenStreetMap lists near the home. `status` is `NEW`, `ADDED` (the person added it: `store_location_id` is the store location created or reused for it), `KNOWN` (it is one of the home's stores: `matched_location_id`) or `HIDDEN`. `opening_hours` is the app's JSON format when OpenStreetMap's text could be read with certainty, else NULL (`opening_hours_raw` keeps the original). A new search updates the rows it finds, and deletes `NEW` and `KNOWN` rows it no longer finds; `ADDED` and `HIDDEN` rows are kept.
- A home's stores (`trips.home_locations`) are the locations it has receipts from plus the `store_location_id` of its `ADDED` rows.

## Indexes

`ensure_indexes` (in `app/db/models.py`, run at start-up) creates the indexes in `_INDEXES` that are missing, for the
lookups made most: receipts and price records by home and date, items and aliases by home, online prices by item and
store, store locations by chain, and so on. Each is `CREATE INDEX IF NOT EXISTS`, so older databases get them too.

## How the schema is created

There are no migration scripts. At startup, `Base.metadata.create_all` creates any table that does not exist yet
from the ORM models in `app/db/models.py`, and `ensure_columns` adds the columns listed in `_ADDED_COLUMNS` that an
older database is missing. A change that needs more than a new table or a new column needs code in `ensure_columns`.

## `extraction_runs`: a foreign key with no delete action

`extraction_runs.receipt_id` and `.receipt_draft_id` reference `receipts`/`receipt_drafts` with no
`ON DELETE` action (SQLite's default, equivalent to `RESTRICT`). Nothing in the app currently
writes to this table, but a database that has old rows there — carried over from an earlier version,
or restored from a backup — would fail to delete a receipt or draft with `FOREIGN KEY constraint
failed`, because deleting the parent row while a child row still references it violates the
constraint. `app/services/drafts.py`'s `_detach_extraction_runs()` sets the reference to `NULL`
before every delete of a `Receipt` or `ReceiptDraft` row, which satisfies the constraint regardless
of what is on disk. The ORM model now declares `ondelete="SET NULL"` for a fresh installation's
schema, but SQLite cannot alter an existing table's foreign key definition in place, so an existing
database keeps its original constraint forever; the application-level fix is what actually matters.

## Deleting a receipt is one transaction, not several

`app/services/images.py`'s `purge_draft_images()` used to call `db.commit()` (and, on any error,
`db.rollback()` after logging a warning) itself. Every caller that deletes a receipt or draft calls
it as one step among several in a single logical delete, so that internal commit ended the caller's
transaction early, and the internal rollback-and-swallow meant a failure partway through was hidden
from the caller, which then carried on as though everything up to that point had succeeded. It no
longer commits or catches errors: callers that need the photo removal to be atomic with the rest of
their delete (`delete_saved_receipt`, `delete_draft`) simply include it in their own transaction and
commit once at the end; callers where photo cleanup is a genuinely optional extra after the important
part is already saved (`approve_draft`, `reject_draft`) wrap just that call in its own try/except so a
problem there is logged but never reported as the approval or rejection itself having failed.

## How online prices were read

- `web_deals.method`: `structured` (schema.org product data, microdata or price meta tags on the page), `text` (a price printed next to the product name) or `model` (the model read the page because neither found it). Price checks (`kind = PRICE`) are no longer overwritten: every check is kept for 30 days to show them by date; comparisons and the trip planner use only the latest check per item and store, and only if it is at most 7 days old.
