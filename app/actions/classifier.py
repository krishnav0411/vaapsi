"""Failure classifier — one category per payment-failure signal.

Why classify: the raw last-error string from a payment.failed webhook is
provider vocabulary ("INSUFFICIENT_FUNDS", "card_declined", an error
payload dict); recovery decisions need a small closed set of categories
so urgency modulation (app.scoring) and any future per-category handling
stay deterministic and auditable. classify_failure() is pure: str, dict
(a Razorpay error payload — the code is looked up in the places Razorpay
puts it), or None in; exactly one category out of the frozen set below.

Mapping (exact matches after normalization — lower-case, spaces/hyphens
folded to underscores — then substring fallback for compound codes):
  insufficient_funds                          → TRANSIENT_RETRYABLE
  authentication_required / otp_expired /
  authentication_opted_out                    → AUTH_REQUIRED
  card_declined / gateway_error               → HARD_DECLINE
  mandate_revoked / mandate_invalid           → MANDATE_REVOKED
  timeout / connection / network              → NETWORK
  anything else (incl. None / empty)          → UNKNOWN

UNKNOWN is the safe default: an unrecognized signal must never silently
 masquerade as a friendlier category — callers treat it exactly as they
treated unclassified errors before this module existed.

PROMPT-INJECTION NOTE: the error string/payload is customer-adjacent data
and is treated strictly as data — matched against the frozen map below,
never interpreted as instructions.
"""

from typing import Any

TRANSIENT_RETRYABLE = "TRANSIENT_RETRYABLE"
AUTH_REQUIRED = "AUTH_REQUIRED"
HARD_DECLINE = "HARD_DECLINE"
MANDATE_REVOKED = "MANDATE_REVOKED"
NETWORK = "NETWORK"
UNKNOWN = "UNKNOWN"

# The closed category set — classify_failure returns exactly one of these.
FAILURE_CATEGORIES: frozenset[str] = frozenset(
    {TRANSIENT_RETRYABLE, AUTH_REQUIRED, HARD_DECLINE, MANDATE_REVOKED, NETWORK, UNKNOWN}
)

# Exact-match map over normalized codes. Razorpay emits upper-case snake
# codes on payment.failed (e.g. GATEWAY_ERROR) and the same vocabulary in
# error payloads; normalization makes both meet here.
_EXACT: dict[str, str] = {
    "insufficient_funds": TRANSIENT_RETRYABLE,
    "authentication_required": AUTH_REQUIRED,
    "otp_expired": AUTH_REQUIRED,
    "authentication_opted_out": AUTH_REQUIRED,
    "card_declined": HARD_DECLINE,
    "gateway_error": HARD_DECLINE,
    "mandate_revoked": MANDATE_REVOKED,
    "mandate_invalid": MANDATE_REVOKED,
    "timeout": NETWORK,
    "connection": NETWORK,
    "network": NETWORK,
}

# Substring fallback for compound codes (e.g. "GATEWAY_ERROR_TIMEOUT"):
# FIRST match wins, so the more specific/consequential needles come first.
_SUBSTRING: tuple[tuple[str, str], ...] = (
    ("mandate_revoked", MANDATE_REVOKED),
    ("mandate_invalid", MANDATE_REVOKED),
    ("authentication_required", AUTH_REQUIRED),
    ("authentication_opted_out", AUTH_REQUIRED),
    ("otp_expired", AUTH_REQUIRED),
    ("insufficient_funds", TRANSIENT_RETRYABLE),
    ("card_declined", HARD_DECLINE),
    ("gateway_error", HARD_DECLINE),
    ("timeout", NETWORK),
    ("connection", NETWORK),
    ("network", NETWORK),
)


def _extract_code(payload: dict[str, Any]) -> Any:
    """The error code/string inside a Razorpay error payload, or None.

    Payload shapes vary by event family/version; the code is looked up in
    the places Razorpay puts it: a top-level `code`/`error_code`, an
    `error` object's `code`/`error_code`, and the human-readable
    `description`/`error_description` fields as a last resort.
    """
    for key in ("code", "error_code"):
        value = payload.get(key)
        if value:
            return value
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("code", "error_code", "description", "error_description"):
            value = error.get(key)
            if value:
                return value
    for key in ("description", "error_description"):
        value = payload.get(key)
        if value:
            return value
    return None


def _normalize(raw: Any) -> str:
    """Lower-case; spaces/hyphens folded to underscores; trimmed."""
    return str(raw).strip().lower().replace(" ", "_").replace("-", "_")


def classify_failure(last_error: str | dict[str, Any] | None) -> str:
    """Classify one failure signal into exactly one FAILURE_CATEGORIES member.

    `last_error` may be a raw code string, a Razorpay error payload dict
    (the code is extracted from its known shapes), or None (no failure on
    record → UNKNOWN). Never raises; unrecognized input is UNKNOWN.
    """
    if last_error is None:
        return UNKNOWN
    if isinstance(last_error, dict):
        last_error = _extract_code(last_error)
        if last_error is None:
            return UNKNOWN
    if not isinstance(last_error, (str, int, float)):
        return UNKNOWN
    token = _normalize(last_error)
    if not token:
        return UNKNOWN
    exact = _EXACT.get(token)
    if exact is not None:
        return exact
    for needle, category in _SUBSTRING:
        if needle in token:
            return category
    return UNKNOWN
