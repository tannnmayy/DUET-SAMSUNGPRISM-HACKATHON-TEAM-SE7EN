"""The GPU placement for shared machines (bench/place_gpus.py)."""

import pytest

from bench import place_gpus


def rows(*free, util=50):
    return [{"index": str(i), "name": "A100 40GB", "total": 40960, "free": f, "util": util}
            for i, f in enumerate(free)]


def test_a_free_gpu_takes_everything_as_on_samsungs_machine():
    plan = place_gpus.choose(rows(15700, 39900, 18000), offline=False)
    assert plan == {"single": "1"}
    lines = place_gpus.exports(plan)
    assert lines[0] == "export CUDA_VISIBLE_DEVICES=1" and "unset DUET_LLM_GPUS" in lines


def test_the_dgx_on_28_sep_splits_the_model_and_places_the_rest_on_leftovers():
    # every A100 had 15-20 GB free (other people's jobs)
    free = (15700, 15700, 16000, 15600, 18800, 18300, 18700, 19900)
    plan = place_gpus.choose(rows(*free), offline=False)
    assert plan["llm"] == ["4", "7"]                      # the two freest, 13 GiB each
    assert plan["agent"] == "6" and plan["scoring"] == "5"
    lines = place_gpus.exports(plan)
    assert "export DUET_LLM_GPUS=4,7" in lines and "export DUET_AGENT_GPUS=6" in lines
    assert "export DUET_SCORING_GPUS=5" in lines


def test_an_offline_evaluation_needs_only_the_model():
    assert place_gpus.choose(rows(15000, 24000), offline=True) == {"single": "1"}
    plan = place_gpus.choose(rows(14000, 15000, 9000), offline=True)
    assert plan == {"llm": ["0", "1"]}


def test_no_room_says_what_is_free():
    with pytest.raises(SystemExit, match="GPU 0 9000 MiB"):
        place_gpus.choose(rows(9000, 12000), offline=True)
