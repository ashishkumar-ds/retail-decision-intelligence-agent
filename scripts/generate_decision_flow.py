#!/usr/bin/env python3
"""Generate docs/images/decision-flow.json - the plain-language, one-minute
decision flow in the same Stub Register palette as the detailed architecture
diagram (docs/images/architecture.png).

The palette is imported from `generate_architecture_dunnhumby.py` so the two
images cannot drift apart. Rendering is a separate step; use
`scripts/render_decision_flow.py` to regenerate the JSON, render the PNG, and
stamp the JSON hash the PNG was built from.

Design language:
    - full-bleed indigo masthead with the indigo-deep keyline
    - one paper register card on the warm stock ground
    - paper plates with hairline rules and sharp corners
    - green main path, magenta human gate, slate dotted feedback loop
    - amber risk diamond, panel-filled recorded-decision cylinder
"""
from __future__ import annotations

import json
from pathlib import Path

from generate_architecture_dunnhumby import (  # noqa: E402
    AMBER_INK,
    FLASH,
    GREEN_INK,
    INDIGO,
    INDIGO_DEEP,
    INK,
    MUTED,
    PANEL,
    PAPER,
    ROSE,
    RULE,
    SLATE,
    STAMP_INK,
    STOCK,
    YELLOW,
)

W = 1400
GROUND_H = 570
CARD_X = 28
CARD_Y = 124
CARD_W = W - 2 * CARD_X
CARD_H = 410

NODE_FONT = 16
NODE_H = 80
MAIN_Y = 250


def _rect(eid, x, y, w, h, fill, border=None, border_width=None, texts=None):
    rect = {
        "tag": "Shape", "id": eid, "x": x, "y": y, "width": w, "height": h,
        "bgColor": fill, "borderColor": border or fill, "styleMode": "plain",
        "cornerRadius": "sharp", "texts": texts or [{"text": ""}],
    }
    if border_width is not None:
        rect["borderWidth"] = border_width
    return rect


def _node(eid, x, y, w, h, text, fill=PAPER, border=RULE, text_color=INK,
          border_width=1, shape="rectangle", font_size=NODE_FONT):
    node = _rect(
        eid, x, y, w, h, fill, border, border_width=border_width,
        texts=[{"text": text, "fontSize": font_size, "color": text_color}],
    )
    node["shape"] = shape
    node["containerId"] = "tcj-flow-card"
    return node


def _label(eid, x, y, text, color, size=12, width=200, halign="left"):
    return {
        "tag": "Textbox", "id": eid, "x": x, "y": y, "width": width, "height": 24,
        "text": text, "fontSize": size, "color": color, "hAlign": halign,
    }


def _conn(fr, to, color, label=None, line_style="solid", end_arrowhead="triangle"):
    conn = {
        "from": fr, "to": to, "color": color, "lineStyle": line_style,
        "lineWidth": 2.5, "typeface": "clean", "fontSize": 12,
    }
    if label:
        conn["label"] = label
    if end_arrowhead is None:
        conn["endArrowhead"] = None
    return conn


