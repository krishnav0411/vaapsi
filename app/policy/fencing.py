"""Dispatch fencing — look-before-leap and verify-after-write for outreach.

Why fencing: Vaapsi's picture of a subscription is built from webhook
events that arrive late, out of order, or not at all. Between "we decided
to act" and "our action hit the wire", Razorpay's own state can move — the
customer resumed, cancelled, or paid. Acting on the stale picture means
chasing a paying customer or dunning a cancelled one. The fences close
those windows:

- fingerprint_subscription(): a stable SHA-256 over the DECISION-RELEVANT
  fields only (status, auth_attempts, short_url presence, current period,
  remaining cycles). Pure: irrelevant payload churn (notes, ids,
  timestamps, customer metadata) can never change the hash, so the
  stale-inference guard only trips on what matters.
- guard_dispatch(): look-before-leap — fetch the subscription fresh right
  before outreach is considered; anything not "halted" blocks the cycle.
  The caller writes FENCE_BLOCKED and takes NO further action.
- verify_after_write(): verify-after-write — re-fetch after a payment
  link exists; if the world moved (no longer halted), best-effort cancel
  the link and ALWAYS append a COMPENSATION ledger row (success or
  failure of the cancel — the attempt is the evidence). Razorpay's API
  may not support link cancellation: every cancel path is wrapped, and a
  failure degrades to a logged no-op, never an exception.

Fencing degrades, never raises: any transport fault inside a fence is
caught, logged, and reported as data. A fence that can crash the pipeline
is worse than no fence — the ledger rows tell the auditor what was (and
was not) verified.

Stale-inference guard (two-transaction pattern): the orchestrator
snapshots the fingerprint BEFORE the LLM diagnosis call, lets inference
run with no lock, then re-computes from a fresh fetch; a changed
fingerprint appends DISCARDED_STALE and leaves the episode for the next
cycle. The provider is the lock.
"""

import hashlib
import logging
import sqlite3
from typing import Any

from app.audit import ledger
from app.core.episodes import DEFAULT_MODE

logger = logging.getLogger(__name__)

# The only subscription status from which recovery outreach may be built.
HALTED_STATUS = "halted"

# Ledger vocabulary for the fence rows (outcomes are queryable evidence).
FENCE_BLOCKED_OUTCOME = "FENCE_BLOCKED"
DISCARDED_STALE_OUTCOME = "DISCARDED_STALE"
COMPENSATION_OUTCOME = "COMPENSATION"


