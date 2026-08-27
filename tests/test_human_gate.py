"""D3 Stage 3 human-gate tests — threshold boundary, atomicity, decide paths.

Fresh tmp data_dir per test (house fixture pattern from test_policy.py /
test_actions.py), kill switch explicitly off so nothing leaks from the
local .env. Covers: requires_human_gate's strict-inequality boundary
(49900 no / 50100 yes, exactly 50000 no) and its policy precondition
(a BLOCKED verdict never enqueues anything); enqueue atomicity (approval
row + SCORED→GATED transition + one hash-chained ledger row land together,
and a bad enqueue writes nothing at all); the approve path (GATED → SENT
through the same ActionClient the orchestrator uses, exactly one new
ledger row); the reject path (GATED → CLOSED, outcome 'human_rejected');
and exactly-one-decision enforcement (double-decide raises, unknown ids
raise, kill switch leaves the approval PENDING)."""

import pytest

from app.actions.recovery_link import RecoveryLinkActionClient
from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain
from app.core.episodes import (
    create_episode,
    get_episode,
    transition,
    void_open_episodes,
)
from app.db import get_conn, init_db
from app.gates.human_gate import (
    ApprovalError,
    ApprovalNotFoundError,
    DoubleDecisionError,
    decide,
    enqueue_for_approval,
    get_approval,
    requires_human_gate,
)
from app.policy.engine import HUMAN_GATE_THRESHOLD_PAISE, PolicyDecision
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"

SEND = PolicyDecision(ok=True, action="SEND", reason="all_rules_pass", details={})
BLOCKED = PolicyDecision(ok=False, action="BLOCKED", reason="cohort_gate", details={})


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Fresh store per test + kill switch explicitly off (hermetic vs .env)."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "kill_switch", False)
    with get_conn() as conn:
        init_db(conn)
        yield conn


def _scored(db, subscription_id: str = "sub_GATE_T", cohort: str = "TREATMENT") -> dict:
    """Clean episode driven NEW → DIAGNOSED → SCORED by the legal path."""
    ep = create_episode(db, subscription_id=subscription_id, halt_ts_utc=HALT_TS, cohort=cohort)
    ep = transition(db, ep["id"], "DIAGNOSED")
    return transition(db, ep["id"], "SCORED")


def _enqueue(db, episode: dict, reason: str = "tier3_escalation") -> str:
    return enqueue_for_approval(db, episode, reason)


# ── requires_human_gate: the amount boundary + policy precondition ──


def test_threshold_boundary(db):
    """amount > threshold gates; exactly at it does not (strict inequality,
    the same boundary the scorer's tier rules apply)."""
    ep = _scored(db)
    assert ep["attempt_count"] == 0
    assert not requires_human_gate({**ep, "amount_paise": 49900}, SEND)
    assert not requires_human_gate({**ep, "amount_paise": HUMAN_GATE_THRESHOLD_PAISE}, SEND)
    assert requires_human_gate({**ep, "amount_paise": 50100}, SEND)


def test_blocked_policy_decision_never_gates(db):
    """A BLOCKED verdict means zero writes of any kind — even an over-
    threshold amount must not enqueue an approval."""
    ep = _scored(db)
    assert not requires_human_gate({**ep, "amount_paise": 999999}, BLOCKED)


def test_missing_amount_defaults_to_plan_price(db):
    """No amount on the episode → the price the dispatch would actually
    charge (49900 plan price) — below the threshold, no gate."""
    ep = _scored(db)
    assert not requires_human_gate(ep, SEND)


# ── enqueue_for_approval: atomicity + state contract ──────────────────


