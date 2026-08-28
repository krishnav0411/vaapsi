"""Runs the three failure drills end to end in one offline deterministic pass.

House pattern (demo_d2.py / demo_d3.py): a throwaway DB in a temp dir, a
frozen daytime clock, stub/fault transports — zero network, repeatable
verdicts. One run walks the whole D4 failure story in order:

  Drill 1 — webhook replay storm (app.chaos.replay): the SAME
    subscription.halted delivery fired 30 times (25 identical + 5
    shuffled-key variants, jittered timestamps inside one 5-minute
    idempotency window) through the pure process_webhook seam, against an
    already-open recovery episode → exactly 1 webhook_events row, 30
    archive files (every delivery archived, even duplicates), still
    exactly 1 episode — ingest stays idempotent and inert under fire.
  Drill 2 — Razorpay 5xx mid-action (app.chaos.faults +
    app.actions.execute): FaultyActionClient(2) fails the first two sends
    with real httpx 500/503 errors → exponential backoff 0.2s/0.4s
    (captured via the executor's _sleep seam, no real time burned) →
    third attempt succeeds: 1 dispatch, 0 DLQ rows. A second episode
    faces FaultyActionClient(3) → retries exhaust → the exact payload is
    quarantined to the dlq table and the episode STILL transitions to
    SENT in the same transaction; drain_dlq through a healthy stub
    re-dispatches it → DRAINED + a DLQ_DRAINED ledger row; a re-drain
    finds nothing (idempotent). No lost action, no pretended success.
  Drill 3 — LLM outage (app.chaos.llm_outage): one run_recovery_cycle per
    episode through the REAL OpenAICompatibleClient aimed at a dead
    base_url (socket-free transport raising timeout/500 like a
    real dead endpoint) → every decision DEGRADED with
    tier-appropriate fallback variants, the dead consult evidenced in the
    ledger (request hash present, model output absent), and CONTROL
    blocked at the cohort gate with zero outreach writes — policy intact
    during the outage, per-episode independence throughout.

Quiet-hours note: during REAL quiet hours this drill shows
BLOCKED-after-DEGRADED (proven live); the demo
freezes a daytime clock — 10:00 UTC == 15:30 IST, outreach window open —
so the run deterministically shows dispatched DEGRADED. The freeze forces
quiet-hours COMPLIANCE for the demo, it does not disable the rule (the
window logic itself is covered by tests/test_policy.py).

The run closes with verify_chain over the WHOLE ledger — every drill's
evidence lands in one hash chain — plus a reconciliation of row
arithmetic. Exit code: 0 iff every assertion passes; any failure prints
DEMO FAILED and exits non-zero."""

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # allow direct execution: python scripts/demo_d4.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.actions import execute as execute_module
from app.actions.execute import drain_dlq, execute_episode_action
from app.actions.recovery_link import RecordingStub, RecoveryLinkActionClient
from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain
from app.chaos.faults import FaultyActionClient
from app.chaos.llm_outage import (
    DEAD_BASE_URL,
    OUTAGE_MODEL_LABEL,
    dead_endpoint_client,
    run_outage_drill,
)
from app.chaos.replay import fire_replay_storm
from app.core.episodes import create_episode, get_episode, transition
from app.db import connect, init_db
from app.policy import engine
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"
HALT_EPOCH = int(datetime(2026, 8, 28, 5, 0, 0, tzinfo=timezone.utc).timestamp())
# 10:00 UTC == 15:30 IST — daytime, the outreach window is open.
FROZEN_NOW = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)
# Demo-only webhook secret (NOT a credential): the storm fires signature-
# valid deliveries, so the receiver needs a non-empty secret — pinned here
# so a local .env can never flip this run.
DEMO_WEBHOOK_SECRET = "demo_d4_storm_signing_secret"
STORM_IDENTICAL = 25  # + 5 shuffled-key variants = 30 deliveries (storm shape)

# Drill 2: (suffix, error codes, FaultyActionClient fail_first). RETRY
# recovers inside the retry budget; DLQ exhausts it and must drain later.
DRILL2_SCENARIO = (
    ("RETRY", ["GATEWAY_ERROR"], 2),
    ("DLQ", ["CARD_DECLINED"], 3),
)
# Drill 3: (suffix, cohort, error codes) → tiers 1/2/3 + one CONTROL.
DRILL3_SCENARIO = (
    ("T1", "TREATMENT", ["GATEWAY_ERROR"]),
    ("T2", "TREATMENT", ["CARD_DECLINED"]),
    ("T3", "TREATMENT", ["GATEWAY_ERROR"] * 3),
    ("C1", "CONTROL", ["GATEWAY_ERROR"]),
)

