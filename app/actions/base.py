"""ActionClient protocol — the seam between policy approval and Razorpay I/O.

Why a Protocol (structural typing, not inheritance): the executor must depend
on the *interface*, never the transport. Production injects the real
Razorpay-backed client; tests and the demo inject an offline stub; future
transports plug in without touching the policy/execute path.
No HTTP at import time anywhere in this package — clients are constructed
only when explicitly instantiated by the caller.
"""

import sqlite3
from typing import Any, Protocol

from app.policy.engine import PolicyDecision


class ActionClient(Protocol):
    """One outbound action per policy-approved episode, wrapped for the ledger."""

    def create_recovery_link(
        self,
        conn: sqlite3.Connection,
        episode: dict[str, Any],
        policy_decision: PolicyDecision,
    ) -> dict[str, Any]:
        """Create the recovery payment link for a SCORED, policy-approved episode.

        Returns {'action_id', 'channel': 'payment_link', 'rzp_payload',
        'rzp_response'} — the exact payload sent and the exact response
        received, so the caller's ledger row can carry byte-true evidence.
        """
        ...
