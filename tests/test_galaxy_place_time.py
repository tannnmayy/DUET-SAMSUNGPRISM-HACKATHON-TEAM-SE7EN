"""DUET for Galaxy: where the user is, the time, how far, and times said aloud (from the S24 run of 22 Oct 2026).

- "Can you fetch my location?" and "how far is the Chennai airport from me?" have tools: the phone's GPS, named;
- "what time is it?" is read from the clock, "in Singapore" from that place's time zone, never from a web search;
- a web search knows today's date and time, and where the user is when the question is about "near me";
- the voice says "eleven oh five", not "eleven-zero five";
- "8am" written as one word is the morning; "I am at home" says nothing about the half of the day;
- a request that is partly beyond the fast mind (a calendar) goes to the cloud mind whole;
- a calendar event needs a time the user said, on a day they said.
"""

import asyncio
import datetime as dt
import json

from duet_voice.coordinator import Coordinator
from duet_voice.galaxy import fast, readings, server_tools
from duet_voice.galaxy.confirm import speakable
from duet_voice.galaxy.modes import MODES
from duet_voice.galaxy.tools import PhoneToolbox, days_said, next_occurrence

HERE = {"status": "ok", "latitude": 12.8165, "longitude": 80.0396, "accuracy_m": 20}
REVERSE = {"address": {"village": "Potheri", "city": "Chennai Metropolitan Area", "state": "Tamil Nadu"}}


def fake_fetch(answers):
    """A fetch that answers by the URL's start, and records what it was asked."""
    asked = []

    async def fetch(url, timeout=6.0, headers=None):
        asked.append(url)
        for start, answer in answers.items():
            if url.startswith(start):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError("unexpected url " + url)
    fetch.asked = asked
    return fetch


def ctx(here=HERE, now=dt.datetime(2026, 10, 22, 11, 5)):
    calls = []

    async def locate():
        calls.append(1)
        return here
    return {"now": now, "locate": locate, "calls": calls}


# --- where the user is ------------------------------------------------------------------------------------

def test_location_is_the_phones_gps_with_the_place_named():
    server_tools._named_places.clear()
    fetch = fake_fetch({server_tools.NOMINATIM + "/reverse": REVERSE})
    out = asyncio.run(server_tools.get_location({}, ctx=ctx(), fetch=fetch))
    assert out["status"] == "ok" and out["place"] == "Potheri, Chennai, Tamil Nadu"
    assert out["latitude"] == 12.8165 and out["source"] == "the phone's GPS"


def test_where_am_i_is_a_plain_reading_said_from_the_place():
    for words in ("Where am I?", "Where am I right now?", "Can you fetch my location?", "What's my current location?"):
        assert readings.which(words) == "get_location", words
    assert readings.which("Take me to my location's nearest mall") is None
    here = {"status": "ok", "latitude": 12.8165, "longitude": 80.0396, "place": "Potheri, Chennai, Tamil Nadu"}
    assert readings.answer("get_location", here, "where am i?") == "You're in Potheri, Chennai, Tamil Nadu."
    assert readings.answer("get_location", {"status": "ok", "latitude": 1, "longitude": 2}, "where am i?") is None


def test_a_phone_with_no_coordinates_is_an_error_not_a_crash():
    async def locate():
        return {"status": "ok", "tool": "get_location"}          # an app that answered without a fix
    out = asyncio.run(server_tools.get_location({}, ctx={"locate": locate}))
    assert out["status"] == "error"
    out = asyncio.run(server_tools.get_distance({"to": "airport"}, ctx={"locate": locate}))
    assert out["status"] == "error"


def test_a_phone_that_cannot_locate_is_said_plainly():
    out = asyncio.run(server_tools.get_location({}, ctx=ctx(here={"status": "needs_permission", "error": "Location"})))
    assert out["status"] == "needs_permission"
    assert asyncio.run(server_tools.get_location({}, ctx=None))["status"] == "error"


def test_the_toolbox_asks_the_phone_once_a_minute_and_passes_the_clock():
    sent = []

    async def send(tool, args, timeout):
        sent.append(tool)
        return json.dumps(HERE)
    box = PhoneToolbox(Coordinator(), MODES["assistant"]["tools"], send)
    assert asyncio.run(box.locate())["latitude"] == 12.8165
    asyncio.run(box.locate())
    assert sent == ["get_location"]
    assert isinstance(box.server_context()["now"], dt.datetime)


def test_an_app_without_location_is_an_error_not_a_crash():
    async def send(tool, args, timeout):
        raise RuntimeError("unknown tool")
    box = PhoneToolbox(Coordinator(), MODES["assistant"]["tools"], send)
    assert asyncio.run(box.locate())["status"] == "error"


# --- the time ---------------------------------------------------------------------------------------------

