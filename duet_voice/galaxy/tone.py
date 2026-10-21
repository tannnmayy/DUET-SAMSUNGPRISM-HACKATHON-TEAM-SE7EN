"""How the user said it: the voice, read next to the words.

Every user speech segment is measured the moment it ends, from the audio DUET already keeps (AudioTape), in a
worker thread, while Whisper transcribes it. The measurements of a request's segments are then read together with
its words into a Reading (urgent, unsure, distressed, frustrated, a raised voice), which the coordinator's hold, the
toolbox, both minds, the voice and the timeline all use.

What the cues rest on (measured on 21 Oct 2026; docs/ROUND2_TONE_PLAN.md):
- Vocal effort. CREMA-D actors, each clip brought to one level as the phone's gain control does, against the same
  actor's neutral take: angry speech was brighter (energy above 1 kHz against below) by 18.9 dB at the median and
  higher by 14 semitones; its loudness rose by only 2.3 dB. Two of the three cues (brighter, higher, louder), each
  against the speaker's own earlier segments, marked 9 of 10 angry takes, 7 of 10 fearful, 0 of 10 sad and 0 of 10
  neutral, and none of the 13 plain lines from the S24 recordings or the 40 plain Kokoro lines.
- Emotion. emotion2vec-web-distill (a 9.7 MB ONNX distillation of emotion2vec+ large, about 10 ms a segment on the
  CPU) caught 19 of 30 angry, fearful or sad CREMA-D clips at a negative score of 0.6 or more, with 1 false alarm in
  20 calm ones. It reads quiet audio as sad and loud audio as angry, and one synthetic voice's plain lines as sad, so
  it gets DUET's audio as it arrives and only counts together with distress words. (SenseVoice-Small, tried first,
  answered "unknown" on 49 of 50 English clips.)
- Pitch at the end of an utterance. Against each speaker's usual ending, a rise of 4 semitones flagged 6 of 29 plain
  statements and found only 8 of 29 rises added to them; plain statements in the S24 recordings often end rising.
  So it is used only with DUET_TONE_PITCH=1. Unsure rests on the words (a hedge next to the value) and the voice's
  timing (a long pause before the value).

Tone is advisory (rule 5 of docs/ROUND2_PLAN.md): it may hold an action longer, ask before one, change how DUET speaks,
or stop DUET's own voice. It never runs an action and never cancels one on its own; an action still needs words.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("duet.galaxy.tone")

SR = 16000
FRAME = 320                    # 20 ms

EMOTION_REPO = "thomashallock/emotion2vec-web-distill"
EMOTION_REVISION = "566a831be26e64f7e641241c26154793b7dd1428"     # pinned: the files measured on 21 Oct 2026
EMOTION_FILE = "distill-student-v4.fused.onnx"
EMOTION_SHA256 = "136b83ee1cbf16b102b341398534604a7f8ddadff1f07b50bc76739fa04a3795"
EMOTION_WINDOW = 48000          # the model reads 3 s of 16 kHz audio
# the model's 18 poses, and how each maps onto the teacher's classes (its distill-anchor-mix.json)
POSES = ["calm", "content", "warm", "happy", "amused", "excited", "surprised", "smug", "confused", "skeptical",
         "annoyed", "disgusted", "afraid", "angry", "tense", "sad", "weary", "bored"]
POSE_TEMPERATURE = 0.0375
_MIX = {"calm": {"neutral": 1}, "content": {"happy": .5, "neutral": .5}, "warm": {"happy": .65, "neutral": .35},
        "happy": {"happy": 1}, "amused": {"happy": .75, "surprised": .25}, "excited": {"happy": .6, "surprised": .4},
        "surprised": {"surprised": 1}, "smug": {"happy": .6, "disgusted": .25, "neutral": .15},
        "confused": {"surprised": .5, "neutral": .5}, "skeptical": {"disgusted": .4, "neutral": .4, "surprised": .2},
        "annoyed": {"angry": .55, "disgusted": .45}, "disgusted": {"disgusted": 1}, "afraid": {"fearful": 1},
        "angry": {"angry": 1}, "tense": {"angry": .5, "fearful": .5}, "sad": {"sad": 1},
        "weary": {"sad": .6, "neutral": .4}, "bored": {"neutral": .65, "sad": .35}}
CLASSES = ["angry", "disgusted", "fearful", "happy", "neutral", "sad", "surprised"]

# thresholds (from the measurements above)
EFFORT_TILT_DB = 6.0            # brighter than the speaker's usual
EFFORT_PITCH_ST = 4.0           # higher than the speaker's usual
EFFORT_LOUD_DB = 6.0            # louder than the speaker's usual (often flattened by the phone's gain control)
LOUD_ALONE_DB = 8.0             # loud enough to count on its own
NEGATIVE_STRONG = 0.6           # emotion model: angry + fearful + sad
NEGATIVE_WITH_EFFORT = 0.4
FILLER_PAUSE_S = 0.6            # "set an alarm for, um, ... 4:30": a pause after a filler, before the value
MIDPHRASE_PAUSE_S = 1.0         # "set an alarm for ... 4:30": a long pause in the middle of the phrase
PITCH_RISE_ST = 4.0             # terminal rise over the speaker's usual ending (only with DUET_TONE_PITCH=1)
HEDGE_GAP_WORDS = 3             # a hedge counts within this many words of the value


def enabled() -> bool:
    return os.environ.get("DUET_TONE", "1") == "1"


def pitch_enabled() -> bool:
    return os.environ.get("DUET_TONE_PITCH", "0") == "1"


# --- measuring one segment -----------------------------------------------------------------------------------

@dataclass
class Features:
    """One user speech segment, as measured."""
    on: float = 0.0              # wall-clock time of its first speech frame
    off: float = 0.0             # ... and of the end of its last one
    speech_s: float = 0.0        # seconds of speech in it
    loud_db: float = float("nan")          # 90th percentile of the speech frames' level
    tilt_db: float = float("nan")          # median energy at 1-4 kHz against 80 Hz-1 kHz, in dB
    f0_hz: float = float("nan")            # median pitch of the voiced frames
    longest_pause_s: float = 0.0           # the longest silence inside the segment
    emotion: Optional[Dict[str, float]] = None   # class probabilities, when the model is there
    pose: str = ""                          # the model's strongest pose ("calm", "sad", ...)
    end_rise_st: float = float("nan")       # terminal pitch rise (only with DUET_TONE_PITCH=1)
    delta: Dict[str, float] = field(default_factory=dict)   # against the speaker's usual, when it was measured
    words: str = ""                         # its transcript, attached when it arrives
    worded: bool = False                    # a transcript arrived for it
    ended: float = 0.0                      # wall-clock time DUET heard it end (its place in the conversation)
    measured: bool = True                   # False while it is still being measured

    @property
    def negative(self) -> float:
        e = self.emotion or {}
        return float(e.get("angry", 0.0) + e.get("fearful", 0.0) + e.get("sad", 0.0))

    def raised(self) -> List[str]:
        return raised(self.delta)

    def summary(self) -> Dict[str, Any]:
        """For the run log: what was measured, rounded."""
        out = {"speech_s": round(self.speech_s, 2), "loud_db": _r(self.loud_db), "tilt_db": _r(self.tilt_db),
               "f0_hz": _r(self.f0_hz, 0), "pause_s": round(self.longest_pause_s, 2),
               "delta": {k: _r(v) for k, v in self.delta.items()}}
        if self.emotion:
            out.update(negative=round(self.negative, 2), pose=self.pose)
        if not np.isnan(self.end_rise_st):
            out["end_rise_st"] = _r(self.end_rise_st)
        return out


# what analyse() measures (copied into a segment's place in the Listener)
_MEASURED = ("on", "off", "speech_s", "loud_db", "tilt_db", "f0_hz", "longest_pause_s", "emotion", "pose", "end_rise_st")


def _r(v: float, nd: int = 1):
    return None if v is None or np.isnan(v) else round(float(v), nd)


def _frames(x: np.ndarray, n: int, hop: int) -> np.ndarray:
    count = 1 + (len(x) - n) // hop if len(x) >= n else 0
    if count <= 0:
        return np.zeros((0, n), dtype=np.float32)
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    return x[idx]


def _pitch(x: np.ndarray, rise: bool) -> Tuple[float, float]:
    """(median pitch in Hz, terminal rise in semitones) from Praat's autocorrelation tracker (75-600 Hz, so a shout
    is still tracked). The median over a whole segment is robust; the terminal rise is not (see the module notes),
    so it is measured only when asked for. NaN without parselmouth: effort is then read from brightness and level."""
    try:
        import parselmouth
    except ImportError:
        return float("nan"), float("nan")
    p = parselmouth.Sound(x.astype(np.float64), SR).to_pitch_ac(time_step=0.01, pitch_floor=75, pitch_ceiling=600)
    f = p.selected_array["frequency"]
    v = f > 0
    if v.sum() < 5:
        return float("nan"), float("nan")
    median = float(np.median(f[v]))
    if not rise or v.sum() < 10:
        return median, float("nan")
    t = p.xs()
    tv, st = t[v], 12 * np.log2(f[v] / median)
    last, before = st[tv >= tv[-1] - 0.10], st[(tv >= tv[-1] - 0.60) & (tv < tv[-1] - 0.10)]
    if len(last) < 3 or len(before) < 5:
        return median, float("nan")
    return median, float(np.median(last) - np.min(before))


def analyse(pcm: np.ndarray, t0: float = 0.0, with_emotion: bool = True) -> Optional[Features]:
    """Measure one segment of 16 kHz mono audio that starts at wall-clock time t0. None when there is too little
    speech in it."""
    x = np.asarray(pcm, dtype=np.float32)
    if len(x) < SR // 4:
        return None
    fr = _frames(x, FRAME, FRAME)
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)
    speech = (db > db.max() - 30) & (db > -60)
    if speech.sum() < 5:
        return None
    idx = np.where(speech)[0]
    out = Features(on=t0 + idx[0] * FRAME / SR, off=t0 + (idx[-1] + 1) * FRAME / SR,
                   speech_s=float(speech.sum() * FRAME / SR))
    out.loud_db = float(np.percentile(db[speech], 90))
    spec = np.abs(np.fft.rfft(fr[speech] * np.hanning(FRAME), axis=1)) ** 2
    freqs = np.fft.rfftfreq(FRAME, 1 / SR)
    hi = spec[:, (freqs >= 1000) & (freqs < 4000)].sum(axis=1) + 1e-12
    lo = spec[:, (freqs >= 80) & (freqs < 1000)].sum(axis=1) + 1e-12
    out.tilt_db = float(np.median(10 * np.log10(hi / lo)))
    out.f0_hz, out.end_rise_st = _pitch(x[idx[0] * FRAME:(idx[-1] + 1) * FRAME], pitch_enabled())
    gaps = np.diff(idx) - 1
    out.longest_pause_s = float(gaps.max() * FRAME / SR) if len(gaps) else 0.0
    if with_emotion:
        out.emotion, out.pose = emotion(x)
    return out


# --- the emotion model --------------------------------------------------------------------------------------

_model: Dict[str, Any] = {"session": None, "tried": False}
_model_lock = threading.Lock()
_MIX_MATRIX = np.array([[_MIX[p].get(c, 0.0) for c in CLASSES] for p in POSES], dtype=np.float32)


def _session():
    """The ONNX session, loaded once per process (downloaded from Hugging Face the first time, pinned to the
    measured revision and checked against its SHA-256). None when it cannot be had: tone then works without
    emotion."""
    with _model_lock:
        if _model["tried"]:
            return _model["session"]
        _model["tried"] = True
        if os.environ.get("DUET_TONE_EMOTION", "1") != "1":
            return None
        try:
            import hashlib
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(EMOTION_REPO, EMOTION_FILE, revision=EMOTION_REVISION)
            with open(path, "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != EMOTION_SHA256:
                    raise RuntimeError("the emotion model's checksum does not match")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            _model["session"] = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
            log.info("tone: emotion model ready (%s)", EMOTION_REPO)
        except Exception as exc:  # tone without emotion still works
            log.warning("tone: no emotion model (%s); the voice's effort and timing are still read", str(exc)[:160])
        return _model["session"]


def status() -> Dict[str, Any]:
    """For /api/health: are the tone channel's parts on this machine? (Nothing is loaded or downloaded.)"""
    if not enabled():
        return {"ok": None, "why": "off (DUET_TONE=0)"}
    import importlib.util
    missing = []
    if importlib.util.find_spec("parselmouth") is None:
        missing.append("Praat: pip install -r requirements.txt")
    if os.environ.get("DUET_TONE_EMOTION", "1") == "1":
        try:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(EMOTION_REPO, EMOTION_FILE, revision=EMOTION_REVISION)
        except Exception:
            cached = None
        if not isinstance(cached, str):
            missing.append("the emotion model: the agent downloads it when it starts (10 MB, needs internet)")
    return {"ok": not missing, "why": "; ".join(missing)}


