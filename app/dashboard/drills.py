"""Drill runners for the /drills console (D8) — isolated, bounded, honest.

Why this module: the drills console needs to RUN the three D4 chaos drills
(replay storm, gateway 5xx, LLM outage) from a dashboard button without
ever touching the live store. The law here is REUSE: every drill delegates
to the exact tested functions the D4 tests drive —

- replay storm  → app.chaos.replay.fire_replay_storm (the pure
  process_webhook seam, 30 signature-valid deliveries)
- gateway 5xx   → app.actions.execute.execute_episode_action over
  app.chaos.faults.FaultyActionClient (real httpx.HTTPStatusError 500/503,
  backoff captured via the executor's _sleep seam)
- LLM outage    → app.chaos.llm_outage.run_outage_drill over
  dead_endpoint_client() (the REAL adapter against a dead base_url,
  socket-free)

Nothing drill-shaped is reimplemented here. Each run gets a FRESH
throwaway SQLite store in its own temp directory (created and cleaned up
inside the same call — idempotent by construction), and the few global
seams the drills need (archive dir, drill webhook secret, engine clock,
sleep clock) are swapped and restored in ``finally`` blocks so the live
process state survives every drill, pass or fail. A drill that fails
RAISES — the dispatcher turns the exception into the console's honest
red result with the error text verbatim. Synchronous and bounded: every
runner is offline, socket-free and finishes in well under 30 seconds.
"""

import json
import sqlite3
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.actions import execute as execute_module
from app.actions.execute import execute_episode_action
from app.actions.recovery_link import RecoveryLinkActionClient
from app.chaos.faults import FaultyActionClient
from app.chaos.llm_outage import dead_endpoint_client, run_outage_drill
from app.chaos.replay import fire_replay_storm
from app.core.episodes import create_episode, transition
from app.db import init_db
from app.policy import engine as policy_engine
from app.settings import get_settings

# The drills only ever know these ids; the console renders the catalog.
DRILLS: tuple[dict[str, str], ...] = (
    {
        "drill_id": "replay_storm",
        "title": "Replay storm",
        "description": (
            "Fires 30 signature-valid duplicate webhook deliveries (25 identical + "
            "5 shuffled-key variants, jittered timestamps inside one 5-minute "
            "idempotency window) through the pure ingest seam on an isolated "
            "store, and proves idempotency: exactly 1 webhook_events row, one "
            "archive file per delivery, zero recovery episodes."
        ),
    },
    {
        "drill_id": "gateway_5xx",
        "title": "Gateway 5xx",
        "description": (
            "Drives outreach through FaultyActionClient — real "
            "httpx.HTTPStatusError 500/503 at the ActionClient seam. Two fails "
            "then success: exactly 1 dispatch, 0 DLQ rows, backoff 0.2s/0.4s. "
            "Three fails: the payload is quarantined to the DLQ (PENDING) and "
            "the episode still transitions SENT in the same transaction."
        ),
    },
    {
        "drill_id": "llm_outage",
        "title": "LLM outage",
        "description": (
            "Drives full recovery cycles through the REAL OpenAI-compatible "
            "adapter aimed at a dead endpoint (socket-free MockTransport): "
            "every decision degrades to the rules-only fallback with DEGRADED "
            "stamped in the ledger, and the cohort gate still blocks CONTROL "
            "with zero outreach writes."
        ),
    },
)

# Signature-valid deliveries need a secret; the drill store is throwaway,
# so the drill carries its own (never read from or written to .env).
DRILL_WEBHOOK_SECRET = "vaapsi-drill-secret-0123456789abcdef"

# 10:00 UTC == 15:30 IST — the outreach window is open at this instant, so
# quiet-hours can never flip a drill verdict by accident of wall-clock time.
FROZEN_NOW = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)

HALT_TS = "2026-08-28T05:00:00+00:00"

# Error-code streaks → deterministic tiers (scorecard rules, first match):
# 1 transient → tier 1, non-transient → tier 2, 3 straight → tier 3.
_OUTAGE_SCENARIOS: tuple[tuple[str, str, list[str]], ...] = (
    ("sub_DRILL_T1", "TREATMENT", ["GATEWAY_ERROR"]),
    ("sub_DRILL_T2", "TREATMENT", ["CARD_DECLINED"]),
    ("sub_DRILL_T3", "TREATMENT", ["GATEWAY_ERROR"] * 3),
    ("sub_DRILL_C1", "CONTROL", ["GATEWAY_ERROR"]),
)

# In-memory last-run record per drill (console "last run" readout). Server
# process state only — never persisted, the drills themselves are stateless.
_LAST_RUNS: dict[str, dict[str, Any] | None] = {}


class UnknownDrillError(LookupError):
    """The drill id is not one of the catalog's three drills."""


