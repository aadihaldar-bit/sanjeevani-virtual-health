"""Find nearby pharmacies for medicine pickup.

Three modes, chosen automatically by configured provider credentials:

* **Geoapify mode (preferred when GEOAPIFY_API_KEY is set)** — uses its Places API for
  reliable nearby business results on the free plan.
* **OpenStreetMap mode (fallback, no key needed)** — queries the free Overpass API for
  `amenity=pharmacy` near the user, computes straight-line (haversine) distance, and reads the
  OSM `opening_hours` tag to tell if it is open now. Ranks by distance + an open-now boost.
* **Foursquare mode (when FOURSQUARE_API_KEY is set)** — uses Foursquare Places, which also returns
  **customer ratings**, so pharmacies can additionally be ranked by rating. Rendered on the same
  OpenStreetMap/Leaflet map on the front end.

Nothing here needs an API key to work in OSM mode. Ratings only appear in Foursquare mode.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncio
import time

import httpx

from config import settings

# Small in-memory cache (the public Overpass mirrors are rate-limited; caching makes repeat
# look-ups instant and avoids re-hitting them). Keyed by rounded lat/lon + radius.
_CACHE: Dict[str, Any] = {}
_CACHE_TTL_S = 900  # 15 minutes
_GEOCODE_CACHE: Dict[str, Any] = {}
_GEOCODE_CACHE_TTL_S = 86400  # 24 hours
_GEOCODE_LOCK = asyncio.Lock()
_LAST_NOMINATIM_REQUEST_AT = 0.0
# The locator is interactive. If all public mirrors are unhealthy, return the clearly-labelled
# fallback within roughly 15 seconds rather than holding the Pharmacy screen in a loading state.
OVERPASS_QUERY_TIMEOUT_S = 4
OVERPASS_RESULT_LIMIT = 100

# Several public Overpass mirrors — they are rate-limited and intermittently 502/504, so we try
# them in turn (with a short retry) instead of depending on any single one.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
OVERPASS_URL = OVERPASS_MIRRORS[0]
FOURSQUARE_SEARCH_URL = "https://api.foursquare.com/v3/places/search"
GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"
GEOAPIFY_GEOCODE_SEARCH_URL = "https://api.geoapify.com/v1/geocode/search"
GEOAPIFY_AUTOCOMPLETE_URL = "https://api.geoapify.com/v1/geocode/autocomplete"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
GEOAPIFY_REVERSE_GEOCODE_URL = "https://api.geoapify.com/v1/geocode/reverse"
# Sanjeevani serves the UAE: address search stays inside it (ISO 3166 country code for Geoapify and Nominatim).
SEARCH_COUNTRY = "ae"
_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _parse_open_now(spec: Optional[str], now: Optional[datetime] = None) -> Optional[bool]:
    """Best-effort 'is it open right now' from an OSM opening_hours string.

    Handles the common cases (24/7, 'Mo-Su 08:00-22:00', 'Mo-Fr 09:00-21:00; Sa 10:00-18:00').
    Returns None when the format is one we do not confidently understand (so the UI shows
    'hours unavailable' rather than a wrong answer)."""
    if not spec:
        return None
    spec = spec.strip()
    if spec in ("24/7", "24/7 open", "Mo-Su 00:00-24:00"):
        return True
    now = now or datetime.now()
    today = _DAYS[now.weekday()]
    minutes_now = now.hour * 60 + now.minute
    matched_a_rule = False
    try:
        for rule in spec.split(";"):
            rule = rule.strip()
            m = re.match(r"^([A-Za-z,\-]+)\s+([0-9:,\- ]+)$", rule)
            if not m:
                # a rule with times but no day prefix => every day
                m2 = re.match(r"^([0-9]{1,2}:[0-9]{2}-[0-9]{1,2}:[0-9]{2})$", rule)
                if m2:
                    days_part, times_part = "Mo-Su", rule
                else:
                    continue
            else:
                days_part, times_part = m.group(1), m.group(2)
            if not _day_in_range(days_part, today):
                continue
            for span in times_part.split(","):
                tm = re.match(r"^\s*([0-9]{1,2}):([0-9]{2})\s*-\s*([0-9]{1,2}):([0-9]{2})\s*$", span)
                if not tm:
                    continue
                matched_a_rule = True
                start = int(tm.group(1)) * 60 + int(tm.group(2))
                end = int(tm.group(3)) * 60 + int(tm.group(4))
                if end <= start:            # crosses midnight
                    if minutes_now >= start or minutes_now < end:
                        return True
                elif start <= minutes_now < end:
                    return True
    except Exception:
        return None
    return False if matched_a_rule else None


def _day_in_range(days_part: str, today: str) -> bool:
    for token in days_part.split(","):
        token = token.strip()
        if "-" in token:
            a, b = token.split("-", 1)
            if a in _DAYS and b in _DAYS:
                i, j, t = _DAYS.index(a), _DAYS.index(b), _DAYS.index(today)
                if i <= j and i <= t <= j:
                    return True
                if i > j and (t >= i or t <= j):      # wraps the week
                    return True
        elif token == today:
            return True
    return False


async def _overpass_pharmacies(lat: float, lon: float, radius_m: int) -> List[Dict[str, Any]]:
    # Node-only keeps the query light (pharmacies are almost always mapped as nodes), which the
    # rate-limited public mirrors handle far more reliably than node+way+center.
    query = (
        f"[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_S}];"
        f'node["amenity"="pharmacy"](around:{radius_m},{lat},{lon});'
        f"out tags {OVERPASS_RESULT_LIMIT};"
    )
    elements = None
    last_exc = None
    # Do one short pass over the mirrors. Public Overpass is best-effort; the UI should return
    # labelled fallback data promptly rather than leave a patient-facing screen loading for minutes.
    async with httpx.AsyncClient(timeout=5.0) as client:
        for mirror in OVERPASS_MIRRORS:
            try:
                r = await client.post(mirror, data={"data": query},
                                      headers={"User-Agent": "sanjeevani-virtual-health/pickup"})
                r.raise_for_status()
                elements = r.json().get("elements", [])
                break
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.25)
                continue
    if elements is None:
        raise RuntimeError(f"All Overpass mirrors failed (last error: {last_exc})")
    out = []
    for el in elements:
        tags = el.get("tags", {}) or {}
        plat = el.get("lat") or (el.get("center") or {}).get("lat")
        plon = el.get("lon") or (el.get("center") or {}).get("lon")
        if plat is None or plon is None:
            continue
        # Prefer the explicit English OSM tags so that the product remains English-first even
        # when the map is used in a country whose primary map labels are in another language.
        name = tags.get("name:en") or tags.get("brand:en") or tags.get("name") or tags.get("brand") or tags.get("operator") or "Pharmacy"
        addr = ", ".join(x for x in [
            tags.get("addr:housenumber"), tags.get("addr:street:en") or tags.get("addr:street"),
            tags.get("addr:city:en") or tags.get("addr:city") or tags.get("addr:suburb:en") or tags.get("addr:suburb")] if x) or None
        hours = tags.get("opening_hours")
        out.append({
            "id": f"osm:{el.get('type')}/{el.get('id')}",
            "name": name, "lat": plat, "lon": plon,
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "address": addr,
            "opening_hours_raw": hours,
            "open_now": _parse_open_now(hours),
            "rating": None, "rating_count": None,
        })
    return out


async def _foursquare_pharmacies(lat: float, lon: float, radius_m: int) -> List[Dict[str, Any]]:
    headers = {"Authorization": settings.FOURSQUARE_API_KEY, "accept": "application/json"}
    params = {
        "ll": f"{lat},{lon}", "radius": str(radius_m), "query": "pharmacy",
        "sort": "DISTANCE", "limit": "30", "locale": "en",
        "fields": "fsq_id,name,location,geocodes,distance,rating,stats,tel,hours",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(FOURSQUARE_SEARCH_URL, params=params, headers=headers)
        r.raise_for_status()
        results = r.json().get("results", [])
    out = []
    for p in results:
        main = (p.get("geocodes") or {}).get("main") or {}
        plat, plon = main.get("latitude"), main.get("longitude")
        if plat is None or plon is None:
            continue
        hours = p.get("hours") or {}
        rating = p.get("rating")
        out.append({
            "id": f"fsq:{p.get('fsq_id')}",
            "name": p.get("name") or "Pharmacy", "lat": plat, "lon": plon,
            "phone": p.get("tel"),
            "address": (p.get("location") or {}).get("formatted_address"),
            "opening_hours_raw": hours.get("display"),
            "open_now": hours.get("open_now"),
            # Foursquare rating is 0-10; normalise to 0-5 for display consistency.
            "rating": round(rating / 2, 1) if isinstance(rating, (int, float)) else None,
            "rating_count": (p.get("stats") or {}).get("total_ratings"),
        })
    return out


async def _geoapify_pharmacies(lat: float, lon: float, radius_m: int) -> List[Dict[str, Any]]:
    """Fetch named pharmacy POIs from Geoapify Places using the configured server-side key."""
    params = {
        "categories": "commercial.health_and_beauty.pharmacy,healthcare.pharmacy",
        "filter": f"circle:{lon},{lat},{radius_m}",
        "bias": f"proximity:{lon},{lat}",
        "limit": "30",
        "lang": "en",
        "apiKey": settings.GEOAPIFY_API_KEY,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(GEOAPIFY_PLACES_URL, params=params)
        response.raise_for_status()
        features = response.json().get("features", [])

    out = []
    for index, feature in enumerate(features):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        plon, plat = coordinates[0], coordinates[1]
        if not isinstance(plat, (int, float)) or not isinstance(plon, (int, float)):
            continue
        categories = properties.get("categories") or []
        if categories and not any(str(category).endswith(".pharmacy") for category in categories):
            continue
        contact = properties.get("contact") or {}
        hours = properties.get("opening_hours")
        # Unnamed places would otherwise show their street as if it were the pharmacy's name.
        street = properties.get("street")
        name = properties.get("name") or (f"Pharmacy, {street}" if street else "Pharmacy")
        # Geoapify occasionally carries a pharmacy category on diagnostic-lab POIs. They are not
        # useful pickup locations, so keep the recommendation list limited to retail pharmacies.
        if re.search(r"\b(diagnostic|laboratory|clinic|hospital)\b", name, flags=re.IGNORECASE):
            continue
        out.append({
            "id": f"geoapify:{properties.get('place_id') or index}",
            "name": name,
            "lat": plat,
            "lon": plon,
            "phone": contact.get("phone") or properties.get("phone"),
            "address": properties.get("formatted") or properties.get("address_line2"),
            "opening_hours_raw": hours,
            "open_now": _parse_open_now(hours) if isinstance(hours, str) else None,
            "rating": None,
            "rating_count": None,
        })
    return out


async def geocode_address(address: str) -> Optional[Dict[str, Any]]:
    """Resolve a user-entered address or landmark using Nominatim without an API key.

    Requests are cached and serialized at one per second to be considerate of the public
    Nominatim service. Returned labels are requested in English for the English-first UI.
    """
    global _LAST_NOMINATIM_REQUEST_AT
    normalized = " ".join(address.split())
    cache_key = normalized.casefold()
    hit = _GEOCODE_CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _GEOCODE_CACHE_TTL_S:
        return hit[1]

    async with _GEOCODE_LOCK:
        # Check the cache once more after waiting for another request to finish.
        hit = _GEOCODE_CACHE.get(cache_key)
        if hit and (time.time() - hit[0]) < _GEOCODE_CACHE_TTL_S:
            return hit[1]
        remaining = 1.0 - (time.time() - _LAST_NOMINATIM_REQUEST_AT)
        if remaining > 0:
            await asyncio.sleep(remaining)
        headers = {"User-Agent": settings.NOMINATIM_USER_AGENT, "Accept-Language": "en"}
        if settings.GEOAPIFY_API_KEY.strip():
            url = GEOAPIFY_GEOCODE_SEARCH_URL
            params = {"text": normalized, "limit": "1", "lang": "en", "filter": f"countrycode:{SEARCH_COUNTRY}",
                      "apiKey": settings.GEOAPIFY_API_KEY}
        else:
            url = NOMINATIM_SEARCH_URL
            params = {"q": normalized, "format": "jsonv2", "limit": "1", "addressdetails": "0", "accept-language": "en",
                      "countrycodes": SEARCH_COUNTRY}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params, headers=headers)
            _LAST_NOMINATIM_REQUEST_AT = time.time()
            response.raise_for_status()
            payload = response.json()

    if settings.GEOAPIFY_API_KEY.strip():
        results = payload.get("features", [])
        if not results:
            return None
        properties = results[0].get("properties") or {}
        coordinates = (results[0].get("geometry") or {}).get("coordinates") or []
        try:
            lat = float(properties.get("lat", coordinates[1]))
            lon = float(properties.get("lon", coordinates[0]))
        except (IndexError, TypeError, ValueError):
            return None
        result = {"lat": lat, "lon": lon, "display_name": properties.get("formatted") or normalized}
        _GEOCODE_CACHE[cache_key] = (time.time(), result)
        return result

    if not payload:
        return None
    first = payload[0]
    try:
        lat, lon = float(first["lat"]), float(first["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    result = {"lat": lat, "lon": lon, "display_name": first.get("display_name") or normalized}
    _GEOCODE_CACHE[cache_key] = (time.time(), result)
    return result


async def reverse_geocode(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Convert GPS coordinates (lat, lon) to a formatted address string."""
    cache_key = f"rev:{round(lat, 5)},{round(lon, 5)}"
    hit = _GEOCODE_CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _GEOCODE_CACHE_TTL_S:
        return hit[1]

    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT, "Accept-Language": "en"}
    try:
        if settings.GEOAPIFY_API_KEY.strip():
            params = {"lat": str(lat), "lon": str(lon), "lang": "en", "apiKey": settings.GEOAPIFY_API_KEY}
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(GEOAPIFY_REVERSE_GEOCODE_URL, params=params)
                res.raise_for_status()
                features = res.json().get("features", [])
                if features:
                    props = features[0].get("properties") or {}
                    formatted = props.get("formatted") or props.get("address_line1")
                    if formatted:
                        result = {"lat": lat, "lon": lon, "display_name": formatted}
                        _GEOCODE_CACHE[cache_key] = (time.time(), result)
                        return result
        # Fallback to Nominatim reverse geocode
        params = {"lat": str(lat), "lon": str(lon), "format": "jsonv2", "accept-language": "en"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(NOMINATIM_REVERSE_URL, params=params, headers=headers)
            res.raise_for_status()
            data = res.json()
            formatted = data.get("display_name")
            if formatted:
                result = {"lat": lat, "lon": lon, "display_name": formatted}
                _GEOCODE_CACHE[cache_key] = (time.time(), result)
                return result
    except Exception as e:
        print(f" [!] Reverse geocoding failed: {e}")

    fallback_name = f"Location ({lat:.4f}, {lon:.4f})"
    return {"lat": lat, "lon": lon, "display_name": fallback_name}


_AUTOCOMPLETE_CLIENT: Optional[httpx.AsyncClient] = None


def _autocomplete_client() -> httpx.AsyncClient:
    """One kept-alive connection for type-ahead: a new TLS handshake per keystroke pause adds about half a second."""
    global _AUTOCOMPLETE_CLIENT
    if _AUTOCOMPLETE_CLIENT is None or _AUTOCOMPLETE_CLIENT.is_closed:
        _AUTOCOMPLETE_CLIENT = httpx.AsyncClient(timeout=10.0)
    return _AUTOCOMPLETE_CLIENT


async def address_suggestions(query: str) -> List[Dict[str, Any]]:
    """Return real address and apartment suggestions for the picker dropdown."""
    normalized = " ".join(query.split())
    if len(normalized) < 3:
        return []
    if not settings.GEOAPIFY_API_KEY.strip():
        exact = await geocode_address(normalized)
        return [exact] if exact else []
    params = {"text": normalized, "limit": "7", "lang": "en", "filter": f"countrycode:{SEARCH_COUNTRY}",
              "apiKey": settings.GEOAPIFY_API_KEY}
    response = await _autocomplete_client().get(GEOAPIFY_AUTOCOMPLETE_URL, params=params)
    response.raise_for_status()
    features = response.json().get("features", [])
    suggestions = []
    for feature in features:
        properties = feature.get("properties") or {}
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        try:
            lat = float(properties.get("lat", coordinates[1]))
            lon = float(properties.get("lon", coordinates[0]))
        except (IndexError, TypeError, ValueError):
            continue
        label = properties.get("formatted") or properties.get("address_line1")
        if label:
            # line1 is the place or street, line2 the area and city: the dropdown shows them on two lines.
            suggestions.append({"display_name": label, "lat": lat, "lon": lon,
                                "line1": properties.get("address_line1") or label,
                                "line2": properties.get("address_line2") or ""})
    return suggestions


SANJEEVANI_PHARMACY_BRANCHES: List[Dict[str, Any]] = [
    {
        "id": "sanjeevani-reem",
        "name": "Sanjeevani Day Surgery Center, Al Reem Island",
        "counter": "Ground Floor Pharmacy, Gate 1 (Al Reem Island)",
        "address": "Shams Abu Dhabi, Al Reem Island, Abu Dhabi",
        "lat": 24.49479,
        "lon": 54.40824,
        "phone": "+971 2 614 8888",
        "open_hours": "08:00 - 22:00",
        "open_now": True,
    },
    {
        "id": "sanjeevani-najda",
        "name": "Sanjeevani Hospital, Al Najda Street",
        "counter": "Main Lobby Express Outpatient Pharmacy (24/7)",
        "address": "Al Najda St, Al Danah, Abu Dhabi",
        "lat": 24.47910,
        "lon": 54.38110,
        "phone": "+971 2 508 5555",
        "open_hours": "Open 24/7",
        "open_now": True,
    },
    {
        "id": "sanjeevani-bmc",
        "name": "Sanjeevani Medical City, MBZ City",
        "counter": "Central Outpatient & 24/7 Emergency Pharmacy, Ground Floor",
        "address": "Mohamed Bin Zayed City, Abu Dhabi",
        "lat": 24.35448,
        "lon": 54.53878,
        "phone": "+971 2 201 1111",
        "open_hours": "Open 24/7",
        "open_now": True,
    },
    {
        "id": "sanjeevani-bateen",
        "name": "Sanjeevani Oasis Medical Center, Al Bateen",
        "counter": "Al Bateen Clinic Outpatient Pharmacy",
        "address": "Al Bateen, Abu Dhabi",
        "lat": 24.45543,
        "lon": 54.34327,
        "phone": "+971 2 614 8888",
        "open_hours": "08:00 - 21:00",
        "open_now": True,
    },
    {
        "id": "sanjeevani-shahama",
        "name": "Sanjeevani Medical Centre, Deerfields Mall",
        "counter": "Deerfields Mall Level 2 Pharmacy Counter",
        "address": "Al Shahama, Abu Dhabi",
        "lat": 24.51670,
        "lon": 54.67380,
        "phone": "+971 2 501 0000",
        "open_hours": "09:00 - 22:00",
        "open_now": True,
    },
    {
        "id": "sanjeevani-dubai-szr",
        "name": "Sanjeevani Hospital for Advanced Surgery, Dubai",
        "counter": "Main Lobby Central Pharmacy (24/7)",
        "address": "Sheikh Zayed Road, Al Quoz 1, Dubai",
        "lat": 25.13220,
        "lon": 55.22120,
        "phone": "+971 4 407 0100",
        "open_hours": "Open 24/7",
        "open_now": True,
    },
    {
        "id": "sanjeevani-dubai-barsha",
        "name": "Sanjeevani Medical Centre, Barsha Heights",
        "counter": "Barsha Heights Ground Floor Pharmacy",
        "address": "Al Barsha Heights (Tecom), Dubai",
        "lat": 25.09712,
        "lon": 55.17758,
        "phone": "+971 4 429 8000",
        "open_hours": "08:00 - 22:00",
        "open_now": True,
    },
    {
        "id": "sanjeevani-sharjah",
        "name": "Sanjeevani Specialty Hospital, Sharjah",
        "counter": "Main Lobby Outpatient Pharmacy (24/7)",
        "address": "Al Fayha, Kuwait St, Sharjah",
        "lat": 25.35120,
        "lon": 55.40220,
        "phone": "+971 6 501 1111",
        "open_hours": "Open 24/7",
        "open_now": True,
    },
    {
        "id": "sanjeevani-alain",
        "name": "Sanjeevani Royal Hospital, Al Ain",
        "counter": "Central OPD Pharmacy Counter 2 (24/7)",
        "address": "Hamdan Bin Zayed St, Al Jimi, Al Ain",
        "lat": 24.24820,
        "lon": 55.73810,
        "phone": "+971 3 799 9999",
        "open_hours": "Open 24/7",
        "open_now": True,
    },
]


def recommend_sanjeevani_branches(lat: float, lon: float) -> List[Dict[str, Any]]:
    """Return all Sanjeevani Pharmacy pickup branches ranked strictly ascending by distance from lat/lon."""
    branches = []
    for b in SANJEEVANI_PHARMACY_BRANCHES:
        dist_m = round(haversine_m(lat, lon, b["lat"], b["lon"]))
        dist_km = round(dist_m / 1000.0, 2)
        branches.append({
            **b,
            "distance_m": dist_m,
            "distance_km": dist_km,
            "formatted_label": f"{b['name']} ({dist_km} km away)",
            "full_value": f"{b['name']} - {b['counter']}",
        })
    branches.sort(key=lambda x: x["distance_m"])
    if branches:
        branches[0]["is_nearest"] = True
    return branches


def _sample_pharmacies(lat: float, lon: float) -> List[Dict[str, Any]]:
    """Clearly-labelled SAMPLE pharmacies near the given point, used only when every public
    Overpass mirror is unreachable (they are rate-limited). Positioned around the user so the
    map/UI still demonstrates, but named 'Sample Pharmacy' so it is never mistaken for live data."""
    spots = [
        ("Sample Pharmacy — City Centre", 0.004, 0.003, "24/7", True),
        ("Sample Pharmacy — Al Noor", -0.006, 0.005, "Mo-Su 08:00-23:00", None),
        ("Sample Community Pharmacy", 0.008, -0.004, "Mo-Su 09:00-22:00", None),
        ("Sample Care Pharmacy", -0.009, -0.007, None, None),
        ("Sample Family Pharmacy", 0.012, 0.009, "Mo-Sa 09:00-21:00", None),
    ]
    out = []
    for i, (name, dlat, dlon, hrs, open_now) in enumerate(spots):
        out.append({
            "id": f"sample:{i}", "name": name, "lat": lat + dlat, "lon": lon + dlon,
            "phone": None, "address": "Sample data (Overpass unavailable)",
            "opening_hours_raw": hrs, "open_now": open_now if open_now is not None else _parse_open_now(hrs),
            "rating": None, "rating_count": None,
        })
    return out


def _rank(pharmacies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lower score = better. Distance is primary; open-now and good ratings pull a store up."""
    def score(p: Dict[str, Any]) -> float:
        s = (p.get("distance_m") or 0) / 1000.0            # km as the base cost
        if p.get("open_now") is True:
            s -= 1.5                                        # strong boost for open now
        elif p.get("open_now") is False:
            s += 2.0                                        # penalise closed
        if isinstance(p.get("rating"), (int, float)):
            s -= (p["rating"] / 5.0) * 1.0                 # up to 1 km-equivalent for a 5-star
        return s
    for p in pharmacies:
        p["score"] = round(score(p), 3)
    return sorted(pharmacies, key=lambda x: x["score"])


async def find_nearby_pharmacies(lat: float, lon: float, radius_m: Optional[int] = None) -> Dict[str, Any]:
    """Return ranked nearby pharmacies. Uses Foursquare (with ratings) if a key is set, else OSM."""
    radius_m = radius_m or settings.PHARMACY_SEARCH_RADIUS_M
    requested_radius_m = radius_m
    use_geoapify = bool(settings.GEOAPIFY_API_KEY.strip())
    use_fsq = bool(settings.FOURSQUARE_API_KEY.strip()) and not use_geoapify
    source = "geoapify" if use_geoapify else "foursquare" if use_fsq else "openstreetmap"

    cache_key = f"{round(lat, 3)},{round(lon, 3)},{radius_m},{source}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL_S:
        return {**hit[1], "cached": True}
    try:
        if use_geoapify:
            pharmacies = await _geoapify_pharmacies(lat, lon, radius_m)
        elif use_fsq:
            pharmacies = await _foursquare_pharmacies(lat, lon, radius_m)
        else:
            pharmacies = await _overpass_pharmacies(lat, lon, radius_m)
    except Exception:
        # If a configured business provider fails, try OSM; if OSM (or everything) fails, serve labelled sample data so
        # the UI still works during a public-Overpass outage/rate-limit.
        try:
            if use_geoapify or use_fsq:
                source = f"openstreetmap ({'Geoapify' if use_geoapify else 'Foursquare'} fallback)"
                pharmacies = await _overpass_pharmacies(lat, lon, radius_m)
            else:
                raise RuntimeError("overpass unavailable")
        except Exception:
            source = "offline-sample (live pharmacy service unavailable)"
            pharmacies = _sample_pharmacies(lat, lon)

    # A locality or rural address may genuinely have no pharmacy inside the user-selected radius.
    # With Geoapify we can still provide useful, real nearby choices by widening only the provider
    # search, while returning explicit metadata so the UI never implies they are inside the radius.
    expanded_search_m = None
    if not pharmacies and use_geoapify:
        expanded_search_m = max(20_000, requested_radius_m)
        try:
            pharmacies = await _geoapify_pharmacies(lat, lon, expanded_search_m)
            if pharmacies:
                source = f"geoapify (nearest results within {expanded_search_m // 1000} km)"
        except Exception:
            expanded_search_m = None
    if not pharmacies and not (use_geoapify or use_fsq):
        source = "offline-sample (no results from live service)"
        pharmacies = _sample_pharmacies(lat, lon)
    for p in pharmacies:
        p["distance_m"] = round(haversine_m(lat, lon, p["lat"], p["lon"]))
        p["distance_km"] = round(p["distance_m"] / 1000.0, 2)
    ranked = _rank(pharmacies)
    result = {
        "source": source,
        "ratings_available": use_fsq and "foursquare" in source,
        "origin": {"lat": lat, "lon": lon},
        "radius_m": requested_radius_m,
        "expanded_search_m": expanded_search_m,
        "count": len(ranked),
        "pharmacies": ranked,
        "cached": False,
    }
    if ranked:
        _CACHE[cache_key] = (time.time(), result)
    return result
