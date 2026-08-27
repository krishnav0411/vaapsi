"""Episode state machine — one bounded, auditable recovery cycle per halt.

Why episodes: recovery must be bounded (a single open cycle per halted
subscription, capped outreach) and every state change must be auditable.
An episode walks a strict linear path NEW → DIAGNOSED → SCORED → GATED →
SENT → VERIFIED → CLOSED (with one dispatch shortcut: policy-approved
SCORED outreach transitions straight to SENT — GATED is the human-gate
detour only); any open state can be VOIDED by a stop event
(`subscription.charged` — never chase a paying customer — or
`subscription.cancelled`/`completed`). VOIDED and CLOSED are terminal.

Every transition writes its hash-chained ledger row via app.audit.ledger on
the caller's connection, so the state change and its evidence commit or
roll back together — a state change without a ledger row is exactly the gap
an auditor hunts for, so creation and every transition each land with their
own ledger row. Validation happens before any write, so an illegal
transition leaves both tables untouched even before rollback.

Events arrive out of order (proven live in D1), so nothing here assumes
ordered delivery: creation is idempotent while an episode is open (DB-level
partial unique index), voiding is a no-op when nothing is open, and the
state machine itself never double-transitions. The TREATMENT/CONTROL
cohort gate is enforced by the event layer above: CONTROL events are
recorded upstream and never reach this module.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.audit import ledger

# The exact episode states. VOIDED is kept separate from CLOSED so "stopped
# by a charge/cancel event" stays queryable at a glance.
EPISODE_STATES: tuple[str, ...] = (
    "NEW",
    "DIAGNOSED",
    "SCORED",
    "GATED",
    "SENT",
    "VERIFIED",
    "CLOSED",
    "VOIDED",
)

# Open = outreach may still originate from the episode. VERIFIED is
# deliberately excluded: payment was already verified, so a later charged
# event is the success path (→ CLOSED), never a reason to void the recovery.
OPEN_STATES: frozenset[str] = frozenset({"NEW", "DIAGNOSED", "SCORED", "GATED", "SENT"})

# Terminal — no transitions out; void_open_episodes ignores them.
TERMINAL_STATES: frozenset[str] = frozenset({"CLOSED", "VOIDED"})

# Single source of truth for legality; transition() validates every state
# change against this map and raises on anything else. VOIDED is reachable
# from every open state because the stop event can land at any point of the
# cycle — the whole point of stop-on-charge. SCORED → SENT is the
# policy-approved dispatch shortcut (app.actions.execute): outreach that
# passes every rule leaves immediately; GATED is the human-gate detour
# (app.gates.human_gate): the human approves (→ SENT, outreach dispatches)
# or rejects (→ CLOSED, outcome 'human_rejected' — a deliberate end, not a
# stop event, so it does not reuse VOIDED whose reasons are charge/cancel).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "NEW": frozenset({"DIAGNOSED", "VOIDED"}),
    "DIAGNOSED": frozenset({"SCORED", "VOIDED"}),
    "SCORED": frozenset({"SENT", "GATED", "VOIDED"}),
    "GATED": frozenset({"SENT", "CLOSED", "VOIDED"}),
    "SENT": frozenset({"VERIFIED", "VOIDED"}),
    "VERIFIED": frozenset({"CLOSED"}),
    "CLOSED": frozenset(),
    "VOIDED": frozenset(),
}

# Void reason → the canonical stop event that produced it. payment_link.paid
# / invoice.paid void with reason "charged" but pass their own trigger_event.
VOID_REASONS: dict[str, str] = {
    "charged": "subscription.charged",
    "cancelled": "subscription.cancelled",
}

# Engine-mode taxonomy (PLAN.md, D5 dashboard): NORMAL | DEGRADED | KILLED.
# Ledger rows default to NORMAL; the kill switch (app.settings kill_switch,
# VAAPSI_KILL_SWITCH env) flips mode to KILLED via ledger_fields.
DEFAULT_MODE = "NORMAL"

_EPISODE_COLUMNS: tuple[str, ...] = (
    "id",
    "subscription_id",
    "cohort",
    "state",
    "halt_ts_utc",
    "attempt_count",
    "last_action_ts_utc",
    "void_reason",
    "created_ts_utc",
    "updated_ts_utc",
)

_SELECT_EPISODE = f"SELECT {', '.join(_EPISODE_COLUMNS)} FROM episodes"
_OPEN_PLACEHOLDERS = ", ".join("?" for _ in OPEN_STATES)


class TransitionError(ValueError):
    """A transition not present in ALLOWED_TRANSITIONS, or a bad void reason."""


class EpisodeNotFoundError(LookupError):
    """The episode id does not exist in the episodes table."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_episode(row: sqlite3.Row) -> dict[str, Any]:
    return {name: row[name] for name in _EPISODE_COLUMNS}


