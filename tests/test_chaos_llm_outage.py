"""D4 Drill 3 tests — LLM outage → DEGRADED, per-episode independence.

run_outage_drill drives real recovery cycles through the REAL
OpenAICompatibleClient over a dead endpoint (socket-free MockTransport
raising the exact httpx exceptions a dead base_url produces — timeout and
500 through the adapter's own retry-once). Covers: outage client → every
episode DEGRADED with tier-appropriate fallbacks and the consult evidenced
in the ledger; mixed run (1 healthy sub + 1 dead client) → per-episode
independence (NORMAL stays NORMAL, DEGRADED stays contained); CONTROL
blocked with zero outreach writes even during the outage — policy intact;
the dead client itself raises LLMUnavailable after exactly 2 transport
hits (initial + the adapter's one retry). Engine clock frozen at a
daytime instant (quiet-hours out of scope here — window logic lives in
tests/test_policy.py); per-test tmp data_dir, fully offline."""

import json
from datetime import datetime, timezone

import pytest

from app.audit.ledger import iter_rows
from app.chaos.llm_outage import (
    DEAD_BASE_URL,
    OUTAGE_MODEL_LABEL,
    dead_endpoint_client,
    run_outage_drill,
)
from app.core.episodes import create_episode, get_episode
from app.db import get_conn, init_db
from app.llm.base import LLMUnavailable
from app.llm.openai_compat import OpenAICompatibleClient
from app.orchestrator import run_recovery_cycle
from app.policy import engine
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"
# 10:00 UTC == 15:30 IST — the outreach window is open at this instant.
FROZEN_NOW = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)

# Error-code streaks → deterministic tiers (scorecard rules, first match):
# 1 transient → tier 1, non-transient → tier 2, 3 straight → tier 3.
SCENARIOS = (
    ("T1", "TREATMENT", ["GATEWAY_ERROR"]),
    ("T2", "TREATMENT", ["CARD_DECLINED"]),
    ("T3", "TREATMENT", ["GATEWAY_ERROR"] * 3),
    ("C1", "CONTROL", ["GATEWAY_ERROR"]),
)


class _HealthyLLM:
    """Schema-valid healthy recommender — the NORMAL-mode stand-in."""

    model_name = "healthy-fake"

    def recommend(self, payload: dict) -> dict:
        choice = {
            "action": "send_payment_link",
            "channel": "payment_link",
            "message_variant": "gentle",
        }
        return {**choice, "raw": dict(choice)}


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


def _seed_events(conn, sub_id: str, error_codes: list[str]) -> None:
    """Synthetic payment.failed evidence; insertion REVERSED vs occurrence —
    events arrive out of order (proven live), scoring must order by ts."""
    for offset, code in enumerate(error_codes):
        minute = len(error_codes) - offset
        ts = f"2026-08-28T04:{minute:02d}:00+00:00"
        payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": f"pay_{sub_id}_{offset}",
                        "status": "failed",
                        "subscription_id": sub_id,
                        "error_code": code,
                    }
                }
            },
        }
        conn.execute(
            "INSERT INTO webhook_events (idempotency_key, event_id, event, "
            "subscription_id, event_ts_utc, received_ts_utc, payload_json, raw_path) "
            "VALUES (?, NULL, 'payment.failed', ?, ?, ?, ?, NULL)",
            (f"outage_{sub_id}_{offset}", sub_id, ts, ts, json.dumps(payload)),
        )


def _halted(conn, suffix: str, cohort: str, error_codes: list[str]) -> str:
    sub_id = f"sub_OUT_{suffix}"
    _seed_events(conn, sub_id, error_codes)
    create_episode(conn, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort=cohort)
    return sub_id


def test_dead_endpoint_client_is_real_adapter_failing_transport():
    """The drill reuses the REAL OpenAICompatibleClient against a dead
    base_url: a consult raises LLMUnavailable after exactly 2 transport
    hits — the initial attempt plus the adapter's single retry."""
    client = dead_endpoint_client()
    assert isinstance(client, OpenAICompatibleClient)

    with pytest.raises(LLMUnavailable):
        client.recommend({"subscription_id": "sub_X", "tier": 1, "features": {}})

    assert len(client.transport_hits) == 2  # timeout flavor, then 500, then dead
    assert all(url.startswith(DEAD_BASE_URL) for url in client.transport_hits)
    assert client.model_name == OUTAGE_MODEL_LABEL
    client.close()


