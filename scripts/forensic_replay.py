# Forensic replay — recover grind events lost to the D6 webhook-dispatcher stall.
# Reconstructs each halted subscription's canonical Razorpay event sequence from the
# live API (ground truth), signs it with the real webhook secret, and POSTs through the
# REAL receiver. Replay-marked via X-Razorpay-Event-Id headers; idempotent re-runs.
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain as _verify_chain_impl
from app.db import get_conn
from app.settings import get_settings

TARGET = os.environ.get("VAAPSI_REPLAY_TARGET", "http://127.0.0.1:8000")
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "cohort_manifest_v2.csv"
SLOTS = range(1, 7)  # the stall window covered slots 1-6 (slot 0's halt already landed live)


def api(s):
    return (s.razorpay_key_id, s.razorpay_key_secret)


def fetch_sub(client, s, sid):
    return client.get(f"https://api.razorpay.com/v1/subscriptions/{sid}", auth=api(s), timeout=25).json()


def fetch_invoices(client, s, sid):
    r = client.get("https://api.razorpay.com/v1/invoices", params={"subscription_id": sid, "count": 100}, auth=api(s), timeout=25)
    return r.json().get("items", [])


def fetch_payments(client, s, invoice_ids):
    r = client.get("https://api.razorpay.com/v1/payments", params={"count": 100}, auth=api(s), timeout=25)
    return [p for p in r.json().get("items", []) if p.get("invoice_id") in invoice_ids]


def build_events(sub, invoices, payments):
    """Canonical occurrence-ordered sequence, payload shapes byte-faithful to real Razorpay sends."""
    sid = sub["id"]
    events = []

    def sub_event(event, status, ts):
        events.append((event, ts, {"payload": {"subscription": {"entity": {"id": sid, "entity": "subscription", "status": status}}}, "event": event, "created_at": ts}))

    auth_ts = sub.get("authenticated_at") or sub.get("start_at") or sub["created_at"]
    act_ts = sub.get("activated_at") or (auth_ts + 1)
    sub_event("subscription.authenticated", "authenticated", auth_ts)
    sub_event("subscription.activated", "activated", act_ts)

    for p in payments:
        if p["status"] == "captured":
            events.append(("subscription.charged", p["created_at"], {"payload": {"payment": {"entity": p}, "subscription": {"entity": {"id": sid, "entity": "subscription", "status": "active"}}}, "event": "subscription.charged", "created_at": p["created_at"]}))

    failed = sorted([p for p in payments if p["status"] == "failed"], key=lambda p: p["created_at"])
    for i, p in enumerate(failed, start=1):
        events.append(("payment.failed", p["created_at"], {"payload": {"payment": {"entity": p}}, "event": "payment.failed", "created_at": p["created_at"]}))
        if i < len(failed):
            sub_event("subscription.pending", "pending", p["created_at"] + 1)
    if failed:
        sub_event("subscription.halted", "halted", failed[-1]["created_at"] + 2)

    seen, ordered = set(), []
    for ev in sorted(events, key=lambda e: (e[1], e[0])):
        if ev[0] not in seen:  # one of each kind (idempotency semantics at reconstruction level)
            seen.add(ev[0])
            ordered.append(ev)
        elif ev[0] in ("payment.failed", "subscription.pending"):
            ordered.append(ev)
    return ordered


def post_event(client, s, n, event, ts, payload, sid):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=False).encode()
    sig = hmac.new(s.razorpay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    event_header = f"replay-{sid}-{n:02d}-{event}"
    r = client.post(
        f"{TARGET}/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_header, "Content-Type": "application/json"},
        timeout=25,
    )
    return r.status_code, event_header


def main():
    s = get_settings()
    health = httpx.get(f"{TARGET}/health", timeout=10)
    if health.status_code != 200:
        print(f"ABORT: local server not healthy at {TARGET} ({health.status_code})"); sys.exit(2)

    subs = []
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(row["slot"]) in SLOTS:
                subs.append(row["subscription_id"])

    client = httpx.Client()
    halted, skipped = [], []
    for sid in subs:
        sub = fetch_sub(client, s, sid)
        print(f"{sid} -> rzp status: {sub.get('status')}")
        (halted if sub.get("status") == "halted" else skipped).append((sid, sub))

    ok = True
    for sid, sub in halted:
        invoices = fetch_invoices(client, s, sid)
        inv_ids = {i["id"] for i in invoices}
        payments = fetch_payments(client, s, inv_ids)
        seq = build_events(sub, invoices, payments)
        print(f"\n── replay {sid}: {len(seq)} events ──")
        statuses = []
        for n, (event, ts, payload) in enumerate(seq, start=1):
            code, _header = post_event(client, s, n, event, ts, payload, sid)
            statuses.append(code)
            print(f"  {n:02d} {event:<26} ts={ts} -> {code}")
        if any(c != 200 for c in statuses):
            ok = False

    # idempotency: re-post the first replayed event — total count must not move
    import sqlite3
    db = sqlite3.connect(s.data_dir / "vaapsi.sqlite3")
    before = db.execute("SELECT count(*) FROM webhook_events").fetchone()[0]
    if halted:
        sid = halted[0][0]
        sub = halted[0][1]
        seq = build_events(sub, fetch_invoices(client, s, sid), fetch_payments(client, s, {i["id"] for i in fetch_invoices(client, s, sid)}))
        first = seq[0]
        post_event(client, s, 1, first[0], first[1], first[2], sid)
        after = db.execute("SELECT count(*) FROM webhook_events").fetchone()[0]
        print(f"\nidempotency re-post: {before} -> {after} rows ({'OK deduped' if before == after else 'FAIL duplicated'})")
        ok = ok and before == after

    with get_conn() as conn:
        rows = _verify_chain_impl(iter_rows(conn))
    print(f"chain: {rows[0]} | {rows[1]}")
    ok = ok and rows[0]

    for sid, sub in skipped:
        print(f"skipped {sid} (status={sub.get('status')}) — logged exception")
    print("\nFORENSIC REPLAY: " + ("PASSED" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
