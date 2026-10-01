"""A small offline panchang (§13.12): tithi, amanta lunar months with
adhika (leap) months, sunrise and the aparahna period — no new dependencies.

- Sun and Moon ecliptic longitudes from Meeus, *Astronomical Algorithms*
  (ch. 25 low-precision Sun, ~0.01°; ch. 47 Moon with the full Table 47.A
  longitude series, ~10″). Nutation cancels in the Moon − Sun difference.
- Tithi = ⌊((λMoon − λSun) mod 360°) / 12°⌋ + 1 (1–30; 16–30 are the dark
  half). 15 = Pournami, 30 = Amavasya.
- Months are **amanta** (new moon to new moon, as in Telugu calendars) and are
  named after the Sun's sidereal sign (Lahiri ayanamsa) at the month's
  starting new moon: Meena → Chaitra, Mesha → Vaishakha … A month in which the
  Sun changes no sign is **adhika**; a shraddha falls in the regular (nija)
  month of that name.
- The observance day follows the `tithi_rule` App setting: `aparahna` (the
  day on which the tithi covers more of the afternoon period — the 4th of the
  five parts of daytime — the usual shraddha rule), or `sunrise` (the tithi
  current at sunrise).
- Times are UTC datetimes; days are local (a ZoneInfo) at a latitude/longitude
  (Home Assistant's home location).
"""
import functools
import math
from datetime import date, datetime, timedelta, timezone

MASAS = ["Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada", "Ashwayuja", "Kartika",
         "Margashira", "Pushya", "Magha", "Phalguna"]
MASAS_TE = ["చైత్రం", "వైశాఖం", "జ్యేష్ఠం", "ఆషాఢం", "శ్రావణం", "భాద్రపదం", "ఆశ్వయుజం", "కార్తీకం", "మార్గశిరం",
            "పుష్యం", "మాఘం", "ఫాల్గుణం"]
MASAS_HI = ["चैत्र", "वैशाख", "ज्येष्ठ", "आषाढ़", "श्रावण", "भाद्रपद", "आश्विन", "कार्तिक", "मार्गशीर्ष", "पौष", "माघ",
            "फाल्गुन"]
TITHIS = ["Padyami", "Vidiya", "Tadiya", "Chavithi", "Panchami", "Shashti", "Saptami", "Ashtami", "Navami",
          "Dashami", "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi"]
TITHIS_TE = ["పాడ్యమి", "విదియ", "తదియ", "చవితి", "పంచమి", "షష్ఠి", "సప్తమి", "అష్టమి", "నవమి", "దశమి", "ఏకాదశి",
             "ద్వాదశి", "త్రయోదశి", "చతుర్దశి"]
TITHIS_HI = ["प्रतिपदा", "द्वितीया", "तृतीया", "चतुर्थी", "पंचमी", "षष्ठी", "सप्तमी", "अष्टमी", "नवमी", "दशमी", "एकादशी",
             "द्वादशी", "त्रयोदशी", "चतुर्दशी"]
PAKSHA = {"shukla": {"en": "Shukla", "te": "శుక్ల", "hi": "शुक्ल"}, "krishna": {"en": "Bahula", "te": "బహుళ", "hi": "कृष्ण"}}
SYNODIC = 29.530588853

# Meeus Table 47.A — (D, M, M', F, ΣL coefficient in 1e-6 degrees)
_MOON = [
    (0, 0, 1, 0, 6288774), (2, 0, -1, 0, 1274027), (2, 0, 0, 0, 658314), (0, 0, 2, 0, 213618),
    (0, 1, 0, 0, -185116), (0, 0, 0, 2, -114332), (2, 0, -2, 0, 58793), (2, -1, -1, 0, 57066),
    (2, 0, 1, 0, 53322), (2, -1, 0, 0, 45758), (0, 1, -1, 0, -40923), (1, 0, 0, 0, -34720),
    (0, 1, 1, 0, -30383), (2, 0, 0, -2, 15327), (0, 0, 1, 2, -12528), (0, 0, 1, -2, 10980),
    (4, 0, -1, 0, 10675), (0, 0, 3, 0, 10034), (4, 0, -2, 0, 8548), (2, 1, -1, 0, -7888),
    (2, 1, 0, 0, -6766), (1, 0, -1, 0, -5163), (1, 1, 0, 0, 4987), (2, -1, 1, 0, 4036),
    (2, 0, 2, 0, 3994), (4, 0, 0, 0, 3861), (2, 0, -3, 0, 3665), (0, 1, -2, 0, -2689),
    (2, 0, -1, 2, -2602), (2, -1, -2, 0, 2390), (1, 0, 1, 0, -2348), (2, -2, 0, 0, 2236),
    (0, 1, 2, 0, -2120), (0, 2, 0, 0, -2069), (2, -2, -1, 0, 2048), (2, 0, 1, -2, -1773),
    (2, 0, 0, 2, -1595), (4, -1, -1, 0, 1215), (0, 0, 2, 2, -1110), (3, 0, -1, 0, -892),
    (2, 1, 1, 0, -810), (4, -1, -2, 0, 759), (0, 2, -1, 0, -713), (2, 2, -1, 0, -700),
    (2, 1, -2, 0, 691), (2, -1, 0, -2, 596), (4, 0, 1, 0, 549), (0, 0, 4, 0, 537),
    (4, -1, 0, 0, 520), (1, 0, -2, 0, -487), (2, 1, 0, -2, -399), (0, 0, 2, -2, -381),
    (1, 1, 1, 0, 351), (3, 0, -2, 0, -340), (4, 0, -3, 0, 330), (2, -1, 2, 0, 327),
    (0, 2, 1, 0, -323), (1, 1, -1, 0, 299), (2, 0, 3, 0, 294),
]