def test_the_time_here_comes_from_the_clock():
    out = asyncio.run(server_tools.get_time({}, ctx=ctx()))
    assert out["time"] == "11:05" and out["said"] == "11:05 in the morning"
    assert out["date"].startswith("Thursday 22 October")


def test_the_time_somewhere_else_comes_from_its_time_zone():
    fetch = fake_fetch({server_tools.GEOCODE: {"results": [{"name": "Singapore", "country": "Singapore",
                                                            "latitude": 1.29, "longitude": 103.85}]},
                        server_tools.FORECAST: {"utc_offset_seconds": 28800, "timezone": "Asia/Singapore"}})
    out = asyncio.run(server_tools.get_time({"place": "Singapore"}, ctx=ctx(), fetch=fetch))
    assert out["status"] == "ok" and out["utc_offset"] == "+08:00" and out["time_zone"] == "Asia/Singapore"
    utc = dt.datetime.now(dt.timezone.utc)
    want = (utc + dt.timedelta(hours=8)).strftime("%H:%M")
    assert out["time"] in (want, (utc + dt.timedelta(hours=8, minutes=1)).strftime("%H:%M"))


def test_what_time_is_it_is_a_plain_reading_here_or_in_a_named_place():
    assert readings.which("What time is it?") == "get_time"
    assert readings.which("But wait, isn't it afternoon now?") == "get_time"
    assert readings.which("What's the date today?") == "get_time"
    assert readings.which("What time is it at the moment?") == "get_time"
    assert readings.args_for("get_time", "What time is it at the moment?") == {}
    # 22 Oct 2026: London was read right (7:25) but said "half past seven", then "the exact time" went to a search
    for words, place in (("What time is it in London?", "London"), ("What time is it in Singapore?", "Singapore"),
                         ("What's the exact time in London right now?", "London"),
                         ("what's the current time in New York please", "New York")):
        assert readings.which(words) == "get_time", words
        assert readings.args_for("get_time", words) == {"place": place}, words
    assert readings.which("What time does the Apple event start in California?") is None
    reading = {"status": "ok", "where": "where the user is", "time": "11:05", "said": "11:05 in the morning",
               "date": "Thursday 22 October 2026"}
    assert readings.answer("get_time", reading, "what time is it?") == "It's 11:05 in the morning."
    assert readings.answer("get_time", reading, "what's the date today?") == "It's Thursday 22 October 2026."
    london = {"status": "ok", "where": "London, United Kingdom", "time": "07:25", "said": "7:25 in the morning",
              "date": "Thursday 22 October 2026"}
    assert readings.answer("get_time", london, "what time is it in london?") == "It's 7:25 in the morning in London."


# --- how far ----------------------------------------------------------------------------------------------

def test_how_far_starts_from_the_phone_and_goes_by_road():
    server_tools._named_places.clear()
    fetch = fake_fetch({server_tools.NOMINATIM + "/reverse": REVERSE,
                        server_tools.NOMINATIM + "/search": [{"lat": "12.9934", "lon": "80.1726",
                                                              "name": "Chennai International Airport"}],
                        server_tools.OSRM: {"routes": [{"distance": 27500, "duration": 1920}]}})
    out = asyncio.run(server_tools.get_distance({"to": "Chennai airport"}, ctx=ctx(), fetch=fetch))
    assert out["status"] == "ok" and out["to"] == "Chennai International Airport"
    assert out["by_road_km"] == 27.5 and out["drive_minutes"] == 32 and 15 < out["straight_line_km"] < 30
    assert out["from"] == "Potheri, Chennai, Tamil Nadu"
    assert any("viewbox=" in u for u in fetch.asked)          # a place near the user is preferred


def test_how_far_without_a_route_still_answers_in_a_straight_line():
    server_tools._named_places.clear()
    fetch = fake_fetch({server_tools.NOMINATIM + "/reverse": REVERSE,
                        server_tools.NOMINATIM + "/search": [{"lat": "12.9934", "lon": "80.1726", "name": "Airport"}],
                        server_tools.OSRM: TimeoutError("no route service")})
    out = asyncio.run(server_tools.get_distance({"to": "airport"}, ctx=ctx(), fetch=fetch))
    assert out["status"] == "ok" and "by_road_km" not in out and out["straight_line_km"] > 0


# --- the web search knows when and where ------------------------------------------------------------------

def test_a_web_search_carries_the_date_and_time_and_for_near_me_the_place():
    server_tools._named_places["12.817,80.040"] = "Potheri, Chennai, Tamil Nadu"
    asked, place = asyncio.run(server_tools.search_context("good dosa places near me", ctx()))
    assert "Thursday 22 October 2026, 11:05" in asked and "Potheri, Chennai" in asked and place.startswith("Potheri")
    plain, place = asyncio.run(server_tools.search_context("who won the match yesterday", ctx()))
    assert "22 October 2026" in plain and "Potheri" not in plain and place == ""
    seen = []

    async def generate(model, prompt, config, deadline_s=None):
        seen.append(prompt)
        raise RuntimeError("no network in tests")
    out = asyncio.run(server_tools.web_search({"question": "good dosa places near me"}, generate=generate, ctx=ctx()))
    assert out["status"] == "no_answer" and "good dosa places near Potheri, Chennai, Tamil Nadu" in seen[0]


