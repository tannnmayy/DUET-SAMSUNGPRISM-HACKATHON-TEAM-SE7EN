#!/usr/bin/env python3
"""Checks to run on the exact commit that will carry the submission tag.

    python tools/pretag_check.py            # checks HEAD
    python tools/pretag_check.py <commit>   # checks another commit

Samsung judges the commit tagged PRISM_GENAI_HACKATHON_Y2026, and "everything
referenced in your submission must be present in the tagged commit". So the
checks read the committed tree (git ls-tree / git show), never the working
copy: a file that exists locally but was never committed fails here.

Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_BYTES = 95 * 1024 * 1024          # GitHub rejects files over 100 MB
KIT_FILES = ("harness/scorer.py", "harness/runner.py", "harness/mock_env.py",
             "harness/protocol.py", "run_local.py", "eval_submission.py")
SECRET_PATTERNS = (r"hf_[A-Za-z0-9]{30,}", r"sk-[A-Za-z0-9]{20,}", r"AIza[0-9A-Za-z_\-]{30,}",
                   r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def main() -> int:
    commit = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    problems, notes = [], []
    tree = {}
    for line in git("ls-tree", "-r", "-l", commit).splitlines():
        meta, path = line.split("\t", 1)
        size = meta.split()[3]
        tree[path] = int(size) if size.isdigit() else 0

    # 1. entry point and packaging
    for required in ("submission.yaml", "requirements.txt", "agent/agent.py", "README.md",
                     "Dockerfile", *KIT_FILES):
        if required not in tree:
            problems.append("missing from the commit: " + required)
    sub = git("show", commit + ":submission.yaml") if "submission.yaml" in tree else ""
    reqs = git("show", commit + ":requirements.txt") if "requirements.txt" in tree else ""
    pins_yaml = sorted(re.findall(r"^\s*-\s*([A-Za-z0-9_.\-]+==[^\s#]+)", sub, re.M))
    pins_txt = sorted(l.strip() for l in reqs.splitlines()
                      if l.strip() and not l.strip().startswith("#"))
    if pins_yaml != pins_txt:
        problems.append("requirements.txt and submission.yaml disagree: %s vs %s" % (pins_txt, pins_yaml))
    if 'entry_point: "agent.agent:ParticipantAgent"' not in sub:
        problems.append("submission.yaml entry_point is not agent.agent:ParticipantAgent")

    # 2. the kit is unmodified since each file first appeared at the repo root
    #    (the kit arrived nested and was flattened in Phase 0, byte-identical)
    for path in KIT_FILES:
        added = git("log", "--diff-filter=A", "--format=%H", commit, "--", path).split()
        if not added:
            notes.append("no commit adds " + path)
            continue
        first = added[-1]
        try:
            if git("show", first + ":" + path) != git("show", commit + ":" + path):
                problems.append("kit file changed since the kit was committed: " + path)
        except subprocess.CalledProcessError:
            notes.append("could not compare " + path + " with the first commit")

    # 3. every relative link in README.md resolves inside the commit
    readme = git("show", commit + ":README.md") if "README.md" in tree else ""
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", readme):
        if re.match(r"^[a-z]+://", target) or target.startswith("mailto:"):
            continue
        path = os.path.normpath(target).replace("\\", "/")
        if path not in tree and not any(p.startswith(path.rstrip("/") + "/") for p in tree):
            problems.append("README links to a path not in the commit: " + target)
    for url in re.findall(r"https?://[^\s)>\]]+", readme):
        if "youtu" in url or "drive.google" in url:
            notes.append("video link present: " + url + " (open it in a private window)")

    # 4. size and secrets
    for path, size in tree.items():
        if size > MAX_BYTES:
            problems.append("file over 95 MB (GitHub limit): %s (%.1f MB)" % (path, size / 1e6))
        if path.endswith((".safetensors", ".bin", ".pt", ".onnx", ".gguf")) and size > 5e6:
            problems.append("model weights committed: " + path)
    text_like = [p for p, s in tree.items() if s < 2e6 and p.endswith(
        (".py", ".md", ".yaml", ".yml", ".json", ".txt", ".js", ".html", ".sh", ".cfg", ".toml"))]
    for path in text_like:
        body = git("show", commit + ":" + path)
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, body):
                problems.append("possible secret in " + path)

    # 5. no AI attribution anywhere in history (project rule, PROJECT.md 6.9)
    log = git("log", "--format=%B", commit)
    if re.search(r"co-authored-by|generated with", log, re.I):
        problems.append("commit messages carry attribution lines (PROJECT.md 6.9)")

    # 6. deliverables we expect to exist
    for expected in ("app/server.py", "duet/live.py", "pyproject.toml"):
        if expected not in tree:
            notes.append("not in commit yet: " + expected)

    for note in notes:
        print("note: " + note)
    for problem in problems:
        print("FAIL: " + problem)
    print("%s: %d file(s), %d problem(s)" % (commit, len(tree), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
