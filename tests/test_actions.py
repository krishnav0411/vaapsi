"""D2 action-layer tests — the policy-gated execute path, fully offline.

No HTTP anywhere: RecoveryLinkActionClient runs with client=None, which
swaps in the RecordingStub, so dispatch is deterministic and network-free.
Each test gets a fresh tmp data_dir (its own SQLite file) and an explicitly-
off kill switch, so nothing leaks in from the local .env or a previous
test; the frozen daytime clock (15:30 IST, window open) keeps quiet-hours
out of the picture — those rules are covered in test_policy.py. Covers:
block writes literally nothing (ledger row count unchanged, episode
untouched), send path SCORED → SENT with attempt_count+1 and exactly one
new ledger row carrying the rzp payload, the stub payload shape, and
CONTROL staying blocked through the same execute entry point."""

import sqlite3
from datetime import datetime, timezone

import pytest

from app.actions.execute import execute_episode_action
from app.actions.recovery_link import RecoveryLinkActionClient
from app.audit.ledger import iter_rows
from app.core.episodes import create_episode, get_episode, transition
from app.db import get_conn, init_db
from app.policy import engine
from app.policy.engine import evaluate
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"
# 10:00 UTC == 15:30 IST — the outreach window is open at this instant.
FROZEN_NOW = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Fresh store per test + kill switch explicitly off (hermetic vs .env)."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "kill_switch", False)
    with get_conn() as conn:
        init_db(conn)
        yield conn


@pytest.fixture()
def freeze_clock(monkeypatch):
    monkeypatch.setattr(engine, "_now_utc", lambda: FROZEN_NOW)


def _scored(db, subscription_id: str, cohort: str = "TREATMENT") -> dict:
    """Clean episode driven NEW → DIAGNOSED → SCORED by the legal path."""
    ep = create_episode(db, subscription_id=subscription_id, halt_ts_utc=HALT_TS, cohort=cohort)
    ep = transition(db, ep["id"], "DIAGNOSED")
    return transition(db, ep["id"], "SCORED")


def _ledger_count(db: sqlite3.Connection) -> int:
    return len(list(iter_rows(db)))


def test_block_writes_zero_rows(db):
    """Non-SCORED episode → BLOCKED, and NOTHING is written: the ledger row
    count is unchanged and the episode row is untouched."""
    ep = create_episode(db, subscription_id="sub_BLK1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
    before = _ledger_count(db)

    result = execute_episode_action(db, ep)

    assert result["dispatched"] is False
    assert result["policy"]["reason"] == "max_attempts"
    assert result["policy"]["state"] == "NEW"
    assert _ledger_count(db) == before
    after = get_episode(db, ep["id"])
    assert after["state"] == "NEW"
    assert after["attempt_count"] == 0


def test_send_path_scores_to_sent_with_one_ledger_row(db, freeze_clock):
    """SEND on SCORED: episode → SENT, attempt_count+1, exactly one new
    ledger row carrying the rzp payload with the attempt-1 reference_id."""
    ep = _scored(db, "sub_SEND1")
    before = _ledger_count(db)

    result = execute_episode_action(
        db, ep, client=RecoveryLinkActionClient(client=None)  # explicit injection
    )

    assert result["dispatched"] is True
    assert result["policy"]["cohort"] == "TREATMENT"
    after = get_episode(db, ep["id"])
    assert after["state"] == "SENT"
    assert after["attempt_count"] == 1
    assert after["last_action_ts_utc"] is not None

    rows = list(iter_rows(db))
    assert len(rows) == before + 1  # exactly one new ledger row
    sent_row = rows[-1]
    assert sent_row["outcome"] == "EPISODE_SENT"
    assert sent_row["rzp_call"] is not None
    assert sent_row["rzp_call"]["reference_id"] == f"vaapsi:{ep['id'][:24]}:1"
    assert sent_row["mode"] == "NORMAL"
    assert sent_row["policy_eval"]["action"] == "SEND"
    assert sent_row["policy_eval"]["reason"] == "all_rules_pass"


def test_stub_action_wrapper_shape(db, freeze_clock):
    """The ActionClient wrapper: stub response, channel, payload fields."""
    ep = _scored(db, "sub_SHAPE1")
    client = RecoveryLinkActionClient(client=None)

    result = client.create_recovery_link(db, ep, evaluate(db, ep["subscription_id"], ep))

    assert result["action_id"].startswith("act_")
    assert result["channel"] == "payment_link"
    payload = result["rzp_payload"]
    assert payload["amount"] == 49900  # integer paise, never float
    assert payload["currency"] == "INR"
    assert payload["reference_id"] == f"vaapsi:{ep['id'][:24]}:1"
    assert payload["description"] == f"Vaapsi recovery — subscription {ep['subscription_id']}"
    assert payload["notes"]["vaapsi_episode_id"] == ep["id"]
    assert result["rzp_response"]["stub"] is True
    assert result["rzp_response"]["link_id"].startswith("link_stub_")


def test_control_cohort_through_execute_stays_blocked(db, freeze_clock):
    """CONTROL episodes pass through execute but the cohort gate refuses:
    blocked verdict, zero ledger writes, zero attempts, state untouched."""
    ep = _scored(db, "sub_CTRL1", cohort="CONTROL")
    before = _ledger_count(db)

    result = execute_episode_action(db, ep)

    assert result["dispatched"] is False
    assert result["policy"]["reason"] == "cohort_gate"
    assert result["policy"]["cohort"] == "CONTROL"
    after = get_episode(db, ep["id"])
    assert after["state"] == "SCORED"  # blocked episodes never move
    assert after["attempt_count"] == 0
    assert _ledger_count(db) == before


def test_fence_receives_real_razorpay_link_id(db, freeze_clock, monkeypatch):
    """Regression: Razorpay's create response carries the link under "id",
    not "link_id". A real-shape response must reach the verify-after-write
    fence — with a fencing client injected, the fence must see the id."""
    from app.orchestrator import run_recovery_cycle

    class RecordingFence:
        def __init__(self): self.verify_calls = []
        def fetch_subscription(self, subscription_id):
            # platform exhausted its retries → REQUEST_RETRY stands down,
            # the cycle falls through to the payment-link dispatch
            return {"status": "halted", "short_url": None, "auth_attempts": 99}
        def cancel_payment_link(self, link_id):
            return {"cancelled": True, "id": link_id}

    fence = RecordingFence()
    seen = {}
    real_verify = __import__("app.policy.fencing", fromlist=["verify_after_write"]).verify_after_write

    def spy(conn, fc, subscription_id, link_id):
        seen["link_id"] = link_id
        return real_verify(conn, fc, subscription_id, link_id)

    monkeypatch.setattr("app.orchestrator.fencing.verify_after_write", spy)

    ep = _scored(db, "sub_FENCE1")
    real_shape_response = {
        "id": "plink_REGRESSION1",
        "short_url": "https://rzp.io/rzp/regress1",
        "amount": 49900,
        "status": "created",
    }

    class StubClient:
        def create_payment_link(self, payload):
            return dict(real_shape_response)

    class _Actions:
        def create_recovery_link(self, conn, episode, decision):
            return {
                "action_id": "act_regress",
                "channel": "payment_link",
                "rzp_payload": {},
                "rzp_response": dict(real_shape_response),
            }

    summary = run_recovery_cycle(
        db, ep["subscription_id"], client=None, action_client=_Actions(), fence_client=fence
    )

    assert summary["status"] == "dispatched"
    assert seen.get("link_id") == "plink_REGRESSION1", (
        "fence must receive the real Razorpay 'id' field"
    )
