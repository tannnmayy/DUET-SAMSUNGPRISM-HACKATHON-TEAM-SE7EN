"""No benchmark item is written into the agent.

Samsung disqualifies submissions that pattern-match FDB-v3 test items. This test
reads every argument value the benchmark expects and fails if any distinctive
one (a name, an id, a city, a product, an amount) appears in any string the
agent contains: prompts, tool schemas, examples, defaults. Generic vocabulary
("passport", "checking", "apartment") is allowed: it names what the tools do,
not what a test says. Numeric code constants (16000 Hz) are not strings and are
not checked.
"""

import ast
import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
AGENT_DIR = REPO / "duet_voice"
# the same settings the agent reads, so this test runs wherever the benchmark is,
# not only when another test happened to load them first
try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env.local")
except ImportError:
    pass
FDB = Path(os.environ.get("FDB_V3_DIR", REPO / "third_party" / "Full-Duplex-Bench" / "v3"))

# words that describe a tool's purpose and may legitimately appear in schemas
GENERIC = {"passport", "driver_license", "driver license", "id_card", "visa", "checking", "savings",
           "credit_card", "utilities", "electricity", "phone", "internet", "water", "platinum", "gold",
           "premium", "basic", "driving", "walking", "transit", "biking", "max_price", "min_bedrooms",
           "pets_allowed", "neighborhood", "usd", "eur", "gbp", "jpy", "cad", "true", "false", "travel",
           "apartment", "airport", "office", "home", "gym", "work"}


def expected_values():
    data = json.loads((FDB / "benchmark_data_v2.json").read_text(encoding="utf-8"))
    values = set()
    for sc in data["scenarios"]:
        for call in sc["expected_tool_calls"]:
            for v in call.get("args", {}).values():
                if isinstance(v, str) and not v.startswith("$"):
                    values.add(v.strip())
                elif isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 10:
                    values.add(str(int(v)) if float(v).is_integer() else str(v))
    return {v for v in values if v.lower() not in GENERIC and len(v) >= 3}


def agent_strings():
    out = []
    for path in AGENT_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append((path.name, node.value))
    return out


@pytest.mark.skipif(not (FDB / "benchmark_data_v2.json").exists(), reason="FDB-v3 checkout not present")
def test_no_benchmark_value_in_agent_strings():
    strings = agent_strings()
    hits = []
    for value in expected_values():
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", re.I)
        for name, s in strings:
            if pattern.search(s):
                hits.append("%s in %s" % (value, name))
    assert not hits, "benchmark values found in the agent: %s" % sorted(hits)
