"""Tests for epoch-versioned state and slot provenance (M1, M3, M6)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duet.state import ConversationState, SRC_AUDIO, SRC_TEXT


def test_snapshot_shape_matches_protocol_convention():
    st = ConversationState()
    st.set_intent("book_flight")
    st.set_slot("destination", "Denver")
    snap = st.snapshot()
    assert set(snap) == {"intent", "slots"}
    assert snap["intent"] == "book_flight"
    assert snap["slots"] == {"destination": "Denver"}


def test_snapshot_omits_none_values():
    st = ConversationState()
    st.set_slot("destination", "Denver")
    st.set_slot("date", None)
    assert st.snapshot()["slots"] == {"destination": "Denver"}


def test_snapshot_values_are_plain_scalars():
    """The scorer does norm(actual) == norm(expected); nested objects break it."""
    st = ConversationState()
    st.set_slot("destination", "New York", confidence=0.4, source=SRC_AUDIO)
    value = st.snapshot()["slots"]["destination"]
    assert isinstance(value, str)
    assert value == "New York"


def test_provenance_is_retained_on_overwrite():
    st = ConversationState()
    st.set_slot("destination", "Boston", at_ms=900)
    st.set_slot("destination", "New York", at_ms=1950)
    assert st.get("destination") == "New York"
    assert st.previous_value("destination") == "Boston"
    assert len(st.superseded["destination"]) == 1


def test_confidence_and_source_are_recorded():
    st = ConversationState()
    st.set_slot("destination", "Austin", confidence=0.42, source=SRC_AUDIO)
    slot = st.get_slot("destination")
    assert slot.confidence == 0.42
    assert slot.source == SRC_AUDIO
    assert not slot.is_confident(0.55)
    assert st.low_confidence_slots(0.55) == [slot]


def test_changed_since_is_the_invalidation_probe():
    st = ConversationState()
    st.set_slot("destination", "Boston")
    st.set_slot("passenger_name", "Alice")
    epoch_before = st.epoch

    st.bump_epoch("interruption", at_ms=1900)
    st.set_slot("destination", "New York", at_ms=1900)

    changed = st.changed_since(st.epoch)
    assert changed == {"destination"}, changed
    # passenger_name was written in the older epoch and is untouched
    assert "passenger_name" not in changed
    assert st.changed_since(epoch_before) == {"destination", "passenger_name"}


def test_dropped_slots_count_as_changed():
    st = ConversationState()
    st.set_slot("destination", "Boston")
    st.bump_epoch("retraction", at_ms=1500)
    st.drop_slot("destination")
    assert "destination" in st.changed_since(st.epoch)
    assert not st.has("destination")
    assert st.snapshot()["slots"] == {}


def test_bump_epoch_increments_and_records_reason():
    st = ConversationState()
    assert st.epoch == 0
    new = st.bump_epoch("interruption:correction", at_ms=1900)
    assert new == 1 and st.epoch == 1
    assert st.last_bump_reason == "interruption:correction"
    assert st.last_bump_at_ms == 1900


def test_undo_restores_previous_slots_but_advances_epoch():
    """M6. Restoring must NOT rewind the epoch counter, or work spawned under
    the current epoch would wrongly be considered live again."""
    st = ConversationState()
    st.set_intent("book_flight")
    st.set_slot("destination", "Boston")

    st.bump_epoch("interruption", at_ms=1900)
    st.set_slot("destination", "New York", at_ms=1900)
    epoch_after_correction = st.epoch

    assert st.undo_last() is True
    assert st.get("destination") == "Boston"
    assert st.epoch > epoch_after_correction, "epoch must move forward on undo"


def test_undo_with_no_history_is_safe():
    st = ConversationState()
    assert st.undo_last() is False
    assert st.restore_epoch(99) is False


def test_intent_change_clears_slots():
    st = ConversationState()
    st.set_intent("book_flight")
    st.set_slot("destination", "Boston")
    st.set_slot("passenger_name", "Alice")

    st.bump_epoch("interruption:intent_change", at_ms=2000)
    st.reset_for_intent_change("device_help")

    assert st.intent == "device_help"
    assert st.snapshot()["slots"] == {}
    # provenance survives so we can still explain what was abandoned
    assert st.previous_value("destination") == "Boston"


def test_explain_last_change_names_the_correction():
    st = ConversationState()
    st.set_slot("destination", "Boston")
    st.bump_epoch("interruption", at_ms=1900)
    st.set_slot("destination", "New York", at_ms=1900)
    assert st.explain_last_change() == "destination: Boston -> New York"


def test_checkpoint_is_isolated_from_later_mutation():
    """Checkpoints must not alias live state, or undo would be a no-op."""
    st = ConversationState()
    st.set_slot("destination", "Boston")
    st.bump_epoch("interruption", at_ms=1)
    st.set_slot("destination", "New York", at_ms=1)
    st.set_slot("date", "Friday", at_ms=1)

    cp = st.checkpoints()[-1]
    assert cp.slots["destination"].value == "Boston"
    assert "date" not in cp.slots


def test_utterance_id_is_recorded_as_provenance():
    st = ConversationState()
    st.utterance_id = 7
    st.set_slot("destination", "Denver", span=(12, 18))
    slot = st.get_slot("destination")
    assert slot.utterance_id == 7
    assert slot.span == (12, 18)


def test_default_source_is_text():
    st = ConversationState()
    st.set_slot("destination", "Denver")
    assert st.get_slot("destination").source == SRC_TEXT
