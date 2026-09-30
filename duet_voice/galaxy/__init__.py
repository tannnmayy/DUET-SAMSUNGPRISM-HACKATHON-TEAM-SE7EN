"""DUET for Galaxy: the use-case extension (Samsung PRISM, the 20% part).

The same two-mind agent and coordinator as the benchmark submission, on a Galaxy
phone: the phone app joins a LiveKit room, DUET listens and talks full duplex, and
every action DUET takes on the phone (an alarm, a setting, a call, a smart-home
device) is sent to the phone over LiveKit RPC and passes the DUET coordinator
first (commit gate, exactly-once ledger, failure policy).

Three modes share one platform: `assistant` (phone help and troubleshooting),
`care` (a companion for an older person living alone) and `drive` (an in-car
co-driver with car-to-home control). Nothing here is used by the benchmark run.
"""
