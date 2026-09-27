#!/usr/bin/env python3
"""Run one of the benchmark's evaluation scripts, unmodified, with the judge we have.

    python bench/official_eval.py evaluate_pass_rate.py --provider duet ... --use-llm

With a usable OPENAI_API_KEY this is exactly the official command (gpt-4o judge).
Otherwise the scripts' OpenAI client is redirected to a Gemini model through
Gemini's OpenAI-compatible endpoint (see bench/judge.py), and the run folder's
run_config records that the judge was a proxy.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    script = sys.argv[1]
    fdb = Path(os.environ.get("FDB_V3_DIR", ".")).resolve()
    sys.path.insert(0, str(fdb))
    sys.argv = [str(fdb / script)] + sys.argv[2:]
    module_name = Path(script).stem
    module = __import__(module_name)
    from bench import judge
    used = judge.install(module)
    print("judge:", judge.judge_label() if "--use-llm" in sys.argv or module_name == "analyze_tool_latency"
          else "none (exact match)", flush=True)
    if module_name == "analyze_tool_latency":
        # this script builds its own OpenAI() client inside main(); without any
        # judge it would crash before measuring anything, so give it one that
        # declines: first-response and tool-call latencies are still computed,
        # and only the judge-dependent task-completion latency is left empty.
        if used:
            module.OpenAI = lambda *a, **k: judge.GeminiJudge(used)
        elif not judge.openai_usable():
            module.OpenAI = lambda *a, **k: judge.NoJudge()
    module.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
