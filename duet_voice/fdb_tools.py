"""The twelve FDB-v3 tools, as this agent exposes them to its thinker.

Names and argument names are exactly the benchmark's, because the scorer
matches on them. Three things differ from the reference agents in the
benchmark repository, each for a reason that holds for any real voice agent:

1. **Optional means optional.** A real apartment search does not need a budget,
   and a product search does not need a price cap. The reference schema makes
   `bedrooms` and `max_price` required, which forces a model to invent values
   the user never said. Here they are optional, and so are two common filters
   (`pets_allowed` for apartments, `category` for products).
2. **Calls go through the DUET coordinator** (commit gate, idempotency ledger,
   failure policy), and the backend runs in a worker thread. The reference
   agents call the mock synchronously inside the event loop, so an injected
   API delay froze their audio loop.
3. **The log is identical.** Every executed call is appended to
   `/tmp/agent_tool_calls.log` in the benchmark's format, with the arguments
   the agent issued. The only exception is a call that never executed: one
   superseded at the gate, answered from the ledger, or refused by the backend.

The backend is the benchmark's own `mock_apis.MockAPIRegistry`, imported from
the FDB-v3 checkout (`FDB_V3_DIR`). Where the mock's Python signature requires
a value that the tool schema leaves optional, the adapter fills a neutral
backend default (`BACKEND_DEFAULTS`). The logged call keeps what the agent
actually asked for.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from .config import CONFIG
from .coordinator import CallRecord, Coordinator, Superseded

log = logging.getLogger("duet.tools")

# --- schemas -------------------------------------------------------------------------

def _obj(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}

TOOL_SPECS: List[Dict[str, Any]] = [
    # Travel & identity
    {"name": "search_flights", "kind": "read",
     "description": "Search available flights to a destination on a date.",
     "parameters": _obj({
         "destination": {**S, "description": "Destination city or airport, as the user named it, e.g. 'Oslo'."},
         "date": {**S, "description": "Travel date as the user said it, e.g. 'October 4'. Do not add a year the user did not say."},
     }, ["destination", "date"])},
    {"name": "book_flight", "kind": "write",
     "description": "Book a flight ticket for a passenger. Use after search_flights has found the flight.",
     "parameters": _obj({
         "passenger_name": {**S, "description": "Passenger's name exactly as the user gave it."},
     }, ["passenger_name"])},
    {"name": "update_identity_doc", "kind": "write",
     "description": "Update the number of the user's identity document on file (passport, driver license, national ID).",
     "parameters": _obj({
         "doc_type": {**S, "description": "Document type in snake_case, e.g. 'passport', 'driver_license', 'id_card'."},
         "doc_number": {**S, "description": "The new document number, letters and digits joined with no spaces, e.g. 'X1234567'."},
     }, ["doc_type", "doc_number"])},
    # Finance & billing
    {"name": "get_card_benefits", "kind": "read",
     "description": "Look up the benefits of a credit card type.",
     "parameters": _obj({
         "card_type": {**S, "description": "Card tier or name as the user said it, lowercase, e.g. 'platinum'."},
     }, ["card_type"])},
    {"name": "get_exchange_rate", "kind": "read",
     "description": "Convert an amount between two currencies at the current rate.",
     "parameters": _obj({
         "amount": {**N, "description": "Amount to convert, as a number."},
         "from_currency": {**S, "description": "ISO 4217 code of the source currency, e.g. 'USD'."},
         "to_currency": {**S, "description": "ISO 4217 code of the target currency, e.g. 'EUR'."},
     }, ["amount", "from_currency", "to_currency"])},
    {"name": "modify_autopay", "kind": "write",
     "description": "Set which account pays a bill automatically.",
     "parameters": _obj({
         "bill_type": {**S, "description": "Bill in snake_case, e.g. 'credit_card', 'utilities', 'electricity', 'phone'."},
         "source_account": {**S, "description": "Account that should pay, e.g. 'checking' or 'savings'."},
     }, ["bill_type", "source_account"])},
    # Housing & location
    {"name": "search_apartments", "kind": "read",
     "description": "Search rental apartment listings. Pass only the criteria the user actually gave.",
     "parameters": _obj({
         "city": {**S, "description": "City to search in."},
         "bedrooms": {**I, "description": "Number of bedrooms."},
         "max_price": {**N, "description": "Maximum monthly rent."},
         "pets_allowed": {**B, "description": "True if the user needs a pet-friendly place."},
     }, [])},
    {"name": "calculate_commute", "kind": "read",
     "description": "Estimate travel time between two places.",
     "parameters": _obj({
         "origin_address": {**S, "description": "Starting place as the user described it, or an address or id from an earlier result."},
         "destination_address": {**S, "description": "Destination as the user described it."},
         "mode": {**S, "enum": ["driving", "walking", "transit", "biking"],
                  "description": "Travel mode; 'driving' when the user does not say."},
     }, ["origin_address", "destination_address", "mode"])},
    {"name": "update_search_filter", "kind": "write",
     "description": "Change one saved apartment-search filter. Call once per filter the user changes.",
     "parameters": _obj({
         "filter_name": {**S, "description": "Filter key in snake_case, e.g. 'max_price', 'min_bedrooms', 'pets_allowed', 'neighborhood'."},
         "value": {**S, "description": "New value, e.g. '2400', 'true', 'Riverside'."},
     }, ["filter_name", "value"])},
    # E-commerce
    {"name": "track_order", "kind": "read",
     "description": "Get the shipping status of an order.",
     "parameters": _obj({
         "order_id": {**S, "description": "Order id, spelled-out letters and digits joined with no spaces or dashes, e.g. 'XK42Q7'."},
     }, ["order_id"])},
    {"name": "search_products", "kind": "read",
     "description": "Search the product catalog.",
     "parameters": _obj({
         "query": {**S, "description": "What the user is looking for, e.g. 'running shoes'."},
         "max_price": {**N, "description": "Price cap, only if the user gave one."},
         "category": {**S, "description": "Product category, only if the user named one."},
     }, ["query"])},
    {"name": "add_to_cart", "kind": "write",
     "description": "Add a product to the shopping cart.",
     "parameters": _obj({
         "product_id": {**S, "description": "Product id, from the user or from a search result."},
         "quantity": {**I, "description": "How many; 1 when the user does not say."},
     }, ["product_id", "quantity"])},
]

SPEC_BY_NAME = {s["name"]: s for s in TOOL_SPECS}
WRITE_TOOLS = {s["name"] for s in TOOL_SPECS if s["kind"] == "write"}

# Values the mock's Python signature needs but the schema leaves optional.
BACKEND_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "search_apartments": {"city": "any", "bedrooms": 1, "max_price": 5000.0},
}


# --- the backend -------------------------------------------------------------------

_registry = None
_registry_lock = threading.Lock()


def _load_registry():
    """The benchmark's own mock backend, so results match the reference agents."""
    global _registry
    with _registry_lock:
        if _registry is None:
            fdb = CONFIG.fdb_dir
            if fdb and fdb not in sys.path:
                sys.path.insert(0, fdb)
            from mock_apis import MockAPIRegistry  # type: ignore
            _registry = MockAPIRegistry(latency_profile=CONFIG.latency_profile)
            log.info("mock backend loaded from %s (latency profile %s)",
                     getattr(sys.modules.get("mock_apis"), "__file__", "?"), CONFIG.latency_profile)
    return _registry


