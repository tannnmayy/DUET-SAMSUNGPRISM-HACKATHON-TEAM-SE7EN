# DUET SmartThings Family Care

Caregiver voice on the existing dual-mind agent. This is a **secondary**
opt-in (`DUET_USE_CASE=family`). The FDB-v3 path and Smart Appliance Care are
unchanged when the setting is left at `benchmark` or `appliance`.

Live ears are the same **faster-whisper** stack the rest of DUET uses. There
is no extra speech model.

## Architecture

The talker, thinker, coordinator, epochs, commit gate, idempotency ledger and
failure policy are the same objects as the benchmark agent. The extension is a
second **toolset** and a deterministic **session state**.

```
user speech (faster-whisper)
    → talker (short acknowledgement, never a result)
    → thinker (plans tool calls)
         → coordinator (epoch · commit gate · ledger · failure policy)
              → family tools
                    → SmartThings Family Care household (mock)
                    → Knox consent vault (mock; real adapter refuses without a token)
                    → Galaxy Watch vitals (only after consent)
                    → DUET messenger (mock texts/calls)
              → FamilySessionState (what is true)
    → spoken answer grounded in tool results
```

Principle: **the LLM decides what should happen; deterministic code decides
what is true and whether an action is allowed.** Watch heart rate is never
returned without Knox `health_read` consent. A text with the same contact and
purpose is never sent twice.

## Workflow

Check member → (optional) Knox consent → Watch vitals → text or call → handoff.

1. `list_household` — Family Care members, optional name filter.
2. `get_member_status` — presence and inactivity. Live read; skips the ledger.
3. `get_watch_vitals` — `consent_required` until Knox allows it. Never invents HR.
4. `request_health_consent` — session-scoped Knox `health_read` for one member.
5. `send_care_text` — exactly-once per contact+purpose (DUET messaging).
6. `place_care_call` — ambulance/emergency only with `explicit_emergency` true.
7. `get_outbound_status` — “did you text her?”
8. `prepare_care_handoff` — caregiver packet from session state.

## Demo flow

`python -m duet_voice.family.demo`:

1. “Check on Mum” — inactivity alert.
2. “Wait, actually Dad” — new workflow, recent presence.
3. Heart rate without consent → `consent_required`.
4. Knox grant, then Watch band **elevated**.
5. Ambulance without an explicit emergency → `refused`. Text Priya **once**.
6. “Did you text her?” → `already_done`.
7. Caregiver handoff.

## Simulated vs real

| Piece | Status |
|---|---|
| Dual-mind agent, coordinator, epochs, gate, ledger | Existing DUET |
| Speech | Existing faster-whisper (live agent) |
| SmartThings Family Care | Mock household. Public SmartThings does not expose inactivity alerts |
| Galaxy Watch heart rate | Mock. Served only after Knox consent |
| Knox | Mock vault by default. `RealKnoxVault` refuses unless `DUET_KNOX_TOKEN` is set; URL must be https |
| Texts / calls | Mock messenger. `DUET_FAMILY_SMS_URL` must be https or it is refused |

Tokens, phones and vitals are redacted in traces. Member ids stay visible.

## Setup

```bash
python -m duet_voice.family.demo     # no GPU; mock household, Knox, Watch, texts
python -m duet_voice.chat --use-case family
export DUET_USE_CASE=family          # live voice, same tools, faster-whisper
python -m duet_voice.agent console
```

Leave `DUET_USE_CASE` unset for any FDB / `reproduce.sh` run.