def test_enqueue_writes_approval_gated_transition_and_one_ledger_row(db):
    ep = _scored(db, "sub_ENQ1")
    before = len(list(iter_rows(db)))

    approval_id = _enqueue(db, ep)

    approval = get_approval(db, approval_id)
    assert approval["status"] == "PENDING"
    assert approval["episode_id"] == ep["id"]
    assert approval["subscription_id"] == ep["subscription_id"]
    assert approval["reason"] == "tier3_escalation"
    assert approval["decided_ts_utc"] is None
    after = get_episode(db, ep["id"])
    assert after["state"] == "GATED"

    rows = list(iter_rows(db))
    assert len(rows) == before + 1  # exactly one ledger row for the enqueue
    (gate_row,) = rows[-1:]
    assert gate_row["outcome"] == "EPISODE_GATED"
    assert gate_row["human_gate"] is True
    assert gate_row["policy_eval"]["decision"] == "enqueue_human_gate"
    assert gate_row["policy_eval"]["approval_id"] == approval_id
    assert gate_row["trigger_event"] == "human_gate.enqueued"
    ok, detail = verify_chain(rows)
    assert ok, detail


def test_enqueue_on_non_scored_episode_writes_nothing(db):
    """Validation before ANY write: enqueueing a NEW episode raises and
    leaves no approval row, no ledger row, no state change."""
    ep = create_episode(db, subscription_id="sub_ENQNEW", halt_ts_utc=HALT_TS, cohort="TREATMENT")
    before_rows = len(list(iter_rows(db)))

    with pytest.raises(ApprovalError):
        _enqueue(db, ep)

    assert get_episode(db, ep["id"])["state"] == "NEW"
    assert len(list(iter_rows(db))) == before_rows
    assert db.execute("SELECT COUNT(*) AS c FROM approvals").fetchone()["c"] == 0


def test_enqueue_stamps_mode_and_extra_ledger_fields(db):
    ep = _scored(db, "sub_ENQ2")

    enqueue_for_approval(
        db,
        ep,
        "amount_over_threshold",
        mode="DEGRADED",
        ledger_fields={"llm_model": "fake-llm"},
    )

    (gate_row,) = [r for r in iter_rows(db) if r["outcome"] == "EPISODE_GATED"]
    assert gate_row["mode"] == "DEGRADED"
    assert gate_row["llm_model"] == "fake-llm"


# ── decide: approve / reject / exactly-one-decision ───────────────────


def test_approve_dispatches_via_action_client_and_sends(db):
    """APPROVED → outreach dispatches through the same ActionClient the
    orchestrator's SEND branch uses; GATED → SENT; exactly one new ledger
    row carrying the approval evidence and the exact Razorpay payload."""
    ep = _scored(db, "sub_APPROVE1")
    approval_id = _enqueue(db, ep, "amount_over_threshold")
    before = len(list(iter_rows(db)))

    result = decide(db, approval_id, approved=True)

    assert result["status"] == "APPROVED"
    assert result["decided_ts_utc"] is not None
    assert result["episode_state_after"] == "SENT"
    after = get_episode(db, ep["id"])
    assert after["state"] == "SENT"
    assert after["attempt_count"] == 1  # a real outreach happened

    rows = list(iter_rows(db))
    assert len(rows) == before + 1  # exactly one ledger row for the decision
    (sent_row,) = rows[-1:]
    assert sent_row["trigger_event"] == "human_gate.approved"
    assert sent_row["policy_eval"]["decision"] == "human_gate_approved"
    assert sent_row["policy_eval"]["approval_id"] == approval_id
    assert sent_row["rzp_call"]["reference_id"] == f"vaapsi:{ep['id'][:24]}:1"
    assert sent_row["mode"] == "NORMAL"
    ok, detail = verify_chain(rows)
    assert ok, detail


def test_approve_uses_injected_client(db):
    """The optional client seam: an explicitly injected ActionClient (the
    prod shape) is used instead of the offline stub."""
    ep = _scored(db, "sub_APPR2")
    approval_id = _enqueue(db, ep)
    seen = {}

    class SpyClient(RecoveryLinkActionClient):
        def create_recovery_link(self, conn, episode, policy_decision):
            seen["called"] = True
            seen["reason"] = policy_decision.reason
            return super().create_recovery_link(conn, episode, policy_decision)

    decide(db, approval_id, approved=True, client=SpyClient(client=None))

    assert seen["called"] is True
    assert seen["reason"] == "human_approved"


