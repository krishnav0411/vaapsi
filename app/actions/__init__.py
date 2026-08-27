"""D2 action layer — policy-approved outreach dispatch.

`execute_episode_action` is the single door every outbound action passes:
it runs the policy engine FIRST, and only a SEND verdict reaches an
injected ActionClient. The client seam (Protocol in `base.py`, payment-link
implementation in `recovery_link.py`) keeps this package HTTP-free at
import and stub-friendly in tests/demo — no network call can originate
from here unless a real RazorpayClient is explicitly injected in prod.
Blocked decisions write nothing; dispatched ones land their evidence
(policy evaluation + exact Razorpay payload) in the hash-chained ledger
through the episode transition, atomically.
"""

from app.actions.base import ActionClient
from app.actions.execute import execute_episode_action
from app.actions.recovery_link import RecoveryLinkActionClient

__all__ = ["ActionClient", "RecoveryLinkActionClient", "execute_episode_action"]
