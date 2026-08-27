"""D2 policy-engine tests.

One test per rule, in the engine's evaluation order, plus boundary
sanity for the 48h interval and the state gate. Determinism comes from
freezing the engine's clock hook (app.policy.engine._now_utc) at a fixed
UTC instant; every test gets a fresh tmp data_dir (its own SQLite file)
and an explicitly-off kill switch, so nothing leaks in from the local
.env or a previous test."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.episodes import create_episode, transition
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


def _scored(db, subscription_id: str = "sub_POL1", cohort: str = "TREATMENT") -> dict:
    """Clean TREATMENT episode driven NEW → DIAGNOSED → SCORED."""
    ep = create_episode(db, subscription_id=subscription_id, halt_ts_utc=HALT_TS, cohort=cohort)
    ep = transition(db, ep["id"], "DIAGNOSED")
    return transition(db, ep["id"], "SCORED")


def _set_outreach(db, episode_id: str, hours_ago: float) -> None:
    """Stamp last_action as if an outreach happened `hours_ago` before the frozen now."""
    ts = (FROZEN_NOW - timedelta(hours=hours_ago)).isoformat()
    db.execute("UPDATE episodes SET last_action_ts_utc = ? WHERE id = ?", (ts, episode_id))


def test_kill_switch_blocks_everything(db, monkeypatch):
    ep = _scored(db, "sub_KILL1")
    monkeypatch.setattr(get_settings(), "kill_switch", True)

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is False
    assert decision.action == "BLOCKED"
    assert decision.reason == "kill_switch"
    assert decision.details["kill_switch"] is True


def test_three_attempts_block(db, freeze_clock):
    ep = _scored(db, "sub_CAP1")
    db.execute("UPDATE episodes SET attempt_count = 3 WHERE id = ?", (ep["id"],))

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is False
    assert decision.reason == "max_attempts"
    assert decision.details["attempt_count"] == 3


def test_outreach_2h_ago_blocks_cooling(db, freeze_clock):
    ep = _scored(db, "sub_COOL1")
    _set_outreach(db, ep["id"], hours_ago=2)

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is False
    assert decision.reason == "cooling_off"
    assert decision.details["hours_since_last_outreach"] == 2.0


def test_outreach_30h_ago_blocks_48h_cap(db, freeze_clock):
    ep = _scored(db, "sub_CAP1")
    _set_outreach(db, ep["id"], hours_ago=30)

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is False
    assert decision.reason == "outreach_cap_48h"
    assert decision.details["min_interval_hours"] == 48


def test_outreach_49h_ago_passes_interval(db, freeze_clock):
    ep = _scored(db, "sub_OK1")
    _set_outreach(db, ep["id"], hours_ago=49)

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is True


def test_quiet_hours_block(db, monkeypatch):
    ep = _scored(db, "sub_QUIET1")
    quiet_ist = datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc)  # 22:00 IST
    monkeypatch.setattr(engine, "_now_utc", lambda: quiet_ist)

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is False
    assert decision.reason == "quiet_hours"
    assert decision.details["ist_hour"] == 22


def test_control_cohort_blocks(db, freeze_clock):
    ep = _scored(db, "sub_CTRL1", cohort="CONTROL")

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is False
    assert decision.reason == "cohort_gate"
    assert decision.details["cohort"] == "CONTROL"


def test_unscored_episode_blocks(db, freeze_clock):
    ep = create_episode(db, subscription_id="sub_NEW1", halt_ts_utc=HALT_TS, cohort="TREATMENT")

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is False
    assert decision.reason == "max_attempts"
    assert decision.details["state"] == "NEW"


def test_all_rules_pass_sends(db, freeze_clock):
    ep = _scored(db, "sub_SEND1")

    decision = evaluate(db, ep["subscription_id"], ep)

    assert decision.ok is True
    assert decision.action == "SEND"
    assert decision.reason == "all_rules_pass"
    assert decision.details["attempt_count"] == 0
    assert decision.details["cohort"] == "TREATMENT"
    assert decision.details["ist_hour"] == 15
