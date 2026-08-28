"""halt→episode consumer — the live pipeline's missing link (D6).

In the live D6 grind, `subscription.halted` deliveries landed in
webhook_events but nothing converted them into recovery episodes —
run_recovery_cycle kept returning no_open_episode and the pipeline idled
while real halts piled up. This consumer closes that gap: it runs inside
app.ingest.receiver.process_webhook right after the event row is stored,
so a halt that passes ingest immediately opens its bounded recovery cycle
on the caller's connection (episode + ledger rows commit together with
the event row).

Design constraints:
- Cohort gate: only TREATMENT subscriptions ever get episodes. A CONTROL
  halt is observed and deliberately ignored — that IS the experiment's
  counterfactual — with one stdout line so the operator can see the gate
  firing. An unknown cohort (no cohorts row) is ignored the same way.
- Idempotent under out-of-order / replayed deliveries: create_episode's
  partial unique index guarantees at most one open episode per
  subscription and returns an existing one untouched (no second row, no
  second ledger row). This module additionally treats IntegrityError as
  "already exists" and returns the open episode instead of raising.
- The consumer must NEVER fail the webhook ingest: the receiver wraps the
  call in a savepoint + try/except (see receiver._run_halt_consumer), so
  a broken consumer can neither fail the delivery nor leave a half-written
  episode behind.

Amounts: the recovery plan price is RECOVERY_PLAN_PAISE from
app.actions.recovery_link — the single source of what a recovery cycle
will ask the customer to pay; it is surfaced in the creation log line,
never recomputed here.
"""

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from app.actions.recovery_link import RECOVERY_PLAN_PAISE
from app.core.episodes import create_episode, get_open_episodes

HALT_EVENT = "subscription.halted"
COHORT_TREATMENT = "TREATMENT"


def maybe_create_episode(
    conn: sqlite3.Connection,
    event_row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any] | None:
    """Open the recovery episode for a stored `subscription.halted` row.

    Returns the created (or pre-existing open) episode dict, or None when
    the event must not create one: a non-halted event, an unknown cohort,
    or the CONTROL gate. The cohort lookup decides BEFORE any write, so a
    CONTROL halt leaves episodes and ledger byte-identical.

    The returned episode's recovery plan will bill RECOVERY_PLAN_PAISE —
    imported from app.actions.recovery_link so there is exactly one place
    that knows the price (integer paise, never float).
    """
    if event_row["event"] != HALT_EVENT:
        return None
    meta = _event_meta(event_row)
    subscription_id = meta["subscription_id"]
    if not subscription_id:
        return None

    found = conn.execute(
        "SELECT cohort FROM cohorts WHERE subscription_id = ?", (subscription_id,)
    ).fetchone()
    if found is None:
        print(f"halt-consumer: sub={subscription_id} has no cohort assignment, no action")
        return None
    if found["cohort"] != COHORT_TREATMENT:
        # The cohort gate, observable: CONTROL halts are counted, never acted on.
        print(f"halt-consumer: CONTROL sub={subscription_id} observed, no action")
        return None

    try:
        episode = create_episode(
            conn,
            subscription_id,
            meta["halt_ts_utc"],
            cohort=COHORT_TREATMENT,
            trigger_event=HALT_EVENT,
        )
    except sqlite3.IntegrityError:
        # Lost a race against a concurrent halt delivery: the partial unique
        # index guarantees exactly one open episode — return theirs.
        existing = get_open_episodes(conn, subscription_id)
        if not existing:
            raise
        episode = existing[0]
    print(
        f"halt-consumer: TREATMENT sub={subscription_id} -> episode={episode['id']} "
        f"state={episode['state']} plan={RECOVERY_PLAN_PAISE} paise"
    )
    return episode


def _event_meta(event_row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    """Subscription meta for the halt: payload entity first, event row fallback.

    The payload's `payload.subscription.entity` is the wire truth; the
    webhook_events row carries the same identity (the receiver keyed the
    row on it), so a malformed/unparseable payload still yields a usable
    subscription id and timestamps.
    """
    try:
        payload = json.loads(event_row["payload_json"])
    except (TypeError, ValueError):
        payload = {}
    # The wire payload can be anything a signature-valid sender posts: treat
    # every nesting level as untrusted shape (a string entity, a list where
    # an object belongs) and fall back to the event row's own identity.
    container = payload.get("payload") if isinstance(payload, dict) else None
    subscription_branch = (
        container.get("subscription") if isinstance(container, dict) else None
    )
    entity = (
        subscription_branch.get("entity")
        if isinstance(subscription_branch, dict)
        else None
    )
    if not isinstance(entity, dict):
        entity = {}
    halt_ts = event_row["event_ts_utc"] or event_row["received_ts_utc"]
    return {
        "subscription_id": str(entity.get("id") or event_row["subscription_id"] or ""),
        "status": entity.get("status"),
        "halt_ts_utc": halt_ts,
    }
