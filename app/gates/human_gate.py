"""Human gate — the GATED state's enqueue/decide consumer (D3 Stage 3).

Why a human gate: outreach above the ₹500 threshold (and tier-3 risk
episodes generally) must not leave on an algorithm's say-so. The
deterministic pipeline routes such episodes here: enqueue_for_approval()
writes an `approvals` row, transitions SCORED → GATED and appends the
hash-chained ledger row — all on the caller's connection, so the queue
entry, the state change and their audit evidence commit or roll back
TOGETHER (house atomicity pattern). Nothing is sent while an episode sits
in GATED: the gate is a hard stop, not a delay.

decide() is the only way out. APPROVED → the episode dispatches through
the same ActionClient + GATED→SENT transition path the orchestrator's SEND
branch uses (the human's approval is the missing authorization; the kill
switch still refuses, because no approval outranks an active incident).
REJECTED → GATED → CLOSED with ledger outcome 'human_rejected' — a
deliberate decision, kept distinct from VOIDED (whose reasons are
charge/cancel stop events). Each decision appends EXACTLY ONE ledger row;
deciding twice raises DoubleDecisionError, so a replayed or racing
decision can never double-send or produce two verdicts.

requires_human_gate() is the policy-side amount check: True only when the
policy engine has approved acting (a BLOCKED verdict writes nothing of any
kind — no outreach, no approval either) AND the episode's amount_paise
exceeds HUMAN_GATE_THRESHOLD_PAISE, imported from app.policy.engine — the
number is never duplicated here. The amount rides on the episode dict
(orchestrators attach the scorer's features); absent that, the plan price
this system would actually charge (app.scoring's single source) is used.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.actions.base import ActionClient
from app.actions.recovery_link import RecoveryLinkActionClient
from app.audit import ledger
from app.core import episodes
from app.policy.engine import HUMAN_GATE_THRESHOLD_PAISE, PolicyDecision
from app.scoring.scorecard import PLAN_PRICE_PAISE
from app.settings import get_settings

APPROVAL_STATUSES: tuple[str, ...] = ("PENDING", "APPROVED", "REJECTED")

# Enqueue reasons (deterministic strings so the approvals table and the
# ledger's policy_eval are reproducible evidence).
GATE_REASON_TIER3 = "tier3_escalation"
GATE_REASON_AMOUNT = "amount_over_threshold"


class ApprovalError(ValueError):
    """An approval operation violated the approvals/episodes contract."""


class ApprovalNotFoundError(LookupError):
    """The approval id does not exist in the approvals table."""


class DoubleDecisionError(ApprovalError):
    """The approval was already decided — exactly one decision, ever."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def requires_human_gate(episode: dict[str, Any], policy_decision: PolicyDecision) -> bool:
    """True when this episode's amount may not leave without a human.

    Two conditions, both required: the policy engine must have approved
    acting at all (action == 'SEND' — a BLOCKED verdict means zero writes,
    and an approval row is a write), and the episode's amount_paise must be
    STRICTLY above HUMAN_GATE_THRESHOLD_PAISE (exactly ₹500 needs no gate —
    the same boundary the scorer's tier rules use).
    """
    if policy_decision.action != "SEND":
        return False
    amount = episode.get("amount_paise", PLAN_PRICE_PAISE)
    return amount > HUMAN_GATE_THRESHOLD_PAISE


