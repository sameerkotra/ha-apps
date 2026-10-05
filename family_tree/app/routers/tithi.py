"""Tithi anniversaries (§13.12) and milestone settings (§13.15).

A birth or death event can carry a tithi, typed in (masa, paksha, tithi) or
worked out from the event's full date. Each year's calendar date is computed
for Home Assistant's home location; a hand correction for one year is kept
(and is a history entry, so it can be undone).
"""
import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from .. import config, db, features, graph as graph_mod, kin, milestones, panchang, tithi
from ..auth import require_admin, require_user
from ..models import Strict
from ..history import Batch

router = APIRouter(prefix="/api", tags=["tithi"])


def _event(conn, eid: str) -> dict:
    row = conn.execute("SELECT e.* FROM events e LEFT JOIN people p ON p.id = e.person_id WHERE e.id = ? "
                       "AND e.person_id IS NOT NULL AND p.deleted_at IS NULL", (eid,)).fetchone()
    if not row:
        raise HTTPException(404, "That event isn't in the tree.")
    if row["type"] not in ("birth", "death"):
        raise HTTPException(422, "A tithi can only be kept for a birth or a death.")
    return dict(row)


def tithi_out(conn, event_id: str, lang: str = "en") -> dict | None:
    t = conn.execute("SELECT * FROM event_tithi WHERE event_id = ?", (event_id,)).fetchone()
    if not t:
        return None
    t = dict(t)
    tithi._refresh_cache(conn)
    year = config.today().year
    nxt, over = tithi.date_in(conn, event_id, t, year)
    if not nxt or nxt < config.today():
        nxt, over = tithi.date_in(conn, event_id, t, year + 1)
    overrides = [{"year": r["year"], "date": r["date"]} for r in conn.execute(
        "SELECT year, date FROM tithi_dates WHERE event_id = ? AND overridden_by IS NOT NULL ORDER BY year", (event_id,))]
    return {"eventId": event_id, "masa": t["masa"], "paksha": t["paksha"], "tithi": t["tithi"], "source": t["source"],
            "label": tithi.label(t, lang), "labelEn": tithi.label(t, "en"),
            "next": nxt.isoformat() if nxt else None, "nextOverridden": over, "overrides": overrides}


def for_person(conn, p, lang: str = "en") -> dict:
    """{"birth": …, "death": …} for the person detail (None where there's no tithi)."""
    out = {}
    for kind, ev in (("birth", p.birth), ("death", p.death)):
        out[kind] = tithi_out(conn, ev["id"], lang) if ev else None
    return out


def _options(ev: dict) -> list:
    if not (ev["date_y"] and ev["date_m"] and ev["date_d"]):
        raise HTTPException(422, "Working out the tithi needs the full date (day, month and year).")
    try:
        day = date(ev["date_y"], ev["date_m"], ev["date_d"])
    except ValueError:
        raise HTTPException(422, "That date isn't a real day.")
    if day.year < 1800:
        raise HTTPException(422, "Tithis can be worked out for dates from 1800 on.")
    lat, lon = config.location()
    out = []
    if ev.get("time"):                       # a known time: the tithi at that moment, no choice needed
        hh, mm = (int(x) for x in ev["time"].split(":"))
        found = [panchang.tithi_moment(datetime(day.year, day.month, day.day, hh, mm, tzinfo=config.tz()), config.tz())]
    else:
        found = panchang.tithis_on(day, lat, lon, config.tz())
    for o in found:
        t = {"masa": o["masa"], "paksha": o["paksha"], "tithi": o["tithi"]}
        out.append(dict(t, adhika=o["adhika"], until=o["until"].strftime("%H:%M"),
                        untilDate=o["until"].date().isoformat(), label=tithi.label(t, kin.current()),
                        labelEn=tithi.label(t, "en")))
    return out


@router.get("/events/{eid}/tithi/options", dependencies=[Depends(features.required("tithi"))])
def tithi_options(eid: str, user: dict = Depends(require_user)):
    """The tithi(s) current on the event's day (sunrise to sunrise) — two when it changed that day."""
    with db.get_conn() as conn:
        ev = _event(conn, eid)
    return {"options": _options(ev), "atTime": ev.get("time"), "locationKnown": config.location_known(),
            "timezone": config.timezone_name()}


class TithiIn(Strict):
    masa: int = Field(ge=1, le=12)
    paksha: str = Field(pattern="^(shukla|krishna)$")
    tithi: int = Field(ge=1, le=15)
    source: str = Field(default="entered", pattern="^(entered|computed)$")


def _drop_cache(conn, eid: str, b: Batch) -> None:
    conn.execute("DELETE FROM tithi_dates WHERE event_id = ? AND overridden_by IS NULL", (eid,))
    for r in conn.execute("SELECT year FROM tithi_dates WHERE event_id = ?", (eid,)).fetchall():
        b.hard_delete("tithi_dates", f"{eid}|{r['year']}")