def prewarm() -> None:
    """Load the emotion model, and Praat, before the first conversation (the model is a 10 MB download the
    first time)."""
    if not enabled():
        return
    _session()
    analyse(np.zeros(SR, dtype=np.float32) + 1e-3 * np.sin(np.arange(SR) * 2 * np.pi * 150 / SR), with_emotion=False)


def emotion(x: np.ndarray):
    """(class probabilities, strongest pose) for a segment, or (None, "") without the model. 3 s windows, one every
    0.75 s (one centred, padded window for a shorter segment), averaged."""
    sess = _session()
    if sess is None:
        return None, ""
    x = np.asarray(x, dtype=np.float32)
    if len(x) < EMOTION_WINDOW:
        pad = EMOTION_WINDOW - len(x)
        windows = [np.pad(x, (pad // 2, pad - pad // 2))]
    else:
        windows = [x[s:s + EMOTION_WINDOW] for s in range(0, len(x) - EMOTION_WINDOW + 1, 12000)][:6]
    try:
        cos = sess.run(None, {"waveform": np.stack(windows).astype(np.float32)})[0]
    except Exception as exc:
        log.warning("tone: emotion model failed (%s)", str(exc)[:120])
        return None, ""
    p = np.exp((cos - cos.max(axis=1, keepdims=True)) / POSE_TEMPERATURE)
    p /= p.sum(axis=1, keepdims=True)
    classes = (p @ _MIX_MATRIX).mean(axis=0)
    return dict(zip(CLASSES, (float(v) for v in classes))), POSES[int(p.mean(axis=0).argmax())]


# --- the speaker's own baseline -------------------------------------------------------------------------------

class Baseline:
    """The speaker's usual voice in this conversation: medians of their plain segments' level, brightness, pitch and
    ending. A naturally loud, bright or high speaker is then not taken for an urgent one."""

    def __init__(self, keep: int = 12) -> None:
        self.keep = keep
        self.items: List[Features] = []

    def ready(self) -> bool:
        return len(self.items) >= 2

    def add(self, f: Features) -> None:
        self.items = (self.items + [f])[-self.keep:]

    def _median(self, name: str) -> float:
        vals = [getattr(f, name) for f in self.items if not np.isnan(getattr(f, name))]
        return float(np.median(vals)) if vals else float("nan")

    def effort(self, f: Features) -> Dict[str, float]:
        """How far this segment is from the speaker's usual: loudness and brightness in dB, pitch in semitones."""
        out: Dict[str, float] = {}
        if not self.ready():
            return out
        for name, key in (("loud_db", "loud"), ("tilt_db", "tilt")):
            base = self._median(name)
            if not np.isnan(base) and not np.isnan(getattr(f, name)):
                out[key] = getattr(f, name) - base
        base_f0 = self._median("f0_hz")
        if not np.isnan(base_f0) and not np.isnan(f.f0_hz) and base_f0 > 0:
            out["pitch"] = float(12 * np.log2(f.f0_hz / base_f0))
        base_rise = self._median("end_rise_st")
        if not np.isnan(base_rise) and not np.isnan(f.end_rise_st):
            out["rise"] = f.end_rise_st - base_rise
        return out


def raised(delta: Dict[str, float]) -> List[str]:
    """The effort cues a segment shows against the speaker's usual: brighter, higher, louder. A raised voice is two
    of them, or loud enough alone; [] otherwise."""
    cues = []
    if delta.get("tilt", 0.0) >= EFFORT_TILT_DB:
        cues.append("brighter %+.0f dB" % delta["tilt"])
    if delta.get("pitch", 0.0) >= EFFORT_PITCH_ST:
        cues.append("higher %+.0f semitones" % delta["pitch"])
    if delta.get("loud", 0.0) >= EFFORT_LOUD_DB:
        cues.append("louder %+.0f dB" % delta["loud"])
    if len(cues) >= 2 or delta.get("loud", 0.0) >= LOUD_ALONE_DB:
        return cues
    return []


# --- the words that go with a tone ------------------------------------------------------------------------------

# a refusal or a stop ("no", "stop", "wait", "don't")
_STOP = re.compile(r"\b(no|nope|stop|wait|don'?t|do not|cancel|hold on|enough|leave it|never ?mind)\b", re.I)
# the whole utterance is a bare no ("No!", "No, no, no.")
_BARE_NO = re.compile(r"^\W*((no|nope|nah|hey)\W*){1,4}$", re.I)
# words of distress and of trouble ("help", "again" and "wrong" alone are too common: "would that help my battery?")
_DISTRESS = re.compile(
    r"\b(pain|pains|painful|hurts?|hurting|fell|fallen|scared|afraid|frightened|please help|need help|sad|upset|"
    r"cry|crying|terrible|awful|worried|anxious|sick|ill|dizzy|lonely|stressed|panic\w*|bleeding|can'?t breathe|"
    r"chest|headache|ache|not (good|okay|ok|well|fine))\b", re.I)
_FRUSTRATED = re.compile(r"\b(not again|still not|still doesn'?t|not working|doesn'?t work|didn'?t work|won'?t work|"
                         r"isn'?t working|useless|annoying|annoyed|frustrat\w*|ridiculous|come on|seriously|"
                         r"you got it wrong|that'?s wrong|stupid)\b", re.I)
_HEDGE = re.compile(r"\b(i think|i guess|i suppose|maybe|probably|perhaps|not sure|i'?m not sure|or so|kind of|"
                    r"sort of|i don'?t know|roughly|around|i believe)\b|\b\w+-ish\b", re.I)
_FILLER = re.compile(r"\b(uh+|um+|er+|erm|hmm+|uhm)\b", re.I)
_VALUE = re.compile(r"\d+(?::\d+)?|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|"
                    r"twenty|thirty|forty|fifty|half|quarter)\b", re.I)
_CUT_OFF = re.compile(r"\b(my|the|a|an|to|for|and|or|with|at|about|of|is|it'?s)\W*$", re.I)


def _hedges(words: str) -> List[Tuple[str, str]]:
    """(hedge, value) for each value a hedge qualifies: one within a few words before it ("for maybe 4:30",
    "around 7") or after it ("4:30, I think", "15 minutes or so", "7-ish"). "I think I want an alarm at 7" is a
    softened request, not an unsure value."""
    out = []
    values = list(_VALUE.finditer(words))
    for h in _HEDGE.finditer(words):
        for v in values:
            if h.end() <= v.start():
                gap = len(re.findall(r"[\w':]+", words[h.end():v.start()]))
            elif h.start() >= v.end():
                gap = len(re.findall(r"[\w':]+", words[v.end():h.start()]))
            else:
                gap = 0                       # "7-ish"
            if gap <= HEDGE_GAP_WORDS:
                out.append((h.group(0), v.group(0)))
    return out


def _hedged_value(words: str) -> Optional[str]:
    """The first hedge that qualifies a value, or None."""
    found = _hedges(words)
    return found[0][0] if found else None


def _numbers(text: str) -> set:
    return {int(n) for n in re.findall(r"\d+", str(text))}


def _action_numbers(args: Dict[str, Any]) -> set:
    """The numbers an action's arguments carry, a clock time on both halves of the day ("16:30": 16, 4 and 30)."""
    out = set()
    for v in (args or {}).values():
        m = re.match(r"\s*(\d{1,2}):(\d{2})\s*$", str(v))
        if m:
            h = int(m.group(1))
            out |= {h, h % 12 or 12, int(m.group(2))}
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out.add(int(v))
        elif isinstance(v, str):
            out |= _numbers(v)
    return out


def bare_no(words: str) -> bool:
    return bool(_BARE_NO.match((words or "").strip()))


# --- one request: its segments and its words, read together -------------------------------------------------------

LABELS = {"urgent": "urgently, with a raised voice", "distressed": "distressed", "frustrated": "frustrated",
          "unsure": "unsure of the value", "raised voice": "with a raised voice"}


@dataclass
class Reading:
    """How the user said this request. Urgent, distressed and frustrated each need a cue from the voice and one
    from the words; unsure needs a hedge next to the value, or a long pause before it."""
    urgent: bool = False         # a raised voice, with words that stop or refuse
    unsure: bool = False         # hedged or hesitant about a value
    upset: str = ""              # "distressed" or "frustrated"
    raised_voice: bool = False   # effort well above the speaker's usual
    hesitant: bool = False       # a long pause before the value (the voice's timing)
    cues: List[str] = field(default_factory=list)
    numbers: Dict[str, float] = field(default_factory=dict)
    values: List[str] = field(default_factory=list)    # the values a hedge qualified, as said ("4:30")

    def about(self, tool: str, args: Dict[str, Any]) -> bool:
        """Is the user unsure of this action's value? With hedged values, only an action that carries one of them
        ("an alarm for 4:30, I think, and a timer for 10 minutes": the alarm, not the timer); a value said in words
        or a pause before it cannot be placed, so it covers every value of the request."""
        if not self.unsure:
            return False
        have = _action_numbers(args)
        placed = [_numbers(v) for v in self.values if _numbers(v)]
        if self.hesitant or not placed:
            return True
        return any(n <= have for n in placed)

    @property
    def label(self) -> str:
        if self.urgent:
            return "urgent"
        if self.upset:
            return self.upset
        if self.unsure:
            return "unsure"
        if self.raised_voice:
            return "raised voice"
        return ""

    def note(self) -> str:
        """For both minds: how the user sounded, and what to do about it in the reply (never an action). Nothing for
        unsure (the phone asks about the value itself) or a raised voice alone (excitement sounds the same)."""
        advice = {"urgent": "Keep your reply very short and calm.",
                  "distressed": "Be gentle and calm, and short; ask how they are before anything else.",
                  "frustrated": "Acknowledge the trouble in a few words, then help; no cheerfulness."}.get(self.label)
        if not advice:
            return ""
        return ("How the user said it (heard in their voice; advisory, it never decides an action): they sounded %s "
                "(%s). %s" % (LABELS[self.label], "; ".join(self.cues), advice))

    def event(self) -> Dict[str, Any]:
        return {"label": self.label, "cues": "; ".join(self.cues),
                "numbers": {k: round(v, 2) for k, v in self.numbers.items()}}


def read(segments: List[Features], words: str) -> Reading:
    """Read a request's measured segments together with its words."""
    r = Reading()
    segs = [s for s in segments if s is not None]
    w = " ".join((words or "").split())
    if not segs:
        segs = []
    last_words = next((s.words for s in reversed(segs) if s.words), "") or w
    # vocal effort, against the speaker's usual: the most raised segment of the request
    best: List[str] = []
    for s in segs:
        cues = s.raised()
        if len(cues) > len(best):
            best = cues
            r.numbers.update({"effort_" + k: v for k, v in s.delta.items()})
    r.raised_voice = bool(best)
    negative = max((s.negative for s in segs), default=0.0)
    if any(s.emotion for s in segs):
        r.numbers["negative"] = negative
    stop = _STOP.search(last_words)
    if r.raised_voice and stop:
        r.urgent = True
        r.cues += ["raised voice: " + ", ".join(best), '"%s"' % stop.group(0)]
    voice_negative = negative >= NEGATIVE_STRONG or (r.raised_voice and negative >= NEGATIVE_WITH_EFFORT)
    distress, frustrated = _DISTRESS.search(w), _FRUSTRATED.search(w)
    if not r.urgent and voice_negative and distress:
        r.upset = "distressed"
        r.cues += ["voice: %s %.2f" % (_strongest_negative(segs), negative), '"%s"' % distress.group(0)]
    elif not r.urgent and (voice_negative or r.raised_voice) and frustrated:
        r.upset = "frustrated"
        r.cues += ["voice: " + (", ".join(best) if r.raised_voice else "%s %.2f" % (_strongest_negative(segs), negative)),
                   '"%s"' % frustrated.group(0)]
    if _VALUE.search(w):
        _read_unsure(r, segs, w)
    if r.raised_voice and not (r.urgent or r.upset):
        r.cues.append("raised voice: " + ", ".join(best))
    return r


def _read_unsure(r: Reading, segs: List[Features], w: str) -> None:
    """Unsure of a value: a hedge next to it (the words), or a long pause before it, after a filler or in the middle
    of the phrase (the voice's timing), or, with DUET_TONE_PITCH=1, a terminal rise with a pause."""
    hedges = _hedges(w)
    hedge = hedges[0][0] if hedges else None
    r.values = [v for _, v in hedges]
    pause, before = 0.0, ""
    valued = [i for i, s in enumerate(segs) if _VALUE.search(s.words or "")]
    if valued and valued[-1] > 0:
        i = valued[-1]
        pause, before = max(0.0, segs[i].on - segs[i - 1].off), segs[i - 1].words or ""
    if valued:
        pause = max(pause, segs[valued[-1]].longest_pause_s)
    if pause:
        r.numbers["pause_s"] = pause
    filler = _FILLER.search(w)
    midphrase = bool(before) and bool(_CUT_OFF.search(before.strip()))
    rise = segs[valued[-1]].delta.get("rise", float("nan")) if valued and pitch_enabled() else float("nan")
    if not np.isnan(rise):
        r.numbers["rise_st"] = rise
    if (filler and pause >= FILLER_PAUSE_S) or (midphrase and pause >= MIDPHRASE_PAUSE_S):
        r.hesitant = True
    if hedge:
        r.unsure = True
        r.cues.append('"%s"' % hedge)
    if r.hesitant:
        r.unsure = True
        r.cues.append("paused %.1f s before the value" % pause + (' after "%s"' % filler.group(0) if filler else ""))
    elif not np.isnan(rise) and rise >= PITCH_RISE_ST and pause >= FILLER_PAUSE_S:
        r.unsure = True
        r.cues += ["pitch rose %+.0f semitones at the end" % rise, "paused %.1f s" % pause]


def _strongest_negative(segs: List[Features]) -> str:
    totals = {c: sum((s.emotion or {}).get(c, 0.0) for s in segs) for c in ("angry", "fearful", "sad")}
    return max(totals, key=totals.get)


# --- one conversation -------------------------------------------------------------------------------------------

class Listener:
    """The tone channel of one conversation: the speaker's baseline, and the segments of the open request.

    A segment gets its place the moment it ends (`expect`), and its measurements fill it a few milliseconds later
    (`add`). Its words come from Whisper a moment after it ended (`heard`): they belong to the latest segment still
    without words that ended at least ASR_MIN_S before they arrived, which keeps them in order when the user speaks
    in quick bursts, and off a noise burst that never gets words. `reading` reads the open request; `close` drops the
    segments of a request that was answered."""

    ASR_MIN_S = 0.12        # no transcript arrives sooner than this after its segment ends

    def __init__(self) -> None:
        self.baseline = Baseline()
        self.segments: List[Features] = []
        self.last: Optional[Reading] = None

    def expect(self, ended: float) -> Features:
        slot = Features(ended=ended, measured=False)
        self.segments.append(slot)
        return slot

    def add(self, f: Optional[Features], into: Optional[Features] = None) -> Optional[Features]:
        """A measured segment (into its place, when it has one): compared with the speaker's usual, then, if it is
        plain, part of it. None when it had too little speech to measure."""
        if into is not None:
            into.measured = True
            if f is None:
                return None
            for name in _MEASURED:
                setattr(into, name, getattr(f, name))
            f = into
        elif f is None:
            return None
        else:
            f.ended = f.ended or f.off
            self.segments.append(f)
        f.delta = self.baseline.effort(f)
        if not self.baseline.ready() or not raised(f.delta):
            self.baseline.add(f)
        return f

    def heard(self, text: str, at: float) -> None:
        """A final transcript, arrived at wall-clock time `at`."""
        if not self.segments:
            return
        waiting = [s for s in self.segments if not s.worded and s.ended <= at - self.ASR_MIN_S]
        s = waiting[-1] if waiting else self.segments[-1]
        s.words = " ".join((s.words + " " + (text or "")).split())
        s.worded = True

    def awaiting_words(self) -> bool:
        """The latest segment was a raised voice whose words have not arrived yet (the coordinator holds actions
        until they do)."""
        s = self.segments[-1] if self.segments else None
        return s is not None and s.measured and not s.worded and bool(s.raised())

    def reading(self, words: str) -> Reading:
        self.last = read([s for s in self.segments if s.measured], words)
        return self.last

    def close(self, before: float) -> None:
        """A request was answered: its segments (those that ended before its reply began) are done with."""
        self.segments = [s for s in self.segments if s.ended > before]


# --- asking about an unsure value ----------------------------------------------------------------------------------

def ask_back(tool: str, args: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(the question, the action as said) for a write whose value the user sounded unsure of, e.g. ("4:30 in the
    afternoon, right?", "set an alarm for 4:30 in the afternoon"). None for an action with no value to confirm."""
    from .confirm import spoken_time
    a = args or {}

    def num(v: Any) -> str:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        return str(int(f)) if f == int(f) else str(f)

    if tool in ("set_alarm", "set_reminder", "create_event") and a.get("time"):
        when = spoken_time(a["time"])
        what = {"set_alarm": "set an alarm for %s", "set_reminder": "set a reminder for %s",
                "create_event": "add it to your calendar at %s"}[tool] % when
        return "%s, right?" % (when[:1].upper() + when[1:]), what
    if tool == "set_timer" and a.get("minutes") is not None:
        m = num(a["minutes"])
        when = "%s minute%s" % (m, "" if m == "1" else "s")
        return "%s, right?" % when, "set a %s timer" % when.replace("minutes", "minute")
    if tool in ("set_brightness", "set_volume") and a.get("percent") is not None:
        p = num(a["percent"])
        return "%s percent, right?" % p, "set the %s to %s percent" % (
            "brightness" if tool == "set_brightness" else "volume", p)
    if tool == "set_screen_timeout" and a.get("seconds") is not None:
        s = num(a["seconds"])
        return "%s seconds, right?" % s, "set the screen timeout to %s seconds" % s
    if tool == "set_car_climate" and a.get("temperature") is not None:
        t = num(a["temperature"])
        return "%s degrees, right?" % t, "set the car to %s degrees" % t
    if tool == "control_home_device" and a.get("value") not in (None, ""):
        v, device = num(a["value"]), str(a.get("device") or "it")
        return "The %s at %s, right?" % (device, v), "set the %s to %s" % (device, v)
    return None
