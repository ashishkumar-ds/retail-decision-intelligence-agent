#!/usr/bin/env python3
"""Live LLM smoke test for the /why rephrase layer.

Calls the real Anthropic API once (cost: a fraction of a cent) and verifies
the full path: rephrase -> grounding gate -> status. Exits 0 only when the
narrative comes back ``llm-grounded``.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python scripts/smoke_llm_live.py [store_id]

The key is read from the environment or from a local ``.env.local`` file
(gitignored). It is never printed or logged.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env.local (KEY=VALUE lines) if present, without overriding real env.
env_file = ROOT / ".env.local"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from memory.history import read_log  # noqa: E402
from rag.corpus import load_corpus  # noqa: E402
from rag.llm_explainer import maybe_llm_narrative  # noqa: E402

if not os.getenv("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY not set (env or .env.local).", file=sys.stderr)
    sys.exit(2)

store_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
records = [r for r in read_log() if r.get("store_id")]
if store_id is None:
    if not records:
        print("ERROR: no recommendation records in logs; pass a store_id.", file=sys.stderr)
        sys.exit(2)
    store_id = records[-1]["store_id"]
latest = next((r for r in reversed(records) if r.get("store_id") == store_id), None)
if latest is None:
    print(f"ERROR: no recommendation record for store {store_id}.", file=sys.stderr)
    sys.exit(2)

# Build Tier-1 evidence the same shape /why uses (inline, no FastAPI needed).

evidence = {
    "store_id": store_id,
    "latest_recommendation": latest,
    "outcome": {},
    "intervention_count": 0,
    "event_count": 0,
    "latest_decision": None,
}

corpus = load_corpus()
narrative, status = maybe_llm_narrative(
    store_id, "", " ".join([
        f"Store {store_id}'s latest recommendation is {latest.get('recommendation')} "
        f"with confidence {latest.get('confidence')} [rec:{latest.get('recommendation_id')}].",
        f"It is driven by a health score of {latest.get('store_health_score')} "
        f"(recovery {latest.get('recovery_pct')} percent, {latest.get('days_remaining')} "
        f"days remaining) [rec:{latest.get('recommendation_id')}].",
    ]),
    evidence, corpus, [], llm_enabled=True,
)

print(f"store_id:      {store_id}")
print(f"guard.llm:     {status}")
print(f"narrative:\n{narrative}")
grounded = status == "llm-grounded"
# Persist the smoke result next to the other audit trails.
log_path = ROOT / "logs" / "llm_smoke.jsonl"
log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "store_id": store_id, "status": status, "grounded": grounded,
        "model": os.getenv("LLM_MODEL", "claude-sonnet-4-5"),
    }) + "\n")
sys.exit(0 if grounded else 1)
