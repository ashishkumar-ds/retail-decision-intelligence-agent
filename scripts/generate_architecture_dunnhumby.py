#!/usr/bin/env python3
"""Generate docs/images/architecture-dunnhumby.json - the architecture
diagram in the dashboard's Stub Register theme (app/globals.css).

Palette (copied from the dashboard's :root tokens):
    stock       #f6f4ef  warm coupon-stock ground
    paper       #fffdf8  register plates
    panel       #f2efe6  neutral fills / group bodies
    rule        #d9d3c6  the register's hairlines
    ink         #212048  prose
    carbon      #3b3f66  mono figures
    muted       #5d6167  captions and legends
    indigo      #2a1b6a  masthead band + register ink
    indigo-deep #1e134f  band keyline
    green       #2abf95  live system (non-text)
    green-ink   #0e7a55  green words
    flash       #cf348d  ACT NOW stamp (non-text)
    stamp-ink   #a4285e  magenta words
    rose        #f8e9f1  act-now tint field
    rose-line   #ecc9dd  rose hairline
    no-signal   #edece4  review tint field
    slate       #6f74a8  no-signal marks
    yellow      #f5e9b8  degraded-state amber
    amber-ink   #8a5a12  amber words

Design language replicated from the board:
    - full-bleed indigo masthead with the indigo-deep keyline and white title
    - a paper register plate on the stock ground, sharp corners, no shadows
    - hairline-ruled group plates with paper node plates inside
    - indigo prose, green-ink for the live service, magenta only for the
      human approval gate and guardrails (the stamp), amber for risk
    - muted captions, no shadows; the register is flat and hairline-ruled

Render:
    eraser-diagrams render docs/images/architecture-dunnhumby.json \\
        --chromium-path <chrome> --scale 2 -o docs/images/architecture.png
"""
from __future__ import annotations

import json
from pathlib import Path

from generate_architecture_diagram import (  # noqa: E402
    GROUP_W,
    build_connections,
    build_entities,
)

STOCK = "#f6f4ef"
PAPER = "#fffdf8"
PANEL = "#f2efe6"
RULE = "#d9d3c6"
INK = "#212048"
CARBON = "#3b3f66"
MUTED = "#5d6167"
INDIGO = "#2a1b6a"
INDIGO_DEEP = "#1e134f"
GREEN = "#2abf95"
GREEN_INK = "#0e7a55"
FLASH = "#cf348d"
STAMP_INK = "#a4285e"
ROSE = "#f8e9f1"
ROSE_LINE = "#ecc9dd"
NO_SIGNAL = "#edece4"
SLATE = "#6f74a8"
YELLOW = "#f5e9b8"
AMBER_INK = "#8a5a12"

LEFT = 40
COL_GAP = 110
COL_STEP = GROUP_W + COL_GAP
SHIFT = 48          # push content down to clear the in-panel column labels
BLEED = 28          # ground / masthead extension beyond the register plate

COLUMN_LABELS = [
    "inputs & grounding",
    "orchestration & memory",
    "decision core",
    "human gate & surfaces",
]
# The same four marker inks the board uses to separate its buckets.
COLUMN_MARKERS = [SLATE, GREEN, FLASH, INDIGO]

# Group chrome. The decision core keeps the dashboard's dark indigo plate;
# guardrails take the rose "needs a decision" field; everything else is a
# neutral register plate.
GROUP_STYLES = {
    "decision-engine": {
        "bg": INDIGO, "border": INDIGO_DEEP, "title": PAPER, "title_bg": INDIGO,
        "border_width": 1,
    },
    "guardrails": {
        "bg": ROSE, "border": FLASH, "title": STAMP_INK, "title_bg": ROSE,
        "border_width": 2,
    },
    "fastapi-service": {
        "bg": PANEL, "border": RULE, "title": GREEN_INK, "title_bg": PANEL,
        "border_width": 1,
    },
    "vetted-knowledge-sources": {
        "bg": NO_SIGNAL, "border": RULE, "title": INDIGO, "title_bg": NO_SIGNAL,
        "border_width": 1,
    },
}
DEFAULT_GROUP = {
    "bg": PANEL, "border": RULE, "title": INDIGO, "title_bg": PANEL,
    "border_width": 1,
}

