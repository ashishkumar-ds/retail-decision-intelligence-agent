#!/usr/bin/env python3
"""Regenerate AND render the dunnhumby architecture diagram, then stamp it.

`generate_architecture_dunnhumby.py` writes the JSON, but the PNG is a separate
render step, so the two can drift: a JSON/label change without a re-render once
shipped a stale image (the node still read "22 golden scenarios" after the count
became 23). This helper does both steps and writes
`docs/images/architecture.png.sha256` = sha256 of the JSON the PNG
was rendered from. `scripts/check.py` compares that stamp to the committed
JSON, so a stale PNG fails the build instead of shipping.

Usage:
    python scripts/render_architecture_dunnhumby.py [--chromium-path <chrome>]
    python scripts/render_architecture_dunnhumby.py --skip-render  # JSON only

Chromium resolution: ``--chromium-path``, else ``$CHROMIUM_PATH``, else
eraser-diagrams' own auto-detection.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_architecture_dunnhumby import main as generate  # noqa: E402

JSON_PATH = ROOT / "docs" / "images" / "architecture-dunnhumby.json"
PNG_PATH = ROOT / "docs" / "images" / "architecture.png"
STAMP_PATH = ROOT / "docs" / "images" / "architecture.png.sha256"


def _render(chromium_path: str | None) -> None:
    cmd = ["eraser-diagrams", "render", str(JSON_PATH), "--scale", "2", "-o", str(PNG_PATH)]
    if chromium_path:
        cmd += ["--chromium-path", chromium_path]
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="generate + render the architecture diagram")
    parser.add_argument("--chromium-path", default=os.environ.get("CHROMIUM_PATH"),
                        help="Chromium executable (default: $CHROMIUM_PATH or auto-detect)")
    parser.add_argument("--skip-render", action="store_true",
                        help="regenerate the JSON only (no PNG, no stamp)")
    args = parser.parse_args()

    generate()
    if args.skip_render:
        return

    _render(args.chromium_path)
    digest = hashlib.sha256(JSON_PATH.read_bytes()).hexdigest()
    STAMP_PATH.write_text(
        "# sha256 of architecture-dunnhumby.json - the source this PNG was rendered from.\n"
        f"{digest}  architecture-dunnhumby.json\n",
        encoding="utf-8",
    )
    print(f"stamped {STAMP_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
