"""Grind dispatcher pass — drive one recovery cycle per NEW episode in creation
order. Prints booleans/counts and cycle summaries only; never credentials.
Self-deletes nothing (kept as scripts/grind_dispatch.py — reusable per cycle).

Usage: .venv/Scripts/python.exe scripts/grind_dispatch.py [--limit N]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        print("BLOCKED: Razorpay test keys unset in .env")
        return 2
    if not settings.llm_api_key:
        print("BLOCKED: LLM key unset in .env")
        return 2

    from app.actions.recovery_link import RecoveryLinkActionClient
    from app.db import connect
    from app.llm.openai_compat import OpenAICompatibleClient
    from app.orchestrator import run_recovery_cycle
    from app.razorpay import RazorpayClient

    conn = connect()
    rows = conn.execute(
        "SELECT id, subscription_id, halt_ts_utc FROM episodes"
        " WHERE state = 'NEW' ORDER BY halt_ts_utc ASC LIMIT ?",
        (args.limit,),
    ).fetchall()
    print(f"NEW episodes to cycle: {len(rows)}")
    if not rows:
        print("nothing to do")
        return 0

    llm = OpenAICompatibleClient()
    actions = RecoveryLinkActionClient(
        client=RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)
    )

    sent = gated = blocked = skipped = other = 0
    for r in rows:
        summary = run_recovery_cycle(
            conn, r["subscription_id"], client=llm, action_client=actions
        )
        conn.commit()
        status = summary.get("status")
        print(
            f"{r['id']} -> {status} tier={summary.get('tier')} "
            f"reason={summary.get('reason')} state={summary.get('state_after')}"
        )
        if status == "dispatched":
            sent += 1
        elif status == "gated":
            gated += 1
        elif status == "blocked":
            blocked += 1
        elif status == "skipped":
            skipped += 1
        else:
            other += 1

    print(f"\ntotals: dispatched={sent} gated={gated} blocked={blocked} "
          f"skipped={skipped} other={other}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