def test_time_and_distance_questions_are_not_searched_directly():
    specs = {"web_search": {}}
    for q in ("what time is it in Singapore right now", "how far is the Chennai airport from me",
              "is it afternoon now", "What is the exact time in London right now?"):
        assert fast.web_question({"kind": "web", "question": q}, q, specs) == ""
    q = "when is the next Apple event"
    assert fast.web_question({"kind": "web", "question": q}, q, specs) == q


# --- the voice says times as people do --------------------------------------------------------------------

def test_times_are_spoken_in_words():
    assert speakable("Alarm set for 11:05 in the morning.") == "Alarm set for eleven oh five in the morning."
    assert speakable("It is 4:30 now.") == "It is four thirty now."
    assert speakable("at 10:00") == "at ten o'clock"
    assert speakable("11:05 PM") == "eleven oh five PM"
    assert speakable("Set for 8am") == "Set for 8 AM"
    assert speakable("Alarm set for 7 in the morning.") == "Alarm set for 7 in the morning."
    assert speakable("The score was 3:2") == "The score was 3:2"


# --- AM and PM --------------------------------------------------------------------------------------------

def test_8am_as_one_word_is_the_morning():
    now = dt.datetime(2026, 10, 22, 11, 14)
    assert next_occurrence("08:00", "set an 8am alarm for all of these", now) == "08:00"
    assert next_occurrence("08:00", "set an alarm at 7:30am", now) == "08:00"     # the half was said: kept
    assert next_occurrence("08:00", "I am at home, set an alarm for 8", now) == "20:00"   # "am" here is not AM


# --- the fast mind and a request it can only partly do ----------------------------------------------------

def test_a_calendar_request_with_an_alarm_goes_to_the_cloud_mind_whole():
    words = "Could you please add all of them on my calendar and set an 8am alarm for all of these?"
    assert "cannot do" in fast.vet_plan([("set_alarm", {"time": "08:00"})], words)
    assert fast.vet_plan([("set_alarm", {"time": "08:00"})], "set an alarm for 8 am") == ""


# --- calendar events need a time and a day the user said --------------------------------------------------

def _event_box(open_words, recent=""):
    sent = []

    async def send(tool, args, timeout):
        sent.append((tool, args))
        return json.dumps({"status": "ok", "event": args.get("title")})
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.heard(open_words)
    coord.turn_committed()
    box = PhoneToolbox(coord, MODES["assistant"]["tools"], send)
    box.recent_words = lambda: recent
    return box, sent


def test_an_event_without_a_time_is_asked_about_not_invented():
    box, sent = _event_box("add my exam on the 29th to the calendar")
    out = json.loads(asyncio.run(box.call("create_event", {"title": "Exam", "date": "2026-10-29"})))
    assert out["status"] == "not_executed" and "what time" in out["reason"] and not sent
    out = json.loads(asyncio.run(box.call("create_event", {"title": "Exam", "date": "2026-10-29", "time": "09:00"})))
    assert out["status"] == "not_executed" and "did not say 09:00" in out["reason"] and not sent


def test_an_event_on_a_day_nobody_said_is_sent_back():
    recent = ("I have an exam on 24th of this month. which is FLA and I have another exam on 29 which is computer "
              "network. And then on 31st, I have another exam which is for maths. They are all at 10 in the morning.")
    box, sent = _event_box("You didn't set the calendar for the three exams, did you?", recent)
    out = json.loads(asyncio.run(box.call("create_event", {"title": "Computer Network Exam", "date": "2026-10-22",
                                                           "time": "10:00"})))
    assert out["status"] == "not_executed" and "24, 29, 31" in out["reason"] and not sent
    out = json.loads(asyncio.run(box.call("create_event", {"title": "Computer Network Exam", "date": "2026-10-29",
                                                           "time": "10:00"})))
    assert out["status"] == "ok" and sent


def test_an_event_for_tomorrow_needs_no_day_number():
    box, sent = _event_box("add a dentist visit tomorrow at 4", "my exam is on the 29th")
    out = json.loads(asyncio.run(box.call("create_event", {"title": "Dentist", "date": "2026-10-23", "time": "16:00"})))
    assert out["status"] == "ok" and sent


def test_days_said_reads_ordinals_and_on_numbers_but_not_times():
    assert days_said("exam on 24th, another on 29 and then the 31st") == {24, 29, 31}
    assert days_said("set an alarm on 7:30 for 20 minutes") == set()
