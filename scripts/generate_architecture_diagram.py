#!/usr/bin/env python3
"""Generate docs/diagrams/architecture.json - an Eraser Diagrams (MDP) model of the
Retail Decision Intelligence Agent architecture.

Render with:
    eraser-diagrams render docs/diagrams/architecture.json \
        --chromium-path <chrome> --scale 2 -o docs/diagrams/architecture.png

Deterministic layout: four left-to-right columns
(sources/grounding -> orchestration/memory -> brain/gates -> human gate/surfaces).
"""
from __future__ import annotations
import json
from pathlib import Path

SHAPE_W, SHAPE_H = 260, 60
PAD = 20            # group inner padding
HEADER = 80         # group title band height
V_GAP = 26          # gap between children inside a group
COL_GAP = 110       # gap between columns
ROW_GAP = 34        # gap between stacked items in a column
LEFT, TOP = 40, 170 # content origin
GROUP_W = PAD + SHAPE_W + PAD

# Extra height for nodes whose labels wrap to multiple lines
# (the renderer auto-grows boxes; groups need static room for that growth).
HEIGHT_OVERRIDES = {
    "pipeline-orchestrator": 104,
    "sweep-scheduler": 72,
    "lifecycle-stages": 88,
    "memory": 88,
    "approval-ledger": 88,
    "evaluation": 72,
    "scoring-rules": 76,
    "optional-llm-layer": 76,
    "tools": 68,
    "why-endpoint": 68,
}

# ("node", id, label, color, icon)
# ("group", id, title, color, [children])
# child: (id, label, color, icon) | ("sub", id, title, color, icon, [children])
COLUMNS = [
    [
        ("group", "sources", "Sources", "gray", [
            ("campaign-audit-evidence", "Campaign Audit Evidence", "gray", "file-text"),
            ("sales-forecasts", "Sales Forecasts", "gray", "trending-up"),
            ("sub", "vetted-knowledge-sources", "Vetted Knowledge Sources", "gray", "book", [
                ("case-studies", "Case Studies", "gray", "book"),
                ("data-dictionary", "Data Dictionary", "gray", "table"),
                ("methodology", "Methodology Docs", "gray", "file-text"),
            ]),
        ]),
        ("group", "rag-layer", "RAG Layer", "yellow", [
            ("evidence-ids", "Evidence IDs", "yellow", "hash"),
            ("deterministic-bm25", "Deterministic BM25", "yellow", "filter"),
        ]),
        ("node", "why-endpoint", "/why/{store_id} Cited Explanations", "yellow", "message-square"),
        ("node", "optional-llm-layer", "Optional LLM Layer (rephrase only, guarded)", "yellow", "brain"),
    ],
    [
        ("node", "tools", "Tools - typed, fail-closed adapters", "blue", "server"),
        ("group", "fastapi-service", "FastAPI Service (app/main.py)", "green", [
            ("pipeline-orchestrator", "Pipeline Orchestrator - route → plan → score → verify → gate", "green", "git-branch"),
            ("sweep-scheduler", "Sweep Scheduler (daily sweep)", "green", "clock"),
        ]),
        ("node", "memory", "Memory - append-only JSONL (fsynced, never rewritten)", "blue", "archive"),
        ("node", "evaluation", "Evaluation - 22 golden scenarios, CI-gated", "purple", "target"),
    ],
    [
        ("group", "decision-engine", "Decision Engine - pure code, no LLM", "purple", [
            ("router", "Router", "purple", "shuffle"),
            ("planner", "Planner", "purple", "map"),
            ("scoring-rules", "Scoring Rules - transparent rule chain", "purple", "list"),
            ("verifier", "Verifier", "purple", "check-circle"),
            ("decision-trajectory", "Decision Trajectory (recorded)", "purple", "git-commit"),
        ]),
        ("group", "guardrails", "Guardrails", "orange", [
            ("approval-gate-set", "Approval Gate Set", "orange", "lock"),
            ("risk-tiers", "Risk Tiers", "orange", "alert-triangle"),
            ("choice-architecture", "Choice Architecture", "orange", "sliders"),
            ("cost-of-inaction", "Cost of Inaction", "orange", "dollar-sign"),
        ]),
    ],
    [
        ("node", "store-manager", "Store Manager", "blue", "user"),
        ("node", "approval-ledger", "Approval Ledger - double-gated, guardrails re-run at decision time", "red", "file-check"),
        ("group", "lifecycle", "Intervention Lifecycle (phase2)", "red", [
            ("lifecycle-stages", "define → approve → start → complete → evaluate", "red", "layers"),
        ]),
        ("group", "presentation", "Presentation", "blue", [
            ("executive-board", "Executive Board", "blue", "layout-dashboard"),
            ("attention-queue", "Attention Queue", "blue", "bell"),
            ("recommendation-cards", "Recommendation Cards", "blue", "credit-card"),
        ]),
        ("node", "executive-team", "Executive Team", "blue", "users"),
    ],
]

