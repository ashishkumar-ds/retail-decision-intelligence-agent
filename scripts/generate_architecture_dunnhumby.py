#!/usr/bin/env python3
"""Generate docs/diagrams/architecture-dunnhumby.json - the architecture
diagram restyled to replicate the theme of dunnhumby's official
"The Complete Journey" user guide (source PDF removed from the repo;
theme values below were extracted from it and are inlined).

Theme extracted from the PDF itself (vector fills + text spans):
    teal   #38B2AB  accent: labels, chips, rules, arrows
    mint   #EBF7F5  panel backgrounds (dataset-details section)
    indigo #26245C  dark bands (cover gradient, closing page); box borders
    black  #000000  headings, body text, box outlines
    gray   #CCCCCC  footer rule

Design language replicated:
    - white page, thin teal top rule, black title + teal chip (cover style; the
      diagram schema has no font-weight field, so every label renders regular)
    - one large mint panel (Group container) holding the diagram
    - teal section labels per column ("Data tables" / "Lookup tables" style)
    - flat white node boxes with thin indigo borders, black text (print look)
    - Decision Engine on deep indigo with white title (cover-gradient nod)
    - gray footer rule + attribution (every page's footer: the author holds the
      copyright, dunnhumby is credited only as the data source)

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


def _label(eid, x, y, text, color, size=13, width=GROUP_W, halign="left"):
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
    # Two margins, deliberately: the FURNITURE (rules + panel) bleeds 12pt past the
    # text on each side so the panel has internal padding, and the CONTENT (title,
    # chip, column labels, nodes, footer text) all sits on LEFT. Everything that
    # should share an edge now shares one - the panel used to end 24pt past the
    # content while starting only 12pt before it, and the rules sat on a third box.
    FURNITURE_PAD = 12
    furniture_x = LEFT - FURNITURE_PAD
    furniture_w = (content_right + FURNITURE_PAD) - furniture_x

    panel = {"x": furniture_x, "y": 150,
             "w": furniture_w,
             "h": content_bottom + 24 - 150}

    header = [
        _rect("tcj-top-rule", furniture_x, 22, furniture_w, 4, TEAL),
        # A Textbox, not a Shape: Shape text is inset by --er-shape-h-inset (7.5% of
        # the shape's own width), so a 1100-wide title box indented the title 82pt and
        # it lined up with neither the rule nor the chip. Textbox text has no inset.
        {"tag": "Textbox", "id": "tcj-title", "x": LEFT, "y": 52, "width": 1100, "height": 40,
         "text": "The Retail Decision Intelligence Agent", "fontSize": 26, "hAlign": "left", "color": BLACK},
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
        _rect("tcj-footer-rule", furniture_x, panel["y"] + panel["h"] + 26, furniture_w, 2, GRAY),
        # The author holds the copyright (see LICENSE); dunnhumby is named as the
        # data source, not as the brand. The page should read as a personal
        # project built on dunnhumby's public dataset, not as their product.
        _label("tcj-footer-copy", LEFT, panel["y"] + panel["h"] + 34,
               "© 2026 Ashish Kumar", FOOTER_GRAY, size=11, width=700),
        _label("tcj-footer-data", content_right - 360, panel["y"] + panel["h"] + 34,
               "data: dunnhumby · The Complete Journey", FOOTER_GRAY,
               size=11, width=360, halign="right"),
    ]

    entities = header + [panel_group] + col_labels + nodes + footer
    # Hide only the trajectory edge: the engine records the trajectory, not the
    # verifier. The verifier -> guardrails rule-gate edge is real (engine.evaluate
    # applies the guardrail approval gate right after verify) and is kept so the
    # Guardrails column is not left floating.
    unlabeled = {("verifier", "decision-trajectory")}
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
