"""Deterministic episode scorer — tier assignment from webhook evidence.

Why deterministic-first: recovery outreach touches real customers, so the
safety-relevant decision (which tier of outreach, whether a human must
review) must never depend on a model's mood. score_episode() is a pure
function of (episodes row, webhook_events for that subscription, cohorts
row): it reads, never writes — the caller owns the episode transition and
its ledger row. Same inputs always yield the same ScoreResult, so the
ledger's features/rationale are reproducible evidence, and an LLM failure
anywhere upstream still leaves a valid rules-only decision.

Features, all carried in the result dict so every input used is auditable:
- last_error_code: from the most recent payment.failed event
  (e.g. GATEWAY_ERROR, CARD_DECLINED); None when no failure is on record.
- consecutive_failures: payment.failed events after the most recent
  successful charge (subscription.charged / payment_link.paid /
  invoice.paid) — the charged event resets the count, because a customer
  who just paid is not mid-failure-streak.
- amount_paise: the ₹499 plan price — single source is
  app.actions.recovery_link.RECOVERY_PLAN_PAISE (the exact number the
  dispatch layer charges), re-exported here rather than duplicated.
- subscription_age_days: halt instant minus cohorts.created_utc when the
  cohort row exists, else minus the episode's own halt_ts (age 0). The
  halt instant — not the wall clock — is the reference, keeping the
  function pure and replay-stable.

Ordering under out-of-order delivery (proven live in D1): events are
ordered by their occurrence timestamp (event_ts_utc, falling back to
received_ts_utc, id as tie-break), never by arrival/insertion order.

Tier rules — evaluated in order, FIRST match wins:
  TIER 1  consecutive_failures <= 1 AND last_error_code is
          transient-looking (GATEWAY_ERROR / NETWORK_ERROR / TIMED_OUT)
  TIER 3  amount_paise > HUMAN_GATE_THRESHOLD_PAISE (constant imported
          from app.policy.engine — never duplicated) OR
          consecutive_failures >= 3
  TIER 2  everything else (standard recovery)

PROMPT-INJECTION NOTE: last_error_code is extracted from webhook payloads
(customer-controlled data paths) and used only for this classification;
it is never interpreted as instructions anywhere.

Failure-category integration (app.actions.classifier): the last error
code is also classified into the closed category set (TRANSIENT_RETRYABLE
/ AUTH_REQUIRED / HARD_DECLINE / MANDATE_REVOKED / NETWORK / UNKNOWN) and
modulates the result's URGENCY — TRANSIENT_RETRYABLE lowers it,
MANDATE_REVOKED / HARD_DECLINE raise it, AUTH_REQUIRED sits at the
medium baseline. Urgency is carried evidence, clamped to [1, 3]; the
tier itself — which routes the human gate, the fallback flavor, and the
dispatch — is deliberately unchanged, so UNKNOWN (and everything else)
behaves exactly as before this integration.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.actions.classifier import (
    AUTH_REQUIRED,
    HARD_DECLINE,
    MANDATE_REVOKED,
    TRANSIENT_RETRYABLE,
    UNKNOWN,
    classify_failure,
)
from app.actions.recovery_link import RECOVERY_PLAN_PAISE
from app.policy.engine import HUMAN_GATE_THRESHOLD_PAISE

# Single source for the plan price: the exact constant the D2 dispatch
# layer puts into Razorpay payloads. Re-exported so tests/monkeypatching
# and the ledger's features dict all see one number.
PLAN_PRICE_PAISE = RECOVERY_PLAN_PAISE

FAILURE_EVENT = "payment.failed"
SUCCESS_EVENTS: tuple[str, ...] = (
    "subscription.charged",
    "payment_link.paid",
    "invoice.paid",
)

# Transient-looking codes: a single one of these is a nudge, not a war.
TRANSIENT_ERROR_CODES = frozenset({"GATEWAY_ERROR", "NETWORK_ERROR", "TIMED_OUT"})

# Tier thresholds (escalation at 3 straight failures; amount gate lives in
# app.policy.engine and is imported, not copied).
GENTLE_MAX_FAILURES = 1
ESCALATE_MIN_FAILURES = 3

# Urgency modulation by failure category (see module docstring): the tier
# is the ROUTING decision and stays frozen; urgency is the classifier's
# modulation on top — clamped to the tier range so it can never widen what
# the pipeline may do. UNKNOWN (and the unmentioned NETWORK) carry zero
# modifier: exactly the pre-classifier behavior.
URGENCY_MODIFIERS: dict[str, float] = {
    TRANSIENT_RETRYABLE: -1.0,
    MANDATE_REVOKED: 1.0,
    HARD_DECLINE: 0.5,
    AUTH_REQUIRED: 0.0,
    UNKNOWN: 0.0,
}

_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class ScoreResult:
    """One scorer verdict: tier plus every feature that produced it.

    `failure_category` is the classifier's closed-set reading of
    last_error_code; `urgency` is the tier modulated by that category,
    clamped to [1, 3] — evidence for the ledger, never a gate input.
    """

    tier: int
    features: dict
    rationale: str
    failure_category: str
    urgency: float


def _parse_utc(ts: str | None) -> datetime:
    """Parse a stored UTC ISO timestamp; naive strings are read as UTC."""
    if ts is None:
        return _MIN_DT
    parsed = datetime.fromisoformat(ts)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _row_instant(row: sqlite3.Row) -> datetime:
    """Occurrence instant of an event row — event_ts first, received fallback.

    event_ts_utc is the Razorpay-side created_at (the true occurrence);
    received_ts_utc is only a fallback for rows that somehow lack it. The
    id tie-break keeps equal-timestamp events deterministically ordered by
    arrival, so out-of-order delivery can never flip a scoring result.
    """
    for candidate in (row["event_ts_utc"], row["received_ts_utc"]):
        if candidate:
            try:
                return _parse_utc(candidate)
            except ValueError:
                continue
    return _MIN_DT


def _payment_entity(payload: dict[str, Any]) -> dict[str, Any]:
    ent = payload.get("payload", {}).get("payment", {}).get("entity", {})
    return ent if isinstance(ent, dict) else {}


def _events_for_subscription(
    conn: sqlite3.Connection, subscription_id: str
) -> list[sqlite3.Row]:
    """Failure + success events belonging to this subscription.

    Why the payload check: the ingest receiver keys the subscription_id
    COLUMN on the entity id of whichever family the event carries — for
    payment.failed that is the payment id (pay_...), not the subscription.
    Standard Razorpay payment entities reference their subscription via
    entity.subscription_id, so both paths are matched here (live D1 data
    shows the column holding pay_ ids).
    """
    placeholders = ", ".join("?" for _ in range(1 + len(SUCCESS_EVENTS)))
    rows = conn.execute(
        "SELECT id, event, subscription_id, event_ts_utc, received_ts_utc, payload_json "
        f"FROM webhook_events WHERE event IN ({placeholders}) ORDER BY id ASC",
        (FAILURE_EVENT, *SUCCESS_EVENTS),
    ).fetchall()
    matched: list[sqlite3.Row] = []
    for row in rows:
        if row["subscription_id"] == subscription_id:
            matched.append(row)
            continue
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        if _payment_entity(payload).get("subscription_id") == subscription_id:
            matched.append(row)
    return matched


def _ordered(events: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Events in occurrence order (see _row_instant); id breaks ties."""
    return sorted(events, key=lambda r: (_row_instant(r), r["id"]))