def _isolated_conn(store_path: Path) -> sqlite3.Connection:
    """A fresh schema-complete SQLite store for exactly one drill run."""
    conn = sqlite3.connect(store_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    init_db(conn)
    return conn


def _seed_failed_payments(conn: sqlite3.Connection, sub_id: str, codes: list[str]) -> None:
    """Synthetic payment.failed evidence, insertion REVERSED vs occurrence —
    the same out-of-order discipline tests/test_chaos_llm_outage.py pins."""
    for offset, code in enumerate(codes):
        minute = len(codes) - offset
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
            (f"drill_{sub_id}_{offset}", sub_id, ts, ts, json.dumps(payload)),
        )


def _run_replay_storm() -> tuple[str, dict[str, Any]]:
    """Drill 1 verbatim: fire_replay_storm on an isolated store.

    The storm needs the drill secret plus a throwaway archive dir (every
    delivery is archived raw) — patched on the settings singleton and
    restored in finally.
    """
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="vaapsi-drill-storm-") as td:
        tmp = Path(td)
        conn = _isolated_conn(tmp / "store.sqlite3")
        try:
            old_data, old_archive, old_secret = (
                settings.data_dir,
                settings.archive_dir,
                settings.razorpay_webhook_secret,
            )
            settings.data_dir = tmp
            settings.archive_dir = tmp / "webhook_archive"
            settings.razorpay_webhook_secret = DRILL_WEBHOOK_SECRET
            try:
                base = {
                    "event": "subscription.halted",
                    "created_at": int(time.time()),
                    "payload": {
                        "subscription": {"entity": {"id": "sub_DRILL_STORM", "status": "halted"}}
                    },
                }
                counts = fire_replay_storm(conn, base)
                conn.commit()
            finally:
                settings.data_dir = old_data
                settings.archive_dir = old_archive
                settings.razorpay_webhook_secret = old_secret
        finally:
            conn.close()
    summary = (
        f"{counts['deliveries']} deliveries → {counts['webhook_rows']} webhook row "
        f"({counts['accepted']} accepted, {counts['duplicates']} duplicates), "
        f"{counts['archived']} archived, {counts['episodes_for_subscription']} episodes"
    )
    return summary, dict(counts)


def _run_gateway_5xx() -> tuple[str, dict[str, Any]]:
    """Drill 2 verbatim: FaultyActionClient at the ActionClient seam.

    Sleeps are captured through the executor's _sleep clock seam (never
    slept — bounded runtime), and the engine clock is frozen at a daytime
    instant so quiet-hours stay out, exactly like tests do.
    """
    sleeps: list[float] = []
    with tempfile.TemporaryDirectory(prefix="vaapsi-drill-5xx-") as td:
        conn = _isolated_conn(Path(td) / "store.sqlite3")
        try:
            old_sleep = execute_module._sleep
            old_now = policy_engine._now_utc
            execute_module._sleep = sleeps.append
            policy_engine._now_utc = lambda: FROZEN_NOW
            try:
                ep_fast = create_episode(
                    conn, subscription_id="sub_DRILL_5XX_FAST", halt_ts_utc=HALT_TS,
                    cohort="TREATMENT",
                )
                ep_fast = transition(conn, ep_fast["id"], "DIAGNOSED")
                ep_fast = transition(conn, ep_fast["id"], "SCORED")
                faulty_fast = FaultyActionClient(
                    RecoveryLinkActionClient(client=None), fail_first=2
                )
                result_fast = execute_episode_action(conn, ep_fast, client=faulty_fast)

                ep_dlq = create_episode(
                    conn, subscription_id="sub_DRILL_5XX_DLQ", halt_ts_utc=HALT_TS,
                    cohort="TREATMENT",
                )
                ep_dlq = transition(conn, ep_dlq["id"], "DIAGNOSED")
                ep_dlq = transition(conn, ep_dlq["id"], "SCORED")
                faulty_dlq = FaultyActionClient(
                    RecoveryLinkActionClient(client=None), fail_first=3
                )
                result_dlq = execute_episode_action(conn, ep_dlq, client=faulty_dlq)
                conn.commit()
            finally:
                execute_module._sleep = old_sleep
                policy_engine._now_utc = old_now

            # ── drill invariants (the whole point of the drill) ─────────
            assert result_fast["dispatched"] is True, "healthy-after-2 run did not dispatch"
            assert "dlq" not in result_fast, "recovered-within-budget run quarantined a DLQ row"
            assert faulty_fast.calls == 3, (
                f"expected 3 wire calls (2 fails + success), got {faulty_fast.calls}"
            )
            assert sleeps == [0.2, 0.4], f"backoff drifted: {sleeps}"
            assert result_dlq["dispatched"] is True
            assert result_dlq["dlq"]["id"].startswith("dlq_")
            assert "500" in result_dlq["dlq"]["error"]
            dlq_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM dlq WHERE status = 'PENDING'"
            ).fetchone()["c"]
            assert dlq_rows == 1, f"expected 1 PENDING DLQ row, got {dlq_rows}"
            sent_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM audit_ledger WHERE outcome = 'EPISODE_SENT'"
            ).fetchone()["c"]
            assert sent_rows == 2, f"expected 2 SENT ledger rows, got {sent_rows}"

        finally:
            conn.close()
    summary = (
        "2×5xx then success → 1 dispatch, 0 DLQ, backoff 0.2s/0.4s · "
        "3×5xx exhausted → DLQ row PENDING + episode SENT, payload kept byte-true"
    )
    evidence = {
        "fail_then_success": {
            "wire_calls": faulty_fast.calls,
            "backoff_seconds": sleeps,
            "dispatched": True,
            "dlq_rows": 0,
        },
        "fail_exhausted": {
            "wire_calls": faulty_dlq.calls,
            "dlq_id": result_dlq["dlq"]["id"],
            "dispatch_error": result_dlq["dlq"]["error"],
            "dlq_rows_pending": 1,
            "sent_ledger_rows": 2,
        },
    }
    return summary, evidence