def test_reject_closes_episode_with_human_rejected_outcome(db):
    """REJECTED → GATED → CLOSED, exactly one ledger row with outcome
    'human_rejected' — a deliberate human end, distinct from VOIDED."""
    ep = _scored(db, "sub_REJECT1")
    approval_id = _enqueue(db, ep)
    before = len(list(iter_rows(db)))

    result = decide(db, approval_id, approved=False)

    assert result["status"] == "REJECTED"
    assert result["decided_ts_utc"] is not None
    assert result["episode_state_after"] == "CLOSED"
    after = get_episode(db, ep["id"])
    assert after["state"] == "CLOSED"
    assert after["attempt_count"] == 0  # nothing was ever sent

    rows = list(iter_rows(db))
    assert len(rows) == before + 1
    (closed_row,) = rows[-1:]
    assert closed_row["outcome"] == "human_rejected"
    assert closed_row["trigger_event"] == "human_gate.rejected"
    assert closed_row["policy_eval"]["decision"] == "human_gate_rejected"
    assert closed_row["rzp_call"] is None  # no outreach evidence on a reject
    ok, detail = verify_chain(rows)
    assert ok, detail


def test_double_decide_raises_and_changes_nothing(db):
    """Exactly one decision per approval — the second call (either flavor)
    raises and leaves the episode and ledger untouched."""
    ep = _scored(db, "sub_DOUBLE1")
    approval_id = _enqueue(db, ep)

    first = decide(db, approval_id, approved=True)
    rows_after_first = len(list(iter_rows(db)))
    state_after_first = get_episode(db, ep["id"])["state"]

    with pytest.raises(DoubleDecisionError):
        decide(db, approval_id, approved=True)
    with pytest.raises(DoubleDecisionError):
        decide(db, approval_id, approved=False)

    assert get_episode(db, ep["id"])["state"] == state_after_first
    assert len(list(iter_rows(db))) == rows_after_first
    assert first["status"] == "APPROVED"


def test_double_decide_on_rejected_approval_raises(db):
    ep = _scored(db, "sub_DOUBLE2")
    approval_id = _enqueue(db, ep)

    decide(db, approval_id, approved=False)

    with pytest.raises(DoubleDecisionError):
        decide(db, approval_id, approved=False)


def test_decide_unknown_approval_raises(db):
    with pytest.raises(ApprovalNotFoundError):
        decide(db, "apr_missing", approved=True)


def test_decide_on_moved_episode_raises_without_writing(db):
    """A stop event can void a GATED episode while its approval waits —
    deciding then must refuse and write nothing (the approval stays
    PENDING; the episode's terminal state is untouched)."""
    ep = _scored(db, "sub_VOIDED1")
    approval_id = _enqueue(db, ep)

    void_open_episodes(db, ep["subscription_id"], "charged")
    before_rows = len(list(iter_rows(db)))

    with pytest.raises(ApprovalError):
        decide(db, approval_id, approved=True)

    assert len(list(iter_rows(db))) == before_rows
    assert get_approval(db, approval_id)["status"] == "PENDING"


def test_kill_switch_blocks_approval_dispatch_but_keeps_it_pending(db, monkeypatch):
    """The kill switch outranks a human approval: the dispatch is refused,
    the approval stays PENDING so the same decision can re-apply after the
    incident, and nothing is written."""
    ep = _scored(db, "sub_KILL1")
    approval_id = _enqueue(db, ep)
    monkeypatch.setattr(get_settings(), "kill_switch", True)
    before_rows = len(list(iter_rows(db)))

    with pytest.raises(ApprovalError):
        decide(db, approval_id, approved=True)

    assert get_approval(db, approval_id)["status"] == "PENDING"
    assert get_episode(db, ep["id"])["state"] == "GATED"
    assert len(list(iter_rows(db))) == before_rows