def _last_error_code(events: list[sqlite3.Row]) -> str | None:
    """Error code of the most recent payment.failed, upper-cased.

    Payload shapes vary by event family/version, so the code is looked up
    in the two places Razorpay puts it: entity.error_code and a top-level
    error object. It is treated strictly as data (never instructions).
    """
    for row in reversed(_ordered(events)):
        if row["event"] != FAILURE_EVENT:
            continue
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return None
        code = _payment_entity(payload).get("error_code")
        if not code and isinstance(payload.get("error"), dict):
            code = payload["error"].get("code") or payload["error"].get("error_code")
        return str(code).strip().upper() if code else None
    return None


def _consecutive_failures(events: list[sqlite3.Row]) -> int:
    """payment.failed count since the most recent successful charge.

    Walks backwards in occurrence order, counting failures until the first
    success — exactly the streak a recovery agent faces. Unordered arrival
    is irrelevant: ordering comes from occurrence instants, not insertion.
    """
    count = 0
    for row in reversed(_ordered(events)):
        if row["event"] == FAILURE_EVENT:
            count += 1
        else:
            break
    return count


def _subscription_age_days(conn: sqlite3.Connection, episode: dict[str, Any]) -> float:
    """Subscription age in days at the halt instant (pure, clock-free).

    cohorts.created_utc when the cohort row exists (D1 substrate stamped
    it at creation); otherwise the episode's own halt_ts — i.e. age 0 —
    so scoring never needs the wall clock and stays replay-stable.
    """
    halt = _parse_utc(episode["halt_ts_utc"])
    row = conn.execute(
        "SELECT created_utc FROM cohorts WHERE subscription_id = ?",
        (episode["subscription_id"],),
    ).fetchone()
    created_ts: str | None = row["created_utc"] if row is not None else None
    if not created_ts:
        # No cohort record: the episode's halt is the best-known creation
        # instant, making the age 0 — never a wall-clock read, never a
        # sentinel that could inflate the feature.
        created_ts = episode["halt_ts_utc"]
    age_seconds = (halt - _parse_utc(created_ts)).total_seconds()
    return round(max(age_seconds, 0.0) / 86400.0, 2)


