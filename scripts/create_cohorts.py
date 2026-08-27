"""Creates the recovery-demo plan and the interleaved control/treatment cohorts.

Idempotent by design: reuses an existing Vaapsi plan (matched by
notes.vaapsi_plan=1) and refuses to re-create a cohort that already has
subscriptions. Every subscription is stamped with its cohort in Razorpay
`notes` AND recorded to data/cohort_manifest.csv + SQLite `cohorts`.

Usage:
    .venv/Scripts/python.exe scripts/create_cohorts.py           # 60 subs (30/30)
    .venv/Scripts/python.exe scripts/create_cohorts.py --count 8 # smoke batch
"""

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.db import connect, init_db
from app.razorpay import RazorpayClient
from app.settings import get_settings

PLAN_ITEM = {
    "name": "Vaapsi Recovery Demo",
    "amount": 49900,  # ₹499 — just under the human-gate threshold
    "currency": "INR",
    "description": "Buildathon test plan (Track 03 recovery experiment)",
}


def find_or_create_plan(client: RazorpayClient) -> dict:
    for plan in client.list_plans(count=100):
        if plan.get("notes", {}).get("vaapsi_plan") == "1":
            print(f"plan exists: {plan['id']} ({plan['item']['name']})")
            return plan
    plan = client.create_plan(
        {
            "period": "monthly",
            "interval": 1,
            "item": PLAN_ITEM,
            "notes": {"vaapsi_plan": "1"},
        }
    )
    print(f"plan created: {plan['id']}")
    return plan


def main() -> int:
    n_subs = 60
    if "--count" in sys.argv:
        n_subs = int(sys.argv[sys.argv.index("--count") + 1])

    settings = get_settings()
    client = RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)
    plan = find_or_create_plan(client)

    conn = connect()
    init_db(conn)
    existing = conn.execute("SELECT COUNT(*) FROM cohorts").fetchone()[0]
    if existing:
        print(f"REFUSING: {existing} cohort subscriptions already exist — "
              "cohort integrity beats convenience. Delete data/vaapsi.sqlite3 to reset.")
        return 1

    manifest_path = settings.data_dir / "cohort_manifest.csv"
    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for slot in range(n_subs):
        cohort = "TREATMENT" if slot % 2 == 0 else "CONTROL"
        sub = client.create_subscription(
            {
                "plan_id": plan["id"],
                "total_count": 6,
                "customer_notify": 0,  # NO Razorpay-side emails — outreach is Vaapsi's job only
                "notes": {
                    "vaapsi_cohort": cohort,
                    "vaapsi_slot": str(slot),
                    "vaapsi_buildathon": "track03",
                },
            }
        )
        sub_id = sub["id"]
        conn.execute(
            "INSERT INTO cohorts (subscription_id, cohort, slot, rzp_status, short_url, created_utc)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (sub_id, cohort, slot, sub.get("status"), sub.get("short_url"), now),
        )
        rows.append(
            {"slot": slot, "subscription_id": sub_id, "cohort": cohort,
             "status": sub.get("status"), "short_url": sub.get("short_url", "")}
        )
        print(f"[{slot:02d}] {cohort:9s} {sub_id}")

    conn.commit()
    conn.close()

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["slot", "subscription_id", "cohort", "status", "short_url"])
        writer.writeheader()
        writer.writerows(rows)

    treatment = sum(1 for r in rows if r["cohort"] == "TREATMENT")
    control = len(rows) - treatment
    print(f"\nDONE: {len(rows)} subscriptions ({treatment} TREATMENT / {control} CONTROL)")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
