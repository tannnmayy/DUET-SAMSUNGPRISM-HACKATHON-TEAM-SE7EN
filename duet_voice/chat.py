"""Typed DUET chat for the use-case toolsets.

The LiveKit console is voice. This is the same thinker, coordinator and
toolbox over stdin, so a washer UE line cannot silently become flight_search.

    python -m duet_voice.chat --use-case appliance
    python -m duet_voice.chat --use-case appliance --once "My Samsung washing machine is showing a UE error"
    python -m duet_voice.chat --use-case family

Leave `--use-case` unset to auto-route: washer/UE loads appliance tools,
Mum/Watch loads family tools. Pin with `--use-case` or `DUET_USE_CASE`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"))


def banner(use_case: str, tools: List[str], backend: str) -> str:
    shown = ", ".join(tools[:8]) + ("…" if len(tools) > 8 else "")
    lines = [
        "DUET text chat",
        "use_case=%s  backend=%s  tools=%s" % (use_case, backend, shown),
    ]
    from .use_case import auto_route_enabled
    if auto_route_enabled():
        lines.append(
            "Auto-route on. Washer/UE uses appliance tools; Mum/Watch/Priya uses family tools."
        )
    elif use_case == "benchmark":
        lines.append(
            "FDB-v3 tools are pinned. Washer/UE would be treated as a trip. "
            "Unset DUET_USE_CASE to auto-route, or pass --use-case appliance."
        )
    lines.append("Type a request and press Enter. /quit to exit.")
    return "\n".join(lines)


def open_turn(coord, text: str) -> int:
    coord.user_started_speaking()
    coord.user_stopped_speaking()
    coord.heard(text)
    coord.turn_committed()
    return coord.epoch


def format_event(ev) -> Optional[str]:
    if ev.kind == "tool_start":
        args = " ".join("%s=%s" % (k, ev.args[k]) for k in ev.args)
        return "tool_call  %s  %s" % (ev.name, args)
    if ev.kind == "tool_done":
        return "tool_result %s  %s" % (ev.name, ev.outcome)
    if ev.kind == "say":
        return "Agent: %s" % (ev.text or "").strip()
    if ev.kind == "error":
        return "error: %s" % ev.text
    if ev.kind == "listen":
        return "listen: %s" % ev.text
    return None


async def run_turn(text: str, *, coord, toolbox, thinker) -> List[str]:
    open_turn(coord, text)
    note = toolbox.session_note() if hasattr(toolbox, "session_note") else ""
    lines: List[str] = []
    async for ev in thinker.run(text, toolbox, note=note, allow_listen=False):
        rendered = format_event(ev)
        if rendered:
            lines.append(rendered)
    return lines


class ChatSession:
    def __init__(self) -> None:
        from .config import CONFIG
        from .coordinator import Coordinator
        from .thinker import make_thinker
        from .use_case import (
            allow_long_tool_chains, auto_route_enabled, make_toolbox, thinker_instructions,
        )

        self.coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
        self.live_case = CONFIG.use_case if not auto_route_enabled() else "benchmark"
        if auto_route_enabled() or self.live_case != "benchmark":
            allow_long_tool_chains()
        self.toolbox = make_toolbox("chat", self.coord, use_case=self.live_case)
        self.thinker = make_thinker(
            tool_specs=self.toolbox.specs, instructions=thinker_instructions(self.live_case))

    def bind(self, text: str) -> None:
        from .thinker import make_thinker
        from .use_case import make_toolbox, resolve_live_use_case, thinker_instructions

        chosen = resolve_live_use_case(text, self.live_case)
        if chosen == self.live_case:
            return
        self.live_case = chosen
        self.toolbox = make_toolbox("chat", self.coord, use_case=chosen)
        self.thinker = make_thinker(
            tool_specs=self.toolbox.specs, instructions=thinker_instructions(chosen))
        tools = [s["name"] for s in self.toolbox.specs]
        print("switching to %s  tools=%s" % (chosen, ", ".join(tools[:6])), flush=True)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--use-case", default=None,
                    help="benchmark | appliance | family. Overrides DUET_USE_CASE.")
    ap.add_argument("--once", default=None, metavar="TEXT",
                    help="Run one user turn and exit.")
    return ap.parse_args(argv)


def _apply_use_case(name: Optional[str]) -> None:
    from . import config
    if name:
        os.environ["DUET_USE_CASE"] = name.strip().lower()
        config.reload()


async def _session(args: argparse.Namespace) -> int:
    from .config import CONFIG

    session = ChatSession()
    tools = [s["name"] for s in session.toolbox.specs]
    print(banner(CONFIG.use_case, tools, CONFIG.llm_backend), flush=True)

    if args.once is not None:
        text = args.once.strip()
        if not text:
            return 2
        print("You: %s" % text, flush=True)
        session.bind(text)
        for line in await run_turn(text, coord=session.coord, toolbox=session.toolbox, thinker=session.thinker):
            print(line, flush=True)
        return 0

    while True:
        try:
            raw = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        text = raw.strip()
        if not text:
            continue
        if text.lower() in ("/quit", "/exit", "quit", "exit"):
            return 0
        try:
            session.bind(text)
            lines = await run_turn(text, coord=session.coord, toolbox=session.toolbox, thinker=session.thinker)
        except Exception as exc:
            print("error: %s" % exc, flush=True)
            continue
        for line in lines:
            print(line, flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    _apply_use_case(args.use_case)
    return asyncio.run(_session(args))


if __name__ == "__main__":
    raise SystemExit(main())