def get_episode(conn: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
    """Fetch one episode as a dict; raise EpisodeNotFoundError if absent."""
    row = conn.execute(f"{_SELECT_EPISODE} WHERE id = ?", (episode_id,)).fetchone()
    if row is None:
        raise EpisodeNotFoundError(f"no episode with id {episode_id!r}")
    return _to_episode(row)


def get_open_episodes(conn: sqlite3.Connection, subscription_id: str) -> list[dict[str, Any]]:
    """All non-terminal episodes for a subscription (0 or 1 by invariant)."""
    rows = conn.execute(
        f"{_SELECT_EPISODE} WHERE subscription_id = ? "
        f"AND state IN ({_OPEN_PLACEHOLDERS}) ORDER BY created_ts_utc ASC",
        (subscription_id, *OPEN_STATES),
    ).fetchall()
    return [_to_episode(r) for r in rows]


def create_episode(
    conn: sqlite3.Connection,
    subscription_id: str,
    halt_ts_utc: str,
    *,
    cohort: str | None = None,
    trigger_event: str | None = None,
) -> dict[str, Any]:
    """Open (or idempotently return) the recovery episode for a halt.

    Created from `subscription.halted` (Nth `subscription.pending` handling
    lands with the event layer in a later stage — pass its trigger_event
    then). Idempotent under out-of-order / replayed deliveries: an existing
    open episode is returned untouched — no second row, no ledger row. A
    halt after a *terminal* episode is a genuinely new cycle and gets a
    fresh episode (fresh caps). The creation itself appends one ledger row
    (outcome EPISODE_CREATED), so the chain tells the story from the halt
    onward. `cohort` defaults to a cohorts-table lookup; the create-only-
    for-TREATMENT gate is enforced upstream.
    """
    open_episode = get_open_episodes(conn, subscription_id)
    if open_episode:
        return open_episode[0]

    if cohort is None:
        found = conn.execute(
            "SELECT cohort FROM cohorts WHERE subscription_id = ?", (subscription_id,)
        ).fetchone()
        cohort = found["cohort"] if found is not None else None

    episode_id = f"ep_{uuid.uuid4().hex}"
    now = _utc_now_iso()
    try:
        conn.execute(
            "INSERT INTO episodes (id, subscription_id, cohort, state, halt_ts_utc, "
            "attempt_count, last_action_ts_utc, void_reason, created_ts_utc, updated_ts_utc) "
            "VALUES (?, ?, ?, 'NEW', ?, 0, NULL, NULL, ?, ?)",
            (episode_id, subscription_id, cohort, halt_ts_utc, now, now),
        )
    except sqlite3.IntegrityError:
        # Lost a race against a concurrent halt delivery: the partial unique
        # index guarantees exactly one open episode — return theirs.
        existing = get_open_episodes(conn, subscription_id)
        if not existing:
            raise
        return existing[0]
    ledger.append(
        conn,
        subscription_id=subscription_id,
        trigger_event=trigger_event or "subscription.halted",
        policy_eval={"decision": "create_episode", "to_state": "NEW"},
        human_gate=False,
        outcome="EPISODE_CREATED",
        mode=DEFAULT_MODE,
    )
    return get_episode(conn, episode_id)


def transition(
    conn: sqlite3.Connection,
    episode_id: str,
    new_state: str,
    ledger_fields: dict[str, Any] | None = None,
    *,
    void_reason: str | None = None,
) -> dict[str, Any]:
    """Apply one validated state change plus its ledger row, atomically.

    The UPDATE and the ledger.append() run on the caller's connection, so
    they share one transaction: the state change and its audit evidence land
    together or not at all. Legality is checked before any write.

    `ledger_fields` is merged over the defaults below — the policy engine
    (next stage) attaches its full rule evaluation, the real trigger event,
    and mode (KILLED under kill switch) through it. Unknown keys raise.

    Reaching SENT counts the outreach: attempt_count += 1 and
    last_action_ts_utc is stamped — the caps (max 3 attempts/halt,
    1 outreach/48h) read exactly these columns.
    """
    row = conn.execute(f"{_SELECT_EPISODE} WHERE id = ?", (episode_id,)).fetchone()
    if row is None:
        raise EpisodeNotFoundError(f"no episode with id {episode_id!r}")
    old_state: str = row["state"]
    if new_state not in ALLOWED_TRANSITIONS[old_state]:
        raise TransitionError(
            f"illegal episode transition {old_state} -> {new_state} "
            f"(episode {episode_id}, subscription {row['subscription_id']})"
        )
    if new_state == "VOIDED":
        if void_reason not in VOID_REASONS:
            raise TransitionError(
                f"voiding requires void_reason in {sorted(VOID_REASONS)}, got {void_reason!r}"
            )
    elif void_reason is not None:
        raise TransitionError("void_reason is only valid when new_state is 'VOIDED'")

    fields: dict[str, Any] = {
        "subscription_id": row["subscription_id"],
        "trigger_event": VOID_REASONS[void_reason] if void_reason else "episode.transition",
        "policy_eval": (
            {
                "decision": "void",
                "reason": f"stop_on_{void_reason}",
                "from_state": old_state,
                "to_state": "VOIDED",
            }
            if void_reason
            else {"decision": "transition", "from_state": old_state, "to_state": new_state}
        ),
        "human_gate": new_state == "GATED",
        "outcome": f"EPISODE_{new_state}",
        "mode": DEFAULT_MODE,
    }
    if ledger_fields:
        unknown = set(ledger_fields) - set(ledger.LEDGER_FIELDS)
        if unknown:
            raise ValueError(f"unknown ledger fields: {sorted(unknown)}")
        fields.update(ledger_fields)

    now = _utc_now_iso()
    attempt_count: int = row["attempt_count"]
    last_action_ts: str | None = row["last_action_ts_utc"]
    if new_state == "SENT":
        attempt_count += 1
        last_action_ts = now

    conn.execute(
        "UPDATE episodes SET state = ?, attempt_count = ?, last_action_ts_utc = ?, "
        "void_reason = ?, updated_ts_utc = ? WHERE id = ?",
        (new_state, attempt_count, last_action_ts, void_reason, now, episode_id),
    )
    ledger.append(conn, **fields)
    return get_episode(conn, episode_id)


def void_open_episodes(
    conn: sqlite3.Connection,
    subscription_id: str,
    reason: str,
    *,
    trigger_event: str | None = None,
) -> list[dict[str, Any]]:
    """Stop-on-charge / stop-on-cancel: void every open episode of a sub.

    Stop-on-charge invariant: `subscription.charged` (or payment_link.paid /
    invoice.paid — pass trigger_event) means the customer paid through the
    normal cycle; `subscription.cancelled`/`completed` means the cycle is
    over. Each open episode → VOIDED with the reason stamped, one ledger row
    per void, same transaction. Idempotent under event replay: no open
    episodes → [] and zero writes, so a duplicate stop event is harmless.
    """
    if reason not in VOID_REASONS:
        raise ValueError(f"reason must be one of {sorted(VOID_REASONS)}, got {reason!r}")
    extras: dict[str, Any] = {"trigger_event": trigger_event} if trigger_event else {}
    return [
        transition(conn, ep["id"], "VOIDED", ledger_fields=extras, void_reason=reason)
        for ep in get_open_episodes(conn, subscription_id)
    ]