def build_document() -> dict:
    band_h = 96

    ground = {
        "tag": "Group", "id": "tcj-flow-ground", "x": 0, "y": 0,
        "width": W, "height": GROUND_H, "bgColor": STOCK, "borderColor": STOCK,
        "borderWidth": 0, "cornerRadius": "sharp", "styleMode": "plain",
    }
    header = [
        _rect("tcj-flow-band", 0, 0, W, band_h, INDIGO, INDIGO),
        _rect("tcj-flow-keyline", 0, band_h, W, 4, INDIGO_DEEP, INDIGO_DEEP),
        _label("tcj-flow-title", 40, 18, "How a decision is made", PAPER,
               size=26, width=800),
        _label("tcj-flow-subtitle", 40, 56,
               "In plain English — one store, one recommendation, one human gate",
               "#c9c3e2", size=14, width=900),
        _rect("tcj-flow-chip", 1210, 24, 150, 26, PAPER, RULE, border_width=1,
              texts=[{"text": "one-minute version", "fontSize": 12, "color": INDIGO}]),
    ]

    card = {
        "tag": "Group", "id": "tcj-flow-card", "x": CARD_X, "y": CARD_Y,
        "width": CARD_W, "height": CARD_H, "bgColor": PAPER,
        "borderColor": RULE, "borderWidth": 1, "cornerRadius": "sharp",
        "styleMode": "plain",
    }

    nodes = [
        _node("data", 56, MAIN_Y, 170, NODE_H, "Store data and evidence"),
        _node("agent", 286, MAIN_Y, 200, NODE_H, "Agent proposes one recommendation"),
        _node("rules", 546, MAIN_Y, 165, NODE_H, "Hard rules check it"),
        _node("risky", 770, 235, 130, 110, "Risky?", fill=YELLOW,
              border=AMBER_INK, text_color=AMBER_INK, shape="diamond"),
        _node("recorded", 970, MAIN_Y, 170, NODE_H, "Recorded decision",
              fill=PANEL, shape="cylinder"),
        _node("outcome", 1190, MAIN_Y, 150, NODE_H, "Outcome measured"),
        _node("human", 960, 140, 210, NODE_H, "Human approves or rejects",
              fill=ROSE, border=FLASH, text_color=STAMP_INK, border_width=2),
    ]

    # Invisible waypoints keep the dotted feedback loop below the flow, not
    # through it. The card is paper, so the 1px paper waypoints disappear.
    waypoints = [
        _rect("tcj-flow-wp-a", 1265, 440, 1, 1, PAPER, PAPER),
        _rect("tcj-flow-wp-b", 141, 440, 1, 1, PAPER, PAPER),
    ]
    for wp in waypoints:
        wp["containerId"] = "tcj-flow-card"

    legend = [
        _rect("tcj-flow-legend-green", 56, 495, 28, 2, GREEN_INK, GREEN_INK),
        _label("tcj-flow-legend-green-text", 92, 483, "main path", MUTED,
               size=12, width=100),
        _rect("tcj-flow-legend-magenta", 230, 495, 28, 2, STAMP_INK, STAMP_INK),
        _label("tcj-flow-legend-magenta-text", 266, 483, "human gate", MUTED,
               size=12, width=110),
        _label("tcj-flow-legend-dots", 410, 481, "· · ·", SLATE, size=16, width=50),
        _label("tcj-flow-legend-dots-text", 460, 483, "next round", MUTED,
               size=12, width=110),
    ]
    for entity in legend:
        if entity["tag"] == "Shape":
            entity["containerId"] = "tcj-flow-card"

    entities = [ground] + header + [card] + nodes + waypoints + legend

    connections = [
        _conn("data", "agent", GREEN_INK),
        _conn("agent", "rules", GREEN_INK),
        _conn("rules", "risky", GREEN_INK),
        _conn("risky", "human", STAMP_INK, label="yes"),
        _conn("human", "recorded", STAMP_INK),
        _conn("risky", "recorded", GREEN_INK, label="no"),
        _conn("recorded", "outcome", GREEN_INK),
        _conn("outcome", "tcj-flow-wp-a", SLATE, line_style="dashed",
              end_arrowhead=None),
        _conn("tcj-flow-wp-a", "tcj-flow-wp-b", SLATE, label="next round",
              line_style="dashed", end_arrowhead=None),
        _conn("tcj-flow-wp-b", "data", SLATE, line_style="dashed"),
    ]
    return {"entities": entities, "connections": connections}


def main() -> None:
    document = build_document()
    out = Path(__file__).resolve().parents[1] / "docs" / "images" / "decision-flow.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {out} ({len(document['entities'])} entities, "
          f"{len(document['connections'])} connections)")


if __name__ == "__main__":
    main()
