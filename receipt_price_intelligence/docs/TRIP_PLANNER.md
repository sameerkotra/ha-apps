# Trip planner

Chooses where to buy a shopping list and the order to drive there, for the lowest **item prices + driving cost**.

## Inputs

- **Home address**: start and end of the trip. Looked up once on the map (Nominatim).
- **Cars**: fuel cars have an economy (mpg or L/100 km) and a fuel price; electric cars have a consumption (kWh/100 mi or kWh/100 km) and an electricity price. An optional "other cost per distance" (tyres, servicing, depreciation) and an optional range. Values are stored in metric and shown in the configured `unit_system`.
  Running cost per km = `consumption / 100 x energy price + other cost`.
- **Items**: unique items from those with a price on record, each with a quantity (default 1). For an item sold by weight a quantity of 1 means your usual purchase (the average weight you buy, for example 2.5 lb).
- **Options**: whether to return home, and optionally a preference for the number of stores.

## Prices

The latest price you paid for the item at each store location, in the item's usual unit (weights are converted). A location that has not been priced for an item borrows the latest price at another location of the same chain and is marked as an estimate. Prices older than 180 days are marked as old. Prices are before tax.

## Where stores are

Store locations you have bought from in this home are considered, plus the ones added from *Stores near home* on the Stores page. An added store has no receipts, so it only takes part when it has a price for an item: from an online price check (checked against the median of what you pay for the item at any store, and dropped if it is less than 0.4 or more than 2.5 times that), a deal, or another branch of the same chain; one with no price for anything on the list is left out with a warning. Their addresses are looked up once and remembered (`addresses.latitude/longitude`; `geocoding_confidence = 0` means the search found nothing). Changing a store's address on the Stores page clears its coordinates so it is looked up again. Stores with no address, or that cannot be found, are left out and listed in the warnings.

## Distances

OSRM's `table` service gives driving distance and time between home and every candidate store. If it cannot be reached, straight-line distance x 1.3 at 40 km/h is used and the result says so. The route drawn for the best plan comes from OSRM's `route` service (a plain straight-line sketch if unavailable). The map is drawn on the page from those coordinates; no map tiles are downloaded.

## The search

1. Per item and store, cost = unit price x amount x quantity, or unavailable.
2. **The number of stores is decided by the planner.** It tries one store, two, and so on up to five, and the cheapest total (items + driving + time) wins; fewer stores win a tie. Candidate stores are the two cheapest sellers of each item plus the nearest and those that sell the most of the list (at most 14 unless every store is needed).
3. For each combination, each item is bought at the cheapest store in it that sells it. A combination that leaves an item unbuyable, or has a store with nothing to buy, is skipped, and one that cannot beat the best trip found so far on item prices alone is skipped without working out a route.
4. For a combination, every visiting order is tried and the cheapest that fits the opening hours is used.
5. **If the list needs more than five stores** (for example many items each sold at only one store), a heuristic is used: start with the cheapest store for every item, drop stores while that lowers the total, and order the stops with nearest-neighbour improved by 2-opt. A trip is always produced when every item can be bought somewhere.

The result includes the best plan, the best plan for each other number of stores, the best single-store plan (and the saving against it), what you would do if driving were free, and `stops_needed`: the fewest stores that can buy everything.

### Preference for the number of stores

`max_stops` is optional and only a preference. If a trip with at most that many stores exists, the cheapest of those is chosen. If the list needs more, the best trip is still returned, with `exceeds_preference` set and `stops_message` saying so ("Buying everything on your list needs 3 stores, so this trip has 3 (you asked for at most 2)").

### When things don't fit

- **Opening hours rule out every order**: the cheapest trip ignoring hours is returned with `hours_ignored`, the stops that would be closed are marked `closed_on_arrival`, and a warning says when each opens. A store is only reported as "left out because it is closed" when it was the cheapest for something you asked for.
- **Items no known store sells**: left out and listed in `unavailable`.
- **Many stores**: at most 40 are kept before driving distances are asked for (the two cheapest sellers of every item first, then the nearest), so the routing service is never given too many points.
- **Bad numbers** (not a number, infinite, negative) are rejected with a message or limited to a sensible range: time value up to 10,000 an hour, 0 to 120 minutes per store.

## Limits

- Prices come from your receipts, not live shelf prices.
- Sales tax and store opening hours are not considered.
- Drive time is not given a money value.
- The public OpenStreetMap servers are for light use: one address search per second, and the demo routing server may be slow or unavailable. Point `geocoder_url` and `routing_url` at your own servers for heavy use or privacy.
- Addresses and coordinates of your home and stores are sent to those servers; receipts and prices are not.

## Time, opening hours and sale prices

- **Value of your time**: an hourly value counted against drive time plus `stop_minutes` in each store (default 10). Total = items + driving + time.
- **Opening hours**: a store with hours set is only visited when it will be open when you arrive and for the time you spend there. The search tries every visiting order, so a store that is closed if you go straight there can still be used later in the day. A store that cannot fit any order is left out and explained in the warnings. Stores or days without hours count as open. The planner never waits for a store to open.
- **Sale prices**: a price seen with a sale, coupon or member discount is marked `on_sale`; with sale prices on (default) the discounted price is used, otherwise the regular price.
- **Shopping list**: lines of a Home Assistant to-do list are matched to items by their words (`2 eggs`, `milk x2`, `Bananas`), and you confirm the matches before they are added.

### Deals and prices found online

With a search server set up (see the documentation), deals found for your items are used as prices: a deal applies to every location of its chain, only ever lowers a price, is converted to the item's own unit (so a per-lb deal compares with per-lb receipts), and is ignored once it has ended. Where the store had no price of its own for the item, the deal creates one (marked as an estimate). Lines using a deal say so, with the date it ends, and the plan carries a reminder to check the store's own ad. Turn it off with *Use deals found online*.

Current shelf prices found on a store's website work the same way with two differences: a price replaces the one from your last receipt in either direction (so the plan uses today's price, not last month's) when it is newer, and it is ignored if it is less than 0.4 or more than 2.5 times what that chain last charged you (almost certainly a misread size or unit). Such lines say "current price online (you paid ...)".
