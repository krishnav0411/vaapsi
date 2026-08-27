"""Policy-gated dispatch — the single door every outbound action goes through.

Why one executor: outreach must never leave without a fresh policy verdict,
and the verdict, the action, and the evidence must commit together.
execute_episode_action() runs the policy engine FIRST; a BLOCKED verdict
returns without touching the database — zero ledger rows on block, because
an action that never happened must not look like one that did. A SEND
verdict on a SCORED episode calls the injected ActionClient and transitions
SCORED → SENT with the full policy evaluation and the exact Razorpay
payload stamped into one hash-chained ledger row; the transition and its
ledger row share the caller's transaction (land together or not at all),
and reaching SENT stamps attempt_count + 1 — exactly the column the caps
read. Razorpay I/O stays behind the ActionClient Protocol (client=None →
offline RecordingStub), so no network call can originate from here unless
prod explicitly injects one.

D4 resilience (drill 2): the ActionClient call is wrapped in bounded
retry with exponential backoff (ACTION_ATTEMPTS, BACKOFF_BASE_SECONDS —
named constants, clock seams `_sleep`/`_utc_now_iso` injectable so tests
freeze time). Only 5xx and transport-level faults are retried — a 4xx is
a permanent rejection, not noise. When every attempt fails, the action is
NOT lost and NOT pretended-successful: the exact payload is written to
the `dlq` table and the episode still transitions to SENT IN THE SAME
TRANSACTION — from Vaapsi's perspective the outreach IS dispatched (the
delivery is async and retried); the DLQ row is the honest record that the
wire said no. drain_dlq() later re-dispatches PENDING rows through a
working transport and marks them DRAINED with a DLQ_DRAINED ledger row,
so the chain narrates the whole outage → drain story. The resilient
execute stays in THIS module (not app/chaos/resilient_execute.py): the
DLQ insert must share the exact transaction as the SENT transition, and
this module already owns that transaction boundary.
"""

import json
import sqlite3
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import httpx

from app.actions.base import ActionClient
from app.actions.recovery_link import (
    RecoveryLinkActionClient,
    build_recovery_link_payload,
)
from app.audit import ledger
from app.core import episodes
from app.policy.engine import evaluate
from app.razorpay import RazorpayError

# Bounded retry: 3 attempts with exponential backoff — 0.2s before the
# second attempt, 0.4s before the third (base * factor**(attempt-1)); no
# sleep after the final failure, the DLQ takes over. Named constants so
# the drill/demo narrative and the tests quote the same numbers.
ACTION_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.2
BACKOFF_FACTOR = 2

# DLQ row lifecycle (schema CHECK in app/db.py).
DLQ_PENDING = "PENDING"
DLQ_DRAINED = "DRAINED"
DLQ_DROPPED = "DROPPED"


def _sleep(seconds: float) -> None:
    """Clock seam: real sleep in prod; tests record/freeze via monkeypatch."""
    time.sleep(seconds)


def _utc_now_iso() -> str:
    """Clock seam for the DLQ failed_ts stamp (kept injectable like _sleep)."""
    return datetime.now(timezone.utc).isoformat()