def test_outage_client_all_episodes_degraded(db, freeze_clock):
    """Dead endpoint → every episode decides DEGRADED with tier-appropriate
    fallbacks; the ledger evidences the failed consult on every SCORED row."""
    subs = [_halted(db, suffix, cohort, codes) for suffix, cohort, codes in SCENARIOS[:3]]

    result = run_outage_drill(db, subs, dead_endpoint_client())

    assert result["episodes"] == 3
    assert result["dispatched"] == ["sub_OUT_T1", "sub_OUT_T2"]
    assert result["gated"] == ["sub_OUT_T3"]
    assert result["blocked"] == []
    assert result["degraded_rows"] == result["cycle_rows"] == 9  # 3 × (diagn+score+act)

    summaries = result["summaries"]
    assert summaries["sub_OUT_T1"]["variant"] == "gentle"  # tier 1 fallback
    assert summaries["sub_OUT_T2"]["variant"] == "standard"  # tier 2 fallback
    assert summaries["sub_OUT_T3"]["reason"] == "tier3_escalation"  # gate, not model
    assert all(s["mode"] == "DEGRADED" for s in summaries.values())

    for row in (r for r in iter_rows(db) if r["outcome"] == "EPISODE_SCORED"):
        assert row["mode"] == "DEGRADED"
        assert row["llm_request_hash"]  # the consult happened…
        assert row["llm_output_raw"] is None  # …and produced nothing usable
        assert row["llm_model"] == OUTAGE_MODEL_LABEL
    after = get_episode(db, summaries["sub_OUT_T1"]["episode_id"])
    assert after["state"] == "SENT" and after["attempt_count"] == 1


def test_mixed_healthy_and_outage_per_episode_independence(db, freeze_clock):
    """One healthy sub + one dead client in the same ledger: the healthy
    episode decides NORMAL (raw model output kept), the outage episode
    degrades in isolation — neither episode's mode leaks into the other."""
    healthy_sub = _halted(db, "H1", "TREATMENT", ["GATEWAY_ERROR"])
    outage_sub = _halted(db, "X1", "TREATMENT", ["CARD_DECLINED"])

    healthy = run_recovery_cycle(db, healthy_sub, _HealthyLLM())
    result = run_outage_drill(db, [outage_sub], dead_endpoint_client())

    assert healthy["mode"] == "NORMAL" and healthy["status"] == "dispatched"
    assert result["summaries"][outage_sub]["mode"] == "DEGRADED"
    assert result["dispatched"] == [outage_sub]

    by_sub = {r["subscription_id"]: r for r in iter_rows(db) if r["outcome"] == "EPISODE_SCORED"}
    assert by_sub[healthy_sub]["mode"] == "NORMAL"
    assert by_sub[healthy_sub]["llm_output_raw"]["message_variant"] == "gentle"
    assert by_sub[outage_sub]["mode"] == "DEGRADED"
    assert by_sub[outage_sub]["llm_output_raw"] is None


def test_policy_still_enforced_during_outage(db, freeze_clock):
    """CONTROL is cohort-gated even while the LLM is dead: SCORED, zero
    attempts, zero outreach rows — the outage cannot widen what Vaapsi does."""
    treatment_sub = _halted(db, "P1", "TREATMENT", ["GATEWAY_ERROR"])
    control_sub = _halted(db, "K1", "CONTROL", ["GATEWAY_ERROR"])

    result = run_outage_drill(db, [treatment_sub, control_sub], dead_endpoint_client())

    assert result["dispatched"] == [treatment_sub]  # daytime, TREATMENT, rules-only
    assert result["blocked"] == [control_sub]
    assert result["summaries"][control_sub]["reason"] == "cohort_gate"
    control_ep = get_episode(db, result["summaries"][control_sub]["episode_id"])
    assert control_ep["state"] == "SCORED" and control_ep["attempt_count"] == 0
    outreach = [
        r
        for r in iter_rows(db)
        if r["subscription_id"] == control_sub
        and r["outcome"] in ("EPISODE_SENT", "EPISODE_GATED")
    ]
    assert outreach == []