def coerce(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Drop nulls and coerce types to the schema (a model may send '2,400')."""
    props = SPEC_BY_NAME[tool]["parameters"]["properties"]
    out: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        want = props.get(key, {}).get("type")
        try:
            if want == "number" and isinstance(value, str):
                value = float(value.replace(",", "").replace("$", "").strip())
            elif want == "integer" and not isinstance(value, bool):
                value = int(float(str(value).replace(",", "")))
            elif want == "boolean" and isinstance(value, str):
                value = value.strip().lower() in ("true", "yes", "1")
            elif want == "string" and not isinstance(value, str):
                value = json.dumps(value) if isinstance(value, (dict, list)) else str(value).lower() if isinstance(value, bool) else str(value)
        except ValueError:
            pass
        if isinstance(value, float) and value.is_integer() and want != "number":
            value = int(value)
        out[key] = value
    return out


def _append_jsonl(path: str, row: Dict[str, Any]) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


class FdbToolbox:
    """One per conversation (LiveKit room)."""

    def __init__(self, room_name: str, coordinator: Coordinator) -> None:
        self.room_name = room_name
        self.coord = coordinator
        self.last_tool_start = 0.0
        self.last_tool_end = 0.0

    def _log_benchmark_call(self, record: CallRecord) -> None:
        _append_jsonl(CONFIG.tool_log, {
            "room": self.room_name,
            "call": {"function": record.tool, "args": record.args,
                     "timestamp_start": record.started, "timestamp_end": record.finished},
        })

    async def call(self, tool: str, raw_args: Dict[str, Any], epoch: Optional[int] = None) -> str:
        """Run one tool call through the coordinator. `epoch`: the epoch the plan
        calling it was made in, so a plan that the user's words have overtaken
        never acts."""
        if tool not in SPEC_BY_NAME:
            log.warning("model called an unknown tool: %s", tool)
            return json.dumps({"status": "error", "error": "unknown tool '%s'" % tool,
                               "instruction": "use only the tools you were given"})
        args = coerce(tool, raw_args)
        backend_args = {**BACKEND_DEFAULTS.get(tool, {}), **args}
        registry = _load_registry()

        async def run() -> Any:
            return await asyncio.to_thread(registry.call, tool, **backend_args)

        try:
            record = await self.coord.execute(
                tool, args, run,
                state_changing=tool in WRITE_TOOLS,
                timeout_s=12.0 if tool in WRITE_TOOLS else 8.0,
                on_committed=self._log_benchmark_call,
                epoch=epoch,
            )
        except Superseded:
            log.info("superseded at the gate: %s %s", tool, args)
            return json.dumps({"status": "not_executed", "reason":
                               "the user started speaking again; wait for their full request"})
        self.last_tool_start, self.last_tool_end = record.started, record.finished
        if record.outcome == "ok":
            return json.dumps(record.result, default=str)
        if record.outcome == "cached":
            return json.dumps({"status": "already_done", "note":
                               "this exact call already ran in this conversation; not repeated",
                               "result": record.result}, default=str)
        if record.outcome == "unknown":
            return json.dumps({"status": "unknown_outcome", "error": record.error, "instruction":
                               "do not retry this action; tell the user it may not have gone "
                               "through and offer to connect them with a human agent"})
        return json.dumps({"status": "error", "error": record.error, "instruction":
                           "tell the user this step failed and what they can do next"})


def livekit_tools(toolbox_getter) -> list:
    """The twelve tools as LiveKit raw function tools bound to a toolbox."""
    from livekit.agents import llm

    tools = []
    for spec in TOOL_SPECS:
        name = spec["name"]

        def make(name: str):
            async def _tool(raw_arguments: Dict[str, Any]) -> str:
                return await toolbox_getter().call(name, raw_arguments)
            return _tool

        tools.append(llm.function_tool(
            make(name),
            raw_schema={"name": name, "description": spec["description"],
                        "parameters": spec["parameters"]},
        ))
    return tools
