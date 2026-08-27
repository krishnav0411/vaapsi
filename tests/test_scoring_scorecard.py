"""D3 Stage 1 scorecard tests — tier boundaries, error-code precedence, purity.

Each test gets a fresh tmp data_dir (its own SQLite file) and an
explicitly-off kill switch, so nothing leaks in from the local .env or a
previous test (house fixture pattern from test_policy.py / test_actions.py).
Webhook events are inserted in deliberately shuffled arrival order in
several tests — events arrive OUT OF ORDER (proven live in D1), so scoring
must order by occurrence timestamp, never insertion order."""

import json
import uuid

import pytest

from app.audit.ledger import iter_rows
from app.core.episodes import create_episode
from app.db import get_conn, init_db
from app.policy.engine import HUMAN_GATE_THRESHOLD_PAISE
from app.scoring import score_episode, scorecard
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Fresh store per test + kill switch explicitly off (hermetic vs .env)."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "kill_switch", False)
    with get_conn() as conn:
        init_db(conn)
        yield conn


def _failed(subscription_id: str, ts: str, error_code: str | None = "GATEWAY_ERROR") -> tuple[str, str]:
    entity = {
        "id": f"pay_{uuid.uuid4().hex[:10]}",
        "status": "failed",
        "subscription_id": subscription_id,
    }
    if error_code is not None:
        entity["error_code"] = error_code
    payload = {"event": "payment.failed", "payload": {"payment": {"entity": entity}}}
    return "payment.failed", json.dumps(payload)


def _charged(subscription_id: str, ts: str) -> tuple[str, str]:
    entity = {"id": subscription_id, "status": "active"}
    payload = {"event": "subscription.charged", "payload": {"subscription": {"entity": entity}}}
    return "subscription.charged", json.dumps(payload)


def _insert(db, event: str, payload_json: str, subscription_id: str, ts: str, *, column_sub: str | None = None) -> None:
    """Insert one webhook event; column_sub overrides the subscription_id
    column (used to reproduce the live ingest shape where payment.* events
    key the column on the payment id)."""
    db.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, subscription_id, "
        "event_ts_utc, received_ts_utc, payload_json, raw_path) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
        (uuid.uuid4().hex, None, event, column_sub or subscription_id, ts, ts, payload_json),
    )


def _episode(db, subscription_id: str) -> dict:
    return create_episode(
        db, subscription_id=subscription_id, halt_ts_utc=HALT_TS, cohort="TREATMENT"
    )


def _score(db, sub_id: str):
    return score_episode(db, _episode(db, sub_id))


def _failures(sub_id: str, codes: list[str], start_hour: int = 1) -> list[tuple[str, str]]:
    return [
        _failed(sub_id, f"2026-08-28T{start_hour + i:02d}:00:00+00:00", code)
        for i, code in enumerate(codes)
    ]


# ── Tier 1: gentle nudge (transient-looking, <= 1 consecutive failure) ──


def test_tier1_single_transient_failure(db):
    kind, payload = _failed("sub_T1A", "2026-08-28T01:00:00+00:00", "GATEWAY_ERROR")
    _insert(db, kind, payload, "sub_T1A", "2026-08-28T01:00:00+00:00")

    result = _score(db, "sub_T1A")

    assert result.tier == 1
    assert result.features["last_error_code"] == "GATEWAY_ERROR"
    assert result.features["consecutive_failures"] == 1
    assert result.features["amount_paise"] == 49900


def test_tier1_boundary_zero_failures_after_charge(db):
    """A charged event after the failure resets the streak to 0 — the most
    recent failure still supplies the error code, so tier 1 holds (cf=0 <= 1)."""
    sub = "sub_T1B"
    kind_f, payload_f = _failed(sub, "2026-08-28T01:00:00+00:00", "GATEWAY_ERROR")
    kind_c, payload_c = _charged(sub, "2026-08-28T04:00:00+00:00")
    # Arrival order deliberately REVERSED vs occurrence order (out-of-order).
    _insert(db, kind_c, payload_c, sub, "2026-08-28T04:00:00+00:00")
    _insert(db, kind_f, payload_f, sub, "2026-08-28T01:00:00+00:00")

    result = _score(db, sub)

    assert result.tier == 1
    assert result.features["consecutive_failures"] == 0
    assert result.features["last_error_code"] == "GATEWAY_ERROR"


# ── Error-code precedence: non-transient codes defeat tier 1 ──────────


def test_card_declined_single_failure_is_tier2(db):
    kind, payload = _failed("sub_PREC1", "2026-08-28T01:00:00+00:00", "CARD_DECLINED")
    _insert(db, kind, payload, "sub_PREC1", "2026-08-28T01:00:00+00:00")

    result = _score(db, "sub_PREC1")

    assert result.tier == 2
    assert result.features["consecutive_failures"] == 1


def test_transient_code_with_two_failures_is_tier2(db):
    """cf=2 breaks tier 1's <= 1 boundary but stays under tier 3's >= 3."""
    sub = "sub_PREC2"
    for i, ts in enumerate(("01:00:00", "02:00:00")):
        kind, payload = _failed(sub, f"2026-08-28T{ts}", "NETWORK_ERROR")
        _insert(db, kind, payload, sub, f"2026-08-28T{ts}")

    result = _score(db, sub)

    assert result.tier == 2
    assert result.features["consecutive_failures"] == 2