def _run_llm_outage() -> tuple[str, dict[str, Any]]:
    """Drill 3 verbatim: run_outage_drill over the dead-endpoint adapter.

    3 TREATMENT episodes (tiers 1/2/3) + 1 CONTROL prove both the DEGRADED
    fallbacks and that policy still blocks CONTROL mid-outage. Engine clock
    frozen daytime; the transport is MockTransport — zero sockets.
    """
    with tempfile.TemporaryDirectory(prefix="vaapsi-drill-llm-") as td:
        conn = _isolated_conn(Path(td) / "store.sqlite3")
        try:
            old_now = policy_engine._now_utc
            policy_engine._now_utc = lambda: FROZEN_NOW
            client = dead_endpoint_client()
            try:
                subs: list[str] = []
                for sub_id, cohort, codes in _OUTAGE_SCENARIOS:
                    _seed_failed_payments(conn, sub_id, codes)
                    create_episode(
                        conn, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort=cohort
                    )
                    subs.append(sub_id)
                result = run_outage_drill(conn, subs, client)
                conn.commit()
            finally:
                client.close()
                policy_engine._now_utc = old_now
        finally:
            conn.close()
    summary = (
        f"{result['episodes']} episodes through a dead LLM endpoint → "
        f"{len(result['dispatched'])} dispatched + {len(result['gated'])} gated "
        f"(rules-only fallbacks, DEGRADED stamped), {len(result['blocked'])} CONTROL "
        f"blocked — {result['degraded_rows']}/{result['cycle_rows']} cycle rows DEGRADED"
    )
    evidence = {
        "episodes": result["episodes"],
        "dispatched": result["dispatched"],
        "gated": result["gated"],
        "blocked": result["blocked"],
        "cycle_rows": result["cycle_rows"],
        "degraded_rows": result["degraded_rows"],
        "llm_model": result["llm_model"],
        "dead_endpoint_transport_hits": len(getattr(client, "transport_hits", [])),
    }
    return summary, evidence


_RUNNERS: dict[str, Callable[[], tuple[str, dict[str, Any]]]] = {
    "replay_storm": _run_replay_storm,
    "gateway_5xx": _run_gateway_5xx,
    "llm_outage": _run_llm_outage,
}


def run_drill(drill_id: str) -> dict[str, Any]:
    """Run one drill synchronously on its own isolated store.

    Returns {drill_id, passed, summary, evidence, ran_ts_utc, duration_ms}.
    Any exception (drill invariants included) becomes passed=False with the
    error text verbatim — an honest red result, never a fake green. The
    result is recorded as the drill's last run for the console readout.
    """
    runner = _RUNNERS.get(drill_id)
    if runner is None:
        raise UnknownDrillError(f"no drill with id {drill_id!r}")
    started = time.monotonic()
    ran_ts = datetime.now(timezone.utc).isoformat()
    try:
        summary, evidence = runner()
        result: dict[str, Any] = {
            "drill_id": drill_id,
            "passed": True,
            "summary": summary,
            "evidence": evidence,
            "ran_ts_utc": ran_ts,
        }
    except Exception as exc:  # noqa: BLE001 - the console's contract is honesty
        result = {
            "drill_id": drill_id,
            "passed": False,
            "summary": f"drill failed: {type(exc).__name__}",
            "evidence": {"error": str(exc)},
            "ran_ts_utc": ran_ts,
        }
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    _LAST_RUNS[drill_id] = result
    return dict(result)


def catalog() -> list[dict[str, Any]]:
    """The drill cards: id, title, description, last-run record (or None)."""
    return [
        {
            "drill_id": drill["drill_id"],
            "title": drill["title"],
            "description": drill["description"],
            "last_run": _LAST_RUNS.get(str(drill["drill_id"])),
        }
        for drill in DRILLS
    ]
