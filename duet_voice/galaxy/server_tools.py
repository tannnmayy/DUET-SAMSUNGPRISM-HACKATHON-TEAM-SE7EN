"""Tools DUET for Galaxy runs itself, not on the phone: the weather, web answers, the time, where the user is,
and how far a place is.

They pass the same coordinator as the phone's tools (they are readings: never held as a
change, retried once if they fail) and show on the phone's timeline like any other step.

- get_weather: Open-Meteo (open-meteo.com), free and keyless: the place is found with its
  geocoding service, then today's conditions and the forecast for the day asked.
- web_search: Gemma 4 26B through Google's API with Grounding with Google Search, asked for a
  short answer with the key facts. Verified on 16 Oct 2026: the API grounds Gemma 4 answers.
  The question carries today's date and time, and where the user is when it asks about "near me".
- get_time: this machine's clock (the user's own time), or a place's time from Open-Meteo's time zone.
- get_location: the phone's GPS (the app's get_location), named with OpenStreetMap's Nominatim.
- get_distance: from the phone's GPS (or a named place) to a place, by road (OSRM), else in a straight line.

A tool that takes `ctx` is given {"now": datetime, "locate": async () -> the phone's GPS reading}
by the toolbox (tools.PhoneToolbox.server_context).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlencode

log = logging.getLogger("duet.galaxy.server_tools")

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
NOMINATIM = "https://nominatim.openstreetmap.org"
OSRM = "https://router.project-osrm.org/route/v1/driving"
# Nominatim's usage policy asks every client to name itself
OSM_HEADERS = {"User-Agent": "DUET-for-Galaxy/1.0 (Samsung PRISM GenAI Hackathon prototype)"}

# WMO weather interpretation codes (open-meteo.com/en/docs), as a person would say them
WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with heavy hail"}


_sessions: Dict[int, tuple] = {}


async def _get_json(url: str, timeout: float = 6.0, headers: Optional[Dict[str, str]] = None) -> Any:
    """GET a JSON document, on a connection kept open per event loop (a new TLS connection for each of the
    weather's two requests cost about a second, measured 17 Oct 2026)."""
    import aiohttp
    loop = asyncio.get_running_loop()
    held = _sessions.get(id(loop))
    if held is None or held[0] is not loop or held[1].closed:
        held = (loop, aiohttp.ClientSession())
        _sessions[id(loop)] = held
    async with held[1].get(url, timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as r:
        r.raise_for_status()
        return await r.json()


_places: Dict[str, Dict[str, Any]] = {}     # a town's coordinates do not change: looked up once


def _day_index(day: str, today: dt.date) -> Optional[int]:
    """'today' / 'tomorrow' / a weekday / YYYY-MM-DD -> days from today (0-6), or None."""
    d = (day or "today").strip().lower()
    if d in ("", "today", "now", "tonight"):
        return 0
    if d == "tomorrow":
        return 1
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    if m:
        delta = (dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) - today).days
        return delta if 0 <= delta <= 6 else None
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(days):
        if name in d:
            return (i - today.weekday()) % 7
    return None


async def get_weather(args: Dict[str, Any], fetch: Callable[[str], Awaitable[Any]] = _get_json,
                      fallback=None) -> Dict[str, Any]:
    """Open-Meteo; if it cannot be reached or fails (17 Oct 2026: 503 for a while), the web answer
    (Gemma with Google Search grounding) instead, so the weather does not depend on one free service."""
    place = str(args.get("place") or "").strip()
    if not place:
        return {"status": "error", "error": "no place given", "instruction": "ask the user which town or city"}
    try:
        return await _open_meteo(place, args, fetch)
    except Exception as exc:
        log.warning("Open-Meteo failed (%s); asking the web instead", str(exc)[:120])
        day = str(args.get("day") or "today")
        web = await (fallback or web_search)({"question": "What is the weather forecast in %s for %s: conditions, "
                                                          "temperature, chance of rain?" % (place, day)})
        if web.get("status") == "ok":
            return {"status": "ok", "place": place, "day": day, "forecast": web.get("answer"),
                    "source": "a web search (the weather service was unavailable)", "sources": web.get("sources", [])}
        return {"status": "error", "error": "the weather service is unavailable: %s" % str(exc)[:120],
                "instruction": "tell the user you could not get the weather just now"}


async def _open_meteo(place: str, args: Dict[str, Any], fetch) -> Dict[str, Any]:
    key = " ".join(place.lower().split())
    p = _places.get(key) if fetch is _get_json else None
    if p is None:
        geo = await fetch(GEOCODE + "?" + urlencode({"name": place, "count": 1, "language": "en", "format": "json"}))
        hits = (geo or {}).get("results") or []
        if not hits:
            return {"status": "error", "error": "no place called %s was found" % place}
        p = hits[0]
        if fetch is _get_json:
            _places[key] = p
    q = {"latitude": p["latitude"], "longitude": p["longitude"], "timezone": "auto", "forecast_days": 7,
         "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
         "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"}
    fc = await fetch(FORECAST + "?" + urlencode(q))
    daily, cur = fc.get("daily") or {}, fc.get("current") or {}
    dates = daily.get("time") or []
    # "today" is the place's own date (its current local time), which can be a day after the
    # forecast's first day just past midnight there
    local_today = str(cur.get("time") or "")[:10]
    start = dates.index(local_today) if local_today in dates else 0
    today = dt.date.fromisoformat(dates[start]) if dates else dt.date.today()
    offset = _day_index(str(args.get("day") or "today"), today)
    i = None if offset is None else start + offset
    if i is None or i >= len(dates):
        return {"status": "error", "error": "the forecast covers only the next 6 days"}
    out: Dict[str, Any] = {
        "status": "ok", "place": ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x),
        "day": dates[i], "conditions": WMO.get(daily["weather_code"][i], "mixed"),
        "high_c": round(daily["temperature_2m_max"][i]), "low_c": round(daily["temperature_2m_min"][i]),
        "chance_of_rain_percent": daily.get("precipitation_probability_max", [None] * 7)[i],
        "source": "Open-Meteo"}
    if offset == 0 and cur:
        out["now"] = {"temperature_c": round(cur.get("temperature_2m", 0)),
                      "feels_like_c": round(cur.get("apparent_temperature", 0)),
                      "conditions": WMO.get(cur.get("weather_code"), "mixed")}
    return out


