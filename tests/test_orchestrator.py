"""D3 Stage 4 orchestrator tests — the full cycle, offline and deterministic.

run_recovery_cycle is driven end to end with a FakeLLM (schema-valid,
tier-flavored) and the default offline stub ActionClient — zero network,
zero real LLM (house pattern: fresh tmp data_dir per test, kill switch
off, frozen daytime clock so quiet-hours can never fire; those rules are
test_policy.py's job). Covers: NORMAL dispatch with tier-appropriate LLM
variants and full ledger evidence (score/features/llm_request_hash/
llm_output_raw/llm_model/mode); DEGRADED fallback on LLM failure AND on
client=None; deterministic gate routing (tier 3 gates even when the model
says dispatch; over-threshold amount gates via requires_human_gate);
CONTROL blocked with zero outreach writes; non-drivable/absent episodes
skipped without writes; and request payloads carrying structured
untrusted data only."""

import json
from datetime import datetime, timezone

import pytest

from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain
from app.core.episodes import create_episode, get_episode
from app.db import get_conn, init_db
from app.llm.base import LLMUnavailable
from app.orchestrator import run_recovery_cycle
from app.policy import engine
from app.scoring import scorecard
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


class FakeLLM:
    """Schema-valid, tier-flavored recommender (the NORMAL-mode stand-in).

    Records every payload so tests can assert the LLM sees only
    structured untrusted data. Set `boom` to make every call raise, or
    `override` to force a specific (still allowlist-clean) recommendation.
    """

    model_name = "fake-llm-test"

    def __init__(self, *, boom: bool = False, override: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.boom = boom
        self.override = override

    def recommend(self, payload: dict) -> dict:
        self.calls.append(payload)
        if self.boom:
            raise LLMUnavailable("fake endpoint down")
        recommendation = self.override or {
            "action": "send_payment_link",
            "channel": "payment_link",
            "message_variant": "gentle" if payload["tier"] == 1 else "standard",
        }
        return {**recommendation, "raw": dict(recommendation)}


def _seed_failure(db, sub_id: str, error_code: str, ts: str, seq: int = 0) -> None:
    """One payment.failed event keyed the way live ingest does (the
    subscription_id COLUMN holds the payment id; the scorer matches via
    the payload's entity.subscription_id)."""
    payload = {
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
    db.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, "
        "subscription_id, event_ts_utc, received_ts_utc, payload_json, raw_path) "
        "VALUES (?, NULL, 'payment.failed', ?, ?, ?, ?, NULL)",
        (f"t_{sub_id}_{seq}_{error_code}", f"pay_{sub_id}", ts, ts, json.dumps(payload)),
    )


def _seed_failures(db, sub_id: str, codes: list[str]) -> None:
    for i, code in enumerate(codes):
        _seed_failure(db, sub_id, code, f"2026-08-28T0{i + 1}:00:00+00:00", seq=i)


def _halt(db, sub_id: str, cohort: str = "TREATMENT") -> dict:
    return create_episode(db, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort=cohort)


def _rows_for(db, sub_id: str) -> list[dict]:
    return [r for r in iter_rows(db) if r["subscription_id"] == sub_id]


# ── NORMAL mode: LLM decides the flavor, evidence lands in the ledger ──


def test_tier1_dispatches_normal_with_gentle_variant(db, freeze_clock):
    sub = "sub_ORCH_T1"
    _seed_failures(db, sub, ["GATEWAY_ERROR"])
    ep = _halt(db, sub)
    llm = FakeLLM()

    summary = run_recovery_cycle(db, sub, llm)

    assert summary["status"] == "dispatched"
    assert summary["tier"] == 1
    assert summary["mode"] == "NORMAL"
    assert summary["variant"] == "gentle"
    after = get_episode(db, ep["id"])
    assert after["state"] == "SENT"
    assert after["attempt_count"] == 1

    rows = _rows_for(db, sub)
    assert [r["outcome"] for r in rows] == [
        "EPISODE_CREATED",
        "EPISODE_DIAGNOSED",
        "EPISODE_SCORED",
        "EPISODE_SENT",
    ]
    scored = rows[2]
    assert scored["score"] == 1.0
    assert scored["features"]["last_error_code"] == "GATEWAY_ERROR"
    assert scored["policy_eval"]["tier"] == 1
    assert scored["policy_eval"]["rationale"].startswith("TIER 1")
    assert scored["mode"] == "NORMAL"
    assert scored["llm_output_raw"]["message_variant"] == "gentle"
    assert len(scored["llm_request_hash"]) == 64
    assert scored["llm_model"] == "fake-llm-test"
    sent = rows[3]
    assert sent["mode"] == "NORMAL"
    assert sent["rzp_call"]["reference_id"] == f"vaapsi:{ep['id'][:24]}:1"
    ok, detail = verify_chain(rows)
    assert ok, detail


def test_tier2_dispatches_standard_variant(db, freeze_clock):
    sub = "sub_ORCH_T2"
    _seed_failures(db, sub, ["CARD_DECLINED", "CARD_DECLINED"])  # cf=2 → tier 2
    _halt(db, sub)

    summary = run_recovery_cycle(db, sub, FakeLLM())

    assert summary["tier"] == 2
    assert summary["mode"] == "NORMAL"
    assert summary["variant"] == "standard"


def test_llm_sees_only_structured_payload(db, freeze_clock):
    """The recommendation request carries the feature dict and counters —
    structured untrusted data only, no free text (the fence lives in the
    adapter; the orchestrator must not add instruction-shaped copy)."""
    sub = "sub_ORCH_PAYLOAD"
    _seed_failures(db, sub, ["GATEWAY_ERROR"])
    ep = _halt(db, sub)
    llm = FakeLLM()

    run_recovery_cycle(db, sub, llm)

    (payload,) = llm.calls
    assert set(payload) == {"subscription_id", "episode_id", "attempt_count", "tier", "features"}
    assert payload["subscription_id"] == sub
    assert payload["episode_id"] == ep["id"]
    assert payload["tier"] == 1
    assert set(payload["features"]) == {
        "last_error_code",
        "consecutive_failures",
        "amount_paise",
        "subscription_age_days",
    }


# ── DEGRADED mode: any LLM failure falls back to rules-only ───────────


def test_llm_failure_falls_back_to_degraded_dispatch(db, freeze_clock):
    sub = "sub_ORCH_BOOM"
    _seed_failures(db, sub, ["GATEWAY_ERROR"])
    ep = _halt(db, sub)

    summary = run_recovery_cycle(db, sub, FakeLLM(boom=True))

    assert summary["status"] == "dispatched"
    assert summary["mode"] == "DEGRADED"
    assert summary["variant"] == "gentle"  # rules-only choice for tier 1
    after = get_episode(db, ep["id"])
    assert after["state"] == "SENT"
    assert after["attempt_count"] == 1

    scored = next(r for r in _rows_for(db, sub) if r["outcome"] == "EPISODE_SCORED")
    assert scored["mode"] == "DEGRADED"
    assert scored["llm_output_raw"] is None  # no usable model output to log
    assert len(scored["llm_request_hash"]) == 64  # the attempt is still evidence
    assert scored["llm_model"] == "fake-llm-test"


def test_none_client_is_degraded_rules_only(db, freeze_clock):
    sub = "sub_ORCH_NONE"
    _seed_failures(db, sub, ["CARD_DECLINED"])  # tier 2
    ep = _halt(db, sub)

    summary = run_recovery_cycle(db, sub, None)

    assert summary["status"] == "dispatched"
    assert summary["mode"] == "DEGRADED"
    assert summary["variant"] == "standard"
    assert get_episode(db, ep["id"])["state"] == "SENT"
    sent = next(r for r in _rows_for(db, sub) if r["outcome"] == "EPISODE_SENT")
    assert sent["mode"] == "DEGRADED"
    assert sent["llm_request_hash"] is None  # no LLM was ever consulted


def test_degraded_variants_are_tier_appropriate(db, freeze_clock):
    """Fallback mapping: tier 1 → gentle, tier 2 → standard, tier 3 → gate."""
    tier1 = "sub_ORCH_FB1"
    tier2 = "sub_ORCH_FB2"
    _seed_failures(db, tier1, ["NETWORK_ERROR"])
    _seed_failures(db, tier2, ["CARD_DECLINED"])
    _halt(db, tier1)
    _halt(db, tier2)

    s1 = run_recovery_cycle(db, tier1, None)
    s2 = run_recovery_cycle(db, tier2, None)

    assert (s1["variant"], s2["variant"]) == ("gentle", "standard")
    assert all(s["mode"] == "DEGRADED" for s in (s1, s2))


# ── Gate routing is deterministic — the model never widens it ─────────


def test_tier3_gates_even_when_llm_says_dispatch(db, freeze_clock):
    """Three consecutive failures → tier 3 → human gate, regardless of the
    model's (valid) recommendation to send a payment link."""
    sub = "sub_ORCH_T3"
    _seed_failures(db, sub, ["GATEWAY_ERROR"] * 3)
    ep = _halt(db, sub)
    llm = FakeLLM(override={"action": "send_payment_link", "channel": "payment_link", "message_variant": "gentle"})

    summary = run_recovery_cycle(db, sub, llm)

    assert summary["tier"] == 3
    assert summary["status"] == "gated"
    assert summary["reason"] == "tier3_escalation"
    assert summary["mode"] == "NORMAL"  # LLM was consulted; routing overrode it
    assert summary["variant"] is None  # no self-serve outreach happened
    assert get_episode(db, ep["id"])["state"] == "GATED"
    approval = db.execute(
        "SELECT status, reason FROM approvals WHERE id = ?", (summary["approval_id"],)
    ).fetchone()
    assert approval["status"] == "PENDING"
    assert approval["reason"] == "tier3_escalation"
    gated = next(r for r in _rows_for(db, sub) if r["outcome"] == "EPISODE_GATED")
    assert gated["human_gate"] is True
    assert gated["mode"] == "NORMAL"
    assert gated["llm_output_raw"]["message_variant"] == "gentle"  # override evidence


def test_tier3_without_client_gates_degraded(db, freeze_clock):
    sub = "sub_ORCH_T3FB"
    _seed_failures(db, sub, ["CARD_DECLINED"] * 3)
    ep = _halt(db, sub)

    summary = run_recovery_cycle(db, sub, None)

    assert summary["status"] == "gated"
    assert summary["mode"] == "DEGRADED"
    assert get_episode(db, ep["id"])["state"] == "GATED"
    gated = next(r for r in _rows_for(db, sub) if r["outcome"] == "EPISODE_GATED")
    assert gated["mode"] == "DEGRADED"
    assert gated["llm_model"] is None


def test_amount_over_threshold_gates_end_to_end(db, freeze_clock, monkeypatch):
    """An over-threshold amount gates the episode end to end: the scorer
    routes it to tier 3, and the gate reason names the amount trigger."""
    sub = "sub_ORCH_AMT"
    _seed_failures(db, sub, ["CARD_DECLINED"])
    ep = _halt(db, sub)
    monkeypatch.setattr(scorecard, "PLAN_PRICE_PAISE", 50100)

    summary = run_recovery_cycle(db, sub, None)

    assert summary["tier"] == 3  # scorer: over-threshold amount IS a tier-3 trigger
    assert summary["status"] == "gated"
    assert summary["reason"] == "amount_over_threshold"
    assert get_episode(db, ep["id"])["state"] == "GATED"


# ── Policy compliance: blocked = zero outreach writes ─────────────────


def test_control_blocked_with_zero_outreach_writes(db, freeze_clock):
    sub = "sub_ORCH_CTRL"
    _seed_failures(db, sub, ["GATEWAY_ERROR"])
    ep = _halt(db, sub, cohort="CONTROL")

    summary = run_recovery_cycle(db, sub, FakeLLM())

    assert summary["status"] == "blocked"
    assert summary["reason"] == "cohort_gate"
    after = get_episode(db, ep["id"])
    assert after["state"] == "SCORED"
    assert after["attempt_count"] == 0
    rows = _rows_for(db, sub)
    assert [r["outcome"] for r in rows] == [
        "EPISODE_CREATED",
        "EPISODE_DIAGNOSED",
        "EPISODE_SCORED",
    ]  # pipeline rows only — no sent/gated action row
    assert all(r["rzp_call"] is None for r in rows)
    ok, detail = verify_chain(rows)
    assert ok, detail


def test_quiet_hours_block_writes_no_action_rows(db, freeze_clock, monkeypatch):
    """A TREATMENT episode blocked by the clock: pipeline rows land (the
    scorer is pure analysis), but zero outreach evidence is written."""
    sub = "sub_ORCH_QUIET"
    _seed_failures(db, sub, ["GATEWAY_ERROR"])
    ep = _halt(db, sub)
    # 17:00 UTC == 22:30 IST — inside the 21:00–09:00 quiet window.
    monkeypatch.setattr(engine, "_now_utc", lambda: datetime(2026, 8, 28, 17, 0, 0, tzinfo=timezone.utc))

    summary = run_recovery_cycle(db, sub, None)

    assert summary["status"] == "blocked"
    assert summary["reason"] == "quiet_hours"
    assert get_episode(db, ep["id"])["state"] == "SCORED"
    assert get_episode(db, ep["id"])["attempt_count"] == 0
    outcomes = [r["outcome"] for r in _rows_for(db, sub)]
    assert "EPISODE_SENT" not in outcomes  # no action row
    assert "EPISODE_GATED" not in outcomes


def test_no_open_episode_is_a_writeless_noop(db):
    before = len(list(iter_rows(db)))

    summary = run_recovery_cycle(db, "sub_ORCH_MISSING", None)

    assert summary["status"] == "no_open_episode"
    assert len(list(iter_rows(db))) == before
    assert db.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()["c"] == 0


def test_rerun_after_dispatch_is_skipped_without_writes(db, freeze_clock):
    """A dispatched (SENT) episode is not drivable — the cycle skips it and
    writes nothing: one bounded cycle per halt, never a double send."""
    sub = "sub_ORCH_RERUN"
    _seed_failures(db, sub, ["GATEWAY_ERROR"])
    ep = _halt(db, sub)
    first = run_recovery_cycle(db, sub, FakeLLM())
    assert first["status"] == "dispatched"
    rows_after_first = len(list(iter_rows(db)))

    second = run_recovery_cycle(db, sub, FakeLLM())

    assert second["status"] == "skipped"
    assert second["state_after"] == "SENT"
    assert get_episode(db, ep["id"])["attempt_count"] == 1  # no second attempt
    assert len(list(iter_rows(db))) == rows_after_first
