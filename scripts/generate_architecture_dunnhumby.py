#!/usr/bin/env python3
"""Generate docs/diagrams/architecture-dunnhumby.json - the same architecture
layout as generate_architecture_diagram.py, restyled with the dunnhumby brand
palette extracted from dunnhumby.com.

Palette (from https://www.dunnhumby.com, inline CSS + stylesheets):
    navy     #190F47  (wordmark / dark hero sections)
    blue     #425CC7  (primary brand blue)
    light    #B3BEE9  (light periwinkle tint)
    coral    #FC8181  (warm accent, from their UI scale)
    amber    #F6AD55  (secondary warm accent, from their UI scale)

Semantic mapping:
    navy  = deterministic brain (code, no LLM) + title banner
    blue  = runtime services, actors
    light = data sources, memory, surfaces
    amber = grounding / quality gates
    coral = human gate / safety

Render:
    eraser-diagrams render docs/diagrams/architecture-dunnhumby.json \\
        --chromium-path <chrome> --scale 2 -o docs/diagrams/architecture-dunnhumby.png
"""
from __future__ import annotations

import json
from pathlib import Path

from generate_architecture_diagram import (  # noqa: E402
    EDGES,
    build_connections,
    build_entities,
)

NAVY = "#190f47"
BLUE = "#425cc7"
LIGHT = "#b3bee9"
CORAL = "#fc8181"
AMBER = "#f6ad55"
WHITE = "#ffffff"

# dunnhumby palette keyed by the named colors used in the base layout
PALETTE = {
    "gray": LIGHT,    # Sources
    "yellow": AMBER,  # RAG layer, /why, optional LLM
    "blue": BLUE,     # Tools, Memory, Presentation, actors
    "green": BLUE,    # FastAPI service
    "purple": NAVY,   # Decision engine (the deterministic brain)
    "orange": CORAL,  # Guardrails
    "red": CORAL,     # Approval ledger, Lifecycle
    "teal": LIGHT,
    "indigo": BLUE,
    "pink": AMBER,    # Evaluation
}


def restyle_banner(entity: dict) -> dict:
    """Navy hero banner with white text, dunnhumby wordmark style (lowercase + spark)."""
    entity["bgColor"] = NAVY
    entity["borderColor"] = NAVY
    entity["height"] = 64
    entity["texts"] = [
        {
            "text": "✳ retail decision intelligence agent — dunnhumby brand edition: plan → execute → measure → re-decide",
            "fontSize": 18,
            "hAlign": "left",
            "color": WHITE,
        }
    ]
    return entity


def build_document() -> dict:
    entities = build_entities()
    for e in entities:
        if e["id"] == "diagram-title":
            restyle_banner(e)
        elif e["tag"] in ("Shape", "Group") and e.get("color") in PALETTE:
            e["color"] = PALETTE[e["color"]]
    connections = [
        {**c, "color": NAVY}
        for c in build_connections()
    ]
    return {"entities": entities, "connections": connections}


def main() -> None:
    document = build_document()
    out = Path(__file__).resolve().parents[1] / "docs" / "diagrams" / "architecture-dunnhumby.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(document['entities'])} entities, {len(document['connections'])} connections)")


if __name__ == "__main__":
    main()
