#!/usr/bin/env python3
"""Generate docs/diagrams/architecture-dunnhumby.json - the architecture
diagram restyled to replicate the theme of dunnhumby's official
"The Complete Journey" user guide (rag/sources/pdfs/tcj_user_guide.pdf).

Theme extracted from the PDF itself (vector fills + text spans):
    teal   #38B2AB  accent: labels, chips, rules, arrows
    mint   #EBF7F5  panel backgrounds (dataset-details section)
    indigo #26245C  dark bands (cover gradient, closing page); box borders
    black  #000000  headings, body text, box outlines
    gray   #CCCCCC  footer rule

Design language replicated:
    - white page, thin teal top rule, black bold title + teal chip (cover style)
    - one large mint panel (Group container) holding the diagram
    - teal section labels per column ("Data tables" / "Lookup tables" style)
    - flat white node boxes with thin indigo borders, black text (print look)
    - Decision Engine on deep indigo with white title (cover-gradient nod)
    - gray footer rule + copyright line + wordmark (every page's footer)

Render:
    eraser-diagrams render docs/diagrams/architecture-dunnhumby.json \\
        --chromium-path <chrome> --scale 2 -o docs/diagrams/architecture-dunnhumby.png
"""
from __future__ import annotations

import json
from pathlib import Path

from generate_architecture_diagram import (  # noqa: E402
    GROUP_W,
    build_connections,
    build_entities,
)

TEAL = "#38b2ab"
MINT = "#ebf7f5"
INDIGO = "#26245c"
BLACK = "#000000"
GRAY = "#cccccc"
FOOTER_GRAY = "#666666"
WHITE = "#ffffff"

LEFT, TOP = 40, 170
COL_GAP = 110
COL_STEP = GROUP_W + COL_GAP
SHIFT = 48          # push content down to clear the in-panel column labels

COLUMN_LABELS = [
    "inputs & grounding",
    "orchestration & memory",
    "decision core",
    "human gate & surfaces",
]


def _rect(eid, x, y, w, h, fill, border=None):
    return {"tag": "Shape", "id": eid, "x": x, "y": y, "width": w, "height": h,
            "bgColor": fill, "borderColor": border or fill, "styleMode": "plain",
            "texts": [{"text": ""}]}


def _label(eid, x, y, text, color, size=13, bold=True, width=GROUP_W, halign="left"):
    return {"tag": "Textbox", "id": eid, "x": x, "y": y, "width": width, "height": 24,
            "text": text, "fontSize": size, "color": color, "hAlign": halign}


def _decorate(entities: list) -> list:
    """Re-skin base entities into the TCJ theme and nest them in the mint panel."""
    out = []
    for e in entities:
        eid = e["id"]
        if eid == "diagram-title":
            continue  # replaced by the TCJ title block
        e["styleMode"] = "plain"
        e["y"] = e["y"] + SHIFT
        if e["tag"] == "Group":
            if eid == "decision-engine":
                e.update({"bgColor": INDIGO, "borderColor": INDIGO,
                          "title": {"text": e["title"]["text"], "color": WHITE,
                                    "bgColor": INDIGO, "border": False}})
            else:
                e.update({"bgColor": WHITE, "borderColor": INDIGO,
                          "title": {"text": e["title"]["text"], "color": TEAL,
                                    "bgColor": WHITE, "border": False}})
        else:
            e.pop("icon", None)
            e.pop("iconProps", None)
            e.update({"bgColor": WHITE, "borderColor": INDIGO,
                      "texts": [{"text": e["texts"][0]["text"], "color": BLACK}]})
        if "containerId" not in e:
            e["containerId"] = "tcj-panel"
        out.append(e)
    return out


def build_document() -> dict:
    nodes = _decorate(build_entities())

    content_right = max(e["x"] + e["width"] for e in nodes)
    content_bottom = max(e["y"] + e["height"] for e in nodes)
    panel = {"x": LEFT - 12, "y": 150,
             "w": content_right + 24 - (LEFT - 12),
             "h": content_bottom + 24 - 150}

    header = [
        _rect("tcj-top-rule", LEFT, 22, content_right + 20 - LEFT, 4, TEAL),
        {"tag": "Shape", "id": "tcj-title", "x": LEFT, "y": 52, "width": 1100, "height": 44,
         "bgColor": WHITE, "borderColor": WHITE, "styleMode": "plain",
         "texts": [{"text": "The Retail Decision Intelligence Agent", "fontSize": 26, "hAlign": "left", "color": BLACK}]},
        {"tag": "Shape", "id": "tcj-chip", "x": LEFT, "y": 106, "width": 118, "height": 26,
         "bgColor": TEAL, "borderColor": TEAL, "styleMode": "plain",
         "texts": [{"text": "architecture", "fontSize": 12, "color": WHITE}]},
    ]

    panel_group = {"tag": "Group", "id": "tcj-panel", "x": panel["x"], "y": panel["y"],
                   "width": panel["w"], "height": panel["h"],
                   "bgColor": MINT, "borderColor": MINT, "styleMode": "plain"}

    col_labels = [
        _label(f"tcj-col-{i}", LEFT + i * COL_STEP, panel["y"] + 16, txt, TEAL, size=14)
        for i, txt in enumerate(COLUMN_LABELS)
    ]
    for lab in col_labels:
        lab["containerId"] = "tcj-panel"

    footer = [
        _rect("tcj-footer-rule", LEFT, panel["y"] + panel["h"] + 26, content_right + 20 - LEFT, 2, GRAY),
        _label("tcj-footer-copy", LEFT, panel["y"] + panel["h"] + 38,
               "© 2026 retail decision intelligence agent · all rights reserved", FOOTER_GRAY,
               size=11, bold=False, width=700),
        _label("tcj-footer-brand", content_right - 240, panel["y"] + panel["h"] + 34, "dunnhumby", BLACK,
               size=17, width=260, halign="right"),
    ]

    entities = header + [panel_group] + col_labels + nodes + footer
    unlabeled = {("verifier", "decision-trajectory"), ("verifier", "guardrails")}
    connections = [{**c, "color": TEAL} for c in build_connections()
                   if (c["from"], c["to"]) not in unlabeled]
    return {"entities": entities, "connections": connections}


def main() -> None:
    document = build_document()
    out = Path(__file__).resolve().parents[1] / "docs" / "diagrams" / "architecture-dunnhumby.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(document['entities'])} entities, {len(document['connections'])} connections)")


if __name__ == "__main__":
    main()
