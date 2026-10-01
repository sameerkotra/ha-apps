"""The built-in catalogue of house upkeep suggestions (Maintenance), the home-profile features
that filter it, the categories, and the seasons.

Pure data and date maths — no database, no I/O. Admins can add their own suggestions (table
maint_suggestions); those use the same fields. A suggestion repeats either every N days/weeks/months/years
after it was last done (`every`), or on fixed seasons (`seasons`: the job is due at the start of each).
"""
from datetime import date

CATEGORIES = {
    "hvac": "Heating & cooling",
    "plumbing": "Plumbing",
    "electrical": "Electrical",
    "safety": "Safety",
    "exterior": "Exterior",
    "appliances": "Appliances",
    "yard": "Yard",
    "interior": "Interior",
    "other": "Other",
}

# Home profile: what the house has (Admin → Maintenance). A suggestion that needs a feature shows only when
# it's ticked; one without `needs` always shows.
FEATURES = {
    "furnace": "Forced-air furnace",
    "central_ac": "Central air conditioning",
    "heat_pump": "Heat pump",
    "gas": "Gas appliances",
    "fireplace": "Fireplace or wood stove",
    "water_heater_tank": "Tank water heater",
    "water_heater_tankless": "Tankless water heater",
    "softener": "Water softener",
    "sump_pump": "Sump pump",
    "septic": "Septic system",
    "well": "Well",
    "sprinklers": "Sprinkler system",
    "gutters": "Gutters",
    "deck": "Deck",
    "pool": "Pool or hot tub",
    "garage_door": "Garage door opener",
    "dryer": "Clothes dryer",
    "dishwasher": "Dishwasher",
    "fridge_filter": "Fridge with a water filter",
    "range_hood": "Range hood",
    "alarms": "Smoke / CO alarms",
}

SEASONS = ("spring", "summer", "autumn", "winter")
SEASON_LABEL = {"spring": "Spring", "summer": "Summer", "autumn": "Autumn", "winter": "Winter"}
# first month of each season, northern hemisphere (meteorological seasons); the south is 6 months later
_START_NORTH = {"spring": 3, "summer": 6, "autumn": 9, "winter": 12}
UNITS = {"day": (1, 365), "week": (1, 52), "month": (1, 36), "year": (1, 10)}


def _s(key, name, icon, category, why, steps, *, needs=None, every=None, seasons=None, who="diy", minutes=15):
    return {"key": key, "name": name, "icon": icon, "category": category, "needs": needs,
            "every": every, "seasons": list(seasons) if seasons else None, "why": why, "steps": steps,
            "who": who, "minutes": minutes}


