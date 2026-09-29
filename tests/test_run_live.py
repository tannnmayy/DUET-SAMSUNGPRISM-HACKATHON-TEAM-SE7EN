"""Continuing a live run that crashed (bench/run_live.py)."""

import types

from bench import run_live


def test_a_recording_never_scored_runs_again_when_a_run_continues(tmp_path):
    done, half, fresh = (tmp_path / n for n in ("travel_01_aaa", "travel_02_bbb", "travel_03_ccc"))
    for d in (done, half, fresh):
        d.mkdir()
    (done / "output_duet.wav").write_bytes(b"x")
    (done / "result_duet.json").write_text("{}")
    (half / "output_duet.wav").write_bytes(b"x")          # recorded, crashed before scoring
    args = types.SimpleNamespace(data_dir=str(tmp_path), force=False, only="")
    assert run_live.drop_unfinished(args) == 1
    assert (done / "output_duet.wav").exists() and not (half / "output_duet.wav").exists()


def test_force_leaves_everything_to_the_runner(tmp_path):
    (tmp_path / "travel_02_bbb").mkdir()
    (tmp_path / "travel_02_bbb" / "output_duet.wav").write_bytes(b"x")
    args = types.SimpleNamespace(data_dir=str(tmp_path), force=True, only="")
    assert run_live.drop_unfinished(args) == 0
    assert (tmp_path / "travel_02_bbb" / "output_duet.wav").exists()