def _jd(dt: datetime) -> float:
    """Julian Day (UT) of an aware datetime."""
    dt = dt.astimezone(timezone.utc)
    return 2440587.5 + dt.timestamp() / 86400.0


def _from_jd(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz=timezone.utc)


def _delta_t(year: float) -> float:
    """TT − UT in seconds (Espenak & Meeus polynomials, simplified)."""
    t = year - 2000
    if year >= 2005:
        return 62.92 + 0.32217 * t + 0.005589 * t * t
    if year >= 1986:
        return 63.86 + 0.3345 * t - 0.060374 * t ** 2 + 0.0017275 * t ** 3 + 0.000651814 * t ** 4 + 0.00002373599 * t ** 5
    if year >= 1961:
        u = year - 1975
        return 45.45 + 1.067 * u - u * u / 260 - u ** 3 / 718
    if year >= 1941:
        u = year - 1950
        return 29.07 + 0.407 * u - u * u / 233 + u ** 3 / 2547
    if year >= 1920:
        u = year - 1920
        return 21.20 + 0.84493 * u - 0.076100 * u * u + 0.0020936 * u ** 3
    if year >= 1900:
        u = year - 1900
        return -2.79 + 1.494119 * u - 0.0598939 * u * u + 0.0061966 * u ** 3 - 0.000197 * u ** 4
    return 0.0


def _T(jd_ut: float) -> float:
    year = 2000 + (jd_ut - 2451545.0) / 365.25
    return (jd_ut + _delta_t(year) / 86400.0 - 2451545.0) / 36525.0


def sun_longitude(jd: float) -> float:
    """Apparent tropical longitude of the Sun, degrees (Meeus ch. 25)."""
    T = _T(jd)
    L0 = 280.46646 + 36000.76983 * T + 0.0003032 * T * T
    M = math.radians(357.52911 + 35999.05029 * T - 0.0001537 * T * T)
    C = ((1.914602 - 0.004817 * T - 0.000014 * T * T) * math.sin(M) + (0.019993 - 0.000101 * T) * math.sin(2 * M)
         + 0.000289 * math.sin(3 * M))
    om = math.radians(125.04 - 1934.136 * T)
    return (L0 + C - 0.00569 - 0.00478 * math.sin(om)) % 360


def moon_longitude(jd: float) -> float:
    """Apparent tropical longitude of the Moon, degrees (Meeus ch. 47)."""
    T = _T(jd)
    Lp = 218.3164477 + 481267.88123421 * T - 0.0015786 * T * T + T ** 3 / 538841 - T ** 4 / 65194000
    D = 297.8501921 + 445267.1114034 * T - 0.0018819 * T * T + T ** 3 / 545868 - T ** 4 / 113065000
    M = 357.5291092 + 35999.0502909 * T - 0.0001536 * T * T + T ** 3 / 24490000
    Mp = 134.9633964 + 477198.8675055 * T + 0.0087414 * T * T + T ** 3 / 69699 - T ** 4 / 14712000
    F = 93.2720950 + 483202.0175233 * T - 0.0036539 * T * T - T ** 3 / 3526000 + T ** 4 / 863310000
    A1 = 119.75 + 131.849 * T
    A2 = 53.09 + 479264.290 * T
    E = 1 - 0.002516 * T - 0.0000074 * T * T
    r = math.radians
    s = 0.0
    for d, m, mp, f, c in _MOON:
        e = E ** abs(m)
        s += c * e * math.sin(r(d * D + m * M + mp * Mp + f * F))
    s += 3958 * math.sin(r(A1)) + 1962 * math.sin(r(Lp - F)) + 318 * math.sin(r(A2))
    om = math.radians(125.04452 - 1934.136261 * T)
    return (Lp + s / 1e6 - 0.00478 * math.sin(om)) % 360