@router.put("/events/{eid}/tithi", dependencies=[Depends(features.required("tithi"))])
def set_tithi(eid: str, body: TithiIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        ev = _event(conn, eid)
        if body.source == "computed":
            opts = _options(ev)
            if not any((o["masa"], o["paksha"], o["tithi"]) == (body.masa, body.paksha, body.tithi) for o in opts):
                raise HTTPException(422, "That isn't the tithi on the event's day — choose one of the options.")
        row = {"masa": body.masa, "paksha": body.paksha, "tithi": body.tithi, "source": body.source}
        b = Batch(conn, user["id"], "Set a tithi")
        old = conn.execute("SELECT * FROM event_tithi WHERE event_id = ?", (eid,)).fetchone()
        if old:
            if (old["masa"], old["paksha"], old["tithi"]) != (body.masa, body.paksha, body.tithi):
                _drop_cache(conn, eid, b)
            b.update("event_tithi", eid, row)
        else:
            b.insert("event_tithi", dict(row, event_id=eid))
        b.touch(ev["person_id"])
        return {"batchId": b.id, "tithi": tithi_out(conn, eid, kin.current())}


@router.delete("/events/{eid}/tithi", dependencies=[Depends(features.required("tithi"))])
def remove_tithi(eid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        ev = _event(conn, eid)
        if not conn.execute("SELECT 1 FROM event_tithi WHERE event_id = ?", (eid,)).fetchone():
            raise HTTPException(404, "There's no tithi on that event.")
        b = Batch(conn, user["id"], "Removed a tithi")
        _drop_cache(conn, eid, b)
        b.hard_delete("event_tithi", eid)
        b.touch(ev["person_id"])
        return {"batchId": b.id}


class OverrideIn(Strict):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.put("/tithi/{eid}/{year}", dependencies=[Depends(features.required("tithi"))])
def override(eid: str, year: int, body: OverrideIn, user: dict = Depends(require_user)):
    """A hand correction for one year ("our priest says the 15th"); never recomputed."""
    try:
        d = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(422, "That date isn't a real day.")
    if not 1800 <= year <= 2200:
        raise HTTPException(422, "That year is out of range.")
    if abs((d - date(year, 7, 1)).days) > 200:
        raise HTTPException(422, f"The corrected date should be in or near {year}.")
    with db.get_conn() as conn:
        ev = _event(conn, eid)
        if not conn.execute("SELECT 1 FROM event_tithi WHERE event_id = ?", (eid,)).fetchone():
            raise HTTPException(404, "Set the tithi first.")
        b = Batch(conn, user["id"], "Corrected a tithi date")
        old = conn.execute("SELECT * FROM tithi_dates WHERE event_id = ? AND year = ?", (eid, year)).fetchone()
        row = {"date": d.isoformat(), "overridden_by": user["id"], "computed_at": config.now_iso()}
        if old and old["overridden_by"]:
            b.update("tithi_dates", f"{eid}|{year}", row)
        else:
            if old:
                conn.execute("DELETE FROM tithi_dates WHERE event_id = ? AND year = ?", (eid, year))
            b.insert("tithi_dates", dict(row, event_id=eid, year=year))
        b.touch(ev["person_id"])
        return {"batchId": b.id, "tithi": tithi_out(conn, eid, kin.current())}


@router.delete("/tithi/{eid}/{year}", dependencies=[Depends(features.required("tithi"))])
def remove_override(eid: str, year: int, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        ev = _event(conn, eid)
        if not conn.execute("SELECT 1 FROM tithi_dates WHERE event_id = ? AND year = ? AND overridden_by IS NOT NULL",
                            (eid, year)).fetchone():
            raise HTTPException(404, "There's no correction for that year.")
        b = Batch(conn, user["id"], "Removed a tithi correction")
        b.hard_delete("tithi_dates", f"{eid}|{year}")
        b.touch(ev["person_id"])
        return {"batchId": b.id, "tithi": tithi_out(conn, eid, kin.current())}


@router.get("/tithi/dates", dependencies=[Depends(features.required("tithi"))])
def dates_for_year(year: int = Query(ge=1800, le=2200), user: dict = Depends(require_user)):
    """Every kept tithi and its date in `year` — for a printed list for the priest."""
    lang = kin.current()
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        tithi._refresh_cache(conn)
        out = []
        for t in tithi.all_tithis(conn):
            p = g.people.get(t["person_id"])
            if not p:
                continue
            d, over = tithi.date_in(conn, t["event_id"], t, year)
            out.append({"eventId": t["event_id"], "type": t["type"], "person": {"id": p.id, "name": p.name},
                        "label": tithi.label(t, lang), "date": d.isoformat() if d else None, "overridden": over})
    out.sort(key=lambda x: x["date"] or "9999")
    lat, lon = config.location()
    return {"year": year, "items": out, "rule": tithi.settings.get("tithi_rule"),
            "location": {"lat": lat, "lon": lon, "known": config.location_known()}, "timezone": config.timezone_name()}


# ---------- milestones (§13.15) ----------
def milestone_out(m: dict) -> dict:
    return {"id": m["id"], "kind": m["kind"], "value": m["value"], "labelEn": m["label_en"], "labelTe": m["label_te"],
            "labelHi": m["label_hi"], "leadDays": m["leads"], "enabled": bool(m["enabled"])}


class MilestoneIn(Strict):
    kind: str = Field(pattern="^(age|anniversary|full_moons)$")
    value: int = Field(ge=1, le=2000)
    labelEn: str = Field(min_length=1, max_length=80)
    labelTe: str | None = Field(default=None, max_length=80)
    labelHi: str | None = Field(default=None, max_length=80)
    leadDays: list[int] = Field(default_factory=lambda: list(milestones.DEFAULT_LEADS), max_length=6)
    enabled: bool = True


class MilestonePatch(Strict):
    labelEn: str | None = Field(default=None, min_length=1, max_length=80)
    labelTe: str | None = Field(default=None, max_length=80)
    labelHi: str | None = Field(default=None, max_length=80)
    leadDays: list[int] | None = Field(default=None, max_length=6)
    enabled: bool | None = None
    value: int | None = Field(default=None, ge=1, le=2000)


def _leads(v: list[int]) -> str:
    if any(not 0 <= x <= 366 for x in v):
        raise HTTPException(422, "Notice days must be between 0 and 366.")
    return json.dumps(sorted(set(v), reverse=True))


def _check_value(kind: str, value: int) -> None:
    if kind in ("age", "anniversary") and value > 120:
        raise HTTPException(422, "That's more years than anyone will reach.")
    if kind == "full_moons" and value < 12:
        raise HTTPException(422, "Count at least 12 full moons.")


@router.get("/milestones", dependencies=[Depends(features.required("milestones"))])
def list_milestones(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"items": [milestone_out(m) for m in milestones.rows(conn, enabled_only=False)]}


@router.post("/milestones", status_code=201, dependencies=[Depends(features.required("milestones"))])
def add_milestone(body: MilestoneIn, admin: dict = Depends(require_admin)):
    _check_value(body.kind, body.value)
    with db.get_conn() as conn:
        milestones.ensure_seeded(conn)
        if conn.execute("SELECT 1 FROM milestones WHERE kind = ? AND value = ?", (body.kind, body.value)).fetchone():
            raise HTTPException(409, "That milestone is already in the list.")
        mid = db.new_id()
        conn.execute("INSERT INTO milestones (id, kind, value, label_en, label_te, label_hi, lead_days, enabled) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (mid, body.kind, body.value, body.labelEn.strip(),
                                                         (body.labelTe or "").strip() or None,
                                                         (body.labelHi or "").strip() or None,
                                                         _leads(body.leadDays), 1 if body.enabled else 0))
        return {"items": [milestone_out(m) for m in milestones.rows(conn, enabled_only=False)]}


@router.patch("/milestones/{mid}", dependencies=[Depends(features.required("milestones"))])
def edit_milestone(mid: str, body: MilestonePatch, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM milestones WHERE id = ?", (mid,)).fetchone()
        if not row:
            raise HTTPException(404, "That milestone isn't in the list.")
        sets = {}
        for k, col in (("labelEn", "label_en"), ("labelTe", "label_te"), ("labelHi", "label_hi")):
            if k in body.model_fields_set:
                v = (getattr(body, k) or "").strip() or None
                if k == "labelEn" and not v:
                    raise HTTPException(422, "The English name can't be empty.")
                sets[col] = v
        if body.leadDays is not None:
            sets["lead_days"] = _leads(body.leadDays)
        if body.enabled is not None:
            sets["enabled"] = 1 if body.enabled else 0
        if body.value is not None:
            _check_value(row["kind"], body.value)
            if conn.execute("SELECT 1 FROM milestones WHERE kind = ? AND value = ? AND id != ?",
                            (row["kind"], body.value, mid)).fetchone():
                raise HTTPException(409, "That milestone is already in the list.")
            sets["value"] = body.value
        if sets:
            conn.execute(f"UPDATE milestones SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                         list(sets.values()) + [mid])
        return {"items": [milestone_out(m) for m in milestones.rows(conn, enabled_only=False)]}


@router.delete("/milestones/{mid}", dependencies=[Depends(features.required("milestones"))])
def delete_milestone(mid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        if not conn.execute("DELETE FROM milestones WHERE id = ?", (mid,)).rowcount:
            raise HTTPException(404, "That milestone isn't in the list.")
        return {"items": [milestone_out(m) for m in milestones.rows(conn, enabled_only=False)]}