WEB_INSTRUCTIONS = ("Search Google for the question first, every time; never say you cannot search or do not know "
                    "where the user is (the context says when and where they are). "
                    "Answer the question from the search results in at most two short sentences, as you would say "
                    "it aloud: the key facts only (numbers, names, dates, times). If the results do not answer it, "
                    "say so plainly. No lists, no markdown, no links.")


# a question about the user's surroundings: the search is told where they are
NEAR_ME = re.compile(r"\b(near (me|here|us)|nearby|nearest|around (me|here)|from (me|here)|close to (me|here)|"
                     r"in my (area|city|town)|where i am|my location|local)\b", re.I)


async def search_context(question: str, ctx: Optional[Dict[str, Any]]) -> tuple:
    """What the search needs that the question does not say: today's date and time here (22 Oct 2026: "Is it
    afternoon now?" was answered "5:48 AM" from a search that did not know the user's time zone), and, for a
    question about their surroundings, where they are. Returns (the context lines, the place's name or '')."""
    now = (ctx or {}).get("now") or dt.datetime.now()
    local = now.astimezone() if now.tzinfo is None else now
    utc = local.astimezone(dt.timezone.utc)
    # UTC said outright: "the exact time in London?" was answered 6:25, the user's time less 5:30, called British
    # Summer Time (22 Oct 2026; London was 7:25)
    lines = ["For context: it is now %s, %s (%s) where the user is, which is %s UTC." % (
        local.strftime("%A %d %B %Y"), local.strftime("%H:%M"), local.strftime("%Z %z").strip(), utc.strftime("%H:%M"))]
    place = ""
    if ctx and NEAR_ME.search(question or ""):
        try:
            here = await asyncio.wait_for(get_location({}, ctx=ctx), 6.0)
        except Exception:  # the search goes on without it
            here = {}
        if here.get("status") == "ok":
            place = here.get("place") or ""
            lines.append("The user is in %s (latitude %.4f, longitude %.4f)." % (
                place or "an unnamed place", here["latitude"], here["longitude"]))
    return " ".join(lines), place


