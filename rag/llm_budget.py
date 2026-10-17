"""Spend guard for the off-path LLM - the one part of the demo that costs money.

Both LLM call sites sit behind PUBLIC, UNAUTHENTICATED GETs (``/why`` and
``/advisory``), so switching the layer on publishes a paid API key's spend to
whoever finds the URL. Two per-process ceilings make that bounded:

* **Per client**, a sliding one-hour window, so no single caller can loop the
  endpoint and spend the account's quota.
* **Global**, a call budget per UTC day, so the account has a hard ceiling no
  matter how many clients or addresses are involved.

They fail differently on purpose. The daily budget is a fact about the account,
not about the caller, so the LLM is simply not called and the deterministic
template is served - the demo keeps working and keeps costing nothing. The
per-client window is a fact about *this caller*, so the endpoint answers 429 with
``Retry-After`` rather than letting a loop look like a normal visitor. Either
way the decision path is untouched: this module gates spend, never output.

State is in-process, therefore per-instance: a multi-instance deploy gets one
budget each, and a redeploy starts them fresh. That is a deliberate trade -
this guards a portfolio demo, not a metered product, and Redis or Postgres would
cost more than the thing being guarded. Limits come from the environment
(``LLM_PER_IP_HOURLY_LIMIT``, ``LLM_DAILY_CALL_BUDGET``); ``0`` means unlimited,
which is what a laptop and the test suite want.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

WINDOW_SECONDS = 3600

_lock = threading.Lock()
# client id -> monotonically growing timestamps of its LLM calls in the window
_ip_calls: dict[str, deque[float]] = {}
# UTC date -> LLM calls made that day
_daily_calls: dict[str, int] = {}


def _limit(raw: str | None, default: int) -> int:
    """Parse an operator knob; a typo must not silently uncap the guard.

    Unparseable and unset both fall back to the documented default - a guard
    that disables itself when misconfigured is not a guard. ``0`` is an explicit
    opt-out and is honoured as written.
    """
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def per_ip_hourly_limit() -> int:
    """LLM-backed requests allowed per client per rolling hour (0 = unlimited)."""
    return _limit(os.getenv("LLM_PER_IP_HOURLY_LIMIT"), 20)


def daily_call_budget() -> int:
    """LLM calls allowed per UTC day across all clients (0 = unlimited)."""
    return _limit(os.getenv("LLM_DAILY_CALL_BUDGET"), 250)


def daily_used(*, now: float | None = None) -> int:
    """Calls consumed today (UTC). Read-only: this does not consume a slot.

    Exposed so ``/metrics`` can show the spend ceiling next to its consumption.
    A budget that silently degrades every request to the template is exactly the
    kind of fail-closed state ADR-0006 says is invisible without a measurement.
    """
    stamp = now if now is not None else time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(stamp))
    with _lock:
        return _daily_calls.get(today, 0)


def reset() -> None:
    """Drop every counter. For tests, and for ops who need a manual reset."""
    with _lock:
        _ip_calls.clear()
        _daily_calls.clear()


def client_ip(forwarded_for: str | None, fallback: str = "") -> str:
    """Best-effort caller identity behind the platform's proxy.

    The LAST ``X-Forwarded-For`` entry is used deliberately: a client may send
    its own ``X-Forwarded-For``, and a proxy that appends the address it
    actually observed appends it *after* the forged one, so the tail is the
    entry the platform vouched for. A spoofed header therefore cannot buy extra
    quota - it can only get that client limited under an id it does not control.
    """
    if forwarded_for:
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if parts:
            return parts[-1]
    return fallback or "unknown"


def ip_allowance(ip: str, *, now: float | None = None) -> tuple[bool, int]:
    """Consume one slot for ``ip``. Returns ``(allowed, retry_after_seconds)``.

    A refusal consumes nothing, so a blocked caller stays blocked for the rest
    of its window instead of pushing the window forward with every attempt.
    """
    limit = per_ip_hourly_limit()
    if limit <= 0:
        return True, 0
    now = time.time() if now is None else now
    with _lock:
        calls = _ip_calls.setdefault(ip, deque())
        cutoff = now - WINDOW_SECONDS
        while calls and calls[0] <= cutoff:
            calls.popleft()
        if len(calls) >= limit:
            return False, max(1, int(calls[0] + WINDOW_SECONDS - now) + 1)
        calls.append(now)
    return True, 0


def daily_allowance(*, now: float | None = None) -> tuple[bool, int]:
    """Consume one call against today's budget. Returns ``(allowed, used_today)``."""
    budget = daily_call_budget()
    if budget <= 0:
        return True, 0
    stamp = now if now is not None else time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(stamp))
    with _lock:
        used = _daily_calls.get(today, 0)
        if used >= budget:
            return False, used
        _daily_calls[today] = used + 1
        # Yesterday's counters can only mislead; keep the map a single key wide.
        for stale in [day for day in _daily_calls if day != today]:
            del _daily_calls[stale]
        return True, used + 1
