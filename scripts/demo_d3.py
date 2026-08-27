"""Acceptance demo for the scoring/decision path: score, guarded LLM, human gate, and the DEGRADED fallback.

Offline and deterministic (house pattern from demo_d2.py): builds a
throwaway DB in a temp dir, inserts synthetic payment.failed events, and
drives TWO phases of 6 synthetic halted episodes each (4 TREATMENT +
2 CONTROL) through run_recovery_cycle — the full D3 pipeline:

  Phase 1 — FakeLLM injected (schema-valid, tier-flavored replies):
    3 TREATMENT episodes dispatch with mode='NORMAL' and tier-appropriate
    variants (tier 1 → gentle, tier 2 → standard); the tier-3 TREATMENT
    episode is enqueued to the approvals table (the model's recommendation
    is overridden by the deterministic escalation); both CONTROL episodes
    are blocked at the cohort gate with zero outreach writes.
  Phase 2 — client=None on six FRESH halts of the same scenario (the
    previous episodes are still open — one recovery cycle per halt, so a
    second pass means new halts): every decision is the rules-only
    fallback, so every row the cycle writes in this phase carries
    mode='DEGRADED', and the routing is identical — still policy-
    compliant (same tiers, same gate, CONTROL still blocked, caps hold).

Determinism — clock injection: the policy engine consults the wall clock
(cooling, 48h cap, quiet hours); the demo pins engine._now_utc (the
engine's only time source) to a fixed daytime instant — 10:00 UTC ==
15:30 IST, outreach window open — so the run is identical on any machine
at any hour and can never bounce off quiet-hours. This forces quiet-hours
COMPLIANCE for the demo, it does not disable the rule: the window logic
itself is covered by tests/test_policy.py. The kill switch is forced off
so a local .env cannot flip this run.

Exit code: 0 iff every assertion passes; any failure prints DEMO FAILED
and exits non-zero."""

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # allow direct execution: python scripts/demo_d3.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain
from app.core.episodes import create_episode, get_episode
from app.db import connect, init_db
from app.orchestrator import run_recovery_cycle
from app.policy import engine
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"
# 10:00 UTC == 15:30 IST — daytime, the outreach window is open.
FROZEN_NOW = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)

# Per-batch scenario: (suffix, cohort, failure error codes). The failure
# streak deterministically sets the tier: 1 transient failure → tier 1
# (gentle), a non-transient code or 2 failures → tier 2 (standard),
# 3 consecutive failures → tier 3 (human gate).
SCENARIO = (
    ("A", "TREATMENT", ["GATEWAY_ERROR"]),
    ("B", "TREATMENT", ["CARD_DECLINED"]),
    ("C", "TREATMENT", ["NETWORK_ERROR", "NETWORK_ERROR"]),
    ("D", "TREATMENT", ["GATEWAY_ERROR"] * 3),
    ("E", "CONTROL", ["GATEWAY_ERROR"]),
    ("F", "CONTROL", ["CARD_DECLINED"]),
)
DISPATCHED_SUFFIXES = ("A", "B", "C")
GATED_SUFFIX = "D"
BLOCKED_SUFFIXES = ("E", "F")
# Every synthetic episode lands 3 pipeline rows (creation + diagnosed +
# scored); episodes the cycle ACTS on land exactly one action row more
# (sent or gated). Blocked CONTROL episodes write zero action rows —
# outreach that never happened must not look like one that did.
PIPELINE_ROWS = 3
EXPECTED_TOTAL_ROWS = 44  # 2 phases × (6×3 pipeline + 3 sent + 1 gated)

# Outcomes the recovery CYCLE writes (vs the event layer's creation row):
# the mode taxonomy labels decisions, so phase-2 assertions cover these.
CYCLE_OUTCOMES = ("EPISODE_DIAGNOSED", "EPISODE_SCORED", "EPISODE_SENT", "EPISODE_GATED")


def _frozen_now() -> datetime:
    """Stand-in for the engine's clock hook — always the fixed daytime instant."""
    return FROZEN_NOW


class FakeLLM:
    """Schema-valid, tier-flavored recommender — the NORMAL-mode stand-in.

    Returns exactly the dict contract the real adapter exposes
    ({'action', 'channel', 'message_variant', 'raw'}), allowlist-clean.
    It records every payload it is shown so the demo can prove the model
    only ever saw structured untrusted data. It never decides the gate:
    the orchestrator routes tier 3 to the human gate regardless.
    """

    model_name = "fake-llm-demo"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def recommend(self, payload: dict) -> dict:
        self.calls.append(payload)
        variant = "gentle" if payload["tier"] == 1 else "standard"
        recommendation = {
            "action": "send_payment_link",
            "channel": "payment_link",
            "message_variant": variant,
        }
        return {**recommendation, "raw": dict(recommendation)}


