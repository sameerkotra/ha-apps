"""Trip planner: home address, cars, and where to shop for a list of items.

Any signed-in user can use and edit these (data is shared within a home). Store and home
addresses are looked up on OpenStreetMap's free services; see ``app/services/geo.py``.
"""

import asyncio

from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.services import ha
from app.api.common import require_home as _home
from app.services import shopping
from app.services import trips, tripcache

from app.logging_config import get_logger

logger = get_logger("api.planner")
router = APIRouter(prefix="/api/v1", tags=["planner"])


class AddressIn(BaseModel):
    address: str | None = Field(None, max_length=500)


class VehicleIn(BaseModel):
    name: str = Field("", max_length=255)
    kind: str = Field("GAS", description="GAS, DIESEL, HYBRID or EV")
    economy: float | str | None = Field(None, description="mpg or L/100 km for fuel cars; kWh/100 mi or kWh/100 km for electric (per the unit system)")
    energy_price: float | str | None = Field(None, description="price per gallon or litre; per kWh for electric")
    other_cost_per_distance: float | str | None = None
    range: float | str | None = None
    is_default: bool = False


class PlanItem(BaseModel):
    item_id: str
    qty: float = 1


class PlanIn(BaseModel):
    items: list[PlanItem]
    vehicle_id: str | None = None
    cost_per_distance: float | str | None = Field(None, description="Used when no car is chosen")
    max_stops: int | None = Field(None, ge=1, le=6, description="Optional preference. The planner works out how many stores it needs, and says so when that is more")
    round_trip: bool = True
    depart_at: str | None = Field(None, description="Local date and time you leave (ISO), default now. Stores with opening hours must be open when you arrive")
    time_cost_per_hour: float | str | None = Field(None, description="What an hour of your time is worth; counts drive and shopping time")
    stop_minutes: float | str | None = Field(10, description="Minutes spent in each store")
    include_sales: bool = Field(True, description="Use sale, coupon and member prices seen on receipts")
    include_web_deals: bool = Field(True, description="Use online prices and deals found for your stores")


class TodoImportIn(BaseModel):
    entity_id: str = Field(..., pattern=r"^todo\.[a-z0-9_]+$")


class LocateIn(BaseModel):
    item_ids: list[str] | None = None


