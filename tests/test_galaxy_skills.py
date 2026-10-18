"""DUET's skills (app/www/skills.json, duet_voice/galaxy/skills.py): a capability declared, with no code.

- each entry becomes a tool of its modes, in the built-ins' shape, with its ledger group;
- the fast mind may act on a skill whose words the user said; the confirmation comes from the entry;
- a broken entry is skipped with a reason, never half-loaded; a new entry is read without a restart.
"""

import json
import os

from duet_voice.galaxy import fast, skills
from duet_voice.galaxy.confirm import confirmation
from duet_voice.galaxy.modes import llm_specs, mode


def test_the_shipped_skills_load_and_join_their_modes():
    names = [s["name"] for s in skills.load()]
    assert {"set_auto_rotate", "set_text_size", "set_do_not_disturb", "open_camera", "open_wifi_panel"} <= set(names)
    tools = {t["name"]: t for t in mode("assistant")["tools"]}
    assert tools["set_auto_rotate"]["kind"] == "write" and tools["set_auto_rotate"]["group"] == "auto_rotate"
    assert tools["open_camera"]["kind"] == "ui"
    assert "set_do_not_disturb" in {t["name"] for t in mode("drive")["tools"]}
    assert "set_text_size" in {t["name"] for t in mode("care")["tools"]}
    # the models see the usual three fields only
    assert set(llm_specs([tools["set_text_size"]])[0]) == {"name", "description", "parameters"}


def test_the_fast_mind_may_act_on_a_skill_the_user_named():
    assert fast.vet_plan([("set_auto_rotate", {"on": False})], "turn off auto rotate") == ""
    assert fast.vet_plan([("set_do_not_disturb", {"on": True})], "I'm in a meeting, silence my phone") == ""
    assert fast.vet_plan([("set_auto_rotate", {"on": False})], "make the screen dimmer") != ""
    assert "set_text_size" in {t["name"] for t in fast.fast_specs(mode("assistant")["tools"])}


def test_a_skill_confirms_itself_only_when_done():
    assert confirmation("set_auto_rotate", {"on": False}, {"status": "ok"}) == "Auto-rotate is off."
    assert confirmation("set_text_size", {"size": "large"}, {"status": "ok"}) == "Text size set to large."
    assert confirmation("set_auto_rotate", {"on": True}, {"status": "error"}) is None


def test_templates_fill_like_the_app():
    assert skills.fill("{on?1:0}", {"on": True}) == "1" and skills.fill("{on?1:0}", {"on": "false"}) == "0"
    assert skills.fill("Text: {size}", {"size": "large"}) == "Text: large"


def test_a_broken_entry_is_skipped_and_a_new_one_is_read_without_a_restart(tmp_path):
    good = {"name": "set_touch_vibration", "modes": ["assistant"], "kind": "write", "description": "x",
            "run": {"type": "setting", "namespace": "system", "key": "haptic_feedback_enabled", "value": "{on?1:0}"}}
    bad = [dict(good, name="Bad Name"), dict(good, name="secure_one", run={"type": "setting", "namespace": "secure", "key": "x"}),
           dict(good, name="no_action", run={"type": "intent"}), dict(good, name="what_kind", kind="delete")]
    path = str(tmp_path / "skills.json")
    with open(path, "w") as fh:
        json.dump({"skills": bad}, fh)
    assert skills.load(path) == []
    assert "lower_snake_case" in skills.problem(bad[0]) and "system setting" in skills.problem(bad[1])
    with open(path, "w") as fh:
        json.dump({"skills": bad + [good]}, fh)
    os.utime(path, (os.path.getatime(path), os.path.getmtime(path) + 5))   # a later edit
    assert [s["name"] for s in skills.load(path)] == ["set_touch_vibration"]
    assert [t["name"] for t in skills.specs_for("assistant", path)] == ["set_touch_vibration"]