# Node plates. Default is paper on a hairline; the exceptions carry the
# dashboard's stamp/amber/live semantics.
NODE_STYLES = {
    "approval-gate-set": (ROSE, FLASH, STAMP_INK, 2, "solid"),
    "approval-ledger": (ROSE, FLASH, STAMP_INK, 2, "solid"),
    "risk-tiers": (YELLOW, AMBER_INK, AMBER_INK, 1, "solid"),
    "evaluation": (PAPER, GREEN_INK, GREEN_INK, 1, "solid"),
    "optional-llm-layer": (PAPER, SLATE, MUTED, 1, "dashed"),
    "decision-trajectory": (NO_SIGNAL, SLATE, MUTED, 1, "dashed"),
    "store-manager": (PAPER, INDIGO, INDIGO, 2, "solid"),
    "executive-team": (PAPER, INDIGO, INDIGO, 2, "solid"),
}
DEFAULT_NODE = (PAPER, RULE, INK, 1, "solid")


def _rect(eid, x, y, w, h, fill, border=None, border_width=None,
          border_style=None, texts=None):
    rect = {"tag": "Shape", "id": eid, "x": x, "y": y, "width": w, "height": h,
            "bgColor": fill, "borderColor": border or fill, "styleMode": "plain",
            "cornerRadius": "sharp", "texts": texts or [{"text": ""}]}
    if border_width is not None:
        rect["borderWidth"] = border_width
    if border_style is not None:
        rect["borderStyle"] = border_style
    return rect


def _label(eid, x, y, text, color, size=13, width=GROUP_W, halign="left"):
    return {"tag": "Textbox", "id": eid, "x": x, "y": y, "width": width, "height": 24,
            "text": text, "fontSize": size, "color": color, "hAlign": halign}