def ayanamsa(jd: float) -> float:
    """Lahiri (Chitrapaksha), degrees."""
    return 23.85306 + 1.39697 * (jd - 2451545.0) / 36525.0


def elongation(jd: float) -> float:
    return (moon_longitude(jd) - sun_longitude(jd)) % 360


def tithi_at(when) -> int:
    """1–30 at a moment (aware datetime or JD)."""
    jd = when if isinstance(when, float) else _jd(when)
    return int(elongation(jd) // 12) + 1


def sidereal_sun_sign(jd: float) -> int:
    """0 = Mesha … 11 = Meena."""
    return int(((sun_longitude(jd) - ayanamsa(jd)) % 360) // 30)


def _solve(jd0: float, target: float, window: float = 3.0) -> float:
    """The moment near jd0 (within ±window days, elongation increasing) when elongation = target."""
    def f(jd):
        return ((elongation(jd) - target + 180) % 360) - 180
    lo, hi = jd0 - window, jd0 + window
    # step to a bracket
    step = 0.25
    a, fa = lo, f(lo)
    x = lo + step
    while x <= hi + 1e-9:
        fx = f(x)
        if fa < 0 <= fx and fx - fa < 90:
            lo_b, hi_b = a, x
            for _ in range(50):
                mid = (lo_b + hi_b) / 2
                if f(mid) < 0:
                    lo_b = mid
                else:
                    hi_b = mid
            return (lo_b + hi_b) / 2
        a, fa = x, fx
        x += step
    raise ValueError("no solution")


def new_moons(start: datetime, end: datetime) -> list:
    """New-moon moments (JD) from start to end."""
    jd = _jd(start) - SYNODIC
    out = []
    # first approximate new moon at or before start
    e = elongation(jd)
    jd -= e / 360 * SYNODIC
    stop = _jd(end) + SYNODIC
    while jd < stop:
        nm = _solve(jd, 0.0)
        if not out or nm - out[-1] > 20:
            out.append(nm)
        jd = nm + SYNODIC
    return [x for x in out if _jd(start) - SYNODIC <= x <= stop]


@functools.lru_cache(maxsize=16)
def lunar_months(year: int) -> list:
    """Amanta months overlapping the Gregorian year (and a bit around it):
    [{masa 1–12, adhika, start, end (JD)}]."""
    nms = new_moons(datetime(year - 1, 10, 1, tzinfo=timezone.utc), datetime(year + 1, 3, 1, tzinfo=timezone.utc))
    out = []
    for a, b in zip(nms, nms[1:]):
        s0, s1 = sidereal_sun_sign(a), sidereal_sun_sign(b)
        masa = (s0 + 1) % 12 + 1                  # Meena (11) → 1 Chaitra, Mesha (0) → 2 Vaishakha
        out.append({"masa": masa, "adhika": s0 == s1, "start": a, "end": b})
    return out


# ---------- sunrise and the parts of the day ----------
def sun_times(day: date, lat: float, lon: float, tz) -> tuple[datetime, datetime]:
    """(sunrise, sunset) as aware local datetimes (the "sunrise equation", ~1 min)."""
    n = (datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc) - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).days
    n = n + 0.0008 - lon / 360.0
    M = (357.5291 + 0.98560028 * n) % 360
    Mr = math.radians(M)
    C = 1.9148 * math.sin(Mr) + 0.02 * math.sin(2 * Mr) + 0.0003 * math.sin(3 * Mr)
    lam = math.radians((M + C + 180 + 102.9372) % 360)
    j_transit = 2451545.0 + n + 0.0053 * math.sin(Mr) - 0.0069 * math.sin(2 * lam)
    dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_h = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(dec)) / (math.cos(phi) * math.cos(dec))
    cos_h = max(-1.0, min(1.0, cos_h))
    w = math.degrees(math.acos(cos_h)) / 360.0
    rise, sett = _from_jd(j_transit - w), _from_jd(j_transit + w)
    return rise.astimezone(tz), sett.astimezone(tz)


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def observance_day(t_start: float, t_end: float, lat: float, lon: float, tz, rule: str = "aparahna") -> date:
    """The civil day on which a tithi running t_start–t_end (JD) is kept."""
    d0 = _from_jd(t_start).astimezone(tz).date() - timedelta(days=1)
    days = [d0 + timedelta(days=i) for i in range(4)]
    if rule == "sunrise":
        for d in days:
            rise, _ = sun_times(d, lat, lon, tz)
            if t_start <= _jd(rise) < t_end:
                return d
        return _from_jd(t_start).astimezone(tz).date()          # a tithi with no sunrise: the day it begins
    best, best_cover = None, 0.0
    for d in days:
        rise, sett = sun_times(d, lat, lon, tz)
        r, s = _jd(rise), _jd(sett)
        a0, a1 = r + (s - r) * 3 / 5, r + (s - r) * 4 / 5         # aparahna: 4th fifth of daytime
        cover = _overlap(t_start, t_end, a0, a1)
        if cover > best_cover + 1e-9:
            best, best_cover = d, cover
    return best or observance_day(t_start, t_end, lat, lon, tz, "sunrise")


