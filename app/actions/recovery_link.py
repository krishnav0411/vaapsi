"""Recovery payment-link action — the concrete ActionClient for D2.

Builds the Razorpay payment-link payload from the episode row and dispatches
it through a client injected at construction. Dependency injection keeps this
module HTTP-free at import and offline by default: client=None selects the
RecordingStub (deterministic, no network), so tests and the demo never touch
the network and prod opts in by passing a real RazorpayClient.

The plan price is fixed at 49900 paise (the ₹499 recovery plan) — integer
paise, never float; clamping to invoice outstanding lands with the D3
scorer. `reference_id` embeds episode id and the attempt number this send
represents (attempt_count + 1 — the transition to SENT stamps it), making
each Razorpay-side link traceable back to one bounded recovery attempt.
"""

import sqlite3
import uuid
from typing import Any

from app.policy.engine import PolicyDecision
from app.razorpay import RazorpayClient

RECOVERY_PLAN_PAISE = 49900  # fixed ₹499 plan price, integer paise


def build_recovery_link_payload(episode: dict[str, Any]) -> dict[str, Any]:
    """The exact payment-link payload this client dispatches for an episode.

    Module-level (not a private method) so the D4 resilient executor can
    record the byte-true payload it was trying to send into the DLQ when
    every attempt fails — the queued row must be re-dispatchable as-is by
    drain_dlq, not a reconstruction from memory.
    """
    return {
        "amount": RECOVERY_PLAN_PAISE,
        "currency": "INR",
        # Razorpay caps reference_id at 40 chars: full UUID episode ids are 36,
        # so the id segment is truncated to fit "vaapsi:{ep}:{attempt}" — the
        # untruncated id still travels in notes.vaapsi_episode_id (traceability
        # never depends on the reference string).
        "reference_id": f"vaapsi:{episode['id'][:24]}:{episode['attempt_count'] + 1}",
        "description": f"Vaapsi recovery — subscription {episode['subscription_id']}",
        "notes": {"vaapsi_episode_id": episode["id"]},
    }


class RecordingStub:
    """Offline stand-in for RazorpayClient.create_payment_link.

    Returns a deterministic-shape stub response so the full execute path
    (policy → action → ledger) runs with zero network and a recognizable
    response — tests and the demo assert on {'stub': True, 'link_id': ...}.
    """

    def create_payment_link(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"stub": True, "link_id": f"link_stub_{uuid.uuid4().hex}"}


class RecoveryLinkActionClient:
    """Payment-link ActionClient satisfying app.actions.base.ActionClient.

    `client` is the injected transport: a real RazorpayClient in prod, or
    None for the RecordingStub (tests/demo — no HTTP, ever).
    """

    def __init__(self, client: RazorpayClient | None = None) -> None:
        self._client: RazorpayClient | RecordingStub = (
            client if client is not None else RecordingStub()
        )

    def create_recovery_link(
        self,
        conn: sqlite3.Connection,
        episode: dict[str, Any],
        policy_decision: PolicyDecision,
    ) -> dict[str, Any]:
        """Build the payment-link payload from the episode row and send it.

        `        conn` and `policy_decision` are part of the ActionClient contract —
        conn for the ledger idempotency guard (retries never double-send,
        wired with the verify stage), policy_decision for the clamped amount
        once D3 scoring lands. Today's fixed-price payload needs neither.
        """
        payload = build_recovery_link_payload(episode)
        response = self._client.create_payment_link(payload)
        return {
            "action_id": f"act_{uuid.uuid4().hex}",
            "channel": "payment_link",
            "rzp_payload": payload,
            "rzp_response": response,
        }
