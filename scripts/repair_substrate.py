"""D1 repair v2 (resumable): rebuild the cohort substrate with customers attached.

Why: v1 subscriptions had no customer object, which made the Dashboard's
"Charge this now" fall back to an invoice-notification flow (`notify_fails`)
instead of the real subscription-charge simulation.

This script is IDEMPOTENT and RESUMABLE:
  1. cancels v1 subs (skips already-terminal),
  2. archives the v1 manifest (copy),
  3. RECONCILES: adopts any existing substrate=v2 subscriptions from the
     Razorpay API (source of truth — survives crashes mid-run),
  4. creates customers + subscriptions only for missing slots (retry x3),
  5. rewrites data/cohort_manifest.csv + the cohorts table from scratch.

Safe to re-run any number of times.
"""

import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.db import connect, init_db
from app.razorpay import RazorpayClient, RazorpayError
from app.settings import get_settings

N_SUBS = 60
PLAN_ID = "plan_TUupcFbRzvdbNy"


def with_retry(fn, *args, attempts: int = 3, **kwargs):
    """Retry transient network errors (timeouts); re-raise API errors (4xx are real)."""
    delay = 2.0
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except (httpx.ReadTimeout, httpx.TransportError) as e:
            if i == attempts - 1:
                raise
            print(f"  transient {type(e).__name__}, retry {i + 2}/{attempts} in {delay}s…")
            time.sleep(delay)
            delay *= 2


def list_all_subscriptions(client: RazorpayClient) -> list[dict]:
    """Paginate GET /subscriptions (v1 + v2 cohorts together)."""
    out, skip, count = [], 0, 100
    while True:
        r = client._http.get("/subscriptions", params={"count": count, "skip": skip})
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        items = r.json().get("items", [])
        out.extend(items)
        if len(items) < count:
            return out
        skip += count


def main() -> int:
    settings = get_settings()
    client = RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret, timeout=90.0)
    conn = connect()
    init_db(conn)

    # customer_id column may be missing on DBs created before the v2 migration
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cohorts)")]
    if "customer_id" not in cols:
        conn.execute("ALTER TABLE cohorts ADD COLUMN customer_id TEXT")
        conn.commit()

    # ── 1. cancel v1 (skip already-terminal) ───────────────────────
    # v1 ids come from the archived manifest (DB holds only v2 now)
    v1_ids: list[str] = []
    archive = settings.data_dir / "cohort_manifest_v1_cancelled.csv"
    if archive.exists():
        with archive.open(newline="", encoding="utf-8") as f:
            v1_ids = [r["subscription_id"] for r in csv.DictReader(f)]

    cancelled, already_terminal, cancel_failed = 0, 0, 0
    for sid in v1_ids:
        try:
            status = with_retry(client.fetch_subscription, sid).get("status")
        except (RazorpayError, httpx.ReadTimeout):
            status = "?"
        if status in ("cancelled", "completed", "expired"):
            already_terminal += 1
            continue
        try:
            with_retry(client.cancel_subscription, sid)
            cancelled += 1
        except RazorpayError as e:
            if "cancelled status" in str(e) or "completed status" in str(e):
                already_terminal += 1
            else:
                cancel_failed += 1
                print(f"cancel skipped {sid}: {e}")
    print(f"v1: {cancelled} cancelled, {already_terminal} already terminal, {cancel_failed} skipped")

    # ── 2. archive v1 manifest (copy first — Windows locks renames) ──
    manifest = settings.data_dir / "cohort_manifest.csv"
    if manifest.exists() and not archive.exists():
        import shutil

        shutil.copy2(manifest, archive)
        print("v1 manifest archived (copy) →", archive.name)

    # ── 3. RECONCILE existing v2 subs from Razorpay (crash recovery) ──
    adopted: dict[int, dict] = {}
    for sub in list_all_subscriptions(client):
        notes = sub.get("notes", {})
        if notes.get("vaapsi_buildathon") == "track03" and notes.get("vaapsi_substrate") == "v2":
            adopted[int(notes["vaapsi_slot"])] = sub
    print(f"reconcile: found {len(adopted)} existing v2 subscriptions on Razorpay")

    # ── 4. fill missing slots ──────────────────────────────────────
    conn.execute("DELETE FROM cohorts")  # rebuilt from scratch below
    now = datetime.now(timezone.utc).isoformat()
    rows = []

    for slot in range(N_SUBS):
        cohort = "TREATMENT" if slot % 2 == 0 else "CONTROL"
        if slot in adopted:
            sub = adopted[slot]
            customer_id = sub.get("customer_id") or ""
            print(f"[{slot:02d}] {cohort:9s} {sub['id']} cust={customer_id} (adopted)")
        else:
            tag = f"{'t' if cohort == 'TREATMENT' else 'c'}{slot:02d}"
            customer = with_retry(
                client.create_customer,
                {
                    "name": f"Vaapsi {cohort} {slot:02d}",
                    "email": f"vaapsi.{tag}@test.example.com",
                    "contact": f"+91990000{slot:04d}",
                    "fail_existing": "0",
                },
            )
            sub = with_retry(
                client.create_subscription,
                {
                    "plan_id": PLAN_ID,
                    "customer_id": customer["id"],
                    "total_count": 6,
                    "customer_notify": 0,
                    "notes": {
                        "vaapsi_cohort": cohort,
                        "vaapsi_slot": str(slot),
                        "vaapsi_buildathon": "track03",
                        "vaapsi_substrate": "v2",
                    },
                },
            )
            customer_id = customer["id"]
            print(f"[{slot:02d}] {cohort:9s} {sub['id']} cust={customer_id}")

        conn.execute(
            "INSERT INTO cohorts (subscription_id, cohort, slot, customer_id, rzp_status, short_url, created_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sub["id"], cohort, slot, customer_id, sub.get("status"), sub.get("short_url"), now),
        )
        rows.append(
            {"slot": slot, "subscription_id": sub["id"], "cohort": cohort,
             "customer_id": customer_id, "status": sub.get("status"),
             "short_url": sub.get("short_url", "")}
        )

    conn.commit()
    conn.close()

    with manifest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["slot", "subscription_id", "cohort", "customer_id", "status", "short_url"]
        )
        writer.writeheader()
        writer.writerows(rows)

    treatment = sum(1 for r in rows if r["cohort"] == "TREATMENT")
    print(f"\nDONE: {len(rows)} v2 subscriptions ({treatment} TREATMENT / {len(rows)-treatment} CONTROL), all customer-attached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
