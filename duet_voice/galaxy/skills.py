"""DUET's skills: phone capabilities declared in app/www/skills.json, with no code.

Each entry becomes a tool of the modes it lists, and inherits everything the coordinator gives the
built-in tools: the commit gate's hold, the ledger (exactly once; a later change of the same group
replaces an earlier one), the guards (cut off, undecided, dropped), the consent gate when marked, and a
confirmation said only after the phone reports the change done (read back when `verify` is set). The
fast mind may act on a skill whose `named` words the user said. The phone app carries it out with one
generic executor (app/www/phone.js): an Android setting, an intent, or Do Not Disturb.

The shape follows Android 16's AppFunctions (self-describing functions an agent can call): on a
device where DUET may call other apps' functions, an AppFunction would be one more kind of executor.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "app", "www", "skills.json")
KINDS = {"read", "write", "ui"}
RUNS = {"setting", "intent", "dnd"}

_cache: Dict[str, Any] = {"path": None, "mtime": None, "skills": []}


def load(path: str = PATH) -> List[Dict[str, Any]]:
    """The skills in the file, checked (a broken entry is skipped with a reason, never half-loaded)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["skills"]
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh).get("skills", [])
    good = []
    for s in raw:
        why = problem(s)
        if why:
            import logging
            logging.getLogger("duet.skills").warning("skill %s skipped: %s", s.get("name"), why)
            continue
        good.append(s)
    _cache.update(path=path, mtime=mtime, skills=good)
    return good


def problem(s: Dict[str, Any]) -> str:
    """'' when the entry is usable, else why not."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", str(s.get("name", ""))):
        return "name must be lower_snake_case"
    if s.get("kind") not in KINDS:
        return "kind must be read, write or ui"
    if not s.get("description") or not s.get("modes"):
        return "description and modes are needed"
    run = s.get("run") or {}
    if run.get("type") not in RUNS:
        return "run.type must be one of %s" % ", ".join(sorted(RUNS))
    if run["type"] == "setting" and (run.get("namespace") != "system" or not run.get("key")):
        return "a setting skill writes a system setting (namespace system, a key)"
    if run["type"] == "intent" and not run.get("action"):
        return "an intent skill needs an action"
    if s.get("named"):
        try:
            re.compile(s["named"])
        except re.error as exc:
            return "named is not a regular expression: %s" % exc
    return ""


def spec(s: Dict[str, Any]) -> Dict[str, Any]:
    """The tool the models see, in the same shape as the built-in ones (modes.py)."""
    out = {"name": s["name"], "kind": s["kind"], "description": s["description"],
           "parameters": {"type": "object", "properties": s.get("parameters") or {}, "required": s.get("required") or []},
           "skill": True}
    if s.get("consent"):
        out["consent"] = True
    if s.get("group"):
        out["group"], out["target"] = s["group"], []
    return out


def specs_for(mode: str, path: str = PATH) -> List[Dict[str, Any]]:
    return [spec(s) for s in load(path) if mode in s["modes"]]


def named(path: str = PATH) -> Dict[str, str]:
    """{tool: the words that name it}, for the fast mind's check that the user asked for it."""
    return {s["name"]: s["named"] for s in load(path) if s.get("named") and s["kind"] != "read"}


def get(name: str, path: str = PATH) -> Optional[Dict[str, Any]]:
    return next((s for s in load(path) if s["name"] == name), None)


def fill(template: str, args: Dict[str, Any]) -> str:
    """'{on?on:off}' and '{size}' filled from the arguments (the same rules as phone.js)."""
    def one(m: "re.Match") -> str:
        key, yes, no = m.group(1), m.group(3), m.group(4)
        v = args.get(key)
        if m.group(2):
            return yes if v is True or str(v).lower() == "true" else no
        return "" if v is None else str(v)
    return re.sub(r"\{(\w+)(\?([^:}]*):([^}]*))?\}", one, template or "")


def confirmation(name: str, args: Dict[str, Any]) -> Optional[str]:
    s = get(name)
    return fill(s["confirm"], args) if s and s.get("confirm") else None
