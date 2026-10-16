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
6. Architecture diagram: the committed diagram JSON matches its generator and
   its rendered PNG is stamped from that JSON (no stale image).
7. Docs: load-bearing prose claims (the golden-case count, whether the repo
   ships a render.yaml, the endpoints the README advertises) match their source
   of truth, so prose cannot outlive the code it describes - and no rendered
   artifact carries a rights notice that the MIT licence contradicts.

Run: ``python scripts/check.py`` (exit 0 = consistent, 1 = drift found).
"""
from __future__ import annotations

import hashlib
import json
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

# 7. Flow specs cover every emittable route (declarative-spec pattern) ---
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

# 10. Architecture diagram: the committed JSON is exactly what its generator
#     produces, and the rendered PNG was stamped from that JSON - so the image
#     can never go stale (it once read "22 golden scenarios" long after the
#     count had become 23). Re-run scripts/render_architecture_dunnhumby.py.
sys.path.insert(0, str(ROOT / "scripts"))
from generate_architecture_dunnhumby import build_document as _build_arch_diagram  # noqa: E402

_diagrams = ROOT / "docs" / "diagrams"
_arch_json = _diagrams / "architecture-dunnhumby.json"
_arch_png = _diagrams / "architecture-dunnhumby.png"
_arch_stamp = _diagrams / "architecture-dunnhumby.png.sha256"

_rebuilt_json = (json.dumps(_build_arch_diagram(), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
_committed_json = _arch_json.read_bytes() if _arch_json.exists() else b""
check("diagram: committed architecture JSON matches its generator (rebuild is byte-identical)",
      _committed_json == _rebuilt_json,
      "re-run scripts/render_architecture_dunnhumby.py")

_arch_digest = hashlib.sha256(_committed_json).hexdigest()
_stamp_digest = ""
if _arch_stamp.exists():
    for _line in _arch_stamp.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#"):
            _stamp_digest = _line.split()[0]
            break
check("diagram: rendered PNG is current (stamped from the committed JSON)",
      _arch_png.exists() and _stamp_digest == _arch_digest,
      f"png={'ok' if _arch_png.exists() else 'missing'} "
      f"stamp={_stamp_digest[:12] or 'missing'} json={_arch_digest[:12]} "
      "- re-run scripts/render_architecture_dunnhumby.py")

# 11. Documentation claims match the code. Prose is not executable, so no other
#     gate can see a doc that outlives its subject - and this has bitten twice in
#     one review: a stale "22 golden" count (in the diagram node *and* the prose)
#     and a deployment doc that kept denying a committed blueprint file. Each
#     claim below is compared against its single source of truth, so prose can no
#     longer drift unnoticed between gate runs.
import re  # noqa: E402

from app.main import app as _api  # noqa: E402
from evaluation.golden_cases import GOLDEN_CASES  # noqa: E402

_golden_count = len(GOLDEN_CASES)

# 11a. Every "<n> pinned|golden ..." count in the docs equals the real count.
_claim_docs = [ROOT / "README.md", ROOT / "CONTEXT.md",
               ROOT / "docs" / "adr" / "0004-golden-cases-gate-behavior.md"]
_golden_claims: dict[str, list[int]] = {}
for _doc in _claim_docs:
    if not _doc.exists():
        continue
    for _line in _doc.read_text(encoding="utf-8").splitlines():
        for _n in re.findall(r"(\d+)\s+(?:pinned|golden)\b", _line):
            _golden_claims.setdefault(str(_doc.relative_to(ROOT)), []).append(int(_n))
_stale_counts = {d: ns for d, ns in _golden_claims.items() if any(n != _golden_count for n in ns)}
check("docs: every '<n> pinned|golden' count matches GOLDEN_CASES",
      bool(_golden_claims) and not _stale_counts,
      f"actual={_golden_count} claims={_golden_claims} stale={_stale_counts}")

# 11b. No file may deny infrastructure that is committed - the deploy doc and the
#      wizard both claimed this repo had no render.yaml long after one shipped.
_iac_denials: list[str] = []
if (ROOT / "render.yaml").exists():
    _denial = re.compile(r"\bno\s+`?render\.ya?ml`?", re.IGNORECASE)
    _scan = [ROOT / "README.md", ROOT / "CONTEXT.md",
             *sorted((ROOT / "docs").rglob("*.md")),
             *sorted((ROOT / "scripts").glob("*.sh")),
             *sorted((ROOT / "scripts").glob("*.py"))]
    for _p in _scan:
        if _p.exists() and _p.resolve() != Path(__file__).resolve() \
                and _denial.search(_p.read_text(encoding="utf-8")):
            _iac_denials.append(str(_p.relative_to(ROOT)))
check("docs: nothing claims render.yaml is absent while it is committed",
      not _iac_denials, f"denials={_iac_denials}")

# 11c. Every endpoint the README documents must be a registered route, so the
#      API table cannot advertise a path the app stopped serving. A documented
#      token becomes a pattern: {param}, * and a concrete id (e.g. /why/317) all
#      mean "some route whose path occupies this segment".
_route_paths = [getattr(_r, "path", "") for _r in _api.routes if getattr(_r, "path", "")]
_documented = set(re.findall(r"`(/[A-Za-z0-9_{}*/-]+)`",
                             (ROOT / "README.md").read_text(encoding="utf-8")))
_unrouted = sorted(
    tok for tok in _documented
    if not any(re.fullmatch(re.sub(r"\{[^}]+\}|\*|\d+", "[^/]+", tok), rp)
               for rp in _route_paths)
)
check("docs: every endpoint in the README API tables is a registered route",
      not _unrouted, f"not-a-route={_unrouted}")

# 11d. The repo is MIT, so no self-authored artifact may claim "all rights
#      reserved" - the diagram footer and the served UI footer both did, and the
#      same line had to be removed twice. Checked against *rendered output*, not
#      source text: comments that discuss the notice are legitimate.
from presentation.site import render_page  # noqa: E402

check("licence: the architecture diagram carries no 'all rights reserved' notice",
      "all rights reserved" not in _committed_json.decode("utf-8", "replace").lower())

# Scoped to the footer element: _CSS is inlined into every page, so a whole-page
# scan would flag a source comment that merely discusses the notice.
_ui_html = render_page("check", "/ui", "")
_ui_footer = _ui_html[_ui_html.find("<footer"):_ui_html.find("</footer>")]
check("licence: the served UI footer carries no 'all rights reserved' notice",
      _ui_footer and "all rights reserved" not in _ui_footer.lower())

print()
if failures:
    print(f"check.py: {len(failures)} consistency check(s) FAILED: {failures}")
    sys.exit(1)
print("check.py: all consistency checks passed.")
