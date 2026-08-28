"""Drives the halt-to-episode consumer against the live pipeline database.

Unlike demo_d2/d3 (offline, synthetic), this one uses the REAL DB
(data/vaapsi.sqlite3): the whole point of D6 is that `subscription.halted`
events landed there live while nothing consumed them — run_recovery_cycle
kept answering no_open_episode. This demo closes that gap end to end:

  1. Find every TREATMENT subscription with a stored `subscription.halted`
     event and no OPEN episode → call maybe_create_episode(conn, event_row)
     on the newest halt row of each → print the episode table. Idempotent:
     subs that already have an episode are filtered out up front, and the
     consumer's partial-unique-index guarantee makes double-creates
     impossible even on overlap.
  2. Drive run_recovery_cycle for the FIRST created episode ONLY — with
     the real LLM client (OpenAICompatibleClient) and the real
     Razorpay-backed ActionClient (RecoveryLinkActionClient over
     RazorpayClient test keys) — the live D6 pipeline, unstubbed. The rest
     stay NEW, queued for later batches.
  3. Print the full ledger table for that episode (every row the halt and
     the cycle produced) plus the verify_chain verdict over the whole
     ledger.

Exit code: 0 iff every step holds; any failure prints DEMO FAILED and
exits non-zero. Requires the real .env (Razorpay + LLM credentials) —
this demo is the live acceptance run, not an offline rehearsal.
"""

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow direct execution: python scripts/demo_d6.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.actions.recovery_link import RecoveryLinkActionClient
from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain
from app.db import connect, init_db
from app.ingest.halt_consumer import maybe_create_episode
from app.llm.openai_compat import OpenAICompatibleClient
from app.orchestrator import run_recovery_cycle
from app.razorpay import RazorpayClient
from app.settings import get_settings

OPEN_STATES_SQL = "('NEW', 'DIAGNOSED', 'SCORED', 'GATED', 'SENT')"


def _pending_halt_rows(conn) -> list:
    """Newest stored halt row per TREATMENT sub that has no open episode.

    Ordering by (sub, ts, id) and keeping the LAST row per subscription
    picks the most recent delivery — Razorpay retried these halts live, so
    a sub can carry several halted rows and any of them would do, but the
    newest is the truest halt timestamp for the episode.
    """
    rows = conn.execute(
        """
        SELECT w.* FROM webhook_events w
        JOIN cohorts c ON c.subscription_id = w.subscription_id
        WHERE w.event = 'subscription.halted'
          AND c.cohort = 'TREATMENT'
          AND NOT EXISTS (
              SELECT 1 FROM episodes e
              WHERE e.subscription_id = w.subscription_id
                AND e.state IN ('NEW', 'DIAGNOSED', 'SCORED', 'GATED', 'SENT')
          )
        ORDER BY w.subscription_id ASC, w.event_ts_utc ASC, w.id ASC
        """
    ).fetchall()
    newest: dict[str, object] = {}
    for row in rows:
        newest[row["subscription_id"]] = row
    return [newest[sub] for sub in sorted(newest)]


def _print_episode_table(episodes: list[dict]) -> None:
    print(f"\n{'sub':<22} {'episode':<21} {'state':<6} {'halt_ts_utc':<32} {'att':>3}  created_ts_utc")
    print("-" * 110)
    for ep in episodes:
        print(
            f"{ep['subscription_id']:<22} {ep['id']:<21} {ep['state']:<6} "
            f"{ep['halt_ts_utc']:<32} {ep['attempt_count']:>3}  {ep['created_ts_utc']}"
        )


def _print_ledger_table(conn, subscription_id: str) -> None:
    print(f"\nFull ledger for {subscription_id}:")
    print(
        f"{'seq':>5} {'ts_utc':<27} {'trigger_event':<22} {'outcome':<20} "
        f"{'mode':<9} {'gate':>4} {'rzp_call':>8} {'recovered':>9}"
    )
    print("-" * 110)
    rows = conn.execute(
        "SELECT seq, ts_utc, trigger_event, outcome, mode, human_gate, rzp_call, "
        "recovered_paise FROM audit_ledger WHERE subscription_id = ? ORDER BY seq ASC",
        (subscription_id,),
    ).fetchall()
    for r in rows:
        print(
            f"{r['seq']:>5} {r['ts_utc']:<27} {r['trigger_event']:<22} {r['outcome']:<20} "
            f"{r['mode']:<9} {('yes' if r['human_gate'] else 'no'):>4} "
            f"{('yes' if r['rzp_call'] else '-'):>8} {r['recovered_paise']:>9}"
        )


def main() -> int:
    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        print("DEMO FAILED: Razorpay key_id/key_secret unset — the live cycle needs .env")
        return 1
    if not settings.llm_api_key:
        print("DEMO FAILED: VAAPSI_LLM_API_KEY unset — the live cycle needs .env")
        return 1

    conn = connect()
    try:
        init_db(conn)
        print("=" * 110)
        print("Vaapsi D6 acceptance demo — halt→episode consumer on the LIVE pipeline DB")
        print("=" * 110)

        # ── Step 1: consume every unconsumed TREATMENT halt ─────────
        pending = _pending_halt_rows(conn)
        print(f"\nTREATMENT subs with a halted event and no open episode: {len(pending)}")
        episodes: list[dict] = []
        for row in pending:
            episode = maybe_create_episode(conn, row)
            assert episode is not None, (
                f"consumer returned None for TREATMENT halt of {row['subscription_id']}"
            )
            episodes.append(episode)
        conn.commit()
        _print_episode_table(episodes)
        assert episodes, "no unconsumed TREATMENT halts found — nothing to demo"

        # ── Step 2: ONE live recovery cycle, first episode only ─────
        first = episodes[0]
        sub_id = first["subscription_id"]
        print(
            f"\nDriving ONE recovery cycle for {sub_id} ({first['id']}) with the real "
            f"LLM + Razorpay clients — the other {len(episodes) - 1} stay NEW/queued."
        )
        summary = run_recovery_cycle(
            conn,
            sub_id,
            client=OpenAICompatibleClient(),
            action_client=RecoveryLinkActionClient(
                client=RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)
            ),
        )
        conn.commit()
        print(
            f"cycle: status={summary['status']} tier={summary['tier']} mode={summary['mode']} "
            f"variant={summary['variant']} reason={summary['reason']} "
            f"state_after={summary['state_after']}"
        )
        assert summary["episode_id"] == first["id"], "cycle ran on the wrong episode"
        assert summary["status"] != "no_open_episode", (
            "consumer-created episode invisible to the recovery cycle"
        )

        # ── Step 3: full ledger for the episode + chain verdict ─────
        _print_ledger_table(conn, sub_id)
        all_rows = list(iter_rows(conn))
        chain_ok, chain_detail = verify_chain(all_rows)
        print(f"\naudit chain: {'OK: ' if chain_ok else 'FAIL: '}{chain_detail}")
        assert chain_ok, f"hash chain broken: {chain_detail}"
        print(f"queued for later batches: {max(len(episodes) - 1, 0)} NEW episode(s)")
        print("DEMO PASSED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - demo boundary: report, exit non-zero
        print(f"DEMO FAILED: {exc}")
        sys.exit(1)
