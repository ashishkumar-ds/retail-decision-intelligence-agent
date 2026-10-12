"""Approver identity and role resolution (approvals/identity.py).

Closes the provenance gap in the approve/reject path: the ledger's ``actor``
field is whatever the caller *claims* in the request body, while the only
thing actually authenticated is a shared bearer token. This module adds an
authenticated identity layer that is recorded alongside the claim.

Three auth modes, all fail-closed (precedence: map entry → timed token →
shared token):

- **Token map** (``APPROVAL_TOKENS`` env): ``token:user:role`` entries with an
  optional ``:YYYY-MM-DD`` expiry, comma-separated, e.g.
  ``tok1:alice:approver,tok2:bob:viewer:2027-01-01``. Roles: ``approver``
  may decide; ``viewer`` is read-only (403 on decisions). An expired token
  is rejected (403); rotation = issue the replacement alongside the old
  token, switch callers over, then delete the old entry. A malformed entry
  raises at parse time - a half-valid token map must never silently become
  a weaker one.
- **Short-lived tokens** (``APPROVAL_TOKEN_SECRET``): ``v1.user.role.exp.sig``
  HMAC-SHA256 tokens minted by ``scripts/issue_token.py``. No storage, no
  expiry table — expiry rides in the token, the signature proves provenance.
  Rotation overlap via ``APPROVAL_TOKEN_SECRET_PREV`` (accepts both).
- **Shared token** (legacy ``APPROVAL_AUTH_TOKEN``, unchanged contract):
  treated as an unnamed ``approver`` principal labelled ``shared-token``.
  No expiry (another reason to prefer the token map).

The resolved principal is stamped into the record and the audit ledger as
``decided_by``; the caller-claimed ``actor`` is preserved as-is so the
ledger keeps showing both what was claimed and what was proven.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from datetime import date

TOKENS_ENV = "APPROVAL_TOKENS"
SHARED_TOKEN_ENV = "APPROVAL_AUTH_TOKEN"
TIMED_SECRET_ENV = "APPROVAL_TOKEN_SECRET"
TIMED_SECRET_PREV_ENV = "APPROVAL_TOKEN_SECRET_PREV"
TIMED_VERSION = "v1"
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


@dataclass(frozen=True)
class TokenEntry:
    """One parsed ``APPROVAL_TOKENS`` entry: user, role, optional expiry day."""

    user: str
    role: str
    expires: date | None = None

    def expired(self, today: date | None = None) -> bool:
        return self.expires is not None and (today or date.today()) > self.expires


def _parse_expiry(raw: str, entry: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            f"malformed expiry {raw!r} in {TOKENS_ENV} entry {entry!r}; "
            "expected token:user:role[:YYYY-MM-DD]") from None


def parse_token_map(raw: str) -> dict[str, TokenEntry]:
    """Parse ``tok:user:role[:YYYY-MM-DD],...`` → ``{tok: TokenEntry}``.

    Raises ``ValueError`` on any malformed entry, unknown role or malformed
    expiry (fail closed: refuse the whole map, never accept a subset).
    """
    token_map: dict[str, TokenEntry] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) not in (3, 4) or not all(parts):
            raise ValueError(
                f"malformed {TOKENS_ENV} entry {entry!r}; "
                "expected token:user:role[:YYYY-MM-DD]")
        token, user, role = parts[0], parts[1], parts[2]
        if role not in ROLES:
            raise ValueError(
                f"unknown role {role!r} in {TOKENS_ENV}; expected one of {sorted(ROLES)}")
        expires = _parse_expiry(parts[3], entry) if len(parts) == 4 else None
        token_map[token] = TokenEntry(user=user, role=role, expires=expires)
    return token_map


def _timed_secrets() -> list[str]:
    """Current + previous HMAC secrets (rotation overlap accepts both)."""
    current = os.getenv(TIMED_SECRET_ENV, "").strip()
    previous = os.getenv(TIMED_SECRET_PREV_ENV, "").strip()
    return [s for s in (current, previous) if s]


def issue_timed_token(user: str, role: str, ttl_seconds: int,
                       secret: str, now: float | None = None) -> str:
    """Mint a short-lived ``v1.user.role.exp.sig`` token (stdlib HMAC-SHA256).

    No storage, no expiry table: the expiry rides in the token and the
    signature proves it was minted with the server secret. Raises
    ``ValueError`` for unknown roles, non-positive TTLs, or user names
    containing dots (the field separator).
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(ROLES)}")
    if not user or "." in user:
        raise ValueError("user must be non-empty and contain no dots")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    exp = int(now if now is not None else time.time()) + ttl_seconds
    body = f"{TIMED_VERSION}.{user}.{role}.{exp}"
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"),
                    hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_timed_token(bearer_token: str, now: float | None = None) -> Principal:
    """Verify a ``v1.*`` timed token against the configured secret(s).

    Raises ``PermissionError`` for any failure (bad shape, unknown version
    or role, bad signature under every configured secret, expiry) and
    ``IdentityNotConfigured`` when no timed secret exists at all.
    """
    secrets_list = _timed_secrets()
    if not secrets_list:
        raise IdentityNotConfigured("no timed-token secret configured")
    parts = bearer_token.split(".")
    if len(parts) != 5 or parts[0] != TIMED_VERSION or not all(parts[1:]):
        raise PermissionError("malformed timed token")
    _, user, role, exp_raw, sig = parts
    if role not in ROLES:
        raise PermissionError(f"unknown role {role!r} in timed token")
    try:
        exp = int(exp_raw)
    except ValueError:
        raise PermissionError("malformed timed-token expiry") from None
    if (now if now is not None else time.time()) > exp:
        raise PermissionError("approval token expired")
    body = ".".join(parts[:4])
    for secret in secrets_list:
        expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"),
                             hashlib.sha256).hexdigest()
        if secrets.compare_digest(sig, expected):
            return Principal(user=user, role=role, source="timed-token")
    raise PermissionError("unknown approval token")