# Deterministic whole-ledger arithmetic: 7 creations + 6 diagnosed
# + 6 scored + 4 sent + 1 gated + 1 DLQ_DRAINED = 25 hash-chained rows.
EXPECTED_OUTCOMES = {
    "EPISODE_CREATED": 7,
    "EPISODE_DIAGNOSED": 6,
    "EPISODE_SCORED": 6,
    "EPISODE_SENT": 4,
    "EPISODE_GATED": 1,
    "DLQ_DRAINED": 1,
}


def _frozen_now() -> datetime:
    """Stand-in for the engine's clock hook — always the fixed daytime instant."""
    return FROZEN_NOW


def _halt_payload(sub_id: str) -> dict[str, Any]:
    """The storm's base delivery: one subscription.halted at HALT_EPOCH."""
    return {
        "event": "subscription.halted",
        "created_at": HALT_EPOCH,
        "payload": {"subscription": {"entity": {"id": sub_id, "status": "halted"}}},
    }


def _seed_events(conn, sub_id: str, error_codes: list[str]) -> None:
    """Insert synthetic payment.failed evidence; insertion order REVERSED
    versus occurrence — events arrive out of order (proven live in D1)."""
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
            (f"demo_d4_{sub_id}_{offset}", sub_id, ts, ts, json.dumps(payload)),
        )


def _drill1_replay_storm(conn) -> dict[str, Any]:
    """30 replays of one halt delivery against an already-open episode."""
    print()
    print("--- Drill 1 — webhook replay storm (ingest idempotency under fire) ---")
    print(f"{'metric':<34} {'value':>8}")
    print("-" * 100)

    sub_id = "sub_D4STORM"
    create_episode(conn, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort="TREATMENT")
    conn.commit()  # the halt is already a live episode — the storm must not fork it

    storm = fire_replay_storm(conn, _halt_payload(sub_id), deliveries=STORM_IDENTICAL)
    conn.commit()

    events = conn.execute("SELECT COUNT(*) AS c FROM webhook_events").fetchone()["c"]
    archives = sum(
        1 for p in get_settings().archive_dir.rglob("*") if p.is_file()
    )
    episodes = conn.execute(
        "SELECT COUNT(*) AS c FROM episodes WHERE subscription_id = ?", (sub_id,)
    ).fetchone()["c"]
    for label, value in (
        ("deliveries fired (25+5 shuffled)", storm["deliveries"]),
        ("accepted", storm["accepted"]),
        ("duplicates absorbed", storm["duplicates"]),
        ("webhook_events rows", events),
        ("archive files", archives),
        ("episodes for the subscription", episodes),
    ):
        print(f"{label:<34} {value:>8}")

    assert storm["deliveries"] == 30, f"expected 30 deliveries, got {storm['deliveries']}"
    assert storm["identical"] == 25 and storm["shuffled_variants"] == 5
    assert storm["accepted"] == 1 and storm["duplicates"] == 29, (
        f"unexpected dedupe split: {storm['accepted']}/{storm['duplicates']}"
    )
    assert events == 1, f"storm minted {events} webhook_events rows, expected 1"
    assert archives == 30, f"expected 30 archive files (every delivery), got {archives}"
    assert episodes == 1, f"storm left {episodes} episodes, expected exactly 1"
    print("DRILL 1 VERDICT: PASS — 1 row, 30 archives, 1 episode; ingest inert under fire")
    return {"events": events, "archives": archives, "episodes": episodes, "storm": storm}


