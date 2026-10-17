"""Tools DUET for Galaxy runs itself, not on the phone: the weather and web answers.

They pass the same coordinator as the phone's tools (they are readings: never held as a
change, retried once if they fail) and show on the phone's timeline like any other step.

- get_weather: Open-Meteo (open-meteo.com), free and keyless: the place is found with its
  geocoding service, then today's conditions and the forecast for the day asked.
- web_search: Gemma 4 26B through Google's API with Grounding with Google Search, asked for a
  short answer with the key facts. Verified on 16 Oct 2026: the API grounds Gemma 4 answers.
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

# WMO weather interpretation codes (open-meteo.com/en/docs), as a person would say them
WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with heavy hail"}


_sessions: Dict[int, tuple] = {}


async def _get_json(url: str, timeout: float = 6.0) -> Any:
    """GET a JSON document, on a connection kept open per event loop (a new TLS connection for each of the
    weather's two requests cost about a second, measured 17 Oct 2026)."""
    import aiohttp
    loop = asyncio.get_running_loop()
    held = _sessions.get(id(loop))
    if held is None or held[0] is not loop or held[1].closed:
        held = (loop, aiohttp.ClientSession())
        _sessions[id(loop)] = held
    async with held[1].get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
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


async def get_weather(args: Dict[str, Any], fetch: Callable[[str], Awaitable[Any]] = _get_json) -> Dict[str, Any]:
    place = str(args.get("place") or "").strip()
    if not place:
        return {"status": "error", "error": "no place given", "instruction": "ask the user which town or city"}
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


WEB_INSTRUCTIONS = ("Answer the question from the search results in at most two short sentences, as you would say "
                    "it aloud: the key facts only (numbers, names, dates, times). If the results do not answer it, "
                    "say so plainly. No lists, no markdown, no links.")


async def web_search(args: Dict[str, Any], generate=None) -> Dict[str, Any]:
    """A failed search comes back as "no_answer" (said honestly), not as an error the coordinator
    would retry: one search can take 15 s, and a second try would double the wait."""
    try:
        return await _web_search(args, generate)
    except Exception as exc:
        log.warning("web search failed: %s", exc)
        return {"status": "no_answer", "error": str(exc)[:160],
                "instruction": "tell the user you could not look it up just now"}


async def _web_search(args: Dict[str, Any], generate=None) -> Dict[str, Any]:
    question = str(args.get("question") or "").strip()
    if not question:
        return {"status": "error", "error": "no question given"}
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


SERVER_TOOLS: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
    "get_weather": get_weather,
    "web_search": web_search,
}
