"""paid→VERIFIED consumer — closes the recovery loop's last live gap (D6).

When a customer PAYS a recovery link, Razorpay fires `payment_link.paid`
(also `invoice.paid`) — and until now nothing transitioned the SENT episode
to VERIFIED, so a fully recovered subscription still read as "outreach
pending" on the dashboard and in the ledger. This consumer runs inside
app.ingest.receiver.process_webhook right after the event row is stored,
mirroring app.ingest.halt_consumer: it acts on the caller's connection, so
the VERIFIED transition and its ledger row commit together with the event
row (or roll back with it via the receiver's savepoint).

Resolution decision (deliberate, from app.core.episodes' own
documentation): the state machine's success path is SENT → VERIFIED →
CLOSED, and VERIFIED is deliberately excluded from OPEN_STATES because
"payment was already verified, so a later charged event is the success
path (→ CLOSED), never a reason to void the recovery". A recovery link IS
a payment_link — the exact artifact this pipeline dispatched
(app.actions.recovery_link stamps notes.vaapsi_episode_id and
reference_id vaapsi:{ep}:{attempt}) — so a payment_link.paid /
invoice.paid that resolves to an open SENT episode is the recovery being
PAID and must transition it to VERIFIED, never void it. The VOID
"charged" path (subscription.charged via void_open_episodes — whose
docstring also accepts payment_link.paid / invoice.paid passing their own
trigger_event for payments through the *normal* cycle) is already wired
upstream; this consumer never voids.

Matching order (first hit wins):
1. our own notes round-trip: entity notes.vaapsi_episode_id (preferred —
   the untruncated episode id travels there exactly);
2. reference_id `vaapsi:{ep24}:{attempt}` resolved by prefix match on
   episodes.id (Razorpay caps reference_id at 40 chars, so the id
   segment is the 24-char prefix of the full id);
3. the webhook row's subscription_id column → that sub's open SENT
   episode.
No match → stdout note + None; ingest stays accepted either way.

Idempotency: an episode already VERIFIED/CLOSED is a no-op (no second
ledger row, no second transition), so a paid event re-delivered past the
5-minute idempotency window cannot double-credit the recovery. Amounts
are integer paise taken from the event payload when present (never
recomputed, never float).
"""

import json
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from app.core.episodes import (
    EpisodeNotFoundError,
    TransitionError,
    get_episode,
    get_open_episodes,
    transition,
)

VERIFY_EVENTS: tuple[str, ...] = ("payment_link.paid", "invoice.paid")
_NOTE_KEY = "vaapsi_episode_id"
# vaapsi:{ep24}:{attempt} — the reference_id app.actions.recovery_link embeds.
_REFERENCE_PATTERN = re.compile(r"^vaapsi:(?P<ep_prefix>.+):(?P<attempt>\d+)$")


def maybe_verify_episode(
    conn: sqlite3.Connection,
    event_row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any] | None:
    """Transition a SENT episode to VERIFIED for a stored paid-event row.

    Returns the verified episode dict, or None when nothing was verified:
    a non-paid event, no resolvable episode, or an episode not sitting in
    SENT (already VERIFIED/CLOSED → idempotent no-op; any other state →
    no-op note). The receiver wraps this call in a savepoint (see
    receiver._run_verify_consumer), so even an unexpected exception can
    neither fail the webhook ingest nor leave a half-written transition.
    """
    event = event_row["event"]
    if event not in VERIFY_EVENTS:
        return None

    entities = _entities(event_row)
    sub_id = str(event_row["subscription_id"] or "")

    episode = _resolve_by_notes(conn, entities)
    if episode is None:
        episode = _resolve_by_reference(conn, entities)
    if episode is None:
        episode = _resolve_by_subscription(conn, sub_id)
    if episode is None:
        print(f"verify-consumer: {event} sub={sub_id or '?'} matched no episode, no action")
        return None

    if episode["state"] != "SENT":
        if episode["state"] in ("VERIFIED", "CLOSED"):
            print(
                f"verify-consumer: episode={episode['id']} already {episode['state']}, "
                "idempotent no-op"
            )
        else:
            print(
                f"verify-consumer: episode={episode['id']} in state={episode['state']}, "
                f"no action for {event}"
            )
        return None

    ledger_fields: dict[str, Any] = {"trigger_event": event}
    amount = _amount_paise(entities)
    if amount is not None:
        ledger_fields["recovered_paise"] = amount
    try:
        verified = transition(conn, episode["id"], "VERIFIED", ledger_fields)
    except (TransitionError, EpisodeNotFoundError, sqlite3.Error) as exc:
        # Lost a race against a concurrent consumer (or a vanished row)
        # between the read above and the write — treat as already-handled,
        # never fail the ingest.
        print(f"verify-consumer: episode={episode['id']} verify skipped: {exc!r}")
        return None
    print(
        f"verify-consumer: {event} -> episode={verified['id']} VERIFIED"
        + (f" recovered={amount} paise" if amount is not None else "")
    )
    return verified