BUILTIN = [
    # ---- safety ----
    _s("test_alarms", "Test smoke and CO alarms", "🚨", "safety",
       "A dead alarm gives no warning; testing takes seconds.",
       ["Press and hold each alarm's test button until it sounds.", "Replace any that stay silent or are over 10 years old."],
       needs="alarms", every=(1, "month"), minutes=10),
    _s("alarm_batteries", "Replace alarm batteries", "🔋", "safety",
       "Low batteries cause failures and the 3 a.m. chirp.",
       ["Replace the batteries in every smoke and CO alarm.", "Test each one afterwards."],
       needs="alarms", every=(1, "year"), minutes=30),
    _s("fire_extinguisher", "Check fire extinguishers", "🧯", "safety",
       "An extinguisher that has lost pressure won't work when needed.",
       ["Check the gauge is in the green.", "Make sure the pin and seal are intact and it's easy to reach."],
       every=(1, "year"), minutes=10),
    _s("dryer_vent", "Clean the dryer vent", "🌀", "safety",
       "Lint build-up in the duct is a leading cause of house fires and slows drying.",
       ["Unplug the dryer and pull it out.", "Disconnect the duct and brush or vacuum it through to the outside flap."],
       needs="dryer", every=(1, "year"), minutes=45),
    _s("gfci_test", "Test GFCI outlets", "🔌", "electrical",
       "Ground-fault outlets protect against shocks in kitchens, bathrooms and outside.",
       ["Press TEST — the power should cut off.", "Press RESET. Replace any that don't trip."],
       every=(6, "month"), minutes=15),
    _s("chimney", "Chimney inspection and sweep", "🔥", "safety",
       "Creosote build-up can cause chimney fires.",
       ["Book a certified chimney sweep before the heating season."],
       needs="fireplace", seasons=("autumn",), who="pro", minutes=60),
    _s("gas_check", "Gas appliance safety check", "🔥", "safety",
       "Burners and flues that aren't working right can leak carbon monoxide.",
       ["Have a qualified technician check the gas appliances and flues."],
       needs="gas", every=(1, "year"), who="pro", minutes=60),
    # ---- heating & cooling ----
    _s("furnace_filter", "Replace the furnace filter", "🌬️", "hvac",
       "A clogged filter makes the system work harder and lets dust through.",
       ["Note the filter size printed on the old one.", "Slide in the new filter with the airflow arrow toward the furnace."],
       needs="furnace", every=(3, "month"), minutes=10),
    _s("furnace_service", "Service the furnace", "🛠️", "hvac",
       "A yearly tune-up catches problems before the first cold night.",
       ["Book a technician to clean and check the furnace before winter."],
       needs="furnace", seasons=("autumn",), who="pro", minutes=90),
    _s("ac_service", "Service the air conditioner", "❄️", "hvac",
       "Clean coils and the right refrigerant level keep it cool and cheap to run.",
       ["Book a service before the hot weather, or clear leaves from the outdoor unit and rinse its fins."],
       needs="central_ac", seasons=("spring",), who="pro", minutes=90),
    _s("heat_pump_service", "Service the heat pump", "♨️", "hvac",
       "A heat pump runs all year; a yearly check keeps it efficient.",
       ["Book a technician, and keep the outdoor unit clear of leaves and snow."],
       needs="heat_pump", every=(1, "year"), who="pro", minutes=90),
    _s("ac_condensate", "Flush the AC condensate drain", "💧", "hvac",
       "A blocked drain line can overflow and damage ceilings.",
       ["Pour a cup of white vinegar into the drain line access."],
       needs="central_ac", seasons=("summer",), minutes=15),
    _s("vents", "Vacuum vents and returns", "🧹", "hvac",
       "Dusty vents spread dust and restrict airflow.",
       ["Vacuum supply vents and return grilles; wash removable covers."],
       every=(6, "month"), minutes=30),
    # ---- plumbing ----
    _s("water_heater_flush", "Flush the water heater", "🚿", "plumbing",
       "Sediment shortens the tank's life and wastes energy.",
       ["Turn off the power or gas, attach a hose to the drain valve and drain a few buckets until clear."],
       needs="water_heater_tank", every=(1, "year"), minutes=60),
    _s("anode_rod", "Check the water heater anode rod", "🔩", "plumbing",
       "The anode rod stops the tank rusting from inside.",
       ["Check the rod; replace it if it's worn to the core wire."],
       needs="water_heater_tank", every=(3, "year"), minutes=60),
    _s("tankless_descale", "Descale the tankless water heater", "🚿", "plumbing",
       "Scale reduces flow and can damage the heat exchanger.",
       ["Flush it with vinegar using the service valves, following the manual."],
       needs="water_heater_tankless", every=(1, "year"), minutes=60),
    _s("softener_salt", "Top up water softener salt", "🧂", "plumbing",
       "Without salt the softener stops working.",
       ["Keep the salt level at least a quarter full; break up any crust."],
       needs="softener", every=(1, "month"), minutes=10),
    _s("sump_pump", "Test the sump pump", "🌧️", "plumbing",
       "A pump that fails in a storm means a flooded basement.",
       ["Pour a bucket of water into the pit and check the pump starts and drains it."],
       needs="sump_pump", seasons=("spring", "autumn"), minutes=15),
    _s("septic_pump", "Pump the septic tank", "🚽", "plumbing",
       "Regular pumping prevents backups and costly field repairs.",
       ["Book a septic service; ask them to inspect the baffles."],
       needs="septic", every=(3, "year"), who="pro", minutes=120),
    _s("well_test", "Test the well water", "🧪", "plumbing",
       "Well water can pick up bacteria and nitrates without any change in taste.",
       ["Send a sample to a certified lab (bacteria, nitrates, and anything local)."],
       needs="well", every=(1, "year"), minutes=30),
    _s("leak_check", "Check for leaks under sinks", "🔧", "plumbing",
       "Small drips rot cabinets and floors unnoticed.",
       ["Look under every sink and around the toilets and water heater for damp or stains."],
       every=(6, "month"), minutes=20),
    _s("washer_hoses", "Inspect washing machine hoses", "🧺", "plumbing",
       "Burst hoses are a common cause of floods.",
       ["Check for bulges and cracks; replace rubber hoses every 5 years with braided ones."],
       every=(1, "year"), minutes=15),
    _s("drains", "Clean slow drains", "🕳️", "plumbing",
       "Hair and grease build up gradually.",
       ["Clear sink and shower drains with a drain snake; clean the pop-up stoppers."],
       every=(3, "month"), minutes=20),
    _s("main_valve", "Exercise the main water shut-off", "🚰", "plumbing",
       "A valve that hasn't moved in years may seize when you need it.",
       ["Turn the main shut-off fully closed and open again; make sure everyone knows where it is."],
       every=(1, "year"), minutes=10),
    # ---- appliances ----
    _s("range_hood_filter", "Clean the range hood filter", "🍳", "appliances",
       "A greasy filter is a fire risk and stops pulling steam out.",
       ["Soak the metal filter in hot water with degreaser, or put it in the dishwasher."],
       needs="range_hood", every=(3, "month"), minutes=15),
    _s("fridge_filter", "Replace the fridge water filter", "🧊", "appliances",
       "Old filters stop filtering and slow the flow.",
       ["Replace the filter, then run a few litres through to flush it."],
       needs="fridge_filter", every=(6, "month"), minutes=10),
    _s("fridge_coils", "Vacuum the fridge coils", "🧊", "appliances",
       "Dusty coils make the fridge work harder.",
       ["Unplug the fridge and vacuum the coils underneath or behind it."],
       every=(1, "year"), minutes=20),
    _s("dishwasher_filter", "Clean the dishwasher filter", "🍽️", "appliances",
       "A dirty filter leaves grit on dishes and smells.",
       ["Twist out the filter at the bottom, rinse it and scrub it with a soft brush."],
       needs="dishwasher", every=(1, "month"), minutes=10),
    _s("washer_clean", "Clean the washing machine", "🧺", "appliances",
       "Residue and mould build up in the drum and seals.",
       ["Run an empty hot cycle with washer cleaner or vinegar; wipe the door seal."],
       every=(1, "month"), minutes=10),
    _s("garage_door", "Lubricate and test the garage door", "🚗", "exterior",
       "Keeps it quiet and makes sure the auto-reverse works.",
       ["Lubricate rollers and hinges.", "Place a board under the door: it should reverse when it touches it."],
       needs="garage_door", every=(6, "month"), minutes=20),
    # ---- exterior ----
    _s("gutters", "Clean the gutters", "🍂", "exterior",
       "Blocked gutters overflow into walls and foundations.",
       ["Scoop out leaves, then flush the downspouts with a hose."],
       needs="gutters", seasons=("spring", "autumn"), minutes=120),
    _s("roof_check", "Check the roof", "🏠", "exterior",
       "Catching a missing shingle early avoids leaks.",
       ["From the ground (binoculars help), look for missing or lifted shingles and damaged flashing."],
       seasons=("spring",), minutes=30),
    _s("caulking", "Check caulking and weatherstripping", "🪟", "exterior",
       "Gaps let in water and drafts.",
       ["Check around windows, doors and tubs; replace cracked caulk and worn strips."],
       seasons=("autumn",), minutes=60),
    _s("outdoor_taps", "Winterize outdoor taps", "🚰", "exterior",
       "Water left in outdoor pipes freezes and bursts them.",
       ["Disconnect hoses, shut the indoor valve and drain the outside tap."],
       seasons=("autumn",), minutes=20),
    _s("deck", "Clean and seal the deck", "🪵", "exterior",
       "Sealing keeps wood from rotting and splintering.",
       ["Wash the deck; when dry, check for loose boards and apply sealer if water no longer beads."],
       needs="deck", seasons=("summer",), minutes=240),
    _s("foundation", "Check the foundation and drainage", "🧱", "exterior",
       "Water pooling by the house finds its way inside.",
       ["Look for new cracks; make sure soil slopes away and downspouts discharge away from the walls."],
       seasons=("spring",), minutes=30),
    _s("windows", "Wash windows and screens", "🪟", "exterior",
       "Clean screens also let more air through.",
       ["Wash windows inside and out; repair torn screens."],
       seasons=("spring",), minutes=120),
    # ---- yard ----
    _s("sprinklers_start", "Start up and check the sprinklers", "💦", "yard",
       "Broken heads waste water all summer.",
       ["Turn the water on slowly, run each zone and fix broken or misaligned heads."],
       needs="sprinklers", seasons=("spring",), minutes=60),
    _s("sprinklers_winterize", "Winterize the sprinklers", "💦", "yard",
       "Water left in the lines freezes and cracks them.",
       ["Shut off the water and blow out the lines (or book a service)."],
       needs="sprinklers", seasons=("autumn",), minutes=60),
    _s("mower", "Service the lawn mower", "🌱", "yard",
       "A sharp blade and fresh oil make mowing easier.",
       ["Change the oil, sharpen the blade and replace the air filter and spark plug."],
       seasons=("spring",), minutes=60),
    _s("trees", "Trim trees and shrubs near the house", "🌳", "yard",
       "Branches touching the roof let in water and pests.",
       ["Cut back branches touching the house, roof or power lines (call a pro near lines)."],
       seasons=("winter",), minutes=120),
    _s("pool_check", "Check pool or hot tub equipment", "🏊", "yard",
       "Clean filters and balanced water protect the pump and the swimmers.",
       ["Clean the filter, check the pump and heater, and test the water balance."],
       needs="pool", every=(1, "month"), minutes=30),
    # ---- interior ----
    _s("grout", "Reseal grout and bath caulk", "🛁", "interior",
       "Sealed grout keeps water out of the walls and mould away.",
       ["Clean the grout and apply sealer; replace any mouldy caulk."],
       every=(1, "year"), minutes=90),
    _s("pest_check", "Check for pests", "🐜", "interior",
       "Early signs are easier to deal with.",
       ["Look in the attic, basement and under sinks for droppings, damage or damp."],
       every=(6, "month"), minutes=20),
    _s("emergency_kit", "Check the emergency kit", "🎒", "safety",
       "Batteries, water and food have expiry dates.",
       ["Check torches, batteries, water, first aid and food; replace what's expired."],
       every=(1, "year"), minutes=30),
]
BY_KEY = {s["key"]: s for s in BUILTIN}


