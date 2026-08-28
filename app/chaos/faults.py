"""D4 Drill 2 fault injection — a Razorpay 5xx storm at the ActionClient seam.

Why wrap the Protocol and not monkeypatch httpx: the executor already
depends only on app.actions.base.ActionClient, so a flaky transport is
indistinguishable from a real Razorpay outage at exactly the boundary
where recovery decisions happen — no test double leaks into app/actions,
and the retry/DLQ logic is exercised against the same exception shapes a
live httpx.Client produces (real httpx.HTTPStatusError over a synthetic
500/503 response, never a bespoke fake error class). Configurable to
fail the first N calls, then delegate — "fails N times, succeeds on the
N+1th" is the whole drill.
"""

import sqlite3
from collections.abc import Mapping
from typing import Any

import httpx

from app.actions.base import ActionClient
from app.policy.engine import PolicyDecision

RAZORPAY_PAYMENT_LINKS_URL = "https://api.razorpay.com/v1/payment_links"

# The outage alternates these — the two codes the drill exercises.
DEFAULT_FAULT_STATUSES: tuple[int, ...] = (500, 503)


def http_5xx_error(status_code: int) -> httpx.HTTPStatusError:
    """A real httpx.HTTPStatusError carrying a synthetic 5xx response.

    Built from httpx's own Request/Response objects so the raised error is
    byte-for-byte the type (message shape, .request/.response attributes)
    a live RazorpayClient call would surface mid-outage — the resilient
    executor cannot special-case the drill even if it wanted to.
    """
    request = httpx.Request("POST", RAZORPAY_PAYMENT_LINKS_URL)
    response = httpx.Response(
        status_code,
        request=request,
        json={"error": {"code": "SERVER_ERROR", "description": "internal outage"}},
    )
    return httpx.HTTPStatusError(
        f"Server error '{status_code}' for url '{RAZORPAY_PAYMENT_LINKS_URL}'",
        request=request,
        response=response,
    )


class FaultyActionClient:
    """ActionClient wrapper: 5xx-fails the first `fail_first` calls, then works.

    Wraps ANY ActionClient (the real Razorpay-backed one in prod-shaped
    drills, the RecordingStub offline). Each failed call raises a genuine
    httpx.HTTPStatusError with status cycling through `status_codes`
    (500/503 by default). `calls` counts every create_recovery_link
    attempt so drills/tests can assert exactly how hard the wire was hit.
    """

    def __init__(
        self,
        inner: ActionClient,
        *,
        fail_first: int = 2,
        status_codes: tuple[int, ...] = DEFAULT_FAULT_STATUSES,
    ) -> None:
        self._inner = inner
        self._fail_first = fail_first
        self._status_codes = status_codes
        self.calls = 0

    def create_recovery_link(
        self,
        conn: sqlite3.Connection,
        episode: Mapping[str, Any],
        policy_decision: PolicyDecision,
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self._fail_first:
            status = self._status_codes[(self.calls - 1) % len(self._status_codes)]
            raise http_5xx_error(status)
        return self._inner.create_recovery_link(conn, dict(episode), policy_decision)
