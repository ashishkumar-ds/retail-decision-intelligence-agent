"""Root conftest: make the repository importable when pytest is invoked directly.

``tests/*.py`` import the project's packages (``app``, ``decision_engine``,
``rag``, ...). A bare ``pytest`` run - as opposed to ``python -m pytest`` - does
NOT put the working directory on ``sys.path``, and test modules are imported in
path order. So when no *effective* installation of this package exists in the
interpreter, the alphabetically-first test files fail with ModuleNotFoundError
while every later file succeeds - because ``tests/test_golden_evals.py`` happens
to insert the repository root into ``sys.path`` as a side effect. That
order-dependence makes a total import failure look like a partial breakage in
whatever seven files happen to sort first, which is exactly the confusion it
caused.

This mirrors what ``evaluation/*.py`` and ``scripts/*.py`` already do at their
entry points, and makes the suite work on a fresh clone (or any venv) with no
editable install. Installing the package is still the supported path - this only
removes the trap.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