def _bad(e: trips.TripError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/planner/config")
async def planner_config(_: CurrentUser = Depends(get_current_user)):
    """Units and the map services in use (addresses are sent to them)."""
    from app.config import get_settings
    s = get_settings()
    return {**trips.unit_labels(), "geocoder": s.geocoder_url, "routing": s.routing_url, "ha_available": ha.available()}


@router.get("/planner/todo-lists")
async def todo_lists(_: CurrentUser = Depends(get_current_user)):
    """The to-do / shopping lists in Home Assistant (empty when Home Assistant is not available)."""
    if not ha.available():
        return []
    try:
        return await asyncio.to_thread(ha.list_todo_lists)
    except ha.HAError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.post("/homes/{home_id}/planner/todo-import")
async def import_todo_list(home_id: str, request: TodoImportIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Read a Home Assistant to-do list and match its lines to your items.

    Returns each line with its quantity, the best matching item (or none) and other candidates, for
    a person to confirm before adding them to the trip."""
    _home(db, home_id)
    if not ha.available():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Home Assistant is not available to this app")
    try:
        lines = await asyncio.to_thread(ha.get_todo_items, request.entity_id)
    except ha.HAError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    items = trips.available_items(db, home_id, None, 2000)
    return {"entity_id": request.entity_id, "lines": shopping.match_lines(lines, items)}


@router.get("/homes/{home_id}/planner/setup")
async def planner_setup(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """The home's address (and whether it was found on the map), its cars, and unit labels."""
    _home(db, home_id)
    return trips.setup(db, home_id)


@router.put("/homes/{home_id}/planner/address")
async def set_home_address(home_id: str, request: AddressIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Save the home address and look it up on the map. An empty address clears it."""
    _home(db, home_id)
    try:
        return await asyncio.to_thread(trips.set_home_address, db, home_id, request.address)
    except trips.TripError as e:
        raise _bad(e)


@router.get("/homes/{home_id}/vehicles")
async def list_vehicles(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """The home's cars, in the configured unit system."""
    _home(db, home_id)
    return trips.list_vehicles(db, home_id)


@router.post("/homes/{home_id}/vehicles", status_code=status.HTTP_201_CREATED)
async def create_vehicle(home_id: str, request: VehicleIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Add a car. Fuel cars need their economy (mpg or L/100 km) and fuel price; electric cars their
    consumption (kWh per distance) and electricity price."""
    _home(db, home_id)
    try:
        return trips.create_vehicle(db, home_id, request.model_dump())
    except trips.TripError as e:
        raise _bad(e)


@router.patch("/vehicles/{vehicle_id}")
async def update_vehicle(vehicle_id: str, request: VehicleIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Change a car (send all its fields)."""
    try:
        vehicle = trips.update_vehicle(db, vehicle_id, request.model_dump())
    except trips.TripError as e:
        raise _bad(e)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Car not found")
    return vehicle


@router.delete("/vehicles/{vehicle_id}")
async def delete_vehicle(vehicle_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if not trips.delete_vehicle(db, vehicle_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Car not found")
    return {"status": "deleted", "id": vehicle_id}


@router.get("/homes/{home_id}/planner/items")
async def planner_items(home_id: str, q: str | None = None, limit: int = 500, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Items that have a price on record, most bought first. ``amount`` is the usual purchase of an
    item sold by weight (a quantity of 1 means that much), and 1 otherwise."""
    _home(db, home_id)
    return trips.available_items(db, home_id, q, limit)


@router.post("/homes/{home_id}/planner/locate")
async def locate_stores(home_id: str, request: LocateIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Find map coordinates for stores that need them (a few per call, one search a second, results
    are remembered). Call again while ``pending`` is above 0."""
    _home(db, home_id)
    return await asyncio.to_thread(trips.geocode_pending, db, home_id, request.item_ids)


@router.post("/homes/{home_id}/planner/plan")
async def plan_trip(home_id: str, request: PlanIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Where to buy the chosen items, and the order to drive there, for the lowest total of item
    prices plus driving cost.

    Prices are the latest you paid at each store. Returns the best plan, the best plan for other
    numbers of stops, the best single-store plan and what you would do if driving were free, with
    warnings about stores left out, estimated prices and estimated distances.
    """
    _home(db, home_id)
    body = request.model_dump()
    try:
        result = await asyncio.to_thread(trips.make_plan, db, home_id, body)
    except trips.TripError as e:
        raise _bad(e)
    result = jsonable_encoder(result)  # as it is sent, so the saved copy reads back the same
    try:
        await asyncio.to_thread(tripcache.save, db, home_id, body, result)
    except Exception:  # noqa: BLE001 - keeping a copy must never cost the person their plan
        logger.warning("Could not keep the trip plan", exc_info=True)
    return {**result, "saved": None}


@router.post("/homes/{home_id}/planner/plan/saved")
async def saved_plan(home_id: str, request: PlanIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """The plan kept for exactly this request, if one was made within *Keep trip plans* and nothing it uses has
    changed since (receipts, online prices, stores, cars, home address): ``{found: true, ...plan, saved: {at, hours_left}}``,
    else ``{found: false}``. Nothing is looked up or worked out."""
    _home(db, home_id)
    plan = await asyncio.to_thread(tripcache.saved, db, home_id, request.model_dump())
    return {"found": True, **plan} if plan else {"found": False}


@router.get("/homes/{home_id}/planner/last")
async def last_plan(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """The home's newest plan and its request (to show again when the Trip page opens), or ``null``. ``current``
    says whether it still matches the data; ``hours_left`` how long it is kept."""
    _home(db, home_id)
    return await asyncio.to_thread(tripcache.last, db, home_id)


@router.delete("/homes/{home_id}/planner/last")
async def forget_last_plan(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _home(db, home_id)
    tripcache.forget(home_id)
    return {"forgotten": True}