# (from, to, label, line_style)
EDGES = [
    ("campaign-audit-evidence", "tools", "ingest", "solid"),
    ("sales-forecasts", "tools", "typed, fail-closed", "solid"),
    ("vetted-knowledge-sources", "rag-layer", "indexed", "solid"),
    ("tools", "pipeline-orchestrator", "", "solid"),
    ("rag-layer", "decision-engine", "grounded context", "solid"),
    ("pipeline-orchestrator", "router", "", "solid"),
    ("router", "planner", "", "solid"),
    ("planner", "scoring-rules", "", "solid"),
    ("scoring-rules", "verifier", "", "solid"),
    ("verifier", "decision-trajectory", "trajectory recorded", "solid"),
    ("verifier", "guardrails", "rule gates", "dashed"),
    ("pipeline-orchestrator", "memory", "", "solid"),
    ("pipeline-orchestrator", "why-endpoint", "", "solid"),
    ("verifier", "approval-ledger", "budget-affecting actions", "solid"),
    ("store-manager", "approval-ledger", "approve / reject - fail-closed 503", "solid"),
    ("approval-ledger", "lifecycle", "event-sourced", "solid"),
    ("why-endpoint", "rag-layer", "citations to evidence", "dashed"),
    ("why-endpoint", "optional-llm-layer", "rephrase only, guarded", "solid"),
    ("optional-llm-layer", "store-manager", "cited answer", "solid"),
    ("lifecycle", "presentation", "intervention outcomes", "solid"),
    ("presentation", "executive-team", "plan → execute → measure → re-decide", "solid"),
    ("evaluation", "decision-engine", "golden-case CI gates", "dashed"),
]


def _node_h(nid):
    return HEIGHT_OVERRIDES.get(nid, SHAPE_H)


def _child_height(child):
    if child[0] == "sub":
        n = len(child[5])
        return HEADER + n * SHAPE_H + (n - 1) * V_GAP + PAD
    return _node_h(child[0])


def _item_height(item):
    if item[0] == "node":
        return _node_h(item[1])
    children = item[4]
    return HEADER + sum(_child_height(c) for c in children) + (len(children) - 1) * V_GAP + PAD


def _shape(eid, label, icon, color, x, y, w, h, container=None):
    e = {"tag": "Shape", "id": eid, "x": x, "y": y, "width": w, "height": h,
         "color": color, "texts": [{"text": label}]}
    if icon:
        e["icon"] = icon
    if container:
        e["containerId"] = container
    return e


def _group(gid, title, color, x, y, w, h, container=None):
    e = {"tag": "Group", "id": gid, "x": x, "y": y, "width": w, "height": h,
         "color": color, "title": {"text": title}}
    if container:
        e["containerId"] = container
    return e


def build_entities():
    entities = [{"tag": "Shape", "id": "diagram-title", "x": LEFT, "y": 30, "width": 1600, "height": 56,
                 "bgColor": "#ffffff", "borderColor": "#ffffff",
                 "texts": [{"text": "Retail Decision Intelligence Agent — deterministic, human-gated: plan → execute → measure → re-decide", "fontSize": 18, "hAlign": "left"}]}]
    col_x = LEFT
    for column in COLUMNS:
        y = TOP
        for item in column:
            if item[0] == "node":
                _, nid, label, color, icon = item
                entities.append(_shape(nid, label, icon, color, col_x + PAD, y, SHAPE_W, _node_h(nid)))
                y += _node_h(nid) + ROW_GAP
            else:
                _, gid, title, color, children = item
                gheight = _item_height(item)
                entities.append(_group(gid, title, color, col_x, y, GROUP_W, gheight))
                child_y = y + HEADER
                for child in children:
                    if child[0] == "sub":
                        _, sid, stitle, scolor, sicon, sub_children = child
                        sheight = _child_height(child)
                        entities.append(_group(sid, stitle, scolor, col_x + PAD, child_y, SHAPE_W, sheight, container=gid))
                        sub_y = child_y + HEADER
                        for entry in sub_children:
                            eid, label, ecolor, eicon = entry
                            entities.append(_shape(eid, label, eicon, ecolor, col_x + 2 * PAD, sub_y, SHAPE_W - 2 * PAD, SHAPE_H, container=sid))
                            sub_y += SHAPE_H + V_GAP
                        child_y += sheight + V_GAP
                    else:
                        eid, label, ecolor, eicon = child
                        entities.append(_shape(eid, label, eicon, ecolor, col_x + PAD, child_y, SHAPE_W, _node_h(eid), container=gid))
                        child_y += _node_h(eid) + V_GAP
                y += gheight + ROW_GAP
        col_x += GROUP_W + COL_GAP
    return entities


def build_connections():
    connections = []
    for src, dst, label, style in EDGES:
        conn = {"from": src, "to": dst, "lineStyle": style, "endArrowhead": "triangle"}
        if label:
            conn["label"] = label
        connections.append(conn)
    return connections


def main():
    document = {"entities": build_entities(), "connections": build_connections()}
    out = Path(__file__).resolve().parents[1] / "docs" / "diagrams" / "architecture.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(document['entities'])} entities, {len(document['connections'])} connections)")


if __name__ == "__main__":
    main()
