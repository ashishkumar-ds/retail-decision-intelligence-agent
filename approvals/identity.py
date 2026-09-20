"""Approver identity and role resolution (approvals/identity.py).

Closes the provenance gap in the approve/reject path: the ledger's ``actor``
field is whatever the caller *claims* in the request body, while the only
thing actually authenticated is a shared bearer token. This module adds an
authenticated identity layer that is recorded alongside the claim.

Two auth modes, both fail-closed:

- **Token map** (``APPROVAL_TOKENS`` env): ``token:user:role`` entries,
  comma-separated, e.g. ``tok1:alice:approver,tok2:bob:viewer``. Roles:
  ``approver`` may decide; ``viewer`` is read-only (403 on decisions).
  A malformed entry raises at parse time - a half-valid token map must never
  silently become a weaker one.
- **Shared token** (legacy ``APPROVAL_AUTH_TOKEN``, unchanged contract):
  treated as an unnamed ``approver`` principal labelled ``shared-token``.

The resolved principal is stamped into the record and the audit ledger as
``decided_by``; the caller-claimed ``actor`` is preserved as-is so the
ledger keeps showing both what was claimed and what was proven.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

TOKENS_ENV = "APPROVAL_TOKENS"
SHARED_TOKEN_ENV = "APPROVAL_AUTH_TOKEN"
ROLES = frozenset({"approver", "viewer"})
DECISION_ROLE = "approver"


class IdentityNotConfigured(RuntimeError):
    """No server-side credential is configured; decision endpoints stay disabled (503)."""


@dataclass(frozen=True)
class Principal:
    user: str | None
    role: str
    source: str  # "token-map" | "shared-token"

    def label(self) -> str:
        """The authenticated identity string recorded in the ledger."""
        return f"user:{self.user}" if self.user else "shared-token"


def parse_token_map(raw: str) -> dict[str, tuple[str, str]]:
    """Parse ``tok:user:role,tok:user:role`` → ``{tok: (user, role)}``.

    Raises ``ValueError`` on any malformed entry or unknown role (fail closed:
    refuse the whole map, never accept a subset).
    """
    token_map: dict[str, tuple[str, str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"malformed {TOKENS_ENV} entry {entry!r}; expected token:user:role")
        token, user, role = parts
        if role not in ROLES:
            raise ValueError(
                f"unknown role {role!r} in {TOKENS_ENV}; expected one of {sorted(ROLES)}")
        token_map[token] = (user, role)
    return token_map


def resolve_principal(bearer_token: str | None) -> Principal:
    """Resolve the authenticated principal for a bearer token.

    Raises ``PermissionError`` for a token that matches neither the token map
    nor the legacy shared token, and ``IdentityNotConfigured`` when no
    server-side credential exists at all (callers map these to 403 / 503).
    """
    token_map_raw = os.getenv(TOKENS_ENV, "").strip()
    if token_map_raw:
        token_map = parse_token_map(token_map_raw)
        if bearer_token:
            for token, (user, role) in token_map.items():
                if secrets.compare_digest(bearer_token, token):
                    return Principal(user=user, role=role, source="token-map")
        raise PermissionError("unknown approval token")
    configured = os.getenv(SHARED_TOKEN_ENV)
    if not configured:
        raise IdentityNotConfigured("no approval credential configured")
    if bearer_token and secrets.compare_digest(bearer_token, configured):
        return Principal(user=None, role=DECISION_ROLE, source="shared-token")
    raise PermissionError("unknown approval token")


# Alias used at the call site: the token has already been verified against a
# configured credential; this resolves it into the audited principal.
principal_for_token = resolve_principal


def require_decision_role(principal: Principal) -> Principal:
    """Enforce that the principal may decide (fail closed on viewer tokens)."""
    if principal.role != DECISION_ROLE:
        raise PermissionError(
            f"role {principal.role!r} may not decide; {DECISION_ROLE} role required")
    return principal