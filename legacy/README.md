# Legacy: the first Theme 05 kit and DUET v1 (Sep 2026)

Until 26 Sep 2026, Samsung's Theme 05 was scored with its own participant kit
(`kit_v1/harness`, `kit_v1/scenarios`, `kit_v1/eval_submission.py`). DUET v1 was
built for that kit: a rule-based planner with the coordinator ideas this project
still uses (epochs, the reversibility-gated commit, slot provenance, calibrated
abstention), 1,000+ tests, and a 97.4 weighted score on the kit's public set.

Samsung then replaced the kit with the public Full-Duplex-Bench v3. The mentors
also advised against static rule-based planners. The current agent
(`duet_voice/`) keeps DUET v1's coordinator design and replaces its planner with
an LLM thinker.

This folder is kept as the research record (see `kit_v1/notes/`). Nothing in it
is used by the current agent, the reproduction script or the reported results,
and its tests are not maintained.
