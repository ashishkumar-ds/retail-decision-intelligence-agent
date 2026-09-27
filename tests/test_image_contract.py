"""The image must contain every module the runtime imports - first-party and
third-party.

This exists because it silently broke twice: `storage/` and `execution/` were
imported but never `COPY`-ed into the Dockerfile, and `httpx` was imported at
module load by the serving path while being declared only in `[dev]`. Both times
the container died at startup (`ModuleNotFoundError`) while every local test
passed - local runs import from the source tree and from a dev install, the
image does neither.

The check is static on purpose: no Docker daemon needed in CI. It reads the
imports of the tree the Dockerfile actually ships (including imports nested
inside functions, which is how `analytics` and `evaluation` are pulled in) and
asserts every first-party package is copied into the image and every third-party
package is declared in `[project] dependencies`.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"

# Packages that are development-only: never imported by the runtime packages.
DEV_ONLY = {"tests", "scripts"}


def _first_party_packages() -> set[str]:
    return {p.name for p in ROOT.iterdir()
            if p.is_dir() and (p / "__init__.py").exists()
            and p.name not in DEV_ONLY and p.name != "tests"}


def _imported_modules(source: pathlib.Path) -> set[str]:
    """Top-level module names one file imports, read from its AST.

    Parsing, not pattern-matching: a line-anchored regex also matches the word
    "import" in prose inside docstrings and comments (this repository's prose
    quotes imports), which is how this check first reported phantom packages
    such as `IPython` and `cgi`. An AST sees only real import statements - and
    nested ones too, which is how `analytics` and `evaluation` are pulled in.

    Relative imports (`from .corpus import ...`) are skipped: they name a
    sibling module of the package being parsed, never a top-level package.
    """
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def _runtime_packages() -> set[str]:
    """Packages imported by anything the image ships, transitively from app/."""
    packages = _first_party_packages()
    reachable: set[str] = set()
    pending = ["app"]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        directory = ROOT / current
        if not directory.is_dir():
            continue
        for source in directory.rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            for name in _imported_modules(source):
                if name in packages and name not in reachable:
                    pending.append(name)
    return reachable


def _copied_paths() -> set[str]:
    return set(re.findall(r"^COPY\s+(\S+)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE))


def _shipped_directories() -> list[pathlib.Path]:
    """The source directories the image ships - the Dockerfile's own COPY list.

    The image is the authority on what "shipped" means, and it is what the other
    check in this file already compares against. Walking the repository root
    instead dragged in `.venv/`, which is how this file first reported phantom
    packages (`IPython`, `cgi`, `distutils`) while never scanning a real one.
    """
    return sorted(d for name in _copied_paths() if (d := ROOT / name).is_dir())


# Third-party packages shipped code imports only inside a function body, on a
# path the default image deliberately does not serve. Each one is a real
# dependency of an opt-in feature, never of boot:
#   anthropic - `[llm]` extra: rag/llm_explainer.py + rag/advisor.py import it
#               inside the provider call and fall back to the deterministic
#               template when it is missing.
#   pandas    - evaluation/industry_benchmark.py, a one-shot benchmark script.
#   pypdf     - rag/ingest_pdf.py, the one-shot corpus ingestion script.
#   psycopg*  - `[storage]` extra: storage/database.py imports it only when
#               DATABASE_URL selects Postgres (SQLite is the default backend).
# They stay out of [project] dependencies on purpose - a lazily imported extra
# is how the default image stays small. Anything else must be declared.
OPTIONAL_IMPORTS = {"anthropic", "pandas", "psycopg", "psycopg2", "pypdf"}


def _third_party_imports() -> set[str]:
    """Top-level third-party modules imported anywhere in the shipped tree."""
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    shipped = _shipped_directories()
    names: set[str] = set()
    for directory in shipped:
        for source in directory.rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            names |= _imported_modules(source)
    return names - stdlib - {directory.name for directory in shipped}


def _declared_runtime_deps() -> set[str]:
    """Top-level runtime dependencies, as pip would install them in the image."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11 fallback (CI runs 3.10+)
        import tomli as tomllib  # type: ignore[no-redef]
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for requirement in declared["project"]["dependencies"]:
        name = re.split(r"[<>=!;\[\s]", requirement, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def test_every_runtime_package_is_copied_into_the_image():
    copied = _copied_paths()
    missing = sorted(pkg for pkg in _runtime_packages() if pkg not in copied)
    assert not missing, (
        f"Dockerfile does not COPY these runtime packages: {missing}. "
        "The image would fail at startup even though local tests pass."
    )


def test_every_runtime_import_is_a_declared_runtime_dependency():
    """The dependency class that killed the first production deploy.

    ``httpx`` was imported at module load by the serving path (app/main.py,
    tools/*, phase2/portfolio.py) but lived only in ``[dev]`` - so every local
    test passed and the container died on boot with ModuleNotFoundError. This
    test fails if any shipped module imports a third-party package the image
    would not install: it must be declared in ``[project] dependencies``.
    ``OPTIONAL_IMPORTS`` names the lazily imported extras (see the comment
    there) - everything else is required at boot.
    """
    declared = _declared_runtime_deps()
    needed = _third_party_imports() - OPTIONAL_IMPORTS
    missing = sorted(needed - declared)
    assert not missing, (
        f"runtime imports not covered by [project] dependencies: {missing}. "
        "A package importable in tests but missing from the image "
        "will fail at boot - declare it."
    )


def test_dockerfile_binds_the_injected_port_on_all_interfaces():
    """PaaS hosts (Render, Fly, Heroku) inject $PORT and require 0.0.0.0."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "--host 0.0.0.0" in text
    assert "${PORT}" in text, "the CMD must bind the platform-injected PORT"


def test_image_never_bakes_secrets():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert not re.search(r"^\s*ENV\s+\w*(SECRET|TOKEN|API_KEY|PASSWORD)", text, re.MULTILINE)


def test_dockerignore_excludes_secrets_and_local_state():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    entries = {line.strip() for line in ignore if line.strip() and not line.startswith("#")}
    assert ".env.local" in entries and ".env" in entries
    assert "logs/" in entries, "local logs must not be baked into the image"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --- deployment blueprint -----------------------------------------------------

def test_render_blueprint_keeps_secrets_out_of_the_repo():
    """The blueprint may only reference secrets, never contain them: every
    secret-shaped variable must be `sync: false` (filled in the dashboard)."""
    yaml = pytest.importorskip("yaml")
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    service = blueprint["services"][0]
    assert service["runtime"] == "docker"
    assert service["healthCheckPath"] == "/health"
    for var in service["envVars"]:
        key = var["key"]
        if any(token in key for token in ("TOKEN", "KEY", "PASSWORD", "SECRET")):
            assert var.get("sync") is False, f"{key} must be filled in the dashboard, not in git"
            assert "value" not in var, f"{key} has an in-repo value"


def test_render_blueprint_mounts_the_durable_log_volume():
    """The append-only audit trail lives in files under logs/; without a disk
    a deploy discards it (see docs/DEPLOYMENT.md)."""
    yaml = pytest.importorskip("yaml")
    service = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))["services"][0]
    assert service["disk"]["mountPath"] == "/srv/app/logs"


def test_disk_is_never_mounted_on_a_free_plan():
    """Render rejects a blueprint that mounts a disk on the free plan (free
    instances have no persistent disks) - the default posture is Starter."""
    yaml = pytest.importorskip("yaml")
    service = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))["services"][0]
    if "disk" in service:
        assert service.get("plan") not in (None, "free", "hobby"), (
            "a disk on a free plan makes the blueprint undeployable")


def test_deployment_doc_states_the_storage_tradeoff():
    doc = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "audit trail" in doc and "persistent disk" in doc.lower()
    assert "Free tier" in doc  # the demo-only consequence is spelled out
