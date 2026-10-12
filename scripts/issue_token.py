#!/usr/bin/env python3
"""Mint a short-lived approval token (scripts/issue_token.py).

Usage:
    APPROVAL_TOKEN_SECRET=<secret> python scripts/issue_token.py alice approver
    APPROVAL_TOKEN_SECRET=<secret> python scripts/issue_token.py bob viewer --ttl-days 1

The secret is read from the environment (never from argv, so it stays out of
shell history). Prints one ``v1.user.role.exp.sig`` line: hand it to the
operator, who passes it as ``Authorization: Bearer <token>``. The server needs
the same secret in ``APPROVAL_TOKEN_SECRET``; rotate with
``APPROVAL_TOKEN_SECRET_PREV`` holding the old value during overlap.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from approvals.identity import TIMED_SECRET_ENV, issue_timed_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Mint a short-lived approval token.")
    parser.add_argument("user", help="operator name (no dots)")
    parser.add_argument("role", choices=("approver", "viewer"))
    parser.add_argument("--ttl-days", type=float, default=7.0,
                        help="lifetime in days (default: 7)")
    args = parser.parse_args()
    secret = os.getenv(TIMED_SECRET_ENV, "").strip()
    if not secret:
        print(f"{TIMED_SECRET_ENV} is not set; refusing to mint.", file=sys.stderr)
        return 1
    ttl_seconds = int(args.ttl_days * 86400)
    try:
        print(issue_timed_token(args.user, args.role, ttl_seconds, secret))
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