def tithi_window(month: dict, paksha: str, tithi: int) -> tuple[float, float]:
    k = tithi + (15 if paksha == "krishna" else 0)          # 1–30
    mid = month["start"] + (k - 0.5) * SYNODIC / 30
    return _solve(mid, (k - 1) * 12.0, 3.0), _solve(mid + 0.5, (k * 12.0) % 360, 3.0)


def date_for(year: int, masa: int, paksha: str, tithi: int, lat: float, lon: float, tz, rule: str = "aparahna") -> date | None:
    """The day in the Gregorian `year` on which masa/paksha/tithi (nija month) is kept."""
    for m in lunar_months(year):
        if m["masa"] != masa or m["adhika"]:
            continue
        a, b = tithi_window(m, paksha, tithi)
        d = observance_day(a, b, lat, lon, tz, rule)
        if d.year == year:
            return d
    return None


def tithis_on(day: date, lat: float, lon: float, tz) -> list:
    """Every tithi current during a civil day (sunrise to next sunrise), with the
    moment it ends: [{masa, adhika, paksha, tithi, until}] — two entries when it changed."""
    rise, _ = sun_times(day, lat, lon, tz)
    nrise, _ = sun_times(day + timedelta(days=1), lat, lon, tz)
    out = []
    jd = _jd(rise)
    months = lunar_months(day.year)
    while jd < _jd(nrise) and len(out) < 3:
        k = tithi_at(jd)
        m = next(x for x in months if x["start"] <= jd < x["end"])
        end = _solve(jd + 0.5, (k * 12.0) % 360, 1.5)
        out.append({"masa": m["masa"], "adhika": m["adhika"], "paksha": "shukla" if k <= 15 else "krishna",
                    "tithi": k if k <= 15 else k - 15, "until": _from_jd(end).astimezone(tz)})
        jd = end + 1e-5
    return out


def tithi_moment(when: datetime, tz) -> dict:
    """The tithi at one moment (e.g. a known time of birth or death), shaped like a tithis_on() entry."""
    jd = _jd(when)
    k = tithi_at(jd)
    months = lunar_months(when.year) + lunar_months(when.year + 1)
    m = next(x for x in months if x["start"] <= jd < x["end"])
    end = _solve(jd + 0.5, (k * 12.0) % 360, 1.5)
    return {"masa": m["masa"], "adhika": m["adhika"], "paksha": "shukla" if k <= 15 else "krishna",
            "tithi": k if k <= 15 else k - 15, "until": _from_jd(end).astimezone(tz)}


def name(masa: int, paksha: str, tithi: int, lang: str = "en") -> str:
    """"Ashwayuja Bahula Dashami" (or in Telugu / Hindi)."""
    masas = {"te": MASAS_TE, "hi": MASAS_HI}.get(lang, MASAS)
    tithis = {"te": TITHIS_TE, "hi": TITHIS_HI}.get(lang, TITHIS)
    if tithi == 15:
        last = {"en": ("Pournami", "Amavasya"), "te": ("పౌర్ణమి", "అమావాస్య"), "hi": ("पूर्णिमा", "अमावस्या")}[lang if lang in ("te", "hi") else "en"]
        t = last[0] if paksha == "shukla" else last[1]
        return f"{masas[masa - 1]} {t}"
    return f"{masas[masa - 1]} {PAKSHA[paksha].get(lang, PAKSHA[paksha]['en'])} {tithis[tithi - 1]}"


def full_moon_count_date(birth: datetime, n: int = 1000) -> date:
    """The day of the n-th full moon after `birth` (Sahasra Chandra Darshanam for n = 1000)."""
    jd = _jd(birth)
    k = (elongation(jd) - 180) % 360                          # degrees past the last full moon
    first = jd + (360 - k) / 360 * SYNODIC if k else jd       # next full moon, roughly
    approx = first + (n - 1) * SYNODIC
    fm = _solve(approx, 180.0, 3.0)
    return _from_jd(fm).date()