async def web_search(args: Dict[str, Any], generate=None, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A failed search comes back as "no_answer" (said honestly), not as an error the coordinator
    would retry: one search can take 15 s, and a second try would double the wait."""
    try:
        return await _web_search(args, generate, ctx)
    except Exception as exc:
        log.warning("web search failed: %s", exc)
        return {"status": "no_answer", "error": str(exc)[:160],
                "instruction": "tell the user you could not look it up just now"}


async def _web_search(args: Dict[str, Any], generate=None, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    question = str(args.get("question") or "").strip()
    if not question:
        return {"status": "error", "error": "no question given"}
    context, place = await search_context(question, ctx)
    if place:   # "restaurants near me" is searched as "restaurants near Potheri, Chennai, Tamil Nadu"
        question = NEAR_ME.sub("near " + place, question)
    question = question + "\n\n" + context
    from google.genai import types
    from .. import gemma_api
    from ..config import CONFIG
    model = gemma_api.resolved("thinker", CONFIG.thinker_model)
    config = types.GenerateContentConfig(
        system_instruction=WEB_INSTRUCTIONS, tools=[types.Tool(google_search=types.GoogleSearch())],
        **gemma_api.sampling(model), **({"thinking_config": gemma_api.thinking_config(model, "minimal")}))
    resp = await (generate or gemma_api.generate)(model, question, config, deadline_s=20.0)
    parts = (resp.candidates[0].content.parts or []) if resp.candidates else []
    answer = " ".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)).strip()
    sources = []
    gm = getattr(resp.candidates[0], "grounding_metadata", None) if resp.candidates else None
    for chunk in (getattr(gm, "grounding_chunks", None) or [])[:3]:
        web = getattr(chunk, "web", None)
        title = getattr(web, "title", None) if web else None
        if title:
            sources.append(title)
    if not answer:
        return {"status": "no_answer", "error": "the search returned nothing usable",
                "instruction": "tell the user you could not find it"}
    return {"status": "ok", "answer": re.sub(r"[*#`]+", "", answer), "sources": sources}


# --- where the user is, the time, and how far a place is -------------------------------------------------------

_named_places: Dict[str, str] = {}      # rounded coordinates -> a place name: looked up once


def _short_place(address: Dict[str, Any]) -> str:
    """Nominatim's address -> 'Potheri, Chennai, Tamil Nadu'."""
    local = next((address.get(k) for k in ("suburb", "neighbourhood", "village", "hamlet", "quarter", "town")
                  if address.get(k)), "")
    city = next((address.get(k) for k in ("city", "town", "county", "state_district") if address.get(k)), "")
    city = re.sub(r"\s+Metropolitan Area$", "", str(city))
    parts = [p for p in (local, city, address.get("state")) if p]
    return ", ".join(dict.fromkeys(parts))


async def _place_name(lat: float, lon: float, fetch=_get_json) -> str:
    key = "%.3f,%.3f" % (lat, lon)
    if key in _named_places:
        return _named_places[key]
    try:
        r = await fetch(NOMINATIM + "/reverse?" + urlencode({"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14}),
                        headers=OSM_HEADERS)
        name = _short_place((r or {}).get("address") or {}) or str((r or {}).get("display_name") or "")[:80]
    except Exception as exc:  # the coordinates still answer
        log.warning("reverse geocoding failed: %s", str(exc)[:120])
        return ""
    _named_places[key] = name
    return name


async def get_location(args: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None, fetch=_get_json) -> Dict[str, Any]:
    """The phone's GPS (the app's own get_location), with the place's name. 22 Oct 2026: "Can you fetch my
    location?" had no tool at all, so the cloud mind read the battery instead."""
    locate = (ctx or {}).get("locate")
    if locate is None:
        return {"status": "error", "error": "the phone cannot be asked for its location here"}
    here = await locate()
    if not isinstance(here, dict) or here.get("status") != "ok":
        return here if isinstance(here, dict) else {"status": "error", "error": "no location from the phone"}
    if here.get("latitude") is None or here.get("longitude") is None:   # an app that answered without a fix
        return {"status": "error", "error": "the phone gave no coordinates",
                "instruction": "say you could not get their location just now"}
    lat, lon = float(here["latitude"]), float(here["longitude"])
    out = {"status": "ok", "latitude": round(lat, 5), "longitude": round(lon, 5), "source": "the phone's GPS"}
    if here.get("accuracy_m") is not None:
        out["accuracy_m"] = here["accuracy_m"]
    name = await _place_name(lat, lon, fetch)
    if name:
        out["place"] = name
    return out


HERE = re.compile(r"^\s*(here|now|local|my (location|place|city|town)|where i am|current location|this place)?\s*$", re.I)


async def get_time(args: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None, fetch=_get_json) -> Dict[str, Any]:
    """The time now: here (this machine's clock, which is the user's) or in a named place (its time zone from
    Open-Meteo). 22 Oct 2026: "What time is it in Singapore?" went to a web search that "did not provide" it."""
    from .confirm import spoken_time
    place = str(args.get("place") or "").strip()
    if HERE.match(place):
        now = (ctx or {}).get("now") or dt.datetime.now()
        local = now if now.tzinfo else now.astimezone()
        return {"status": "ok", "where": "where the user is", "time": local.strftime("%H:%M"),
                "said": spoken_time(local.strftime("%H:%M")), "date": local.strftime("%A %d %B %Y"),
                "utc_offset": local.strftime("%z")}
    geo = await fetch(GEOCODE + "?" + urlencode({"name": place, "count": 1, "language": "en", "format": "json"}))
    hits = (geo or {}).get("results") or []
    if not hits:
        return {"status": "error", "error": "no place called %s was found" % place}
    p = hits[0]
    fc = await fetch(FORECAST + "?" + urlencode({"latitude": p["latitude"], "longitude": p["longitude"],
                                                 "timezone": "auto", "current": "temperature_2m"}))
    offset = dt.timedelta(seconds=int((fc or {}).get("utc_offset_seconds") or 0))
    local = dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(offset))
    sign = "+" if offset >= dt.timedelta(0) else "-"
    hours, rest = divmod(abs(int(offset.total_seconds())) // 60, 60)
    return {"status": "ok", "where": ", ".join(x for x in (p.get("name"), p.get("country")) if x),
            "time": local.strftime("%H:%M"), "said": spoken_time(local.strftime("%H:%M")),
            "date": local.strftime("%A %d %B %Y"), "utc_offset": "%s%02d:%02d" % (sign, hours, rest),
            "time_zone": fc.get("timezone") or p.get("timezone")}


def _straight_km(a: tuple, b: tuple) -> float:
    import math
    (la1, lo1), (la2, lo2) = [(math.radians(x), math.radians(y)) for x, y in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


async def _find_place(name: str, near: Optional[tuple], fetch=_get_json, city: str = "") -> Optional[Dict[str, Any]]:
    """A place by name: first near `near` (lat, lon), then in the user's city, then anywhere. 22 Oct 2026: "ECR beach"
    searched anywhere was "ECR Coatings", in New Zealand; near the user it is the beach on the East Coast Road."""
    tries = []
    if near:
        lat, lon = near
        tries.append({"q": name, "viewbox": "%.3f,%.3f,%.3f,%.3f" % (lon - 1.5, lat + 1.5, lon + 1.5, lat - 1.5),
                      "bounded": 1})
    if city and city.lower() not in name.lower():
        tries.append({"q": "%s, %s" % (name, city)})
    tries.append({"q": name})
    for q in tries:
        hits = await fetch(NOMINATIM + "/search?" + urlencode({**q, "format": "jsonv2", "limit": 1}), headers=OSM_HEADERS)
        if hits:
            return hits[0]
    return None


async def get_distance(args: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None, fetch=_get_json) -> Dict[str, Any]:
    """How far a place is, from the phone's GPS (or from a named place), by road when a route is found.
    22 Oct 2026: "How far is the Chennai airport from me?" had no location to start from."""
    to = str(args.get("to") or args.get("place") or "").strip()
    if not to:
        return {"status": "error", "error": "no place given", "instruction": "ask the user where to"}
    start = str(args.get("from") or "").strip()
    if start and not HERE.match(start):
        hit = await _find_place(start, None, fetch)
        if not hit:
            return {"status": "error", "error": "no place called %s was found" % start}
        origin, origin_name = (float(hit["lat"]), float(hit["lon"])), str(hit.get("name") or start)
    else:
        here = await get_location({}, ctx=ctx, fetch=fetch)
        if here.get("status") != "ok":
            return {**here, "instruction": "say you could not get their location, and what they can do"}
        origin, origin_name = (here["latitude"], here["longitude"]), here.get("place") or "where you are"
    city = origin_name.split(", ")[1] if origin_name.count(", ") >= 1 else ""
    hit = await _find_place(to, origin, fetch, city)
    if not hit:
        return {"status": "error", "error": "no place called %s was found" % to}
    dest = (float(hit["lat"]), float(hit["lon"]))
    dest_name = str(hit.get("name") or "").strip() or str(hit.get("display_name") or to).split(",")[0]
    out: Dict[str, Any] = {"status": "ok", "from": origin_name, "to": dest_name,
                           "found": ", ".join(str(hit.get("display_name") or "").split(", ")[:4]),
                           "straight_line_km": round(_straight_km(origin, dest), 1), "source": "OpenStreetMap"}
    if out["straight_line_km"] > 1000:
        out["note"] = "this place is far away: if it is not what the user meant, say what was found and ask"
    try:
        r = await fetch("%s/%.5f,%.5f;%.5f,%.5f?overview=false" % (OSRM, origin[1], origin[0], dest[1], dest[0]),
                        timeout=5.0)
        route = ((r or {}).get("routes") or [None])[0]
        if route:
            out["by_road_km"] = round(route["distance"] / 1000.0, 1)
            out["drive_minutes"] = int(round(route["duration"] / 60.0))
    except Exception as exc:  # the straight line still answers
        log.warning("route lookup failed: %s", str(exc)[:120])
    return out


SERVER_TOOLS: Dict[str, Callable[..., Awaitable[Dict[str, Any]]]] = {
    "get_weather": get_weather,
    "web_search": web_search,
    "get_time": get_time,
    "get_location": get_location,
    "get_distance": get_distance,
}