def fingerprint_subscription(payload: dict[str, Any] | None) -> str:
    """Stable SHA-256 over the canonical decision-relevant subscription fields.

    Pure function: same relevant state → same hash, always. The relevant
    set is exactly what recovery decisions depend on — status,
    auth_attempts, whether a short_url exists (presence, not value),
    current period fields, and remaining_cycles. Everything else (ids,
    notes, timestamps, customer data) is deliberately excluded so
    irrelevant provider-side churn cannot spuriously invalidate an
    in-flight decision. Missing fields hash as nulls, so a payload that
    drops a key counts as a change — absence is state too.
    """
    relevant: dict[str, Any] = {
        "status": (payload or {}).get("status"),
        "auth_attempts": (payload or {}).get("auth_attempts"),
        "has_short_url": bool((payload or {}).get("short_url")),
        "current_period": (payload or {}).get("current_period"),
        "current_period_start": (payload or {}).get("current_period_start"),
        "current_period_end": (payload or {}).get("current_period_end"),
        "remaining_cycles": (payload or {}).get("remaining_cycles"),
    }
    material = ledger.canonical_json(relevant).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _fetch_fresh(client: Any, subscription_id: str) -> dict[str, Any]:
    """One provider fetch, never raising — failures come back as data."""
    try:
        fresh = client.fetch_subscription(subscription_id)
    except Exception as exc:  # noqa: BLE001 — fencing degrades, never raises
        logger.warning(
            "fence fetch failed for %s: %s: %s",
            subscription_id,
            type(exc).__name__,
            exc,
        )
        return {"ok": False, "subscription": None, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(fresh, dict):
        return {"ok": False, "subscription": None, "error": "non-dict subscription payload"}
    return {"ok": True, "subscription": fresh, "error": None}


def guard_dispatch(client: Any, episode: dict[str, Any]) -> dict[str, Any]:
    """Look-before-leap: is this subscription still halted, right now?

    Fetches the subscription fresh through `client` (the same
    fetch_subscription surface the real RazorpayClient exposes; tests
    inject a fake). Any status other than "halted" blocks the cycle: the
    caller writes FENCE_BLOCKED (reason + fresh status) and takes NO
    further action this cycle. A fetch failure blocks too — fail-closed:
    an unverifiable world is not one to send outreach into. Never raises.
    """
    subscription_id = episode["subscription_id"]
    fetched = _fetch_fresh(client, subscription_id)
    if not fetched["ok"]:
        return {
            "blocked": True,
            "reason": "fence_fetch_failed",
            "fresh_status": None,
            "subscription": None,
            "error": fetched["error"],
        }
    fresh = fetched["subscription"]
    status = fresh.get("status")
    if status != HALTED_STATUS:
        return {
            "blocked": True,
            "reason": "subscription_not_halted",
            "fresh_status": status,
            "subscription": fresh,
            "error": None,
        }
    return {
        "blocked": False,
        "reason": "halted_confirmed",
        "fresh_status": status,
        "subscription": fresh,
        "error": None,
    }


def _ledger_safe(value: Any) -> Any:
    """Coerce a provider response into ledger-safe JSON material.

    A cancel response that is not JSON-serializable must never blow up the
    audit write (fencing never raises): it degrades to a string rendering,
    which is still evidence.
    """
    try:
        ledger.canonical_json(value)
    except (TypeError, ValueError):
        return f"{type(value).__name__}: {value}"
    return value


def verify_after_write(
    conn: sqlite3.Connection,
    client: Any,
    subscription_id: str,
    link_id: str,
) -> dict[str, Any]:
    """Verify-after-write: is the link we just created still appropriate?

    Re-fetches the subscription after a payment link was created. If the
    state moved such that the outreach is now wrong (anything but
    "halted" — resumed, cancelled, charged since dispatch), best-effort
    cancel the link: the cancel call is wrapped because the provider API
    may not support cancellation and the client may not expose it at all —
    on failure, log and continue. A COMPENSATION ledger row is ALWAYS
    appended whenever compensation is triggered (cancel succeeded or
    failed), so the chain narrates the correction either way. Still
    halted → nothing wrong, no cancel, no row. A failed verification
    fetch degrades to a logged no-op (the link may yet be fine; the next
    cycle re-checks). Never raises.
    """
    fetched = _fetch_fresh(client, subscription_id)
    if not fetched["ok"]:
        return {
            "compensated": False,
            "verified": False,
            "cancelled": False,
            "reason": "verify_fetch_failed",
            "fresh_status": None,
            "error": fetched["error"],
        }
    fresh = fetched["subscription"]
    status = fresh.get("status")
    if status == HALTED_STATUS:
        return {
            "compensated": False,
            "verified": True,
            "cancelled": False,
            "reason": "still_halted",
            "fresh_status": status,
            "error": None,
        }

    cancel_error: str | None = None
    cancel_response: Any = None
    cancelled = False
    try:
        cancel = getattr(client, "cancel_payment_link", None)
        if cancel is None:
            raise NotImplementedError("client exposes no cancel_payment_link")
        cancel_response = cancel(link_id)
        cancelled = True
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        cancel_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "compensation cancel failed for link %s (subscription %s): %s",
            link_id,
            subscription_id,
            cancel_error,
        )

    try:
        ledger.append(
            conn,
            subscription_id=subscription_id,
            trigger_event="fence.verify_after_write",
            policy_eval={
                "decision": "compensation",
                "link_id": link_id,
                "fresh_status": status,
                "cancelled": cancelled,
                "cancel_error": cancel_error,
            },
            rzp_call=_ledger_safe(cancel_response),
            human_gate=False,
            outcome=COMPENSATION_OUTCOME,
            mode=DEFAULT_MODE,
        )
    except Exception as exc:  # noqa: BLE001 — fencing degrades, never raises
        logger.warning(
            "COMPENSATION ledger write failed for link %s (subscription %s): %s: %s",
            link_id,
            subscription_id,
            type(exc).__name__,
            exc,
        )
    return {
        "compensated": True,
        "verified": True,
        "cancelled": cancelled,
        "reason": "outreach_now_wrong",
        "fresh_status": status,
        "error": cancel_error,
    }


def fresh_fingerprint(client: Any, subscription_id: str) -> dict[str, Any]:
    """Fetch + fingerprint in one never-raising step (stale-guard seam).

    Returns {'fingerprint': sha-or-None, 'subscription': payload-or-None,
    'error': str-or-None}. fingerprint None means the world could not be
    read — the caller degrades (the verify_after_write fence still covers
    the dispatch) rather than discarding on missing data.
    """
    fetched = _fetch_fresh(client, subscription_id)
    if not fetched["ok"]:
        return {"fingerprint": None, "subscription": None, "error": fetched["error"]}
    return {
        "fingerprint": fingerprint_subscription(fetched["subscription"]),
        "subscription": fetched["subscription"],
        "error": None,
    }