def _decorate(entities: list) -> list:
    """Re-skin the base entities into the board's register theme."""
    out = []
    for e in entities:
        eid = e["id"]
        if eid == "diagram-title":
            continue  # replaced by the masthead title block
        e["styleMode"] = "plain"
        e["cornerRadius"] = "sharp"
        e["y"] = e["y"] + SHIFT
        if e["tag"] == "Group":
            style = GROUP_STYLES.get(eid, DEFAULT_GROUP)
            e.update({
                "bgColor": style["bg"],
                "borderColor": style["border"],
                "borderWidth": style["border_width"],
                "title": {
                    "text": e["title"]["text"],
                    "color": style["title"],
                    "bgColor": style["title_bg"],
                    "border": False,
                    "fontSize": 15,
                },
            })
        else:
            e.pop("icon", None)
            e.pop("iconProps", None)
            fill, border, text_color, border_width, border_style = NODE_STYLES.get(
                eid, DEFAULT_NODE
            )
            e.update({
                "bgColor": fill,
                "borderColor": border,
                "borderWidth": border_width,
                "borderStyle": border_style,
                "texts": [{"text": e["texts"][0]["text"], "color": text_color}],
            })
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
    # should share an edge now shares one.
    FURNITURE_PAD = 12
    furniture_x = LEFT - FURNITURE_PAD
    furniture_w = (content_right + FURNITURE_PAD) - furniture_x
    right_edge = content_right + FURNITURE_PAD + BLEED

    panel = {"x": furniture_x, "y": 150,
             "w": furniture_w,
             "h": content_bottom + 24 - 150}

    # Full-bleed masthead: the board's indigo band, keyline and paper chip.
    band_h = 96
    header = [
        _rect("tcj-band", 0, 0, right_edge, band_h, INDIGO, INDIGO),
        _rect("tcj-band-keyline", 0, band_h, right_edge, 4, INDIGO_DEEP, INDIGO_DEEP),
        # A Textbox, not a Shape: Shape text is inset by --er-shape-h-inset (7.5% of
        # the shape's own width), so a 1100-wide title box indented the title 82pt and
        # it lined up with neither the rule nor the chip. Textbox text has no inset.
        {"tag": "Textbox", "id": "tcj-title", "x": LEFT, "y": 18, "width": 900, "height": 40,
         "text": "The Retail Decision Intelligence Agent", "fontSize": 26, "hAlign": "left", "color": PAPER},
        {"tag": "Textbox", "id": "tcj-subtitle", "x": LEFT, "y": 56, "width": 900, "height": 24,
         "text": "deterministic, human-gated · plan → execute → measure → re-decide",
         "fontSize": 13, "hAlign": "left", "color": "#c9c3e2"},
        _rect("tcj-chip", content_right - 118, 24, 118, 26, PAPER, RULE, border_width=1,
              texts=[{"text": "architecture", "fontSize": 12, "color": INDIGO}]),
    ]

    panel_group = {"tag": "Group", "id": "tcj-panel", "x": panel["x"], "y": panel["y"],
                   "width": panel["w"], "height": panel["h"],
                   "bgColor": PAPER, "borderColor": RULE, "borderWidth": 1,
                   "cornerRadius": "sharp", "styleMode": "plain"}

    col_labels = []
    for i, txt in enumerate(COLUMN_LABELS):
        x = LEFT + i * COL_STEP
        marker = _rect(f"tcj-col-marker-{i}", x, panel["y"] + 18, 10, 10,
                       COLUMN_MARKERS[i], COLUMN_MARKERS[i])
        marker["containerId"] = "tcj-panel"
        lab = _label(f"tcj-col-{i}", x + 16, panel["y"] + 12, txt, INDIGO,
                     size=13, width=GROUP_W - 16)
        lab["containerId"] = "tcj-panel"
        col_labels.extend([marker, lab])

    footer_y = panel["y"] + panel["h"] + 34
    footer = [
        _rect("tcj-footer-rule", furniture_x, panel["y"] + panel["h"] + 24,
              furniture_w, 1, RULE, RULE),
        # The author holds the copyright (see LICENSE); dunnhumby is named as the
        # data source, not as the brand. The page should read as a personal
        # project built on dunnhumby's public dataset, not as their product.
        _label("tcj-footer-copy", LEFT, footer_y,
               "© 2026 Ashish Kumar", MUTED, size=11, width=700),
        _label("tcj-footer-data", content_right - 360, footer_y,
               "data: dunnhumby · The Complete Journey", MUTED,
               size=11, width=360, halign="right"),
    ]

    ground_h = footer_y + 24 + BLEED
    # A Group, not a Shape: a full-page Shape is treated as a routing obstacle
    # and pushes edge labels off their original corridors. A container paints
    # the same stock ground without joining the obstacle field.
    ground = {"tag": "Group", "id": "tcj-ground", "x": 0, "y": 0,
              "width": right_edge, "height": ground_h,
              "bgColor": STOCK, "borderColor": STOCK, "borderWidth": 0,
              "cornerRadius": "sharp", "styleMode": "plain"}

    geo = {e["id"]: e for e in nodes}
    verifier = geo["verifier"]
    guardrails = geo["guardrails"]
    engine = geo["decision-engine"]
    route_x = engine["x"] + engine["width"] + 30
    start_y = verifier["y"] + verifier["height"] / 2
    enter_y = guardrails["y"] + 40
    # Invisible waypoints route the rule-gate edge around the decision-core
    # plate: a straight drop would cut through the recorded-trajectory node.
    waypoints = [
        _rect("tcj-rule-gate-a", route_x, start_y, 1, 1, PAPER, PAPER),
        _rect("tcj-rule-gate-b", route_x, enter_y, 1, 1, PAPER, PAPER),
    ]
    for wp in waypoints:
        wp["containerId"] = "tcj-panel"

    entities = [ground] + header + [panel_group] + col_labels + nodes + waypoints + footer
    # Hide only the trajectory edge: the engine records the trajectory, not the
    # verifier. The verifier -> guardrails rule-gate edge is real (engine.evaluate
    # applies the guardrail approval gate right after verify) and is kept so the
    # Guardrails column is not left floating.
    unlabeled = {("verifier", "decision-trajectory")}
    connections = []
    for c in build_connections():
        if (c["from"], c["to"]) in unlabeled:
            continue
        if (c["from"], c["to"]) == ("verifier", "guardrails"):
            continue
        if (c["from"], c["to"]) == ("store-manager", "approval-ledger"):
            color = STAMP_INK
        elif c.get("lineStyle") == "dashed":
            color = SLATE
        else:
            color = GREEN_INK
        connections.append({**c, "color": color})
    connections.extend([
        {"from": "verifier", "to": "tcj-rule-gate-a", "lineStyle": "dashed",
         "endArrowhead": None, "color": STAMP_INK},
        {"from": "tcj-rule-gate-a", "to": "tcj-rule-gate-b", "label": "rule gates",
         "lineStyle": "dashed", "endArrowhead": None, "color": STAMP_INK},
        {"from": "tcj-rule-gate-b", "to": "guardrails",
         "lineStyle": "dashed", "endArrowhead": "triangle", "color": STAMP_INK},
    ])
    return {"entities": entities, "connections": connections}


def main() -> None:
    document = build_document()
    out = Path(__file__).resolve().parents[1] / "docs" / "images" / "architecture-dunnhumby.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(document['entities'])} entities, {len(document['connections'])} connections)")


if __name__ == "__main__":
    main()