def _seed_events(conn, sub_id: str, error_codes: list[str]) -> None:
    """Insert the synthetic payment.failed evidence for one subscription.

    Occurrence timestamps are minutes apart; insertion order is REVERSED
    versus occurrence on purpose — events arrive out of order (proven live
    in D1), so the scorer must order by occurrence, never by insertion.
    """
    for offset, code in enumerate(error_codes):
        minute = len(error_codes) - offset  # reversed arrival vs occurrence
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
            (f"demo_{sub_id}_{offset}", sub_id, ts, ts, json.dumps(payload)),
        )


def _run_phase(conn, phase_label: str, client) -> list[dict]:
    """Create + drive one batch of 6 synthetic halted episodes; print the table."""
    phase_tag = "1" if "Phase 1" in phase_label else "2"
    print(f"\n--- {phase_label} ---")
    print(
        f"{'sub':<15} {'cohort':<10} {'tier':>4} {'status':<11} "
        f"{'mode':<9} {'variant':<9} {'reason':<18} {'attempts':>8} {'rows':>5}"
    )
    print("-" * 100)
    summaries = []
    for suffix, cohort, error_codes in SCENARIO:
        sub_id = f"sub_D3{phase_tag}_{suffix}"
        _seed_events(conn, sub_id, error_codes)
        create_episode(conn, subscription_id=sub_id, halt_ts_utc=HALT_TS, cohort=cohort)
        conn.commit()
        summary = run_recovery_cycle(conn, sub_id, client)
        after = get_episode(conn, summary["episode_id"])
        rows = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_ledger WHERE subscription_id = ?",
            (sub_id,),
        ).fetchone()["c"]
        print(
            f"{sub_id:<15} {cohort:<10} {summary['tier']!s:>4} {summary['status']:<11} "
            f"{summary['mode'] or '-':<9} {summary['variant'] or '-':<9} "
            f"{(summary['reason'] or '')[:17]:<18} {after['attempt_count']:>8} {rows:>5}"
        )
        summaries.append(summary)
    conn.commit()
    return summaries


def _assert_row_arithmetic(conn, phase_tag: str) -> None:
    """Each synthetic episode: exactly PIPELINE_ROWS (+1 when the cycle acted)."""
    for suffix, _, _ in SCENARIO:
        sub_id = f"sub_D3{phase_tag}_{suffix}"
        rows = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_ledger WHERE subscription_id = ?",
            (sub_id,),
        ).fetchone()["c"]
        acted = 0 if suffix in BLOCKED_SUFFIXES else 1
        expected = PIPELINE_ROWS + acted
        assert rows == expected, f"{sub_id}: expected {expected} ledger rows, got {rows}"


