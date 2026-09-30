#!/usr/bin/env python3
"""Fast offline evaluation of the thinker: minutes instead of the 90-minute live run.

    python bench/offline_eval.py                          # ASR transcripts from offline_asr.py
    python bench/offline_eval.py --text script            # the scripted text (oracle ears)
    python bench/offline_eval.py --judge --limit 20

It feeds each item's user words to the same thinker (instructions, tool schemas,
coordinator and mock backend as the live agent), runs the tool loop, and scores
the result with the benchmark's own evaluation functions. The live LiveKit run
remains the source of the reported numbers; this tool exists to iterate quickly
and to separate hearing errors (asr vs script) from reasoning errors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from bench.common import FDB_DIR, REPO, discover, provenance  # noqa: E402

load_dotenv(REPO / ".env.local")


def settings(CONFIG) -> Dict[str, Any]:
    """The thinker's effective settings, and, with a local model server, which weights
    it really serves (an OpenAI-compatible server reports them as the model's "root")."""
    s: Dict[str, Any] = {"backend": CONFIG.llm_backend, "seed": CONFIG.seed}
    if CONFIG.llm_backend == "local":
        from duet_voice import llm_local
        smp = llm_local.sampling()
        s.update(temperature=smp["temperature"], top_p=smp["top_p"], top_k=smp["extra_body"]["top_k"],
                 base_url=CONFIG.llm_base_url, served_model=CONFIG.llm_model)
        try:
            import urllib.request
            with urllib.request.urlopen(CONFIG.llm_base_url.rstrip("/") + "/models", timeout=10) as r:
                s["weights"] = json.loads(r.read().decode())["data"][0].get("root")
        except Exception as exc:
            s["weights"] = "unknown (%s)" % type(exc).__name__
    else:
        s["temperature"] = CONFIG.temperature or "model default"
    return s


def turn_audio(folder: Path, segments: List[Dict[str, Any]]):
    """The request's audio, first kept speech to last, as the live agent would send it."""
    from bench.common import read_wav, resample
    from duet_voice.thinker import encode_audio
    kept = [s for s in segments if s.get("text")]
    if not kept:
        return None
    x, sr = read_wav(folder / "input.wav")
    pcm = resample(x, sr)
    a, b = max(0.0, kept[0]["start"] - 0.3), kept[-1]["end"] + 0.3
    return encode_audio(pcm[int(a * 16000): int(b * 16000)])


class Pace:
    """Spaces request starts evenly, for API keys with a low requests-per-minute limit."""

    def __init__(self, rpm: float) -> None:
        self.gap, self.next, self.lock = 60.0 / rpm, 0.0, asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            start = max(now, self.next)
            self.next = start + self.gap
        await asyncio.sleep(start - now)


PACE = None  # set in main() when --rpm is given


async def run_item(sem, args, item, text: str, segments) -> Dict[str, Any]:
    from duet_voice.coordinator import Coordinator
    from duet_voice.fdb_tools import FdbToolbox
    from duet_voice.thinker import RESPOND_NOW_NOTE, make_thinker

    async with sem:
        example_id, speaker, folder, meta = item
        coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
        coord.turn_committed()  # offline, the whole utterance is already in
        toolbox = FdbToolbox("offline-" + folder.name, coord)
        thinker = make_thinker(model=args.model, thinking=args.thinking)
        if PACE is not None:
            attr = "_create" if hasattr(thinker, "_create") else "_generate"
            request = getattr(thinker, attr)

            async def paced(*a, **kw):
                await PACE.wait()
                return await request(*a, **kw)
            setattr(thinker, attr, paced)
        audio = turn_audio(folder, segments) if args.audio and segments else None
        t = time.time()
        final_text, error, steps, listened = "", "", 0, ""
        try:
            # As in the live agent: if the first look decides the user has not
            # finished, the user has in fact gone quiet (the recording is over),
            # so the thinker looks again and must respond.
            for allow_listen, note in ((True, ""), (False, RESPOND_NOW_NOTE)):
                again = False
                async for ev in thinker.run(text, toolbox, audio=audio, note=note, allow_listen=allow_listen):
                    if ev.kind == "say":
                        final_text = ev.text
                    elif ev.kind == "tool_start":
                        steps += 1
                    elif ev.kind == "error":
                        error = ev.text
                    elif ev.kind == "listen":
                        listened, again = ev.text or "yes", True
                if not again:
                    break
        except Exception as exc:  # keep going; the item scores as a failure
            error = "%s: %s" % (type(exc).__name__, exc)
        calls = [{"function": r.tool, "args": r.args} for r in coord.history if r.outcome == "ok"]
        return {"example_id": example_id, "folder": folder.name, "input": text, "calls": calls,
                "final_text": final_text, "error": error, "tool_steps": steps, "kept_listening": listened,
                "seconds": round(time.time() - t, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", choices=["asr", "script"], default="asr")
    ap.add_argument("--asr-file", default=str(REPO / "results" / "offline" / "asr_large-v3-turbo.json"))
    from duet_voice.config import CONFIG
    ap.add_argument("--model", default=CONFIG.thinker_model)
    ap.add_argument("--thinking", default=CONFIG.thinker_thinking)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="comma-separated example ids")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--rpm", type=float, default=0, help="at most this many model requests per minute")
    ap.add_argument("--judge", action="store_true", help="LLM judge for arguments and responses")
    ap.add_argument("--audio", action="store_true", help="the thinker also hears the request audio")
    ap.add_argument("--tag", default="")
    ap.add_argument("--rescore", default="",
                    help="score a saved results/offline/eval_*.json again (e.g. with the judge) "
                         "without calling the thinker")
    args = ap.parse_args()

    return evaluate(args, CONFIG)


def evaluate(args, CONFIG) -> int:
    os.environ.setdefault("FDB_V3_DIR", str(FDB_DIR))
    os.environ["FDB_TOOL_LOG"] = str(REPO / "results" / "offline" / "tool_calls_offline.log")
    sys.path.insert(0, str(FDB_DIR))
    import evaluate_pass_rate as epr  # the benchmark's own scorers
    import evaluate_tool_calls as etc
    from bench import judge

    judge_model = judge.install(etc, epr) if args.judge else None
    items = discover()
    saved = None
    if args.rescore:
        saved = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        kept = {r["folder"] for r in saved["rows"]}
        items = [i for i in items if i[2].name in kept]
        for key in ("text", "audio", "model", "thinking"):
            setattr(args, key, saved["summary"].get(key, getattr(args, key)))
    if args.only:
        wanted = set(args.only.split(","))
        items = [i for i in items if i[0] in wanted or i[2].name in wanted]
    if args.limit:
        items = items[: args.limit]
    asr = {r["folder"]: r for r in json.loads(Path(args.asr_file).read_text(encoding="utf-8"))} \
        if Path(args.asr_file).exists() else {}

    def words(item) -> str:
        if args.text == "script":
            return " ".join(t["user"] for t in item[3]["dialogue"])
        return asr.get(item[2].name, {}).get("transcript", "")

    sem = asyncio.Semaphore(args.concurrency)

    async def run_all():
        global PACE
        PACE = Pace(args.rpm) if args.rpm else None
        return await asyncio.gather(*(run_item(sem, args, it, words(it), asr.get(it[2].name, {}).get("segments"))
                                      for it in items))

    t0 = time.time()
    if saved is not None:
        by_folder = {r["folder"]: r for r in saved["rows"]}
        outs = [{k: by_folder[i[2].name].get(k) for k in
                 ("example_id", "folder", "input", "calls", "final_text", "error", "tool_steps",
                  "kept_listening", "seconds")} for i in items]
    else:
        outs = asyncio.run(run_all())
    rows = []
    for item, out in zip(items, outs):
        scenario = item[3]
        sel = etc.evaluate_scenario(scenario, out["calls"], transcript=out["final_text"],
                                    result_data=None, use_llm=args.judge)
        pas = epr.evaluate_scenario_pass(scenario, out["calls"], transcript=out["final_text"],
                                         result_data=None, use_llm=args.judge)
        rows.append({**out, "difficulty": scenario["difficulty"], "domain": scenario["domain"],
                     "disfluency": scenario.get("disfluency_features", []),
                     "expected": scenario["expected_tool_calls"],
                     "tool_f1": sel["metrics"]["tool_selection_acc"]["score"],
                     "arg_acc": sel["metrics"]["argument_acc"]["score"],
                     "resp": sel["metrics"]["response_qual"].get("score"),
                     "passed": pas["passed"], "why": pas["failure_reason"]})
    n = len(rows)

    def mean(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else float("nan")

    summary = {"n": n, "text": args.text, "audio": args.audio, "model": args.model, "thinking": args.thinking,
               "judge": judge.judge_label() if args.judge else "exact match (no LLM judge)",
               "pass@1": round(sum(r["passed"] for r in rows) / n, 3), "tool_f1": round(mean("tool_f1"), 3),
               "arg_acc": round(mean("arg_acc"), 3), "resp_qual": round(mean("resp"), 3) if args.judge else None,
               "errors": sum(1 for r in rows if r.get("error")),
               "seconds": round(time.time() - t0, 1)}
    by = {}
    for key in ("difficulty", "domain"):
        for r in rows:
            by.setdefault(key + ":" + r[key], []).append(r["passed"])
    for r in rows:
        for f in r["disfluency"] or ["NONE"]:
            by.setdefault("disfluency:" + f, []).append(r["passed"])
    summary["pass_by"] = {k: "%.3f (%d)" % (sum(v) / len(v), len(v)) for k, v in sorted(by.items())}
    summary["tag"] = args.tag
    summary["settings"] = settings(CONFIG) if saved is None else saved["summary"].get("settings")
    summary["provenance"] = provenance()
    if saved is not None:
        summary["rescored_from"] = args.rescore
    out_dir = REPO / "results" / "offline"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / ("eval_%s_%s%s.json" % (args.text, stamp, ("_" + args.tag) if args.tag else ""))
    path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, default=str), encoding="utf-8")
    for r in rows:
        if not r["passed"]:
            print("FAIL %-28s %s | got %s | %s" % (r["folder"][:28], r["why"][:60],
                  json.dumps(r["calls"])[:160], r.get("error", "")[:80]))
    print(json.dumps(summary, indent=1))
    print("wrote", path)
    if summary["errors"]:
        # a key, quota or network problem must not pass for a low score
        first = next(r["error"] for r in rows if r.get("error"))
        print("WARNING: %d of %d items hit a model error, e.g. %s" % (summary["errors"], n, first[:200]))
        return 2 if summary["errors"] == n else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
