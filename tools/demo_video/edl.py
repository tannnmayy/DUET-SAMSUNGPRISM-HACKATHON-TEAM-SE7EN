"""The edit: what is kept of each scenario, what is fast-forwarded, the zooms, captions, chips and the minds.

All times are on the S24 screen recording's clock (seconds). Cameras follow it by their audio offsets (sync.py).
Latencies are from DUET's own traces (results/galaxy/traces), measured from the end of the user's speech; each is
shown when the moment is heard or seen in the screen recording.
"""

FF_SPEED = 4.0

MINDS = {
    "fast": ("FAST MIND", "Gemma 4 E2B  ·  our laptop GPU", (34, 197, 142)),
    "cloud": ("CLOUD MIND", "Gemma 4 26B  ·  Google API", (91, 140, 255)),
    "search": ("CLOUD MIND", "Gemma 4 26B  +  Google Search", (91, 140, 255)),
    "reading": ("NO MODEL CALL", "answered from the phone's reading", (170, 178, 200)),
}

SCENARIOS = [
    {
        "n": 1, "title": "Navigation", "sub": "Live location, a change of mind, and Google Maps",
        "screen": "clip_205305", "camL": ("clip_205608", 5.811), "camR": ("clip_205935", 16.333),
        "t_in": 2.30, "t_out": 53.30, "hold": 0.0, "intro": True,
        "ff": [(23.30, 26.55), (40.30, 43.30)],
        "zooms": [("phone", 11.25, 14.60, 0.53, 1.85), ("phone", 44.10, 53.30, 0.87, 1.75)],
        "captions": [
            ("DUET", 2.58, 5.26, "Hi Naman, I'm DUET. What can I do for you?"),
            ("YOU", 6.14, 8.82, "Hey DUET, what's my current location right now?"),
            ("DUET", 11.38, 13.70, "You're in Potheri, Chennai, Tamil Nadu."),
            ("YOU", 14.20, 15.02, "Okay, that's good."),
            ("YOU", 15.70, 19.68, "DUET, I want to go to the Chennai railway station."),
            ("YOU", 20.26, 23.00, "No, never mind. I need to go to the Chennai airport."),
            ("DUET", 27.05, 27.95, "Sure."),
            ("DUET", 30.45, 32.23, "Starting directions to Chennai airport."),
            ("YOU", 33.72, 37.66, "Okay, thank you, DUET. But then this is via a car."),
            ("YOU", 38.02, 39.96, "I am going to go by walk."),
            ("DUET", 43.46, 44.90, "I can help you with that."),
            ("DUET", 47.41, 49.41, "Starting directions to Chennai airport."),
            ("DUET", 50.76, 52.88, "I've updated your directions to walking."),
        ],
        "chips": [
            ("L", "ok", 11.6, 22.6, "Location from the phone's own GPS"),
            ("L", "no", 23.0, 34.0, "Railway station: never sent to Maps"),
            ("R", "ok", 29.9, 41.0, "Only the airport reached Google Maps"),
            ("L", "info", 34.2, 46.5, "DUET keeps listening behind Maps"),
            ("R", "ok", 47.2, 53.3, "An action already done, changed: car to walking"),
        ],
        "minds": [
            {"t": 10.9, "mind": "reading", "lines": [(11.30, "Location read", "1.5 s")]},
            {"t": 26.45, "mind": "cloud", "lines": [(26.74, "“Sure.”", "2.5 s"), (29.80, "Maps started", "5.7 s")]},
            {"t": 43.30, "mind": "cloud", "lines": [(43.46, "“I can help…”", "2.4 s"), (47.00, "Walking route", "6.0 s")]},
        ],
    },
    {
        "n": 2, "title": "SmartThings home", "sub": "Nearly home: real devices, confirmed by the device",
        "screen": "clip_205438", "camL": ("clip_210606", 0.979), "camR": ("clip_210107", 49.383),
        "t_in": 7.40, "t_out": 46.45, "hold": 0.0,
        "ff": [(16.15, 19.10), (19.72, 27.45), (36.75, 40.85)],
        "zooms": [("phone", 27.60, 31.00, 0.34, 1.85), ("camL", 31.00, 34.20, None, 2.45), ("phone", 41.00, 45.40, 0.72, 1.85)],
        "captions": [
            ("YOU", 7.66, 10.68, "Hi DUET, I'm reaching my house in about 10 minutes."),
            ("YOU", 11.16, 13.40, "Could you please turn on the living room lights"),
            ("YOU", 13.40, 15.94, "and turn on my air conditioning?"),
            ("DUET", 19.29, 19.70, "Sure."),
            ("DUET", 27.68, 28.80, "Still working on it."),
            ("DUET", 29.24, 32.42, "The living room light is now on at 70 percent."),
            ("YOU", 32.42, 33.24, "Okay. Thank you, DUET."),
            ("YOU", 33.24, 36.56, "Could you also open my front door lock?"),
            ("DUET", 41.03, 44.21, "Sure. The front door lock is now unlocked."),
            ("YOU", 44.98, 46.16, "Okay. Thank you, DUET."),
        ],
        "chips": [
            ("L", "info", 11.3, 19.6, "Two devices, one request"),
            ("R", "ok", 27.8, 41.0, "Confirmed by SmartThings' own reply"),
            ("L", "info", 32.6, 41.0, "Talk over DUET at any time"),
            ("R", "ok", 41.5, 46.45, "Front door unlocked by the fast mind"),
        ],
        "minds": [
            {"t": 19.10, "mind": "cloud", "lines": [(19.29, "“Sure.”", "1.7 s"), (28.00, "Living room light on", "10.6 s"),
                                                     (28.30, "Air conditioner on", "10.9 s")]},
            {"t": 40.90, "mind": "fast", "lines": [(41.03, "“Sure.”", "3.1 s"), (41.40, "Front door unlocked", "3.4 s")]},
        ],
    },
    {
        "n": 3, "title": "Messages & alarms", "sub": "Two actions and a correction, in one breath",
        "screen": "clip_205520", "camL": ("clip_210748", 9.684), "camR": ("clip_210045", 13.639),
        "t_in": 4.30, "t_out": 30.90, "hold": 0.0,
        "ff": [],
        "zooms": [("phone", 19.60, 22.30, 0.60, 1.75), ("camR", 26.40, 28.65, None, 2.45), ("phone", 28.65, 30.90, 0.26, 1.80)],
        "captions": [
            ("YOU", 4.53, 6.71, "Could you please text Gupta Hostel"),
            ("YOU", 6.71, 8.45, "that I'll be there for dinner at 9"),
            ("YOU", 8.45, 11.97, "and also set an alarm for 8:50 today."),
            ("YOU", 12.29, 13.33, "No, no, no, wait, it's okay."),
            ("YOU", 13.59, 15.31, "Set an alarm for 8:30."),
            ("DUET", 19.26, 19.90, "Sure."),
            ("DUET", 20.56, 22.36, "Message sent to Gupta Hostel."),
            ("DUET", 22.36, 23.86, "Alarm set for 8:30 in the evening."),
        ],
        "chips": [
            ("L", "no", 12.6, 30.9, "8:50 never ran: you corrected it"),
            ("R", "ok", 20.4, 30.9, "Two actions from one sentence"),
            ("R", "info", 23.4, 30.9, "Proof: the text arrived, the alarm is in Clock"),
        ],
        "minds": [
            {"t": 19.00, "mind": "fast", "lines": [(19.26, "“Sure.”", "2.7 s"), (20.10, "Message sent", "3.6 s"),
                                                    (20.20, "Alarm set, 8:30 PM", "3.6 s"), (20.30, "Model calls", "1")]},
        ],
        # the other phone's missed-call notification shows a phone number: blurred (camera time, source pixels)
        "blurs": {"camR": [(40.15, (325, 175, 490, 335)), (40.60, (330, 140, 485, 305)), (41.60, (350, 85, 495, 235)),
                           (42.30, (350, 30, 505, 200))]},
    },
    {
        "n": 4, "title": "Battery & torch", "sub": "Reasons from the phone's own readings, then acts",
        "screen": "clip_205632", "camL": ("clip_210635", -2.301), "camR": ("clip_210028", 34.105),
        "t_in": 4.90, "t_out": 60.40, "hold": 0.0,
        "ff": [(10.75, 14.05), (15.35, 22.05), (47.10, 51.35), (56.00, 58.25)],
        "zooms": [("phone", 22.40, 27.60, 0.62, 1.70), ("phone", 39.90, 46.90, 0.46, 1.85), ("phone", 58.40, 60.40, 0.42, 1.85)],
        "captions": [
            ("YOU", 5.20, 7.96, "Could you check why is my battery draining so fast?"),
            ("YOU", 9.18, 10.40, "Is it the brightness?"),
            ("DUET", 14.21, 15.19, "Let me check."),
            ("DUET", 22.25, 25.89, "It's not the brightness, as you have adaptive brightness turned on,"),
            ("DUET", 26.01, 28.23, "and it's currently at about 57 percent."),
            ("DUET", 28.89, 30.79, "The main thing I see is that"),
            ("DUET", 30.79, 32.53, "you've spent quite a bit of time"),
            ("DUET", 32.53, 34.39, "on Instagram and making calls today."),
            ("YOU", 34.39, 36.51, "Could you set my brightness to 20%?"),
            ("YOU", 37.76, 39.76, "No, wait. 55%."),
            ("DUET", 42.99, 43.91, "Sure."),
            ("DUET", 44.74, 46.86, "Brightness set to 55 percent."),
            ("YOU", 51.51, 53.73, "Now, could you please turn the torch on?"),
            ("DUET", 58.44, 59.30, "Sure."),
        ],
        "chips": [
            ("L", "info", 14.3, 22.0, "The fast mind covers the wait"),
            ("R", "ok", 22.3, 34.5, "Answers from the phone's own readings"),
            ("L", "info", 34.5, 40.6, "Talk over it: DUET stops and listens"),
            ("L", "no", 40.8, 51.0, "20% held at the gate, then voided"),
            ("R", "ok", 43.4, 60.4, "55% and the torch: fast mind, under 3 s"),
        ],
        "minds": [
            {"t": 14.00, "mind": "cloud", "lines": [(14.21, "“Let me check.”", "2.5 s"), (16.20, "Battery and apps read", "4.6 s"),
                                                     (20.30, "Answer ready", "8.7 s")]},
            {"t": 39.20, "mind": "fast", "lines": [(39.20, "Brightness 20%", "held"), (41.00, "Voided by “No, wait”", "never ran")]},
            {"t": 42.90, "mind": "fast", "lines": [(42.99, "“Sure.”", "2.5 s"), (43.35, "Brightness 55%", "2.6 s")]},
            {"t": 58.30, "mind": "fast", "lines": [(58.44, "“Sure.”", "2.5 s"), (58.70, "Torch on", "2.7 s")]},
        ],
    },
    {
        "n": 5, "title": "Web search", "sub": "Grounded answers, and you can change the question",
        "screen": "clip_205637", "camL": None, "camR": ("clip_205546", 10.849),
        "t_in": 5.80, "t_out": 46.14, "hold": 0.45,
        "ff": [(9.25, 12.95), (18.30, 22.65), (26.10, 35.05)],
        "zooms": [("phone", 22.80, 26.05, 0.41, 1.85), ("phone", 35.30, 46.14, 0.70, 1.55)],
        "captions": [
            ("YOU", 5.98, 8.96, "Could you search and tell me when the next Apple event is?"),
            ("DUET", 13.12, 13.90, "One moment."),
            ("YOU", 14.87, 16.21, "Instead, could you tell me"),
            ("YOU", 16.21, 18.01, "when the latest iPhone was launched?"),
            ("DUET", 22.82, 25.92, "Let me check when the latest iPhone was launched for you."),
            ("DUET", 35.21, 38.91, "The latest iPhone models launched in September 2026"),
            ("DUET", 38.91, 42.93, "are the iPhone 18 Pro, iPhone 18 Pro Max,"),
            ("DUET", 43.13, 46.15, "and the first foldable iPhone, the iPhone Duo."),
        ],
        "chips": [
            ("R", "no", 15.0, 22.6, "Old question dropped mid-answer"),
            ("R", "info", 23.0, 35.0, "The fast mind talks while the cloud searches"),
            ("R", "ok", 35.3, 46.14, "One grounded Google Search"),
        ],
        "minds": [
            {"t": 12.90, "mind": "search", "lines": [(13.12, "“One moment.”", "2.7 s"), (17.10, "Apple answer found", "7.0 s"),
                                                      (18.10, "Dropped: new question", "")]},
            {"t": 22.70, "mind": "search", "lines": [(22.82, "“Let me check…”", "3.7 s"), (35.00, "Answer ready", "15.9 s")]},
        ],
        # the left slot (no second camera): what DUET did, step by step
        "steps": [
            (6.0, 15.0, "info", "Heard: “When is the next Apple event?”", "no", "Dropped: you changed the question"),
            (14.9, None, "info", "Heard: “Instead… when was the latest iPhone launched?”", None, None),
            (22.8, 35.0, "hold", "Searching the web while the fast mind talks", "ok", "One grounded Google Search call"),
            (35.2, None, "ok", "Answered from the search: 15.9 s", None, None),
        ],
    },
]