# ---------- seasons ----------
def is_south(latitude) -> bool:
    try:
        return latitude is not None and float(latitude) < 0
    except (TypeError, ValueError):
        return False


def season_start_month(season: str, south: bool) -> int:
    m = _START_NORTH[season]
    return (m + 6 - 1) % 12 + 1 if south else m


def season_of(d: date, south: bool) -> str:
    for s in SEASONS:
        start = season_start_month(s, south)
        if (d.month - start) % 12 < 3:
            return s
    return "winter"


def season_start(season: str, d: date, south: bool) -> date:
    """The first day of the `season` that contains `d`, or of the next one if `d` isn't in it."""
    m = season_start_month(season, south)
    y = d.year
    if (d.month - m) % 12 < 3:          # inside it: its start may be in the previous year
        return date(y if d.month >= m else y - 1, m, 1)
    return date(y if m > d.month else y + 1, m, 1)


def next_season_start(season: str, d: date, south: bool) -> date:
    """The next first day of `season` on or after `d`."""
    m = season_start_month(season, south)
    first = date(d.year, m, 1)
    return first if first >= d else date(d.year + 1, m, 1)


def season_key(d: date, south: bool) -> str:
    """'2026-autumn': the season `d` is in, labelled by the year it started."""
    s = season_of(d, south)
    return f"{season_start(s, d, south).year}-{s}"


