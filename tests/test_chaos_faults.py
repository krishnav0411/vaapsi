"""D4 Drill 2 tests — Razorpay 5xx mid-action → backoff + DLQ + no lost action.

FaultyActionClient (real httpx.HTTPStatusError over synthetic 500/503)
attacks the executor through the ActionClient seam; the sleeps are
captured via the executor's _sleep clock seam (never actually slept) and
the engine's clock is frozen at a daytime instant so quiet-hours stay out.
Covers: 2 fails then success → exactly 1 dispatch, 0 DLQ rows, backoff
0.2s/0.4s; 3 fails → DLQ row + SENT transition with attempt counted, the
ledger showing the SENT row; drain_dlq with a healthy transport →
DRAINED + DLQ_DRAINED ledger row, re-drain idempotent; a drain that
itself 5xxs keeps the row PENDING. Per-test tmp data_dir, fully offline."""

import json
from datetime import datetime, timezone

import pytest

from app.actions import execute as execute_module
from app.actions.execute import drain_dlq, execute_episode_action
from app.actions.recovery_link import RecordingStub, RecoveryLinkActionClient
from app.audit.ledger import iter_rows
from app.chaos.faults import FaultyActionClient, http_5xx_error
from app.core.episodes import create_episode, get_episode, transition
from app.db import get_conn, init_db
from app.policy import engine
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


@pytest.fixture()
def sleep_log(monkeypatch) -> list[float]:
    """Capture the executor's backoff sleeps instead of sleeping for real."""
    calls: list[float] = []
    monkeypatch.setattr(execute_module, "_sleep", calls.append)
    return calls


def _scored(db, subscription_id: str) -> dict:
    """Clean episode driven NEW → DIAGNOSED → SCORED by the legal path."""
    ep = create_episode(db, subscription_id=subscription_id, halt_ts_utc=HALT_TS, cohort="TREATMENT")
    ep = transition(db, ep["id"], "DIAGNOSED")
    return transition(db, ep["id"], "SCORED")


def test_two_failures_then_success_one_dispatch_zero_dlq(db, freeze_clock, sleep_log):
    """5xx, 5xx, success → ONE dispatch, episode SENT, nothing queued,
    and the backoff pattern (0.2s then 0.4s) fired between attempts."""
    ep = _scored(db, "sub_RETRY1")
    faulty = FaultyActionClient(RecoveryLinkActionClient(client=None), fail_first=2)

    result = execute_episode_action(db, ep, client=faulty)

    assert result["dispatched"] is True
    assert "dlq" not in result  # recovered within the retry budget
    assert faulty.calls == 3
    assert sleep_log == [0.2, 0.4]  # exponential: base * 1, base * 2
    after = get_episode(db, ep["id"])
    assert after["state"] == "SENT"
    assert after["attempt_count"] == 1
    assert db.execute("SELECT COUNT(*) AS c FROM dlq").fetchone()["c"] == 0
    sent_row = list(iter_rows(db))[-1]
    assert sent_row["outcome"] == "EPISODE_SENT"
    assert "dlq_quarantined" not in sent_row["policy_eval"]  # clean send, no quarantine


def test_three_failures_dlq_row_and_sent_transition(db, freeze_clock, sleep_log):
    """Exhausted retries → DLQ row (PENDING, byte-true payload) AND the
    SENT transition in the same transaction, attempt counted, error kept."""
    ep = _scored(db, "sub_DLQ1")
    faulty = FaultyActionClient(RecoveryLinkActionClient(client=None), fail_first=3)

    result = execute_episode_action(db, ep, client=faulty)

    assert result["dispatched"] is True  # dispatched from Vaapsi's perspective
    assert result["dlq"]["id"].startswith("dlq_")
    assert "500" in result["dlq"]["error"]  # the transport's own error surfaced
    assert faulty.calls == 3
    assert sleep_log == [0.2, 0.4]  # backoff between the 3 attempts, none after

    after = get_episode(db, ep["id"])
    assert after["state"] == "SENT"
    assert after["attempt_count"] == 1  # the attempt IS counted

    rows = db.execute("SELECT * FROM dlq").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == result["dlq"]["id"]
    assert row["episode_id"] == ep["id"]
    assert row["status"] == "PENDING"
    assert row["retry_count"] == 0
    assert row["failed_ts_utc"] is not None
    payload = json.loads(row["payload_json"])
    assert payload["amount"] == 49900  # integer paise, the exact queued payload
    assert payload["reference_id"] == f"vaapsi:{ep['id'][:24]}:1"

    sent_row = list(iter_rows(db))[-1]
    assert sent_row["outcome"] == "EPISODE_SENT"
    assert sent_row["rzp_call"] == payload  # ledger carries the attempted send
    assert sent_row["policy_eval"]["dlq_quarantined"] is True
    assert sent_row["policy_eval"]["dispatch_error"] == result["dlq"]["error"]


def test_drain_dlq_with_healthy_client_marks_drained(db, freeze_clock, sleep_log):
    """Healed transport → PENDING rows re-dispatched, DRAINED, ledger shows
    BOTH the SENT row and the DRAIN event; a re-drain finds nothing."""
    ep = _scored(db, "sub_DRAIN1")
    execute_episode_action(
        db, ep, client=FaultyActionClient(RecoveryLinkActionClient(client=None), fail_first=3)
    )

    first = drain_dlq(db, RecordingStub())

    assert first == {"found": 1, "drained": 1, "failed": 0}
    row = db.execute("SELECT status, retry_count FROM dlq").fetchone()
    assert row["status"] == "DRAINED"
    assert row["retry_count"] == 1

    # idempotent: everything already drained → the second drain is a no-op
    second = drain_dlq(db, RecordingStub())
    assert second == {"found": 0, "drained": 0, "failed": 0}
    assert db.execute("SELECT COUNT(*) AS c FROM dlq WHERE status = 'DRAINED'").fetchone()["c"] == 1

    outcomes = [r["outcome"] for r in iter_rows(db)]
    assert outcomes.count("EPISODE_SENT") == 1
    assert outcomes.count("DLQ_DRAINED") == 1
    drain_row = next(r for r in iter_rows(db) if r["outcome"] == "DLQ_DRAINED")
    assert drain_row["trigger_event"] == "dlq.drain"
    assert drain_row["rzp_call"] is not None
    assert drain_row["subscription_id"] == ep["subscription_id"]


def test_drain_failure_keeps_row_pending(db, freeze_clock, sleep_log):
    """The drain itself hitting a dead transport must not lose the action:
    the row stays PENDING with retry_count bumped, zero ledger writes."""
    ep = _scored(db, "sub_DRAIN2")
    execute_episode_action(
        db, ep, client=FaultyActionClient(RecoveryLinkActionClient(client=None), fail_first=3)
    )
    ledger_before = len(list(iter_rows(db)))

    class _StillDown:
        calls = 0

        def create_payment_link(self, payload: dict) -> dict:
            _StillDown.calls += 1
            raise http_5xx_error(503)  # the same outage, still burning

    result = drain_dlq(db, _StillDown())

    assert result == {"found": 1, "drained": 0, "failed": 1}
    assert _StillDown.calls == 1
    row = db.execute("SELECT status, retry_count FROM dlq").fetchone()
    assert row["status"] == "PENDING"
    assert row["retry_count"] == 1
    assert len(list(iter_rows(db))) == ledger_before  # no DRAIN row on failure
