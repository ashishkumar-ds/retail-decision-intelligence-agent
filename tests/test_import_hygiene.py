"""Import-hygiene regression tests.

``tools.campaign_tool`` and ``phase2`` are mutually dependent at module level
(campaign_tool uses the pydantic contract schema; phase2.evaluator uses
campaign_tool helpers). The schema import must stay lazy inside the function,
or importing ``tools.campaign_tool`` first fails with a partially-initialized
module. This test pins that property in a fresh interpreter.
"""
from __future__ import annotations

import subprocess
import sys


def test_campaign_tool_imports_standalone():
    result = subprocess.run(
        [sys.executable, "-c", "import tools.campaign_tool"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_phase2_imports_standalone():
    result = subprocess.run(
        [sys.executable, "-c", "import phase2; import phase2.schemas"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_rag_llm_explainer_imports_standalone():
    """rag.explainer <-> rag.llm_explainer are mutually dependent; the guard
    import in llm_explainer must stay lazy (see the circular-import fix)."""
    result = subprocess.run(
        [sys.executable, "-c", "import rag.llm_explainer; import rag.explainer"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
