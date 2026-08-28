"""Fence tests — the three dispatch fences plus the failure classifier.

Covers: fingerprint stability (pure over relevant fields only), the
look-before-leap guard (blocked / clear / fail-closed), verify-after-write
compensation (cancel succeeding AND failing — the row lands either way),
the stale-inference discard path, REQUEST_RETRY both branches, classifier
coverage across all six categories and input shapes, and one orchestrator
end-to-end with fences active. Ledger rows go through the real ledger API
so every fence path asserts chain validity too. Same hermetic house
pattern as the rest of the suite: fresh tmp data_dir per test, kill switch
off, frozen daytime clock so quiet-hours can never fire.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.actions.classifier import (
    AUTH_REQUIRED,
    HARD_DECLINE,
    MANDATE_REVOKED,
    NETWORK,
    TRANSIENT_RETRYABLE,
    UNKNOWN,
    classify_failure,
)
from app.actions.request_retry import maybe_request_retry
from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain
from app.core.episodes import create_episode, get_episode
from app.db import get_conn, init_db
from app.orchestrator import run_recovery_cycle
from app.policy import engine, fencing
from app.policy.engine import COOLING_HOURS
from app.policy.fencing import (
    COMPENSATION_OUTCOME,
    DISCARDED_STALE_OUTCOME,
    FENCE_BLOCKED_OUTCOME,
    fingerprint_subscription,
    guard_dispatch,
    verify_after_write,
)
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


class FakeFenceClient:
    """fetch_subscription/cancel backed by a mutable payload dict.

    Set `fail_fetch` to make every fetch raise (fail-closed path), or
    swap cancel_payment_link to force a compensation-cancel failure.
    """

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.cancel_calls: list[str] = []
        self.cancel_result: Any = {"id": "link_X", "status": "cancelled"}
        self.fail_fetch = False

    def fetch_subscription(self, subscription_id: str) -> dict[str, Any]:
        if self.fail_fetch:
            raise ConnectionError("provider unreachable")
        return json.loads(json.dumps(self.payload))

    def cancel_payment_link(self, link_id: str) -> dict[str, Any]:
        self.cancel_calls.append(link_id)
        return self.cancel_result


def halted_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "sub_fence",
        "status": "halted",
        "auth_attempts": 2,
        "max_auth_attempts": 4,
        "short_url": "https://rzp.io/i/fence",
        "current_period": 3,
        "current_period_start": 1756000000,
        "current_period_end": 1758600000,
        "remaining_cycles": 6,
        "notes": {"churn": "irrelevant"},
        "customer_id": "cust_fence",
    }
    payload.update(overrides)
    return payload


def _rows_for(db: Any, sub_id: str) -> list[dict[str, Any]]:
    return [r for r in iter_rows(db) if r["subscription_id"] == sub_id]


def _outcome_set(db: Any, sub_id: str) -> set[str]:
    return {r["outcome"] for r in _rows_for(db, sub_id)}



# ── fingerprint ──────────────────────────────────────────────────────


def test_fingerprint_stable_for_same_relevant_state():
    assert fingerprint_subscription(halted_payload()) == fingerprint_subscription(
        halted_payload()
    )


def test_fingerprint_ignores_irrelevant_churn():
    churned = halted_payload(notes={"different": "stuff"}, customer_id="cust_other")
    assert fingerprint_subscription(halted_payload()) == fingerprint_subscription(churned)


def test_fingerprint_changes_on_relevant_change():
    base = fingerprint_subscription(halted_payload())
    assert fingerprint_subscription(halted_payload(status="cancelled")) != base
    assert fingerprint_subscription(halted_payload(auth_attempts=5)) != base
    assert fingerprint_subscription(halted_payload(short_url=None)) != base
    assert fingerprint_subscription(halted_payload(remaining_cycles=0)) != base


def test_fingerprint_treats_absence_as_state():
    base = fingerprint_subscription(halted_payload())
    missing = halted_payload()
    del missing["remaining_cycles"]
    assert fingerprint_subscription(missing) != base
    assert fingerprint_subscription(None) != base


# ── guard_dispatch (fence 1) ─────────────────────────────────────────


def test_guard_clear_when_still_halted():
    guard = guard_dispatch(FakeFenceClient(halted_payload()), {"subscription_id": "sub_fence"})
    assert guard["blocked"] is False
    assert guard["reason"] == "halted_confirmed"


def test_guard_blocks_when_subscription_moved():
    guard = guard_dispatch(
        FakeFenceClient(halted_payload(status="cancelled")), {"subscription_id": "sub_fence"}
    )
    assert guard["blocked"] is True
    assert guard["reason"] == "subscription_not_halted"
    assert guard["fresh_status"] == "cancelled"


def test_guard_fail_closed_when_fetch_fails():
    client = FakeFenceClient(halted_payload())
    client.fail_fetch = True
    guard = guard_dispatch(client, {"subscription_id": "sub_fence"})
    assert guard["blocked"] is True
    assert guard["reason"] == "fence_fetch_failed"


# ── verify_after_write (fence 3) ─────────────────────────────────────


def test_verify_after_write_noop_when_still_halted(db):
    _seed(db, "sub_VAW_OK")
    result = fencing.verify_after_write(db, FakeFenceClient(halted_payload()), "sub_VAW_OK", "link_X")
    assert result["compensated"] is False and result["verified"] is True
    assert not any(
        r["outcome"] == COMPENSATION_OUTCOME for r in _rows_for(db, "sub_VAW_OK")
    )


def test_verify_after_write_compensates_and_lands_row(db):
    _seed(db, "sub_VAW_1")
    client = FakeFenceClient(halted_payload(status="completed"))
    result = verify_after_write(db, client, "sub_VAW_1", "link_1")
    assert result["compensated"] is True and result["cancelled"] is True
    comp = [r for r in _rows_for(db, "sub_VAW_1") if r["outcome"] == COMPENSATION_OUTCOME]
    assert len(comp) == 1
    assert comp[0]["policy_eval"]["fresh_status"] == "completed"
    assert verify_chain(iter_rows(db))[0] is True


def test_verify_after_write_row_lands_even_when_cancel_raises(db):
    _seed(db, "sub_VAW_2")

    class NoCancel(FakeFenceClient):
        def cancel_payment_link(self, link_id: str) -> dict[str, Any]:
            raise RuntimeError("cancellation unsupported")

    result = verify_after_write(db, NoCancel(halted_payload(status="completed")), "sub_VAW_2", "link_2")
    assert result["compensated"] is True and result["cancelled"] is False
    comp = [r for r in _rows_for(db, "sub_VAW_2") if r["outcome"] == COMPENSATION_OUTCOME]
    assert len(comp) == 1
    assert "cancellation unsupported" in comp[0]["policy_eval"]["cancel_error"]
    assert verify_chain(iter_rows(db))[0] is True


def test_verify_after_write_degrades_without_cancel_surface(db):
    _seed(db, "sub_VAW_3")

    class NoCancel(FakeFenceClient):
        cancel_payment_link = None  # type: ignore[assignment]

    result = verify_after_write(db, NoCancel(halted_payload(status="resumed")), "sub_VAW_3", "link_3")
    assert result["compensated"] is True and result["cancelled"] is False
    assert verify_chain(iter_rows(db))[0] is True


def test_verify_after_write_fetch_failure_degrades(db):
    _seed(db, "sub_VAW_4")
    client = FakeFenceClient(halted_payload(status="completed"))
    client.fail_fetch = True
    result = verify_after_write(db, client, "sub_VAW_4", "link_4")
    assert result["compensated"] is False and result["verified"] is False
    # The fence degraded to a logged no-op: no compensation row, chain intact.
    assert verify_chain(iter_rows(db))[0] is True


# ── orchestrator with fences end-to-end ──────────────────────────────


def test_cycle_blocks_when_subscription_no_longer_halted(db, freeze_clock):
    sub = "sub_FENC_BLK"
    _seed_failures_for_fence(db, sub)
    _halt_for_fence(db, sub)
    client = FakeFenceClient(halted_payload(id=sub, status="cancelled"))
    summary = run_recovery_cycle(db, sub, client=None, action_client=None, fence_client=client)
    assert summary["status"] == "blocked"
    assert summary["reason"] == "subscription_not_halted"
    assert FENCE_BLOCKED_OUTCOME in {r["outcome"] for r in _rows_for(db, sub)}
    # The episode is untouched — the next cycle re-evaluates from a live world.
    assert verify_chain(iter_rows(db))[0] is True


def test_cycle_discards_stale_inference(db, freeze_clock):
    sub = "sub_FENC_STALE"
    _seed_failures_for_fence(db, sub)
    _halt_for_fence(db, sub)

    class MovingClient(FakeFenceClient):
        """Halted on the guard fetch, moved by the time the LLM returns."""

        def __init__(self) -> None:
            super().__init__(halted_payload(id="sub_FENC_STALE"))
            self.calls = 0

        def fetch_subscription(self, subscription_id: str) -> dict[str, Any]:
            self.calls += 1
            if self.calls >= 2:  # guard fetch ok; post-LLM recheck sees movement
                self.payload = halted_payload(id="sub_FENC_STALE", status="resumed")
            return super().fetch_subscription(subscription_id)

    summary = run_recovery_cycle(db, "sub_FENC_STALE", client=None, fence_client=MovingClient())
    assert summary["status"] == "blocked"
    assert summary["reason"] == "stale_fingerprint"
    assert any(r["outcome"] == DISCARDED_STALE_OUTCOME for r in _rows_for(db, "sub_FENC_STALE"))
    assert verify_chain(iter_rows(db))[0] is True


def test_cycle_without_fence_client_is_unchanged(db, freeze_clock):
    _seed_failures_for_fence(db, "sub_NOFENCE")
    _halt_for_fence(db, "sub_NOFENCE")
    summary = run_recovery_cycle(db, "sub_NOFENCE", client=None, action_client=None)
    # The offline default is byte-for-byte the pre-fence behavior: dispatch
    # happens (tier-1, rules-only DEGRADED) and no fence rows exist.
    assert summary["status"] == "dispatched"
    outcomes = {r["outcome"] for r in _rows_for(db, "sub_NOFENCE")}
    assert not outcomes & {FENCE_BLOCKED_OUTCOME, DISCARDED_STALE_OUTCOME, COMPENSATION_OUTCOME}


def test_cycle_stands_back_when_platform_retries(db, freeze_clock):
    """Platform still retrying (auth_attempts < max) → REQUEST_RETRY handles
    the cycle: advisory ledger row, episode untouched, no outreach."""
    _seed_failures_for_fence(db, "sub_FENC_OK")
    _halt_for_fence(db, "sub_FENC_OK")
    client = FakeFenceClient(halted_payload(id="sub_FENC_OK", auth_attempts=1, max_auth_attempts=4))
    summary = run_recovery_cycle(db, "sub_FENC_OK", client=None, action_client=None, fence_client=client)
    assert summary["status"] == "request_retry"
    rows = _rows_for(db, "sub_FENC_OK")
    assert any(r["outcome"] == "ACTION_REQUEST_RETRY" for r in rows)
    assert not any(r["outcome"] == COMPENSATION_OUTCOME for r in rows)
    assert verify_chain(iter_rows(db))[0] is True


def test_cycle_dispatches_and_verifies_when_platform_done(db, freeze_clock):
    """Platform retries exhausted → fall-through to the link path; the
    verify-after-write fence runs and stays silent (still halted)."""
    _seed_failures_for_fence(db, "sub_FENC_OK2")
    _halt_for_fence(db, "sub_FENC_OK2")
    client = FakeFenceClient(halted_payload(id="sub_FENC_OK2", auth_attempts=4, max_auth_attempts=4))
    summary = run_recovery_cycle(db, "sub_FENC_OK2", client=None, action_client=None, fence_client=client)
    assert summary["status"] == "dispatched"
    rows = _rows_for(db, "sub_FENC_OK2")
    assert not any(r["outcome"] == COMPENSATION_OUTCOME for r in rows)
    assert verify_chain(iter_rows(db))[0] is True


# ── REQUEST_RETRY ────────────────────────────────────────────────────


def test_request_retry_stands_back_while_platform_retries(db, freeze_clock):
    _seed_failures_for_fence(db, "sub_RETRY_1")
    ep = _halt_for_fence(db, "sub_RETRY_1")
    from app.core.episodes import transition
    ep = transition(db, ep["id"], "DIAGNOSED")
    ep = transition(db, ep["id"], "SCORED")
    client = FakeFenceClient(halted_payload(id="sub_RETRY_1", auth_attempts=1, max_auth_attempts=4))
    result = maybe_request_retry(db, ep, client, mode="NORMAL")
    assert result["handled"] is True
    rows = _rows_for(db, "sub_RETRY_1")
    retry_rows = [r for r in rows if r["outcome"] == "ACTION_REQUEST_RETRY"]
    assert len(retry_rows) == 1
    revisit = retry_rows[0]["policy_eval"]["revisit_at"]
    # revisit_at = (module now) + COOLING_HOURS — parse and check the offset,
    # not the wall clock (request_retry owns its own now()).
    delta = datetime.fromisoformat(revisit) - datetime.now(timezone.utc)
    assert timedelta(hours=COOLING_HOURS - 1) < delta < timedelta(hours=COOLING_HOURS + 1)
    # The episode itself is untouched — the row is advisory: still SCORED,
    # attempt_count unbumped, so the next cycle re-evaluates from scratch.
    after = get_episode(db, ep["id"])
    assert after["state"] == "SCORED" and after["attempt_count"] == 0
    assert verify_chain(iter_rows(db))[0] is True


def test_request_retry_falls_through_when_platform_done_retrying(db):
    ep = _seed(db, "sub_RETRY_2")
    from app.core.episodes import transition
    ep = transition(db, ep["id"], "DIAGNOSED")
    ep = transition(db, ep["id"], "SCORED")
    client = FakeFenceClient(
        halted_payload(id="sub_RETRY_DONE", auth_attempts=4, max_auth_attempts=4)
    )
    result = maybe_request_retry(db, ep, client, mode="NORMAL")
    assert result["handled"] is False


# ── classifier ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("INSUFFICIENT_FUNDS", TRANSIENT_RETRYABLE),
        ("insufficient funds", TRANSIENT_RETRYABLE),
        ("authentication_required", AUTH_REQUIRED),
        ("OTP_EXPIRED", AUTH_REQUIRED),
        ("authentication_opted_out", AUTH_REQUIRED),
        ("card_declined", HARD_DECLINE),
        ("GATEWAY_ERROR", HARD_DECLINE),
        ("gateway error", HARD_DECLINE),
        ("mandate_revoked", MANDATE_REVOKED),
        ("MANDATE_INVALID", MANDATE_REVOKED),
        ({"code": "MANDATE_REVOKED"}, MANDATE_REVOKED),
        ({"error": {"code": "OTP_EXPIRED"}}, AUTH_REQUIRED),
        ("timed out", UNKNOWN),  # normalizes to timed_out; not a mapped needle
        ("timeout", NETWORK),
        ("CONNECTION_RESET", NETWORK),
        ({"code": "NETWORK_ERROR"}, NETWORK),
        ("", UNKNOWN),
        (None, UNKNOWN),
        ({"error": {"description": "curious"}}, UNKNOWN),
        ("payment_cancelled_by_customer", UNKNOWN),
        (42, UNKNOWN),
        (object(), UNKNOWN),
    ],
)
def test_classifier_maps_every_shape(raw: Any, expected: str):
    assert classify_failure(raw) == expected


def test_scorecard_urgency_modulates_but_tier_frozen(db):
    """TRANSIENT_RETRYABLE lowers urgency; tier routing is untouched, so the
    human gate and dispatch behave exactly as before the classifier."""
    from app.scoring.scorecard import score_episode

    db.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, "
        "subscription_id, event_ts_utc, received_ts_utc, payload_json, raw_path) "
        "VALUES ('t_urg_1', 'pay_URG', 'payment.failed', 'pay_sub_URG', ?, ?, ?, NULL)",
        (HALT_TS, HALT_TS, json.dumps(_failed_payload("sub_URG", "INSUFFICIENT_FUNDS"))),
    )
    ep = create_episode(db, subscription_id="sub_URG", halt_ts_utc=HALT_TS, cohort="TREATMENT")
    result = score_episode(db, ep)
    assert result.failure_category == TRANSIENT_RETRYABLE
    assert 1 <= result.urgency <= 3
    # Tier itself unchanged: an UNKNOWN error and an unclassified one route identically.




def _failed_payload(sub_id: str, error_code: str) -> dict[str, Any]:
    return {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_{sub_id}",
                    "status": "failed",
                    "subscription_id": sub_id,
                    "error_code": error_code,
                }
            }
        },
    }


def _seed(db: Any, sub_id: str, error_code: str = "GATEWAY_ERROR") -> dict[str, Any]:
    """Failure trail + open NEW episode, ready for a cycle."""
    db.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, "
        "subscription_id, event_ts_utc, received_ts_utc, payload_json, raw_path) "
        "VALUES (?, NULL, 'payment.failed', ?, ?, ?, ?, NULL)",
        (f"t_{sub_id}_seed", f"pay_{sub_id}", HALT_TS, HALT_TS, json.dumps(_failed_payload(sub_id, error_code))),
    )
    return create_episode(db, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort="TREATMENT")


def _seed_failures_for_fence(db: Any, sub_id: str, error_code: str = "GATEWAY_ERROR") -> None:
    db.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, "
        "subscription_id, event_ts_utc, received_ts_utc, payload_json, raw_path) "
        "VALUES (?, NULL, 'payment.failed', ?, ?, ?, ?, NULL)",
        (f"t_{sub_id}_f0", f"pay_{sub_id}", HALT_TS, HALT_TS, json.dumps(_failed_payload(sub_id, error_code))),
    )


def _halt_for_fence(db: Any, sub_id: str) -> dict[str, Any]:
    return create_episode(db, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort="TREATMENT")
