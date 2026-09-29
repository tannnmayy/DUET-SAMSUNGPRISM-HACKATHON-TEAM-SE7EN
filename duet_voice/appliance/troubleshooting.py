"""Grounded Samsung appliance troubleshooting.

Meanings and steps come from public Samsung support pages. An error code that
is not in this table is returned as unknown: the model must not invent one.
Sources (Samsung support, retrieved 2026-09-28):
- https://www.samsung.com/uk/support/home-appliances/what-do-the-codes-on-my-washing-machine-mean/
- https://www.samsung.com/au/support/home-appliances/samsung-washing-machine-error-codes/
- https://www.samsung.com/de/support/home-appliances/waschmaschinen-fehlermeldungen/
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .safety import CAUTION, PROFESSIONAL_REQUIRED, SAFE_SELF_SERVICE, assert_safe_text

# Documented aliases used on the same family of codes (Samsung publishes both).
_WASHER_ALIASES = {
    "4C": "4E", "NF": "4E", "NF1": "4E", "E1": "4E",
    "5C": "5E", "SE": "5E", "SC": "5E", "E2": "5E",
    "UB": "UE", "UR": "UE", "E4": "UE", "DC": "UE",  # dc listed with UE as unbalance on some pages
    "DC1": "DE", "DC2": "DE", "D C": "DE", "DE1": "DE",
    "OC": "OE",
    "LC": "LE", "LC1": "LE",
    "TC": "TE", "TE1": "TE", "TE2": "TE", "TE3": "TE",
    "TC1": "TE", "TC2": "TE", "TC3": "TE",
    "3C": "3E", "3C1": "3E", "3C2": "3E", "3C3": "3E", "3C4": "3E",
    "1C": "1E",
    "SUD": "SUD", "SUDS": "SUD", "SD": "SUD",
    "HC": "HE", "HC2": "HE", "HR": "HE",
    "9C": "UC", "9C1": "UC", "9C2": "UC",
}

_DRYER_ALIASES = {
    "HE1": "HE", "HE2": "HE",
    "TC": "TE", "TE1": "TE",
    "DC": "DE", "DE1": "DE",
}


@dataclass(frozen=True)
class TroubleStep:
    step_id: str
    title: str
    instructions: str
    expected_result: str
    safety: str
    escalation: str
    may_send_command: str = ""  # pause | stop | "" — queued only, never proof of completion


@dataclass(frozen=True)
class ErrorGuide:
    code: str
    appliance_types: Tuple[str, ...]
    problem: str
    source: str
    steps: Tuple[TroubleStep, ...]
    service_if_unresolved: bool = True


def _norm(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


WASHER_GUIDES: Dict[str, ErrorGuide] = {}
DRYER_GUIDES: Dict[str, ErrorGuide] = {}


def _add(table: Dict[str, ErrorGuide], guide: ErrorGuide) -> None:
    for step in guide.steps:
        assert_safe_text(step.instructions)
        assert_safe_text(step.title)
    table[guide.code] = guide


_add(WASHER_GUIDES, ErrorGuide(
    code="UE", appliance_types=("washer",),
    problem="The washer stopped the spin because the load is unbalanced. Samsung lists this as not a fault.",
    source="Samsung UK/AU washer information codes (Ub, UE, Ur, E4).",
    steps=(
        TroubleStep(
            "ue-redistribute", "Redistribute the load",
            "Pause the cycle if it is running. Open the door when it unlocks. Spread the laundry evenly "
            "around the drum. Mix large and small items; a single heavy towel often will not spin on its own. "
            "Close the door and resume.",
            "The unbalanced-load code clears and the spin continues.",
            SAFE_SELF_SERVICE,
            "If the code returns after a redistributed, mixed load, check that the washer is level, then offer service.",
            may_send_command="pause",
        ),
        TroubleStep(
            "ue-level", "Check that the washer is level",
            "Confirm the washer sits on a flat, stable floor and does not rock. Adjust the feet if they are loose. "
            "Do not move a machine that still has water in the drum.",
            "The washer sits firmly and the next spin completes.",
            SAFE_SELF_SERVICE,
            "If it still reports an unbalanced load on a mixed, moderate load, a technician should inspect the suspension.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="4E", appliance_types=("washer",),
    problem="The washer is not filling, or is filling too slowly (water supply).",
    source="Samsung UK/AU/DE: 4C, 4E, nF.",
    steps=(
        TroubleStep(
            "4e-tap", "Open the water tap",
            "Make sure the water tap feeding the washer is fully open and that water actually flows from it.",
            "Water reaches the inlet hose with normal pressure.",
            SAFE_SELF_SERVICE,
            "If the tap is open and the code remains, clean the inlet mesh next.",
        ),
        TroubleStep(
            "4e-hose", "Check the inlet hose",
            "Look at the fill hose for kinks or a crushed section. Straighten it. Do not disconnect plumbing if you "
            "are not comfortable doing so.",
            "The hose is open and unkinked.",
            SAFE_SELF_SERVICE,
            "If the hose is clear and the code remains, the inlet mesh may be blocked.",
        ),
        TroubleStep(
            "4e-mesh", "Clean the inlet mesh filter",
            "Turn the water tap off. If you can reach the hose coupling at the back of the washer, unscrew it and "
            "rinse the mesh filter. Screw the hose back on, open the tap, and check for drips. Skip this if you "
            "cannot access the coupling safely.",
            "The mesh is clear and the washer fills.",
            CAUTION,
            "If filling still fails, stop. A technician should inspect the inlet valve. Do not open the cabinet.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="5E", appliance_types=("washer",),
    problem="The washer is not draining, or is draining too slowly.",
    source="Samsung UK/AU/DE: 5C, 5E, SE, SC.",
    steps=(
        TroubleStep(
            "5e-hose", "Check the drain hose",
            "Look behind the washer for a kinked, crushed or frozen drain hose. Straighten it. The outlet should "
            "not be pushed too far down a standpipe.",
            "The drain hose is open and the tub begins to empty.",
            SAFE_SELF_SERVICE,
            "If the hose is clear, clean the drain-pump filter next.",
        ),
        TroubleStep(
            "5e-filter", "Clean the drain-pump filter",
            "Pause the cycle. Keep a shallow tray ready: a little water will come out. Open the small service door "
            "at the bottom front, unscrew the drain-pump filter, remove lint and coins, then screw it back in firmly. "
            "Do not put your hand into the pump housing.",
            "The filter is clear and the washer drains.",
            CAUTION,
            "If it still will not drain, stop and book service. Do not run the machine full of water.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="DE", appliance_types=("washer", "dryer"),
    problem="The door is open, or it is not locked.",
    source="Samsung AU/DE: dC, dE, dE1, LO, FL.",
    steps=(
        TroubleStep(
            "de-door", "Close the door firmly",
            "Take anything out from between the door and the seal. Close the door until it clicks, then start again.",
            "The door locks and the code clears.",
            SAFE_SELF_SERVICE,
            "If the door is clear and still will not lock, a technician should inspect the latch. Do not tape or bypass it.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="SUD", appliance_types=("washer",),
    problem="Too many suds. The washer has paused to let them settle.",
    source="Samsung DE/AU: Sud, Sd, SUdS.",
    steps=(
        TroubleStep(
            "sud-detergent", "Reduce detergent",
            "Let the extra-rinse or suds-removal finish if it is running. For the next cycle use less detergent, "
            "and use HE detergent if the machine requires it.",
            "Suds drop and the cycle continues.",
            SAFE_SELF_SERVICE,
            "If suds continue after a reduced dose, book service rather than adding extra chemicals.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="OE", appliance_types=("washer",),
    problem="Water overflow has been detected.",
    source="Samsung AU washer information codes (OE, OC).",
    steps=(
        TroubleStep(
            "oe-stop", "Stop water from entering",
            "Turn the washer off. Close the water taps. Do not start another cycle. If water is still rising, "
            "keep clear of the machine and the floor around it.",
            "Water stops entering the drum.",
            CAUTION,
            "Overflow can be an inlet-valve fault. Book a technician. Do not open the cabinet or defeat sensors.",
        ),
    ),
    service_if_unresolved=True,
))

_add(WASHER_GUIDES, ErrorGuide(
    code="LE", appliance_types=("washer",),
    problem="A leak has been detected.",
    source="Samsung support: LE, LC, LC1.",
    steps=(
        TroubleStep(
            "le-stop", "Stop the machine and the water",
            "Turn the washer off and unplug it from the wall if you can do so without standing in water. Close the "
            "water taps. Do not run another cycle.",
            "The leak is contained and power is off.",
            CAUTION,
            "Leaks need a technician. Do not tilt the machine to look underneath while it is full.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="TE", appliance_types=("washer", "dryer"),
    problem="A temperature sensor (thermistor) error.",
    source="Samsung UK/AU/DE: tE, tE1–tE3, tC1–tC3; AU also groups HE/HC with temperature faults on some washers.",
    steps=(
        TroubleStep(
            "te-power", "Power-cycle the appliance",
            "Turn the appliance off, wait two minutes, and turn it back on. Start the cycle again. Do not open the "
            "cabinet or test electrical parts.",
            "The temperature-sensor code has cleared.",
            SAFE_SELF_SERVICE,
            "If the code returns, a technician must inspect the sensor. This is not a user repair.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="HE", appliance_types=("washer", "dryer"),
    problem="A heating or temperature-related fault. On some washers Samsung groups HE/HC with temperature-sensor errors; on dryers HE/HE1 is a heater fault.",
    source="Samsung AU washer temperature-sensor group (HC, HE, tE); commonly published dryer heater codes HE/HE1.",
    steps=(
        TroubleStep(
            "he-lint", "Clear lint and the exhaust path (dryers)",
            "For a dryer: clean the lint filter. Check that the exhaust vent is not crushed or blocked at the wall. "
            "Do not run the dryer with a blocked vent. For a washer: skip this step.",
            "The lint filter is clean and the vent is open. This does not prove the heater works.",
            SAFE_SELF_SERVICE,
            "If the heating code remains, stop. Heater and thermistor work is professional.",
        ),
        TroubleStep(
            "he-power", "Power-cycle",
            "Turn the appliance off, wait two minutes, and turn it on again. Do not test heating elements, "
            "and do not bypass thermal fuses.",
            "The heating code has cleared.",
            SAFE_SELF_SERVICE,
            "If it returns, book Samsung service. Do not continue running a dryer that will not heat.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="3E", appliance_types=("washer",),
    problem="A motor or motor-electronics fault.",
    source="Samsung DE: 3C / 3E, 3C1–3C4. Contact Samsung service.",
    steps=(
        TroubleStep(
            "3e-service", "Motor faults need a technician",
            "Turn the washer off. Do not open the cabinet, and do not try to turn the drum motor by hand with power applied.",
            "The machine is powered down and ready for service.",
            PROFESSIONAL_REQUIRED,
            "Book Samsung service. There is no safe self-service motor repair.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="1E", appliance_types=("washer",),
    problem="A water-level (pressure sensor) fault.",
    source="Samsung DE: 1C / 1E. Contact Samsung service.",
    steps=(
        TroubleStep(
            "1e-service", "Water-level sensor faults need a technician",
            "Turn the washer off and do not start another cycle. Do not puncture or replace the pressure hose yourself.",
            "The machine is idle and ready for service.",
            PROFESSIONAL_REQUIRED,
            "Book Samsung service.",
        ),
    ),
))

_add(WASHER_GUIDES, ErrorGuide(
    code="UC", appliance_types=("washer",),
    problem="A mains-power or voltage fault.",
    source="Samsung DE: UC / 9C.",
    steps=(
        TroubleStep(
            "uc-power", "Check the wall outlet",
            "Turn the washer off. Confirm it is plugged firmly into a working grounded outlet. Do not use an "
            "extension cord. Do not open the power board.",
            "The outlet is sound and the code has cleared after a restart.",
            CAUTION,
            "If the code remains, unplug the washer and book service. Do not inspect live wiring.",
        ),
    ),
))

_add(DRYER_GUIDES, WASHER_GUIDES["DE"])
_add(DRYER_GUIDES, WASHER_GUIDES["TE"])
_add(DRYER_GUIDES, WASHER_GUIDES["HE"])


def appliance_type_from_model(model: str) -> Optional[str]:
    m = (model or "").upper().replace(" ", "")
    if m.startswith(("WF", "WA", "WW", "WD")):
        return "washer"
    if m.startswith(("DV", "DVE", "DVG", "DC")):
        return "dryer"
    if m.startswith(("RF", "RB", "RS", "RQ")):
        return "refrigerator"
    if m.startswith(("DW", "DD")):
        return "dishwasher"
    return None


def normalize_error_code(code: str, appliance_type: str) -> str:
    raw = _norm(code)
    if not raw:
        return ""
    if appliance_type == "washer":
        return _WASHER_ALIASES.get(raw, raw)
    if appliance_type == "dryer":
        return _DRYER_ALIASES.get(raw, raw)
    return raw


def lookup_guide(model: str, error_code: str, appliance_type: str = "") -> Optional[ErrorGuide]:
    kind = (appliance_type or appliance_type_from_model(model) or "").lower()
    code = normalize_error_code(error_code, kind)
    if not code:
        return None
    if kind == "dryer":
        return DRYER_GUIDES.get(code)
    if kind == "washer":
        return WASHER_GUIDES.get(code)
    return WASHER_GUIDES.get(code) or DRYER_GUIDES.get(code)


def steps_payload(model: str, error_code: str, appliance_type: str = "") -> Dict:
    """Return troubleshooting steps, or a structured unknown-code result.

    Never invents a meaning for a code that is not in the knowledge base.
    """
    kind = (appliance_type or appliance_type_from_model(model) or "").lower()
    code = normalize_error_code(error_code, kind)
    if not error_code or not code:
        return {
            "status": "error",
            "error": "missing_error_code",
            "instruction": "Read the appliance status or diagnostics first. Do not guess an error code.",
        }
    guide = lookup_guide(model, error_code, kind)
    if guide is None or (kind and kind not in guide.appliance_types):
        return {
            "status": "unknown_error_code",
            "error_code": error_code,
            "normalized": code,
            "appliance_type": kind or None,
            "model": model or None,
            "meaning": None,
            "steps": [],
            "instruction": (
                "This error code is not in the grounded knowledge base for this appliance. "
                "Do not invent a meaning. Escalate to Samsung service."
            ),
        }
    steps = []
    for s in guide.steps:
        if s.safety == PROFESSIONAL_REQUIRED:
            spoken = (
                "Do not attempt this repair yourself. %s A Samsung technician should handle it."
                % guide.problem
            )
        elif s.safety == CAUTION:
            spoken = s.instructions + " Skip this step if you are not comfortable; we can book service instead."
        else:
            spoken = s.instructions
        steps.append({
            "step_id": s.step_id,
            "title": s.title,
            "problem": guide.problem,
            "instructions": spoken,
            "expected_result": s.expected_result,
            "safety": s.safety,
            "escalation": s.escalation,
            "may_send_command": s.may_send_command or None,
        })
    return {
        "status": "ok",
        "error_code": code,
        "model": model or None,
        "appliance_type": kind or None,
        "problem": guide.problem,
        "source": guide.source,
        "service_if_unresolved": guide.service_if_unresolved,
        "steps": steps,
    }


def step_by_id(step_id: str) -> Optional[TroubleStep]:
    for table in (WASHER_GUIDES, DRYER_GUIDES):
        for guide in table.values():
            for step in guide.steps:
                if step.step_id == step_id:
                    return step
    return None