def main() -> int:
    settings = get_settings()
    settings.data_dir = Path(tempfile.mkdtemp(prefix="vaapsi_demo_d3_"))
    settings.kill_switch = False  # hermetic: a local .env must not flip this demo

    # Clock injection (see module docstring): pin the engine's only time
    # source to a fixed daytime instant so quiet-hours can never fire here.
    engine._now_utc = _frozen_now

    conn = connect()
    try:
        init_db(conn)
        print("=" * 100)
        print("Vaapsi D3 acceptance demo — offline, deterministic (stub Razorpay, fake LLM)")
        print("=" * 100)

        # ── Phase 1: FakeLLM in the loop → NORMAL decisions ─────────
        fake_llm = FakeLLM()
        phase1 = _run_phase(conn, "Phase 1 — LLM in the loop (FakeLLM)", fake_llm)

        dispatched1 = [s for s in phase1 if s["status"] == "dispatched"]
        gated1 = [s for s in phase1 if s["status"] == "gated"]
        blocked1 = [s for s in phase1 if s["status"] == "blocked"]
        assert len(dispatched1) == 3, f"expected 3 dispatched, got {len(dispatched1)}"
        assert len(gated1) == 1, f"expected 1 gated (tier 3), got {len(gated1)}"
        assert len(blocked1) == 2, f"expected 2 blocked (CONTROL), got {len(blocked1)}"
        assert all(
            s["mode"] == "NORMAL" for s in (*dispatched1, *gated1)
        ), "phase 1 decisions must carry mode NORMAL"

        # Tier-appropriate variants, read back from the ledger's LLM evidence.
        expected_variants = {"A": "gentle", "B": "standard", "C": "standard"}
        for suffix, variant in expected_variants.items():
            sub_id = f"sub_D31_{suffix}"
            (scored_row,) = [
                r
                for r in iter_rows(conn)
                if r["subscription_id"] == sub_id and r["outcome"] == "EPISODE_SCORED"
            ]
            assert scored_row["mode"] == "NORMAL"
            assert scored_row["llm_output_raw"]["message_variant"] == variant, (
                f"{sub_id}: expected variant {variant}, got {scored_row['llm_output_raw']}"
            )

        # The tier-3 episode is gated no matter what the model said.
        (tier3,) = [s for s in phase1 if s["tier"] == 3]
        assert tier3["status"] == "gated" and tier3["reason"] == "tier3_escalation"
        approval = conn.execute(
            "SELECT status, reason FROM approvals WHERE id = ?", (tier3["approval_id"],)
        ).fetchone()
        assert approval["status"] == "PENDING" and approval["reason"] == "tier3_escalation"

        # CONTROL: blocked at the cohort gate with zero outreach writes.
        for s in blocked1:
            after = get_episode(conn, s["episode_id"])
            assert s["reason"] == "cohort_gate", f"CONTROL blocked by cohort gate: {s}"
            assert after["state"] == "SCORED" and after["attempt_count"] == 0
            action_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM audit_ledger WHERE subscription_id = ? "
                "AND outcome IN ('EPISODE_SENT', 'EPISODE_GATED')",
                (s["subscription_id"],),
            ).fetchone()["c"]
            assert action_rows == 0, "CONTROL episode produced outreach evidence"
        _assert_row_arithmetic(conn, "1")

        # ── Phase 2: same scenario, no LLM → DEGRADED fallback ──────
        phase2 = _run_phase(conn, "Phase 2 — no LLM (client=None) → DEGRADED", None)

        dispatched2 = [s for s in phase2 if s["status"] == "dispatched"]
        gated2 = [s for s in phase2 if s["status"] == "gated"]
        blocked2 = [s for s in phase2 if s["status"] == "blocked"]
        assert len(dispatched2) == 3 and len(gated2) == 1 and len(blocked2) == 2, (
            "phase 2 must mirror phase 1's routing without an LLM"
        )
        phase2_subs = frozenset(f"sub_D32_{suffix}" for suffix, _, _ in SCENARIO)
        for row in iter_rows(conn):
            if row["subscription_id"] in phase2_subs and row["outcome"] in CYCLE_OUTCOMES:
                assert row["mode"] == "DEGRADED", (
                    f"phase 2 cycle row {row['outcome']} for {row['subscription_id']} "
                    f"carries mode {row['mode']!r}"
                )
        (tier3_p2,) = [s for s in phase2 if s["tier"] == 3]
        assert tier3_p2["status"] == "gated" and tier3_p2["mode"] == "DEGRADED"
        # Still policy-compliant in fallback mode: caps + cohort gate hold.
        for s in phase2:
            after = get_episode(conn, s["episode_id"])
            assert after["attempt_count"] <= 3, "cap violation in DEGRADED mode"
            if s["status"] == "blocked":
                assert after["attempt_count"] == 0, "blocked episode attempted outreach"
        _assert_row_arithmetic(conn, "2")

        # ── Reconciliation + chain verification ─────────────────────
        rows = list(iter_rows(conn))
        chain_ok, chain_detail = verify_chain(rows)
        total = len(rows)
        sent_rows = sum(1 for r in rows if r["outcome"] == "EPISODE_SENT")
        gated_rows = sum(1 for r in rows if r["outcome"] == "EPISODE_GATED")
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE status = 'PENDING'"
        ).fetchone()["c"]
        print("-" * 100)
        print(
            "phase 1 (NORMAL):   3 dispatched (gentle/standard/standard) | "
            "1 tier-3 gated | 2 CONTROL blocked, zero outreach writes"
        )
        print(
            "phase 2 (DEGRADED): 3 dispatched, same tier-appropriate fallback "
            "variants | 1 tier-3 gated | CONTROL still blocked"
        )
        print(f"approvals: {pending} PENDING (the tier-3 episode of each phase)")
        print(
            f"ledger reconciliation: {total} rows = 12 creation + 12 diagnosed "
            f"+ 12 scored + {sent_rows} sent + {gated_rows} gated"
        )
        print(f"audit chain: {'OK: ' if chain_ok else 'FAIL: '}{chain_detail}")
        assert pending == 2, f"expected 2 PENDING approvals, got {pending}"
        assert (sent_rows, gated_rows, total) == (6, 2, 44), (
            f"unexpected arithmetic: sent={sent_rows} gated={gated_rows} total={total}"
        )
        assert chain_ok, f"hash chain broken: {chain_detail}"
        assert len(fake_llm.calls) == 6, (  # every phase-1 episode: decide before gate
            f"FakeLLM consulted {len(fake_llm.calls)} times, expected 6"
        )
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