def _entities(event_row: sqlite3.Row | Mapping[str, Any]) -> list[dict[str, Any]]:
    """The payload's entity dicts, recovery-relevant families first.

    payment_link before payment so the link's own amount/reference win for
    payment_link.paid; invoice covers invoice.paid. Malformed payloads
    yield [] — matching then falls through to the subscription column.
    """
    try:
        payload = json.loads(event_row["payload_json"])
    except (TypeError, ValueError):
        return []
    nested = payload.get("payload") if isinstance(payload, dict) else None
    if not isinstance(nested, dict):
        return []
    entities: list[dict[str, Any]] = []
    for family in ("payment_link", "payment", "invoice"):
        holder = nested.get(family)
        if not isinstance(holder, dict):
            continue
        entity = holder.get("entity")
        if isinstance(entity, dict):
            entities.append(entity)
    return entities


def _resolve_by_notes(
    conn: sqlite3.Connection,
    entities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Our own notes round-trip: notes.vaapsi_episode_id carries the FULL id."""
    for entity in entities:
        notes = entity.get("notes")
        if not isinstance(notes, dict):
            continue
        episode_id = notes.get(_NOTE_KEY)
        if not episode_id:
            continue
        try:
            return get_episode(conn, str(episode_id))
        except EpisodeNotFoundError:
            continue
    return None


def _resolve_by_reference(
    conn: sqlite3.Connection,
    entities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """reference_id vaapsi:{ep24}:{attempt} → prefix match on episodes.id.

    substr() (not LIKE) so the underscore inside 'ep_' is matched
    literally; among the (near-impossible) prefix collisions the SENT
    episode wins, since SENT is the only state this consumer can verify.
    """
    for entity in entities:
        match = _REFERENCE_PATTERN.match(str(entity.get("reference_id") or ""))
        if match is None:
            continue
        prefix = match.group("ep_prefix")
        row = conn.execute(
            "SELECT id FROM episodes WHERE substr(id, 1, ?) = ? "
            "ORDER BY (state = 'SENT') DESC, created_ts_utc ASC LIMIT 1",
            (len(prefix), prefix),
        ).fetchone()
        if row is None:
            continue
        try:
            return get_episode(conn, row["id"])
        except EpisodeNotFoundError:  # pragma: no cover - row just matched
            continue
    return None


def _resolve_by_subscription(
    conn: sqlite3.Connection,
    subscription_id: str,
) -> dict[str, Any] | None:
    """Last resort: the webhook row's subscription_id → open SENT episode."""
    if not subscription_id:
        return None
    for episode in get_open_episodes(conn, subscription_id):
        if episode["state"] == "SENT":
            return episode
    return None


def _amount_paise(entities: list[dict[str, Any]]) -> int | None:
    """The paid amount in integer paise from the event payload, when present."""
    for entity in entities:
        amount = entity.get("amount")
        if isinstance(amount, int) and not isinstance(amount, bool):
            return amount
    return None
