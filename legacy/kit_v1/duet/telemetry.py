"""Internal observability.

Nothing here ever reaches out_queue. The grading harness records only the
trace; this is for us. Two jobs:

  1. A ring-buffer log we can dump after a run to explain what the agent
     was thinking, without printing during grading.
  2. The Invariant 1 watchdog: no event handler may occupy the event loop
     for longer than config.DISPATCHER_BUDGET_MS. A violation here is the
     single most expensive bug class in this project (PROTOCOL.md section 5:
     a blocking call makes the trace blame us for phantom violations), so we
     measure it rather than hope.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from . import config

_LOG: Deque[Dict[str, Any]] = deque(maxlen=4000)
_HANDLER_TIMINGS: List[Dict[str, Any]] = []
_VIOLATIONS: List[Dict[str, Any]] = []


def reset() -> None:
    """Called at the start of every scenario (a fresh agent instance)."""
    _LOG.clear()
    _HANDLER_TIMINGS.clear()
    _VIOLATIONS.clear()


def log(event: str, **fields: Any) -> None:
    entry = {"t_wall": time.perf_counter(), "event": event, **fields}
    _LOG.append(entry)
    if config.DEBUG:
        parts = " ".join(str(k) + "=" + repr(v) for k, v in fields.items())
        print("[duet] " + event + " " + parts, file=sys.stderr)


def records() -> List[Dict[str, Any]]:
    return list(_LOG)


class Watchdog:
    """Context manager timing a synchronous handler.

    Usage:
        with Watchdog("user_speech_chunk"):
            ...handler body...

    Records every timing so tests can assert the p99. In STRICT mode
    (tests) a violation raises; in grading it is logged and execution
    continues, because a slow handler is a bug but a crash is a zero.
    """

    __slots__ = ("label", "_t0")

    def __init__(self, label: str) -> None:
        self.label = label
        self._t0 = 0.0

    def __enter__(self) -> "Watchdog":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        _HANDLER_TIMINGS.append({"label": self.label, "ms": elapsed_ms})
        if elapsed_ms > config.DISPATCHER_BUDGET_MS:
            v = {"label": self.label, "ms": round(elapsed_ms, 2),
                 "budget_ms": config.DISPATCHER_BUDGET_MS}
            _VIOLATIONS.append(v)
            log("watchdog.violation", **v)
            if config.STRICT and exc_type is None:
                raise AssertionError(
                    "dispatcher budget exceeded: " + self.label + " took "
                    + str(round(elapsed_ms, 2)) + "ms (budget "
                    + str(config.DISPATCHER_BUDGET_MS) + "ms)")
        return False  # never swallow exceptions


def timings() -> List[Dict[str, Any]]:
    return list(_HANDLER_TIMINGS)


def violations() -> List[Dict[str, Any]]:
    return list(_VIOLATIONS)


def percentile(p: float) -> Optional[float]:
    """p in [0,1]. Returns the handler-latency percentile in ms, or None."""
    if not _HANDLER_TIMINGS:
        return None
    vals = sorted(t["ms"] for t in _HANDLER_TIMINGS)
    idx = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
    return vals[idx]