def in_season_soon(seasons, d: date, south: bool, ahead_days: int = 31) -> bool:
    """In one of `seasons` now, or one starts within `ahead_days` (a month's notice)."""
    if not seasons:
        return False
    cur = season_of(d, south)
    if cur in seasons:
        return True
    return any(0 <= (next_season_start(s, d, south) - d).days <= ahead_days for s in seasons)


def calendar_rule_for(seasons, today: date, south: bool) -> tuple[str, str]:
    """(rule, anchor) for a seasonal suggestion: due on the first day of each of its seasons.
    One season → yearly; two six months apart → every 6 months; otherwise every 3 months from the next."""
    starts = sorted(next_season_start(s, today, south) for s in seasons)
    anchor = starts[0]
    if len(seasons) == 1:
        return "years:1", anchor.isoformat()
    months = {season_start_month(s, south) for s in seasons}
    if len(months) == 2 and abs(sorted(months)[1] - sorted(months)[0]) == 6:
        return "months:6:1", anchor.isoformat()
    return "months:3:1", anchor.isoformat()


def every_label(n: int, unit: str) -> str:
    if n == 1:
        return {"day": "Every day", "week": "Every week", "month": "Every month", "year": "Every year"}[unit]
    return f"Every {n} {unit}s"


def repeat_label(s: dict) -> str:
    if s.get("seasons"):
        names = [SEASON_LABEL[x] for x in SEASONS if x in s["seasons"]]
        return ("Every " + " and ".join(n.lower() for n in names)) if names else ""
    n, unit = s["every"]
    return every_label(n, unit) + " after last done"