def _drill2_five_xx(conn, sleeps: list[float]) -> dict[str, Any]:
    """5xx mid-action: backoff to success (RETRY) and DLQ + drain (DLQ)."""
    print()
    print("--- Drill 2 — Razorpay 5xx mid-action (backoff → DLQ → no lost action) ---")
    print(
        f"{'sub':<14} {'client':<12} {'wire':>5} {'backoff':<10} "
        f"{'dispatch':<9} {'dlq':<9} {'drain':>6}"
    )
    print("-" * 100)

    outcomes: dict[str, Any] = {}
    for suffix, codes, fail_first in DRILL2_SCENARIO:
        sub_id = f"sub_D4R_{suffix}"
        ep = create_episode(conn, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort="TREATMENT")
        ep = transition(conn, ep["id"], "DIAGNOSED")
        ep = transition(conn, ep["id"], "SCORED")
        conn.commit()

        faulty = FaultyActionClient(RecoveryLinkActionClient(client=None), fail_first=fail_first)
        sleeps_before = len(sleeps)
        result = execute_episode_action(conn, ep, client=faulty)
        conn.commit()
        backoff = sleeps[sleeps_before:]

        if fail_first < 3:
            # Recovered inside the retry budget: one clean dispatch, no DLQ.
            assert result["dispatched"] is True, f"{sub_id}: dispatch failed outright"
            assert "dlq" not in result, f"{sub_id}: quarantined despite recovering"
            assert faulty.calls == 3, f"{sub_id}: expected 3 wire calls, got {faulty.calls}"
            assert backoff == [0.2, 0.4], f"{sub_id}: backoff pattern {backoff}"
            after = get_episode(conn, ep["id"])
            assert after["state"] == "SENT" and after["attempt_count"] == 1
            dlq_rows = conn.execute("SELECT COUNT(*) AS c FROM dlq").fetchone()["c"]
            assert dlq_rows == 0, f"{sub_id}: {dlq_rows} DLQ rows after a clean send"
            print(
                f"{sub_id:<14} {'5xx×2→ok':<12} {faulty.calls:>5} "
                f"{'0.2/0.4':<10} {'SENT':<9} {'0 rows':<9} {'-':>6}"
            )
            outcomes[suffix] = {"result": result, "calls": faulty.calls, "backoff": backoff}
            continue

        # Exhausted: DLQ row + SENT in one transaction; then a healthy drain.
        assert result["dispatched"] is True, f"{sub_id}: exhausted retries lost the action"
        assert result["dlq"]["id"].startswith("dlq_"), f"{sub_id}: no DLQ id: {result}"
        assert faulty.calls == 3 and backoff == [0.2, 0.4], (
            f"{sub_id}: wire {faulty.calls}, backoff {backoff}"
        )
        row = conn.execute(
            "SELECT status, retry_count, payload_json FROM dlq WHERE id = ?",
            (result["dlq"]["id"],),
        ).fetchone()
        assert row is not None and row["status"] == "PENDING" and row["retry_count"] == 0
        after = get_episode(conn, ep["id"])
        assert after["state"] == "SENT" and after["attempt_count"] == 1, (
            f"{sub_id}: quarantined dispatch must still count and reach SENT"
        )

        first = drain_dlq(conn, RecordingStub())
        conn.commit()
        assert first == {"found": 1, "drained": 1, "failed": 0}, f"{sub_id}: drain {first}"
        second = drain_dlq(conn, RecordingStub())
        assert second == {"found": 0, "drained": 0, "failed": 0}, f"{sub_id}: re-drain {second}"
        row = conn.execute(
            "SELECT status, retry_count FROM dlq WHERE id = ?", (result["dlq"]["id"],)
        ).fetchone()
        assert row["status"] == "DRAINED" and row["retry_count"] == 1
        outcomes_for_sub = [
            r["outcome"]
            for r in iter_rows(conn)
            if r["subscription_id"] == sub_id
        ]
        assert outcomes_for_sub.count("EPISODE_SENT") == 1
        assert outcomes_for_sub.count("DLQ_DRAINED") == 1, (
            f"{sub_id}: ledger must narrate the drain: {outcomes_for_sub}"
        )
        print(
            f"{sub_id:<14} {'5xx×3':<12} {faulty.calls:>5} "
            f"{'0.2/0.4':<10} {'SENT':<9} {'PENDING→DRAINED':<9} {'1/1':>6}"
        )
        outcomes[suffix] = {"result": result, "calls": faulty.calls, "backoff": backoff}

    print(
        "DRILL 2 VERDICT: PASS — backoff then success (0 DLQ rows) | exhausted → "
        "DLQ + SENT, drained + re-drain idempotent"
    )
    return outcomes


