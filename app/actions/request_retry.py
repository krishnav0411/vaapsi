"""REQUEST_RETRY action — stand back when the platform's own dunning is live.

Why this action: Razorpay retries halted-subscription auth/charge attempts
on its own schedule (auth_attempts climbing toward the plan's configured
maximum, an explicit next-retry timestamp when one is pending). While the
platform WILL retry on its own, a customer outreach from Vaapsi is noise
at best and double-dunning at worst. maybe_request_retry() re-checks that
state fresh from the provider (never from cached webhook evidence) and:

- platform will retry  → no customer outreach: one ACTION_REQUEST_RETRY
  ledger row carrying revisit_at = now + COOLING_HOURS (the same cooling
  constant the policy engine uses), episode untouched (state/attempt_count
  unchanged — the row is advisory, the next cycle re-evaluates), and the
  caller stops this cycle without dispatching.
- platform will NOT retry (auth_attempts at/above the maximum, no pending
  next-retry field) → {'handled': False}: the caller falls through to the
  existing payment-link path unchanged.

Contract with the gates: the caller (orchestrator) only consults this
AFTER the policy engine returned SEND — every existing gate (cooling,
48h interval, attempt cap, quiet hours, cohort) and the human-gate
routing (strict > threshold, tier 3) must already have passed, exactly as
for any other action, so MAX_ATTEMPTS_PER_EPISODE still bounds the
episode. An unreadable provider state degrades to handled=False (fall
through to recovery) with a logged warning — a fence that can wedge the
pipeline is worse than no fence.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.audit import ledger
from app.core.episodes import DEFAULT_MODE
from app.policy.engine import COOLING_HOURS

logger = logging.getLogger(__name__)

# Razorpay's configured maximum auth attempts on a halted subscription
# before the platform gives up and hands the recovery to us. Frozen here
# (code, with review) rather than env: a drifting cap is a silent one.
PLATFORM_MAX_AUTH_ATTEMPTS = 4

# Ledger vocabulary for the stand-back row.
REQUEST_RETRY_OUTCOME = "ACTION_REQUEST_RETRY"


def _now_utc() -> datetime:
    """Clock hook — injectable so tests freeze time (house pattern)."""
    return datetime.now(tz=timezone.utc)


def maybe_request_retry(
    conn: sqlite3.Connection,
    episode: dict[str, Any],
    client: Any,
    *,
    mode: str = DEFAULT_MODE,
) -> dict[str, Any]:
    """Re-check platform retry state; stand back or signal fall-through.

    Returns {'handled': bool, 'reason': str, 'revisit_at': str|None}.
    handled=True → the platform will retry on its own: the ACTION_REQUEST_RETRY
    ledger row is already written and the caller must NOT dispatch. handled=False
    → the caller falls through to the existing payment-link path.
    """
    subscription_id = episode["subscription_id"]
    try:
        fresh = client.fetch_subscription(subscription_id)
        assert isinstance(fresh, dict)
    except Exception as exc:  # noqa: BLE001 — degrade to fall-through
        logger.warning(
            "retry-state fetch failed for %s: %s: %s",
            subscription_id,
            type(exc).__name__,
            exc,
        )
        return {
            "handled": False,
            "reason": "retry_state_unavailable",
            "revisit_at": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    status = fresh.get("status")
    auth_attempts = fresh.get("auth_attempts")
    next_retry_at = fresh.get("next_retry_at")
    details = {
        "fresh_status": status,
        "auth_attempts": auth_attempts,
        "platform_max_auth_attempts": PLATFORM_MAX_AUTH_ATTEMPTS,
        "next_retry_at": next_retry_at,
    }

    platform_retries = (
        status == "halted"
        and isinstance(auth_attempts, int)
        and auth_attempts < PLATFORM_MAX_AUTH_ATTEMPTS
    ) or bool(next_retry_at)

    if not platform_retries:
        return {
            "handled": False,
            "reason": "platform_will_not_retry",
            "revisit_at": None,
            "details": details,
        }

    revisit_at = (_now_utc() + timedelta(hours=COOLING_HOURS)).isoformat()
    ledger.append(
        conn,
        subscription_id=subscription_id,
        trigger_event="action.request_retry",
        policy_eval={
            "decision": "request_retry",
            "episode_id": episode["id"],
            "revisit_at": revisit_at,
            "cooling_hours": COOLING_HOURS,
            **details,
        },
        human_gate=False,
        outcome=REQUEST_RETRY_OUTCOME,
        mode=mode,
    )
    return {
        "handled": True,
        "reason": "platform_retry_active",
        "revisit_at": revisit_at,
        "details": details,
    }