# ── Tier 3: escalate to human review ──────────────────────────────────


def test_tier3_three_consecutive_failures_even_transient(db):
    """Boundary: cf=3 escalates regardless of the transient code — tier 3
    wins over tier 1's error-code match because FIRST match order puts the
    <= 1 gate before it."""
    sub = "sub_T3A"
    for i, hour in enumerate((1, 2, 3)):
        kind, payload = _failed(sub, f"2026-08-28T0{hour}:00:00+00:00", "GATEWAY_ERROR")
        _insert(db, kind, payload, sub, f"2026-08-28T0{hour}:00:00+00:00")

    result = _score(db, sub)

    assert result.tier == 3
    assert result.features["consecutive_failures"] == 3


def test_tier3_amount_boundary_at_threshold_and_above(db, monkeypatch):
    """amount > HUMAN_GATE_THRESHOLD_PAISE escalates; exactly == does not."""
    kind, payload = _failed("sub_T3B", "2026-08-28T01:00:00+00:00", "CARD_DECLINED")
    _insert(db, kind, payload, "sub_T3B", "2026-08-28T01:00:00+00:00")

    # Exactly at the threshold (₹500.00): NOT above → tier 2.
    monkeypatch.setattr(scorecard, "PLAN_PRICE_PAISE", HUMAN_GATE_THRESHOLD_PAISE)
    assert _score(db, "sub_T3B").tier == 2

    # One paisa above: escalates → tier 3.
    monkeypatch.setattr(scorecard, "PLAN_PRICE_PAISE", HUMAN_GATE_THRESHOLD_PAISE + 1)
    assert _score(db, "sub_T3B").tier == 3


# ── Feature extraction edges ──────────────────────────────────────────


def test_no_events_scores_tier2_with_defaults(db):
    result = _score(db, "sub_EMPTY")

    assert result.tier == 2
    assert result.features["last_error_code"] is None
    assert result.features["consecutive_failures"] == 0
    # No cohorts row → age measured from the episode's own halt_ts → 0.
    assert result.features["subscription_age_days"] == 0.0


def test_subscription_age_from_cohorts(db):
    db.execute(
        "INSERT INTO cohorts (subscription_id, cohort, slot, customer_id, rzp_status, short_url, created_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sub_AGE1", "TREATMENT", 1, None, None, None, "2026-08-26T05:00:00+00:00"),
    )

    result = _score(db, "sub_AGE1")

    assert result.features["subscription_age_days"] == 2.0


def test_failure_matched_via_entity_subscription_id(db):
    """Live ingest shape: payment.* rows key the subscription_id COLUMN on
    the payment id — the scorer must still find them via the payload's
    entity.subscription_id."""
    kind, payload = _failed("sub_ENT1", "2026-08-28T01:00:00+00:00", "GATEWAY_ERROR")
    _insert(db, kind, payload, "sub_ENT1", "2026-08-28T01:00:00+00:00", column_sub="pay_ENT1")

    result = _score(db, "sub_ENT1")

    assert result.tier == 1
    assert result.features["last_error_code"] == "GATEWAY_ERROR"


def test_success_after_failures_breaks_streak(db):
    """charged ts=04:00 lands after three failures (ts 01–03): streak is 0,
    not 3 — ordering by occurrence time, not arrival."""
    sub = "sub_STREAK1"
    for hour in (1, 2, 3):
        kind, payload = _failed(sub, f"2026-08-28T0{hour}:00:00+00:00", "GATEWAY_ERROR")
        _insert(db, kind, payload, sub, f"2026-08-28T0{hour}:00:00+00:00")
    kind_c, payload_c = _charged(sub, "2026-08-28T04:00:00+00:00")
    _insert(db, kind_c, payload_c, sub, "2026-08-28T04:00:00+00:00")

    result = _score(db, sub)

    assert result.features["consecutive_failures"] == 0
    assert result.tier == 1


# ── Purity + determinism ──────────────────────────────────────────────


def test_score_episode_writes_nothing(db):
    sub = "sub_PURE1"
    ep = _episode(db, sub)
    ledger_before = len(list(iter_rows(db)))
    episodes_before = db.execute("SELECT state, attempt_count FROM episodes WHERE id = ?", (ep["id"],)).fetchone()

    _score(db, sub)

    assert len(list(iter_rows(db))) == ledger_before
    episodes_after = db.execute("SELECT state, attempt_count FROM episodes WHERE id = ?", (ep["id"],)).fetchone()
    assert dict(episodes_after) == dict(episodes_before)


def test_rationale_is_one_deterministic_sentence(db):
    kind, payload = _failed("sub_RAT1", "2026-08-28T01:00:00+00:00", "GATEWAY_ERROR")
    _insert(db, kind, payload, "sub_RAT1", "2026-08-28T01:00:00+00:00")

    first = _score(db, "sub_RAT1")
    second = _score(db, "sub_RAT1")

    assert first.rationale == second.rationale
    assert "\n" not in first.rationale
    assert first.rationale.startswith("TIER 1")
    assert "GATEWAY_ERROR" in first.rationale