def _is_retryable(exc: BaseException) -> bool:
    """5xx and transport faults are worth retrying; 4xx means re-sending the
    identical payload would fail identically — those go straight to the DLQ."""
    if isinstance(exc, RazorpayError):
        return exc.status_code >= 500
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def _dispatch_with_backoff(
    action_client: ActionClient,
    conn: sqlite3.Connection,
    episode: dict[str, Any],
    decision: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Call the ActionClient up to ACTION_ATTEMPTS times with backoff.

    Returns (result, None) on success or (None, last_error) once attempts
    are exhausted (or the fault is non-retryable). The client is called on
    the caller's connection and raises nothing — failures come back as
    data so the DLQ/SENT transaction below stays the only write path.
    """
    last_error: str | None = None
    for attempt in range(1, ACTION_ATTEMPTS + 1):
        try:
            return action_client.create_recovery_link(conn, episode, decision), None
        except (RazorpayError, httpx.HTTPError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if not _is_retryable(exc):
                break
            if attempt < ACTION_ATTEMPTS:
                _sleep(BACKOFF_BASE_SECONDS * BACKOFF_FACTOR ** (attempt - 1))
    return None, last_error


def _quarantine_to_dlq(
    conn: sqlite3.Connection,
    episode: dict[str, Any],
    payload: dict[str, Any],
    error: str,
) -> str:
    """Insert the dead-letter row (PENDING) for an exhaustively failed dispatch.

    The payload is the byte-true payment-link payload the send would have
    carried (same builder the RecoveryLinkActionClient uses), so drain_dlq
    re-dispatches it as-is. Same connection, same transaction as the SENT
    transition — a DLQ row without its SENT episode (or vice versa) is a
    torn write the schema refuses to store.
    """
    dlq_id = f"dlq_{uuid.uuid4().hex}"
    conn.execute(
        "INSERT INTO dlq (id, episode_id, payload_json, error, failed_ts_utc, "
        "retry_count, status) VALUES (?, ?, ?, ?, ?, 0, ?)",
        (
            dlq_id,
            episode["id"],
            ledger.canonical_json(payload),
            error,
            _utc_now_iso(),
            DLQ_PENDING,
        ),
    )
    return dlq_id


def execute_episode_action(
    conn: sqlite3.Connection,
    episode: dict[str, Any],
    *,
    client: ActionClient | None = None,
    mode: str = episodes.DEFAULT_MODE,
) -> dict[str, Any]:
    """Run policy, then dispatch (or refuse) outreach for one episode.

    BLOCKED → {'dispatched': False, 'policy': decision details + reason}
    with zero writes (no episode change, no ledger row). SEND on a SCORED
    episode → calls the ActionClient (with bounded retry/backoff),
    transitions SCORED → SENT with policy_eval + rzp_call + mode stamped
    into the ledger row, and returns {'dispatched': True, 'action': ...}.

    When every attempt fails, the outcome is STILL a dispatch from
    Vaapsi's perspective: the payload is quarantined to the dlq table and
    the episode transitions to SENT in the same transaction — the return
    carries {'dispatched': True, 'dlq': {'id', 'error'}, 'action': None}
    so callers can distinguish "on the wire" from "queued for drain".

    `mode` labels how the outreach decision was reached (engine-mode
    taxonomy: NORMAL | DEGRADED | KILLED) — the D3 orchestrator stamps
    DEGRADED when its rules-only fallback chose the action; callers that
    don't care get the D2 default (NORMAL) untouched.
    """
    decision = evaluate(conn, episode["subscription_id"], episode)
    if decision.action != "SEND" or episode["state"] != "SCORED":
        # Blocked: write NOTHING — the absence of evidence is the evidence.
        return {"dispatched": False, "policy": {"reason": decision.reason, **decision.details}}

    action_client = client if client is not None else RecoveryLinkActionClient(client=None)
    result, dispatch_error = _dispatch_with_backoff(action_client, conn, episode, decision)

    if result is not None:
        episodes.transition(
            conn,
            episode["id"],
            "SENT",
            ledger_fields={
                "policy_eval": asdict(decision),
                "rzp_call": result["rzp_payload"],
                "mode": mode,
            },
        )
        return {"dispatched": True, "action": result, "policy": decision.details}

    # Attempts exhausted: quarantine + SENT land in ONE transaction. The
    # ledger row carries the exact attempted payload as rzp_call evidence
    # plus the dispatch error inside policy_eval — the chain tells the
    # outage story without a second write path.
    payload = build_recovery_link_payload(episode)
    dlq_id = _quarantine_to_dlq(conn, episode, payload, dispatch_error or "unknown failure")
    episodes.transition(
        conn,
        episode["id"],
        "SENT",
        ledger_fields={
            "policy_eval": {
                **asdict(decision),
                "dlq_quarantined": True,
                "dlq_id": dlq_id,
                "dispatch_error": dispatch_error,
            },
            "rzp_call": payload,
            "mode": mode,
        },
    )
    return {
        "dispatched": True,
        "action": None,
        "dlq": {"id": dlq_id, "error": dispatch_error},
        "policy": decision.details,
    }


def drain_dlq(conn: sqlite3.Connection, client: Any) -> dict[str, int]:
    """Re-dispatch every PENDING DLQ row through a working transport.

    `client` needs only create_payment_link(payload) — a RazorpayClient in
    prod, the RecordingStub in tests/demo. Each success marks the row
    DRAINED (retry_count + 1) and appends a DLQ_DRAINED ledger row so the
    hash chain narrates the recovery; a failure bumps retry_count and
    leaves the row PENDING for the next drain. Idempotent: a second drain
    with everything already drained finds zero PENDING rows and writes
    nothing.
    """
    rows = conn.execute(
        "SELECT id, episode_id, payload_json, retry_count FROM dlq "
        "WHERE status = ? ORDER BY failed_ts_utc ASC",
        (DLQ_PENDING,),
    ).fetchall()
    drained = failed = 0
    for row in rows:
        payload = json.loads(row["payload_json"])
        sub = conn.execute(
            "SELECT subscription_id FROM episodes WHERE id = ?", (row["episode_id"],)
        ).fetchone()
        subscription_id = sub["subscription_id"] if sub is not None else "unknown"
        try:
            response = client.create_payment_link(payload)
        except (RazorpayError, httpx.HTTPError):  # drain transport failure → stays queued
            conn.execute(
                "UPDATE dlq SET retry_count = retry_count + 1 WHERE id = ?",
                (row["id"],),
            )
            failed += 1
            continue
        conn.execute(
            "UPDATE dlq SET status = ?, retry_count = retry_count + 1 WHERE id = ?",
            (DLQ_DRAINED, row["id"]),
        )
        ledger.append(
            conn,
            subscription_id=subscription_id,
            trigger_event="dlq.drain",
            policy_eval={
                "decision": "dlq_drain",
                "dlq_id": row["id"],
                "episode_id": row["episode_id"],
            },
            human_gate=False,
            outcome="DLQ_DRAINED",
            rzp_call=response,
            mode=episodes.DEFAULT_MODE,
        )
        drained += 1
    return {"found": len(rows), "drained": drained, "failed": failed}
