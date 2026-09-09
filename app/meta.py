"""Release metadata. Single source of truth for the running service version.

``pyproject.toml`` [project].version must equal ``VERSION``;
``scripts/check.py`` enforces the match so the two cannot drift.
"""
VERSION = "4.2.0"