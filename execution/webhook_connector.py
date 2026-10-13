"""Example real-system adapter (execution/webhook_connector.py).

Proves the :class:`ExecutionConnector` seam against something outside the
journal: POSTs the execution intent to a configured HTTPS endpoint and POSTs
a cancellation on reversal. This is the documented upgrade path from
``DryRunConnector`` — a POS/CRM adapter implements the same two methods and
is injected at ``POST /execute/{store_id}``; gating, idempotency, reversal
and audit keep living in ``execution/connector.py``, never in the adapter.

Standard library only (urllib), no new dependencies. Failures propagate to
the caller — unlike the dry-run default this adapter performs a real
external write, so ``execute_recommendation`` only journals after ``apply``
succeeds and ``reverse_execution`` only journals after ``reverse`` succeeds.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class WebhookConnector:
    """POST intents to a URL; POST a cancellation to reverse.

    The endpoint answers JSON; an ``id`` field becomes the journaled handle
    (otherwise the idempotency key is used so the handle is never empty).
    """

    url: str
    token: str | None = None
    timeout_seconds: float = 10.0
    name: str = "webhook"

    def _post(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        headers = {"content-type": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8") or "{}"
        parsed = json.loads(body)
        return parsed if isinstance(parsed, Mapping) else {}

    def apply(self, intent: Mapping[str, Any]) -> str:
        """POST the intent; return the remote handle for the audit trail."""
        response = self._post({"type": "apply", **dict(intent)})
        remote_id = response.get("id")
        if remote_id:
            return f"webhook:{remote_id}"
        return f"webhook:{intent.get('idempotency_key', 'unknown')}"

    def reverse(self, handle: str) -> None:
        """POST the cancellation for a previously applied handle."""
        self._post({"type": "reverse", "handle": handle})
