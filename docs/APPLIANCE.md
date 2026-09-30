# DUET Smart Appliance Care

Samsung appliance support on the existing dual-mind agent. This is the Round-1
use-case extension (`DUET_USE_CASE=appliance`). The FDB-v3 path is unchanged
when the setting is left at `benchmark`.

## Architecture

The talker, thinker, coordinator, epochs, commit gate, idempotency ledger and
failure policy are the same objects as the benchmark agent. The extension is a
second **toolset** and a deterministic **session state**, not a second orchestrator.

```
user speech
    → talker (short acknowledgement, never a result)
    → thinker (plans tool calls)
         → coordinator (epoch · commit gate · ledger · failure policy)
              → appliance tools
                    → SmartThings adapter (mock or public REST)
                    → troubleshooting knowledge base
                    → Samsung service adapter (mock unless an API URL is set)
              → ApplianceSessionState (what is true)
    → spoken answer grounded in tool results
```

Principle: **the LLM decides what should happen; deterministic code decides
what is true and whether an action is allowed.**

## Workflow

Diagnose → Troubleshoot → Verify → Escalate → Book service → Human handoff.

1. `list_appliances` — SmartThings discovery, optional type filter.
2. `get_appliance_status` / `get_appliance_diagnostics` — live read. Offline,
   unhealthy, missing diagnostics and auth failures are returned as such.
   Nothing is invented.
3. `get_troubleshooting_steps` — grounded knowledge base keyed by model and
   error code. Unknown codes return `unknown_error_code`.
4. `record_troubleshooting_step` — completed, skipped, already_done, or failed.
   Professional-only steps cannot be marked completed by the customer.
   Optional SmartThings commands return `ACCEPTED` (queued), not completion.
5. `verify_appliance_state` — a fresh status read. `resolved` is true only when
   the device is online and no error remains.
6. `find_service_slots` / `book_samsung_service` / `get_service_request` —
   booking is exactly-once per device. A timeout is remembered as unknown and
   is never blindly retried.
7. `prepare_human_handoff` — technician packet from session state.

## Interruptions

New speech advances the coordinator epoch. A plan made on the old words is
superseded at the gate and never executed. Selecting a different appliance
increments `workflow_id` and stops the old troubleshooting path from driving
the current selection. Late tool results may store facts on the device they
belong to; they do not steal the current selection.

Corrections the demo is built around:

- “It's my washer.” / “Wait, actually the dryer.”
- “Tomorrow afternoon.” / “Actually Friday morning.”
- “I already cleaned that.”
- “The error changed.”

## SmartThings

| Adapter | When | What is real |
|---|---|---|
| `MockSmartThingsAdapter` | default (`DUET_SMARTTHINGS=mock`) | In-process household: washer (UE), dryer (HE), fridge (healthy), dishwasher (offline). Commands queue as `ACCEPTED`. Webhooks are `apply_webhook`. |
| `RealSmartThingsAdapter` | `DUET_SMARTTHINGS=real` and `SMARTTHINGS_TOKEN` | Public REST API: `GET /v1/devices`, status, health, `POST .../commands`. Device identity is cached for the session; health and status are always live. Diagnostics reuse the last status body (no second GET). Missing attributes stay missing. Pause/start on a dryer uses `dryerOperatingState`. |

A command result of `ACCEPTED` is queued, not proof the appliance finished.
Rate limits, auth failures, timeouts and offline/unhealthy health states are
mapped to structured errors.

## Troubleshooting

`duet_voice/appliance/troubleshooting.py` only contains codes published on
Samsung support pages (washer 4E/5E/UE/dE/tE/HE/3E/1E/OE/LE/SUD/UC and the
dryer subset dE/tE/HE). Safety:

- `SAFE_SELF_SERVICE` — redistribute a load, open a tap, close a door.
- `CAUTION` — drain-pump filter, inlet mesh; skip if uncomfortable.
- `PROFESSIONAL_REQUIRED` — motor, sealed sensors, heater internals.

No electrical bypass, refrigerant, live-mains or lock-defeat instructions.

## Samsung service

There is **no authorized public Samsung technician-scheduling API** in this
repository.

| Adapter | When |
|---|---|
| `MockSamsungServiceAdapter` | default; demo bookings (`SSR-0001`, …) |
| `RealSamsungServiceAdapter` | only if `SAMSUNG_SERVICE_API_URL` is an **https** URL; otherwise it refuses and the agent keeps the mock desk |

Simulated bookings are labelled `simulated: true` in tool results.

## Human handoff

`prepare_human_handoff` builds a packet from session state: appliance / model /
serial, problem, error code, SmartThings snapshot, diagnostics, steps
attempted and skipped, verification, service request id, appointment, and user
observations. The customer should not have to repeat the problem.

## Truthfulness

- Do not say the appliance is fixed unless verification reports `resolved`.
- Do not say service is booked unless `book_samsung_service` or
  `get_service_request` confirms it.
- Do not say SmartThings completed a command when it returned `ACCEPTED`.
- Do not invent an error-code meaning. Unknown codes escalate.

## Setup

```bash
# Scripted demo (no GPU, no language model, mock household and mock service)
python -m duet_voice.appliance.demo

# Typed chat with the real thinker (same tools). Without --use-case appliance
# the process loads FDB-v3 and a washer UE line becomes flight_search.
python -m duet_voice.chat --use-case appliance
python -m duet_voice.chat --use-case appliance --once "My Samsung washing machine is showing a UE error"

# Live voice, same tools (needs the usual local model server)
export DUET_USE_CASE=appliance          # bash / zsh, same shell
set DUET_USE_CASE=appliance             # Windows cmd
$env:DUET_USE_CASE="appliance"          # Windows PowerShell
python -m duet_voice.agent console
```

The process prints `use_case=appliance` and `list_appliances` at start. If you
see `flight_search`, the appliance flag was not set.

Optional:

| Variable | Effect |
|---|---|
| `DUET_USE_CASE=benchmark` | Default. FDB-v3 tools. |
| `DUET_USE_CASE=appliance` | This use case. |
| `DUET_SMARTTHINGS=mock` | Default household. |
| `DUET_SMARTTHINGS=real` plus `SMARTTHINGS_TOKEN` | Public SmartThings API. |
| `SAMSUNG_SERVICE_API_URL` | Authorized **https** service backend. `http://` and empty values are refused. |

Tokens are never written to traces. Serials and secrets are redacted.

## Demo flow

The scripted demo (`python -m duet_voice.appliance.demo`) walks:

1. User reports a washer problem.
2–5. Identify, status, diagnostics, documented UE (unbalanced load).
6–8. User corrects to the dryer; washer workflow is left behind; dryer HE is diagnosed.
9–10. “I already cleaned that” skips the lint step and continues.
11–12. Verification still shows HE; service is offered.
13–14. “Tomorrow afternoon” then “Actually Friday morning”.
15. Friday morning is booked exactly once (“Did you book it?” does not book again).
16. Technician handoff with the full context.

## Vision

`duet_voice/appliance/vision.py` is a stub for reading an error code from a
camera later. It is not a Round-1 tool and is not registered with the thinker.

## Limitations

- The demo household and the service desk are **simulated**.
- Real SmartThings only exposes what the public API returns. Many appliances
  do not publish a diagnostic error code over SmartThings.
- There is no private Samsung service-booking API here.
- Camera error-code reading is not enabled.
- The benchmark remains the default; this use case is opt-in so FDB-v3
  performance is not affected.