def _drill3_llm_outage(conn) -> dict[str, Any]:
    """Dead-endpoint LLM outage: DEGRADED everywhere, policy intact."""
    print()
    print("--- Drill 3 — LLM outage (real OpenAICompatibleClient → dead base_url) ---")
    print(f"outage client: {OUTAGE_MODEL_LABEL} @ {DEAD_BASE_URL} (socket-free transport)")
    print(
        f"{'sub':<14} {'cohort':<10} {'tier':>4} {'status':<11} "
        f"{'mode':<9} {'variant':<9} {'reason':<18}"
    )
    print("-" * 100)

    subs = []
    for suffix, cohort, codes in DRILL3_SCENARIO:
        sub_id = f"sub_D4L_{suffix}"
        _seed_events(conn, sub_id, codes)
        create_episode(conn, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort=cohort)
        subs.append(sub_id)
    conn.commit()

    dead = dead_endpoint_client()
    result = run_outage_drill(conn, subs, dead)
    conn.commit()

    for sub_id in subs:
        summary = result["summaries"][sub_id]
        print(
            f"{sub_id:<14} {(get_episode(conn, summary['episode_id'])['cohort'] or '-'):<10} "
            f"{summary['tier']!s:>4} {summary['status']:<11} "
            f"{summary['mode'] or '-':<9} {summary['variant'] or '-':<9} "
            f"{(summary['reason'] or '')[:17]:<18}"
        )

    assert result["dispatched"] == ["sub_D4L_T1", "sub_D4L_T2"], result["dispatched"]
    assert result["gated"] == ["sub_D4L_T3"], "tier 3 must gate regardless of the model"
    assert result["blocked"] == ["sub_D4L_C1"], "CONTROL must stay blocked during outage"
    assert result["summaries"]["sub_D4L_T1"]["variant"] == "gentle"
    assert result["summaries"]["sub_D4L_T2"]["variant"] == "standard"
    assert result["degraded_rows"] == result["cycle_rows"] == 11, (
        f"expected 11 DEGRADED cycle rows, got {result['degraded_rows']}/{result['cycle_rows']}"
    )
    assert len(dead.transport_hits) == 8, (
        f"expected 8 dead-wire hits (4 consults × attempt+retry), got {len(dead.transport_hits)}"
    )
    dead.close()
    print(
        "DRILL 3 VERDICT: PASS — all decisions DEGRADED, tier-appropriate fallbacks, "
        "CONTROL blocked (policy intact during outage)"
    )
    print(
        "quiet-hours note: in REAL quiet hours this drill shows BLOCKED-after-DEGRADED "
        "(proven live); this demo freezes a daytime "
        "clock so the story deterministically shows dispatched DEGRADED."
    )
    return result


def _verify_chain_and_reconcile(conn) -> None:
    """verify_chain over the whole ledger + exact row arithmetic."""
    print()
    print("--- Ledger — verify_chain over the whole run ---")
    rows = list(iter_rows(conn))
    chain_ok, chain_detail = verify_chain(rows)
    counts = {outcome: 0 for outcome in EXPECTED_OUTCOMES}
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    breakdown = " + ".join(f"{n} {name}" for name, n in counts.items() if n)
    print(f"ledger: {len(rows)} rows = {breakdown}")
    print(f"audit chain: {'OK: ' if chain_ok else 'FAIL: '}{chain_detail}")
    assert counts == EXPECTED_OUTCOMES, f"row arithmetic drifted: {counts}"
    assert chain_ok, f"hash chain broken: {chain_detail}"


def main() -> int:
    settings = get_settings()
    settings.data_dir = Path(tempfile.mkdtemp(prefix="vaapsi_demo_d4_"))
    settings.kill_switch = False  # hermetic: a local .env must not flip this demo
    settings.razorpay_webhook_secret = DEMO_WEBHOOK_SECRET

    # Clock injection (see module docstring): pin the engine's only time
    # source to a fixed daytime instant so quiet-hours can never fire here.
    engine._now_utc = _frozen_now
    # Backoff capture: the executor's sleep seam records instead of sleeping,
    # so the demo proves the 0.2s/0.4s pattern without burning real time.
    sleeps: list[float] = []
    execute_module._sleep = sleeps.append

    conn = connect()
    try:
        init_db(conn)
        print("=" * 100)
        print("Vaapsi D4 acceptance demo — all three failure drills, offline + deterministic")
        print("=" * 100)
        _drill1_replay_storm(conn)
        _drill2_five_xx(conn, sleeps)
        _drill3_llm_outage(conn)
        _verify_chain_and_reconcile(conn)
        print()
        print("DEMO PASSED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"DEMO FAILED: {exc}")
        sys.exit(1)