def get_approval(conn: sqlite3.Connection, approval_id: str) -> dict[str, Any]:
    """Fetch one approval as a dict; raise ApprovalNotFoundError if absent."""
    row = conn.execute(
        "SELECT id, episode_id, subscription_id, reason, status, "
        "created_ts_utc, decided_ts_utc FROM approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    if row is None:
        raise ApprovalNotFoundError(f"no approval with id {approval_id!r}")
    return dict(row)


def enqueue_for_approval(
    conn: sqlite3.Connection,
    episode: dict[str, Any],
    reason: str,
    *,
    mode: str = episodes.DEFAULT_MODE,
    ledger_fields: dict[str, Any] | None = None,
) -> str:
    """Queue a SCORED episode for human review; return the approval id.

    Writes the approvals row (status PENDING), transitions SCORED → GATED
    and appends the gate's ledger row in ONE transaction on the caller's
    connection (the transition carries the ledger row — house pattern).
    The episode must be SCORED: validation happens before any write, so an
    enqueue attempt on anything else raises and leaves both tables
    untouched. `mode` and `ledger_fields` (e.g. LLM evidence) flow into the
    GATED transition's ledger row so the row records how the decision to
    gate was reached, not just that it happened.
    """
    if episode["state"] != "SCORED":
        raise ApprovalError(
            f"human gate enqueues SCORED episodes only, got state "
            f"{episode['state']!r} (episode {episode['id']})"
        )
    approval_id = f"apr_{uuid.uuid4().hex}"
    conn.execute(
        "INSERT INTO approvals (id, episode_id, subscription_id, reason, "
        "status, created_ts_utc, decided_ts_utc) VALUES (?, ?, ?, ?, 'PENDING', ?, NULL)",
        (approval_id, episode["id"], episode["subscription_id"], reason, _utc_now_iso()),
    )
    fields: dict[str, Any] = {
        "trigger_event": "human_gate.enqueued",
        "policy_eval": {
            "decision": "enqueue_human_gate",
            "reason": reason,
            "from_state": "SCORED",
            "to_state": "GATED",
            "approval_id": approval_id,
        },
        "mode": mode,
    }
    if ledger_fields:
        # Validate before the INSERT so a bad field can never leave a
        # half-written enqueue inside the caller's transaction.
        unknown = set(ledger_fields) - set(ledger.LEDGER_FIELDS)
        if unknown:
            raise ValueError(f"unknown ledger fields: {sorted(unknown)}")
        fields.update(ledger_fields)
    episodes.transition(conn, episode["id"], "GATED", fields)
    return approval_id


def decide(
    conn: sqlite3.Connection,
    approval_id: str,
    approved: bool,
    *,
    client: ActionClient | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Apply exactly one human decision to a PENDING approval.

    APPROVED → dispatch outreach through the same path the orchestrator's
    SEND branch uses (ActionClient + GATED → SENT transition, one ledger
    row carrying the approval evidence and the exact Razorpay payload);
    REJECTED → GATED → CLOSED with ledger outcome 'human_rejected' (one
    ledger row). The approvals row is stamped with the status and
    decided_ts_utc in the same transaction. `note` is the human's stated
    reason (the D8 approvals-inbox capture): when non-empty it rides the
    decision's ledger evidence as decision_note, so the chain records not
    just the verdict but the operator's stated why. Any second decide on
    the same approval raises DoubleDecisionError; unknown ids raise
    ApprovalNotFoundError; a GATED episode that has moved (e.g. voided by
    a stop event while pending) raises ApprovalError with nothing written.
    """
    approval = get_approval(conn, approval_id)
    if approval["status"] != "PENDING":
        raise DoubleDecisionError(
            f"approval {approval_id} already decided: {approval['status']}"
        )
    episode = episodes.get_episode(conn, approval["episode_id"])
    if episode["state"] != "GATED":
        raise ApprovalError(
            f"approval {approval_id} expects a GATED episode, "
            f"got {episode['state']!r} — nothing decided"
        )
    if approved and get_settings().kill_switch:
        # The kill switch outranks any human approval: leave the approval
        # PENDING so the same decision can be re-applied after the incident.
        raise ApprovalError("kill switch active — approval left PENDING, not dispatched")

    now = _utc_now_iso()
    evidence = {"approval_id": approval_id, "gate_reason": approval["reason"]}
    decision_note = note.strip()
    if decision_note:
        evidence["decision_note"] = decision_note
    if approved:
        action_client = client if client is not None else RecoveryLinkActionClient(client=None)
        policy_decision = PolicyDecision(
            ok=True,
            action="SEND",
            reason="human_approved",
            details={"subscription_id": approval["subscription_id"], **evidence},
        )
        result = action_client.create_recovery_link(conn, episode, policy_decision)
        episodes.transition(
            conn,
            episode["id"],
            "SENT",
            ledger_fields={
                "trigger_event": "human_gate.approved",
                "policy_eval": {
                    "decision": "human_gate_approved",
                    "from_state": "GATED",
                    "to_state": "SENT",
                    **evidence,
                },
                "rzp_call": result["rzp_payload"],
                "mode": episodes.DEFAULT_MODE,
            },
        )
        status = "APPROVED"
    else:
        episodes.transition(
            conn,
            episode["id"],
            "CLOSED",
            ledger_fields={
                "trigger_event": "human_gate.rejected",
                "policy_eval": {
                    "decision": "human_gate_rejected",
                    "from_state": "GATED",
                    "to_state": "CLOSED",
                    **evidence,
                },
                "outcome": "human_rejected",
                "mode": episodes.DEFAULT_MODE,
            },
        )
        status = "REJECTED"
    conn.execute(
        "UPDATE approvals SET status = ?, decided_ts_utc = ? WHERE id = ?",
        (status, now, approval_id),
    )
    state_after = episodes.get_episode(conn, episode["id"])["state"]
    return {**get_approval(conn, approval_id), "episode_state_after": state_after}
