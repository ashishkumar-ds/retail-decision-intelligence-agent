"""The image must contain every first-party package the runtime imports.

This exists because it silently broke once: `storage/` and `execution/` were
imported but never `COPY`-ed into the Dockerfile, so the container would have
failed at startup (`ModuleNotFoundError`) while every local test passed -
local runs import from the source tree, the image does not.

The check is static on purpose: no Docker daemon needed in CI. It collects
every first-party package imported anywhere in the runtime packages (including
imports nested inside functions, which is how `analytics` and `evaluation` are
pulled in) and asserts each one is copied into the image.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"

# Packages that are development-only: never imported by the runtime packages.
DEV_ONLY = {"tests", "scripts"}

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def _first_party_packages() -> set[str]:
    return {p.name for p in ROOT.iterdir()
            if p.is_dir() and (p / "__init__.py").exists()
            and p.name not in DEV_ONLY and p.name != "tests"}


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
            for name in _IMPORT_RE.findall(source.read_text(encoding="utf-8")):
                if name in packages and name not in reachable:
                    pending.append(name)
    return reachable


def _copied_paths() -> set[str]:
    return set(re.findall(r"^COPY\s+(\S+)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE))


def test_every_runtime_package_is_copied_into_the_image():
    copied = _copied_paths()
    missing = sorted(pkg for pkg in _runtime_packages() if pkg not in copied)
    assert not missing, (
        f"Dockerfile does not COPY these runtime packages: {missing}. "
        "The image would fail at startup even though local tests pass."
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