def resolve_principal(bearer_token: str | None) -> Principal:
    """Resolve the authenticated principal for a bearer token.

    Precedence: token-map entry → timed token → legacy shared token, so a
    short-lived token keeps working while static entries rotate underneath
    it. Raises ``PermissionError`` for a token that matches nothing, and
    ``IdentityNotConfigured`` when no server-side credential exists at all
    (callers map these to 403 / 503).
    """
    token_map_raw = os.getenv(TOKENS_ENV, "").strip()
    secrets_list = _timed_secrets()
    configured = os.getenv(SHARED_TOKEN_ENV)
    if bearer_token:
        if token_map_raw:
            for token, entry in parse_token_map(token_map_raw).items():
                if secrets.compare_digest(bearer_token, token):
                    if entry.expired():
                        raise PermissionError("approval token expired")
                    return Principal(user=entry.user, role=entry.role, source="token-map")
        if secrets_list and bearer_token.startswith(TIMED_VERSION + "."):
            return verify_timed_token(bearer_token)
        if configured and secrets.compare_digest(bearer_token, configured):
            if token_map_raw:
                raise PermissionError("unknown approval token")  # map mode supersedes shared
            return Principal(user=None, role=DECISION_ROLE, source="shared-token")
    if token_map_raw or secrets_list or configured:
        raise PermissionError("unknown approval token")
    raise IdentityNotConfigured("no approval credential configured")


# Alias used at the call site: the token has already been verified against a
# configured credential; this resolves it into the audited principal.
principal_for_token = resolve_principal


def require_decision_role(principal: Principal) -> Principal:
    """Enforce that the principal may decide (fail closed on viewer tokens)."""
    if principal.role != DECISION_ROLE:
        raise PermissionError(
            f"role {principal.role!r} may not decide; {DECISION_ROLE} role required")
    return principal