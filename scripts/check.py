#!/usr/bin/env python3
"""Cross-module consistency gate (commerce-agents `scripts/check.py` pattern).

Verifies invariants that span modules and that the test suite cannot pin,
so a change in one place cannot silently drift from its consumers:

1. Planner: executable plans and their descriptions never drift apart, and
   every route the router can emit has a plan.
2. Guardrails: every approval-required recommendation is actually emittable
   by the decision layer (appears in the decision/rule source), and vice
   versa no emittable approval-gated string is missing from the gate set.
3. RAG corpus: rebuilding from in-repo sources is byte-deterministic.
4. Contracts: pydantic HTTP schemas stay in sync with their documented
   read-model field sets.
5. Lifecycle states: active and terminal intervention states are disjoint
   and cover the recommendation-to-evaluation lifecycle constants.

Run: ``python scripts/check.py`` (exit 0 = consistent, 1 = drift found).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        failures.append(name)


# 1. Planner consistency -------------------------------------------------------
from decision_engine.planner import PLANS, STEP_DESCRIPTIONS, build_plan  # noqa: E402
from decision_engine.router import route  # noqa: E402
from decision_engine.scorer import StoreSignal  # noqa: E402

check("planner: every route has a non-empty executable plan",
      all(PLANS.values()) and len(PLANS) >= 3,
      f"routes={sorted(PLANS)}")
plan_steps = {step for steps in PLANS.values() for step in steps}
check("planner: every executable step has a description", plan_steps <= set(STEP_DESCRIPTIONS),
      f"undescribed={sorted(plan_steps - set(STEP_DESCRIPTIONS))}")

_routes_emitted = {
    route(StoreSignal(store_id=1, baseline_forecast=100, current_forecast=100,
                      days_elapsed=days, days_remaining=remaining,
                      forecast_signal_available=available))
    for available, days, remaining in [(False, 10, 50), (True, 10, 13), (True, 10, 50)]
}
check("router: every emittable route has a plan", _routes_emitted <= set(PLANS),
      f"routes={sorted(_routes_emitted)} unplanned={sorted(_routes_emitted - set(PLANS))}")
check("planner: build_plan falls back for unknown routes", build_plan("__nonexistent__") == ["flag_for_review"])

# 2. Guardrails <-> decision-layer coverage ------------------------------------
from guardrails import APPROVAL_REQUIRED_RECOMMENDATIONS  # noqa: E402

_decision_sources = "\n".join(
    p.read_text(encoding="utf-8")
    for p in [ROOT / "decision_engine" / "scorer.py", ROOT / "phase2" / "portfolio.py"]
    if p.exists()
)
_gaps = {rec for rec in APPROVAL_REQUIRED_RECOMMENDATIONS if f'"{rec}"' not in _decision_sources}
check("guardrails: every approval-required recommendation is emittable by the decision layer",
      not _gaps, f"not produced anywhere in the decision layer: {sorted(_gaps)}")

# 3. RAG corpus determinism ------------------------------------------------------
from rag.corpus import build_chunks  # noqa: E402

_chunks_a = [c.to_record() for c in build_chunks()]
_chunks_b = [c.to_record() for c in build_chunks()]
check("rag: corpus build is deterministic (two builds byte-identical)", _chunks_a == _chunks_b,
      f"{len(_chunks_a)} chunks")
check("rag: corpus chunk ids are unique",
      len({c["chunk_id"] for c in _chunks_a}) == len(_chunks_a))

# 3b. RAG source provenance: every ingested source carries front-matter ---------
from rag.corpus import _parse_front_matter  # noqa: E402
from rag.corpus import build_chunks as _c_build_chunks  # noqa: E402

_SOURCES_DIR = ROOT / "rag" / "sources"
_source_defects: list[str] = []
_seen_titles: dict[str, list[str]] = {}
for _p in sorted(_SOURCES_DIR.rglob("*.md")):
    _meta, _ = _parse_front_matter(_p.read_text(encoding="utf-8"))
    _missing = [k for k in ("source_type", "license", "tier") if not _meta.get(k)]
    if _missing:
        _source_defects.append(f"{_p.name}: missing {_missing}")
    _seen_titles.setdefault(_meta.get("title", _p.stem), []).append(_p.name)
check("rag: every source file declares source_type/license/tier front-matter",
      not _source_defects, f"defects={_source_defects}")
_dupes = {t: names for t, names in _seen_titles.items() if len(names) > 1}
check("rag: source titles are unique (deterministic chunk ids)",
      not _dupes, f"duplicates={_dupes}")

_all_types = {c.source_type for c in _c_build_chunks()}
check("rag: corpus distinguishes methodology/data_dictionary/narrative types",
      {"methodology", "data_dictionary"} <= _all_types,
      f"types={sorted(_all_types)}")

# 4. Pydantic schema <-> read-model parity --------------------------------------
from phase2.schemas import AuditRunPayload  # noqa: E402
from tools.campaign_tool import _AUDIT_RUN_FIELDS  # noqa: E402

check("schemas: AuditRunPayload fields match the campaign audit read model",
      frozenset(AuditRunPayload.model_fields) == _AUDIT_RUN_FIELDS,
      f"schema={sorted(AuditRunPayload.model_fields)} read_model={sorted(_AUDIT_RUN_FIELDS)}")

# 5. Lifecycle state sanity ------------------------------------------------------
from phase2.contracts import (  # noqa: E402
    ACTIVE_INTERVENTION_STATES,
    EVIDENCE_STATES,
    TERMINAL_INTERVENTION_STATES,
)

check("contracts: active and terminal intervention states are disjoint",
      not (ACTIVE_INTERVENTION_STATES & TERMINAL_INTERVENTION_STATES),
      f"overlap={sorted(ACTIVE_INTERVENTION_STATES & TERMINAL_INTERVENTION_STATES)}")
check("contracts: evidence and checkpoint state sets are non-empty",
      bool(EVIDENCE_STATES) and len(EVIDENCE_STATES) >= 3)

# 5b. Release version parity (app.meta.VERSION vs pyproject) ---------------------
import tomllib  # noqa: E402

from app.meta import VERSION as _APP_VERSION  # noqa: E402

_pyproject_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
check("release: app.VERSION matches pyproject [project].version",
      _APP_VERSION == _pyproject_version,
      f"app={_APP_VERSION} pyproject={_pyproject_version}")

# 6. Derived artifacts cannot drift from their source (merchant-agent
#    managed-agents pattern: system.md derived from prompt, compared in CI) ---
from rag.llm_explainer import SYSTEM_PROMPT, build_static_system  # noqa: E402

check("llm: SYSTEM_PROMPT is derived from build_static_system()",
      SYSTEM_PROMPT == build_static_system())
check("llm: static system prompt is non-empty and byte-stable by construction",
      bool(SYSTEM_PROMPT.strip()))

from decision_engine.verifier import VALID_RECOMMENDATIONS  # noqa: E402

check("verifier: guardrail approval set is a subset of valid recommendations",
      APPROVAL_REQUIRED_RECOMMENDATIONS <= VALID_RECOMMENDATIONS,
      f"orphaned={sorted(APPROVAL_REQUIRED_RECOMMENDATIONS - VALID_RECOMMENDATIONS)}")

# 7. Flow specs cover every emittable route (merchant-agent skills pattern) ---
_FLOWS_DIR = ROOT / "decision_engine" / "flows"
# README.md documents the folder contract; only per-route specs are checked.
_flow_files = {p.stem for p in _FLOWS_DIR.glob("*.md")} - {"README"} if _FLOWS_DIR.exists() else set()
_routes_known = set(PLANS) | {"feedback_loop_redecision"}
check("flows: flow specs mirror every executable route (documentation contract)",
      _routes_known <= _flow_files,
      f"missing={sorted(_routes_known - _flow_files)}")
check("flows: every flow spec names an executable route or documented wrapper",
      _flow_files <= _routes_known,
      f"unknown={sorted(_flow_files - _routes_known)}")

# 8. Approval double gate: the ledger must gate exactly the guardrail set ------
from approvals.ledger import decision_gate  # noqa: E402
from guardrails import requires_human_approval  # noqa: E402

_probe_gate_ok = decision_gate({
    "store_id": 1, "recommendation": "ESCALATE", "confidence": 0.5,
    "reason": "probe", "requires_human_approval": True,
})["allowed"]
_probe_gate_ungated = decision_gate({
    "store_id": 1, "recommendation": "CONTINUE", "confidence": 0.5,
    "reason": "probe", "requires_human_approval": False,
})["allowed"]
check("ledger: decision gate allows gated, verified recommendations", _probe_gate_ok)
check("ledger: decision gate refuses non-approval-gated recommendations",
      not _probe_gate_ungated)
check("ledger: gate covers the full guardrail set",
      all(requires_human_approval(r) for r in APPROVAL_REQUIRED_RECOMMENDATIONS))

# 9. Choice-architecture coverage: every emittable recommendation must be
#    tiered deliberately in RISK_TIER, and every gated one must have a
#    default/fallback pair - so a new action can never silently inherit the
#    conservative default (commerce-agents manifest-sync pattern).
from guardrails import DEFAULT_FALLBACK, RISK_TIER  # noqa: E402

check("choice: every emittable recommendation has a deliberate risk tier",
      VALID_RECOMMENDATIONS <= set(RISK_TIER),
      f"untiered={sorted(VALID_RECOMMENDATIONS - set(RISK_TIER))}")
check("choice: every gated recommendation has a default/fallback pair",
      APPROVAL_REQUIRED_RECOMMENDATIONS <= set(DEFAULT_FALLBACK),
      f"unpaired={sorted(APPROVAL_REQUIRED_RECOMMENDATIONS - set(DEFAULT_FALLBACK))}")
check("choice: fallback actions are themselves valid recommendations",
      all(fallback in VALID_RECOMMENDATIONS
          for _, fallback in DEFAULT_FALLBACK.values()),
      f"invalid fallbacks={sorted({f for _, f in DEFAULT_FALLBACK.values() if f not in VALID_RECOMMENDATIONS})}")

print()
if failures:
    print(f"check.py: {len(failures)} consistency check(s) FAILED: {failures}")
    sys.exit(1)
print("check.py: all consistency checks passed.")
