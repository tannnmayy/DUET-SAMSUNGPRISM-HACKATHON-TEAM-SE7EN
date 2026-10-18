"""The weather on the phone: Open-Meteo, with the web answer when it fails (no network in these tests)."""

import asyncio

from duet_voice.galaxy import server_tools


async def fake_fetch(url):
    if "geocoding" in url:
        return {"results": [{"name": "Chennai", "admin1": "Tamil Nadu", "country": "India", "latitude": 13.08, "longitude": 80.27}]}
    return {"current": {"time": "2026-10-17T20:00", "temperature_2m": 27.6, "apparent_temperature": 31.2, "weather_code": 61},
            "daily": {"time": ["2026-10-17", "2026-10-18"], "weather_code": [63, 3], "temperature_2m_max": [30.4, 31.1],
                      "temperature_2m_min": [24.8, 25.0], "precipitation_probability_max": [80, 20]}}


async def broken_fetch(url):
    raise RuntimeError("503, message='Service Unavailable'")


def test_the_weather_today_and_tomorrow():
    r = asyncio.run(server_tools.get_weather({"place": "Chennai"}, fetch=fake_fetch))
    assert r["status"] == "ok" and r["place"].startswith("Chennai") and r["conditions"] == "rain"
    assert r["now"] == {"temperature_c": 28, "feels_like_c": 31, "conditions": "light rain"}
    r = asyncio.run(server_tools.get_weather({"place": "Chennai", "day": "tomorrow"}, fetch=fake_fetch))
    assert r["day"] == "2026-10-18" and r["high_c"] == 31 and "now" not in r


def test_when_the_weather_service_fails_the_web_answers():
    asked = []

    async def web(args):
        asked.append(args["question"])
        return {"status": "ok", "answer": "Light rain, about 28 degrees.", "sources": ["weather.com"]}
    r = asyncio.run(server_tools.get_weather({"place": "Chennai"}, fetch=broken_fetch, fallback=web))
    assert r["status"] == "ok" and r["forecast"] == "Light rain, about 28 degrees." and "unavailable" in r["source"]
    assert "Chennai" in asked[0]

    async def no_web(args):
        return {"status": "no_answer"}
    r = asyncio.run(server_tools.get_weather({"place": "Chennai"}, fetch=broken_fetch, fallback=no_web))
    assert r["status"] == "error" and "could not get the weather" in r["instruction"]
