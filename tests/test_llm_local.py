"""The local-model backend (vLLM's OpenAI-compatible API), with the server replaced
by scripted replies, and the model-server launcher's GPU plan (no GPU needed)."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from duet_voice import llm_local
from duet_voice.coordinator import Coordinator, Superseded
from duet_voice.llm_local import LocalThinker, salvage_calls

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import llm_server  # noqa: E402


def reply(content=None, calls=()):
    tool_calls = [NS(id="call_%d" % i, function=NS(name=name, arguments=json.dumps(args) if isinstance(args, dict) else args))
                  for i, (name, args) in enumerate(calls)]
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls or None))],
              usage=NS(prompt_tokens=100, completion_tokens=10))


class Box:
    def __init__(self):
        self.coord = Coordinator(commit_hold_s=0.0)
        self.coord.turn_committed()
        self.calls = []
        self.on_call = None

    async def call(self, tool, args, epoch=None):
        self.calls.append((tool, args))
        if self.on_call:
            self.on_call()
        return json.dumps({"status": "success", "tool": tool})


def run(script, box=None, **kw):
    th = LocalThinker(model="qwen-test")
    sent = []

    async def fake_create(messages, tools):
        sent.append(([dict(m) for m in messages], [t["function"]["name"] for t in tools]))
        return script.pop(0)
    th._create = fake_create
    box = box or Box()

    async def go():
        return [ev async for ev in th.run("track order XK42Q7", box, **kw)]
    return asyncio.run(go()), box, sent, th


def test_a_tool_call_then_an_answer_keeps_a_well_formed_history():
    evs, box, sent, th = run([reply(calls=[("track_order", {"order_id": "XK42Q7"})]),
                              reply("It is out for delivery.")])
    assert box.calls == [("track_order", {"order_id": "XK42Q7"})]
    assert [e.kind for e in evs] == ["decided", "tool_start", "tool_done", "say"]
    assert evs[-1].text == "It is out for delivery." and evs[-1].usage["calls"] == 2
    roles = [m["role"] for m in th.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert th.history[1]["tool_calls"][0]["function"]["name"] == "track_order"
    assert th.history[2]["tool_call_id"] == th.history[1]["tool_calls"][0]["id"]
    # the first request offered keep_listening; the second did not
    assert "keep_listening" in sent[0][1] and "keep_listening" not in sent[1][1]


def test_keep_listening_alone_acts_on_nothing_and_remembers_nothing():
    evs, box, sent, th = run([reply(calls=[("keep_listening", {"reason": "the id is still coming"})])])
    assert [e.kind for e in evs] == ["listen"] and evs[0].text == "the id is still coming"
    assert box.calls == [] and th.history == []


def test_keep_listening_next_to_real_work_is_answered_too():
    evs, box, sent, th = run([reply(calls=[("keep_listening", {"reason": "x"}), ("track_order", {"order_id": "A1"})]),
                              reply("Done.")])
    assert box.calls == [("track_order", {"order_id": "A1"})]
    tool_msgs = [m for m in th.history if m["role"] == "tool"]
    assert len(tool_msgs) == 2  # every call gets a response


def test_a_tool_call_left_in_the_text_is_recovered():
    raw = 'Sure.<tool_call>\n{"name": "track_order", "arguments": {"order_id": "B7"}}\n</tool_call>'
    evs, box, sent, th = run([reply(raw), reply("Out for delivery.")])
    assert box.calls == [("track_order", {"order_id": "B7"})]
    calls, rest = salvage_calls(raw)
    assert calls[0]["name"] == "track_order" and rest == "Sure."


def test_arguments_that_are_not_json_are_refused_not_executed():
    evs, box, sent, th = run([reply(calls=[("track_order", "{order_id: B7")]),
                              reply(calls=[("track_order", {"order_id": "B7"})]),
                              reply("Out for delivery.")])
    assert box.calls == [("track_order", {"order_id": "B7"})]
    assert "not a valid JSON object" in [m for m in th.history if m["role"] == "tool"][0]["content"]


def test_an_empty_reply_is_asked_again_once():
    evs, box, sent, th = run([reply(""), reply("Hello!")])
    assert evs[-1].text == "Hello!" and len(sent) == 2


def test_a_plan_overtaken_during_a_tool_call_stops_at_once():
    box = Box()
    box.on_call = box.coord.user_started_speaking   # the user speaks while the call runs
    with pytest.raises(Superseded):
        run([reply(calls=[("track_order", {"order_id": "A1"})]), reply("never asked")], box=box)


def test_the_talker_on_the_local_model(monkeypatch):
    class Completions:
        async def create(self, **kw):
            assert kw["messages"][0]["role"] == "system" and kw["max_tokens"] <= 64
            return reply("Sure, checking that now.")
    monkeypatch.setattr(llm_local, "client", lambda: NS(chat=NS(completions=Completions())))
    usage = {}
    assert asyncio.run(llm_local.acknowledge("where is my order", usage)) == "Sure, checking that now."
    assert usage["calls"] == 1


def test_sampling_follows_the_model_card_with_a_fixed_seed(monkeypatch):
    import dataclasses
    from duet_voice import config
    s = llm_local.sampling()
    assert (s["temperature"], s["top_p"], s["extra_body"]["top_k"], s["seed"]) == (0.7, 0.8, 20, 7)
    monkeypatch.setattr(llm_local, "CONFIG", dataclasses.replace(config.CONFIG, temperature="0"))
    assert llm_local.sampling()["temperature"] == 0.0


# --- the model-server launcher ------------------------------------------------------------------

def gpu(total_mib, cc, used_mib=500):
    return lambda: {"index": "0", "name": "test GPU", "total_mib": total_mib, "used_mib": used_mib,
                    "compute_capability": cc}


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("DUET_LLM_VARIANT", "DUET_LLM_GPU_GIB", "DUET_LLM_GPUS", "DUET_LLM_TP"):
        monkeypatch.delenv(name, raising=False)


def test_the_4_bit_build_everywhere_with_the_same_budget_on_any_size(monkeypatch, clean_env):
    plans = []
    for total, cc in ((46068, 8.9), (49140, 8.6), (40960, 8.0), (81559, 9.0)):  # L40S, A6000, A100 40GB, H100
        monkeypatch.setattr(llm_server, "gpu", gpu(total, cc))
        plans.append(llm_server.plan())
    assert {p["variant"] for p in plans} == {"w4a16"}
    assert {p["budget_gib"] for p in plans} == {22.0}                 # the same memory on every card
    assert all(p["repo"].startswith("RedHatAI/") and p["tensor_parallel"] == 1 for p in plans)
    assert 0.48 < plans[0]["gpu_memory_utilization"] < 0.50          # 22 GiB of a 48 GB L40S
    cmd = llm_server.command("python", plans[0])
    assert "--tool-call-parser" in cmd and cmd[cmd.index("--tool-call-parser") + 1] == "hermes"
    assert "--tensor-parallel-size" not in cmd


def test_the_fp8_build_on_request(monkeypatch, clean_env):
    monkeypatch.setenv("DUET_LLM_VARIANT", "fp8")
    monkeypatch.setattr(llm_server, "gpu", gpu(81559, 9.0))          # an 80 GB H100
    p = llm_server.plan()
    assert p["variant"] == "fp8" and p["budget_gib"] == 33.0 and not p["notes"]
    monkeypatch.setattr(llm_server, "gpu", gpu(40960, 8.0))          # an A100: warned, not silent
    assert any("8.9" in n for n in llm_server.plan()["notes"])


def test_the_model_split_over_two_half_free_gpus(monkeypatch, clean_env):
    monkeypatch.setenv("DUET_LLM_GPUS", "4,7")
    monkeypatch.setattr(llm_server, "gpu", gpu(40960, 8.0, used_mib=24000))   # a shared A100, ~16.5 GiB free
    p = llm_server.plan()
    assert p["tensor_parallel"] == 2 and p["per_gpu_gib"] == 12.5 and p["gpus"] == "4,7"
    cmd = llm_server.command("python", p)
    assert cmd[cmd.index("--tensor-parallel-size") + 1] == "2"
    env = llm_server.server_env(p, {"CUDA_VISIBLE_DEVICES": "0"})
    assert env["CUDA_VISIBLE_DEVICES"] == "4,7" and env["VLLM_WORKER_MULTIPROC_METHOD"] == "spawn"


def test_a_gpu_busy_with_other_jobs_is_refused_with_a_way_out(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(llm_server, "gpu", gpu(40960, 8.0, used_mib=25700))  # 14.9 GiB free, as on 28 Sep
    monkeypatch.setattr(llm_server, "healthy", lambda *a, **k: False)
    with pytest.raises(SystemExit, match="place_gpus"):
        llm_server.start("python", tmp_path)


def test_a_gpu_too_small_is_refused_before_anything_starts(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(llm_server, "gpu", gpu(6144, 8.6))           # this laptop's 6 GB GPU
    monkeypatch.setattr(llm_server, "healthy", lambda *a, **k: False)
    with pytest.raises(SystemExit, match="too small"):
        llm_server.start("python", tmp_path)


def run_reviewed(script):
    """The phone app's thinker: a second look before its answer is spoken."""
    th = LocalThinker(model="qwen-test", review="Second look: anything missing? Else reply OK")
    th._create = lambda messages, tools: asyncio.sleep(0, result=script.pop(0))
    box = Box()

    async def go():
        return [ev async for ev in th.run("set an alarm for 4:30", box)]
    return asyncio.run(go()), box, th


def test_a_second_look_that_finds_nothing_keeps_the_draft_and_a_clean_history():
    evs, box, th = run_reviewed([reply("Set for 4:30."), reply("OK")])
    assert [e.kind for e in evs] == ["decided", "review", "say"] and evs[-1].text == "Set for 4:30."
    assert [m["role"] for m in th.history] == ["user", "assistant"]


def test_a_second_look_calls_the_action_the_draft_only_claimed():
    evs, box, th = run_reviewed([reply("Set for 4:30."), reply(calls=[("set_alarm", {"time": "04:30"})]),
                                 reply("Set for 4:30 in the morning.\n\nOK")])
    assert box.calls == [("set_alarm", {"time": "04:30"})]
    assert evs[-1].kind == "say" and evs[-1].text == "Set for 4:30 in the morning."


def test_the_benchmark_thinker_takes_no_second_look():
    evs, box, sent, th = run([reply("Hello!")])
    assert [e.kind for e in evs] == ["decided", "say"] and th.review is None