def score_episode(conn: sqlite3.Connection, episode: dict[str, Any]) -> ScoreResult:
    """Score one episode into a recovery tier — pure, no writes, no network.

    `episode` is an episodes-row dict (as returned by app.core.episodes).
    Reads the subscription's payment.failed / success events and the
    cohorts row, computes the feature dict (carried verbatim for the
    ledger), and applies the tier rules in documented order with FIRST
    match winning. Returns a ScoreResult whose rationale is one
    deterministic sentence naming the inputs that decided the tier.
    """
    events = _events_for_subscription(conn, episode["subscription_id"])
    features: dict[str, Any] = {
        "last_error_code": _last_error_code(events),
        "consecutive_failures": _consecutive_failures(events),
        "amount_paise": PLAN_PRICE_PAISE,
        "subscription_age_days": _subscription_age_days(conn, episode),
    }
    return _decide(features)


def _decide(features: dict[str, Any]) -> ScoreResult:
    """Apply the tier rules in order; FIRST match wins (see module docstring).

    The rationale states the exact condition that matched — one fixed
    sentence per tier branch, so two identical feature dicts always
    produce byte-identical rationales.
    """
    code_display = features["last_error_code"] or "UNKNOWN"
    amount = features["amount_paise"]
    consecutive_failures = features["consecutive_failures"]
    age_days = features["subscription_age_days"]
    category = classify_failure(features["last_error_code"])

    if consecutive_failures <= GENTLE_MAX_FAILURES and features["last_error_code"] in TRANSIENT_ERROR_CODES:
        return ScoreResult(
            tier=1,
            features=features,
            rationale=(
                f"TIER 1 gentle nudge: {consecutive_failures} consecutive failure(s) "
                f"with transient-looking last error {code_display}; amount {amount} paise; "
                f"subscription age {age_days} days. Failure category {category}."
            ),
            failure_category=category,
            urgency=_urgency(1, category),
        )

    if amount > HUMAN_GATE_THRESHOLD_PAISE:
        return ScoreResult(
            tier=3,
            features=features,
            rationale=(
                f"TIER 3 human review: amount {amount} paise exceeds the "
                f"{HUMAN_GATE_THRESHOLD_PAISE} paise human-gate threshold; "
                f"{consecutive_failures} consecutive failure(s), last error {code_display}. "
                f"Failure category {category}."
            ),
            failure_category=category,
            urgency=_urgency(3, category),
        )

    if consecutive_failures >= ESCALATE_MIN_FAILURES:
        return ScoreResult(
            tier=3,
            features=features,
            rationale=(
                f"TIER 3 human review: {consecutive_failures} consecutive failures "
                f"meets the escalation bar of {ESCALATE_MIN_FAILURES}; "
                f"last error {code_display}; amount {amount} paise. "
                f"Failure category {category}."
            ),
            failure_category=category,
            urgency=_urgency(3, category),
        )

    return ScoreResult(
        tier=2,
        features=features,
        rationale=(
            f"TIER 2 standard recovery: {consecutive_failures} consecutive failure(s), "
            f"last error {code_display}; amount {amount} paise; "
            f"subscription age {age_days} days. Failure category {category}."
        ),
        failure_category=category,
        urgency=_urgency(2, category),
    )


def _urgency(tier: int, category: str) -> float:
    """Tier modulated by the failure category, clamped to the tier range.

    Unknown categories carry zero modifier (defensive — classify_failure
    already only emits the frozen set), so urgency degrades to the tier.
    """
    modifier = URGENCY_MODIFIERS.get(category, 0.0)
    return round(max(1.0, min(3.0, tier + modifier)), 2)
