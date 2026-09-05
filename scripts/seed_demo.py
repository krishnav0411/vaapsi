"""Sanitized public-demo seeder — build a safe, chain-true store from nothing.

    python -m scripts.seed_demo --db data/demo_seed.sqlite3 --episodes 6 --verify

Why a seeder: the public demo (VAAPSI_PUBLIC_DEMO=1) must show a real,
explorable dashboard WITHOUT carrying a single real subscription id,
credential or customer PII. So this script builds the store from fake
constants using the REAL house write paths — app.db.init_db for the
schema, app.core.episodes create/transition for every episode (each of
which appends through app.audit.ledger.append), app.policy.merchant's
ensure_default_row for the frozen DEFAULT policy — so the seeded ledger is
byte-for-byte what production would have written for these fake events,
and app.audit.verify_chain passes on it out of the box.

Sanitization contract (checked by tests/test_demo_mode.py):
- every subscription id is sub_DEMOTxxx / sub_DEMOCxxx, every event id
  evt_DEMOxxxx — nothing maps to a real Razorpay entity;
- no credentials appear anywhere in the data (there are none to leak);
- no customer PII: customers are fake cust_DEMOxxx ids at most, and the
  cohorts table carries customer_id NULL;
- money is realistic integer paise (₹499 / ₹999 plans).

Seed-on-boot: app.main's lifespan calls seed_store() when a demo
deployment finds no store file — ephemeral container SQLite means every
cold start reseeds, an existing store boots as-is.

--verify runs the real chain verifier after seeding and prints the
verdict; the process exits nonzero if the chain is broken (it should
never be — seed_store refuses to hand back a store that fails
verification anyway).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # direct execution: python scripts/seed_demo.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.actions.recovery_link import build_recovery_link_payload
from app.audit import ledger as audit_ledger
from app.audit.verify_chain import verify_chain
from app.core.episodes import create_episode, transition
from app.db import init_db
from app.policy.merchant import DEFAULT_MERCHANT_ID, ensure_default_row

DEFAULT_DB = Path("data/demo_seed.sqlite3")
DEFAULT_EPISODES = 6

# Cohort experiment shape: 30 TREATMENT / 30 CONTROL subscriptions, all
# fake. Episode slots are the first N TREATMENT subscriptions (episodes
# are a TREATMENT-only concept — CONTROL is recorded, never acted on).
COHORTS_PER_ARM = 30

# Money is realistic integer paise. 49900 is the actual plan price the
# pipeline charges (app.actions.recovery_link.RECOVERY_PLAN_PAISE); the
# ₹999 variant exists so cohort/webhook payloads don't look stamped.
PLAN_PRICE_PAISE = 49900
ALT_PLAN_PRICE_PAISE = 99900

# Episode mix: a single fully-recovered episode so the Overview hero shows
# an honest 100% recovery rate (1/1 VERIFIED). The full cycle writes 5
# ledger rows (EPISODE_CREATED, DIAGNOSED, SCORED, EPISODE_SENT, VERIFIED).
EPISODE_STATE_PLAN: tuple[str, ...] = ("VERIFIED",)

# Halt recency per episode slot (hours ago) — sits comfortably past the 6h
# cooling window and inside the 7-day M1 window.
HALT_HOURS_AGO: tuple[int, ...] = (96,)


def _treatment_sub(slot: int) -> str:
    return f"sub_DEMOT{slot:03d}"


def _control_sub(slot: int) -> str:
    return f"sub_DEMOC{slot:03d}"


def _connect(db_path: Path) -> sqlite3.Connection:
    """Seeder-owned connection to the TARGET store (same pragmas as app.db)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _remove_store_files(db_path: Path) -> None:
    """Drop the store (and WAL sidecars) so seeding always starts clean."""
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _idempotency_key(event: str, subscription_id: str, ts: datetime) -> str:
    """Same scheme the real receiver uses (event|sub|5-minute bucket)."""
    bucket = ts.replace(minute=(ts.minute // 5) * 5, second=0, microsecond=0)
    material = f"{event}|{subscription_id}|{bucket.isoformat()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _insert_webhook_event(
    conn: sqlite3.Connection,
    *,
    event: str,
    subscription_id: str,
    occurred: datetime,
    payload: dict[str, Any],
    event_seq: int,
) -> None:
    """One webhook_events row shaped like a real Razorpay delivery, fake ids."""
    conn.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, "
        "subscription_id, event_ts_utc, received_ts_utc, payload_json, raw_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            _idempotency_key(event, subscription_id, occurred),
            f"evt_DEMO{event_seq:04d}",
            event,
            subscription_id,
            _iso(occurred),
            _iso(occurred + timedelta(seconds=1)),
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
    )


def _halted_payload(subscription_id: str, customer_id: str, plan_price: int, occurred: datetime) -> dict[str, Any]:
    """Real subscription.halted event shape, fake entities throughout."""
    return {
        "event": "subscription.halted",
        "payload": {
            "subscription": {
                "entity": {
                    "id": subscription_id,
                    "entity": "subscription",
                    "status": "halted",
                    "plan_id": f"plan_DEMO{plan_price // 100}",
                    "customer_id": customer_id,
                    "short_url": None,
                    "paid_count": 3,
                    "remaining_count": 5,
                    "notes": {"source": "vaapsi-demo-seed"},
                }
            }
        },
        "created_at": int(occurred.timestamp()),
    }


def _paid_payload(subscription_id: str, episode_id: str, amount: int, occurred: datetime) -> dict[str, Any]:
    """Real payment_link.paid shape — the recovery link the demo SENT."""
    return {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": f"plink_DEMO{subscription_id[-3:]}",
                    "entity": "payment_link",
                    "status": "paid",
                    "amount": amount,
                    "currency": "INR",
                    "reference_id": f"vaapsi:{episode_id[:24]}:1",
                    "short_url": "https://rzp.io/i/demo-seed-link",
                    "notes": {"vaapsi_episode_id": episode_id},
                }
            }
        },
        "created_at": int(occurred.timestamp()),
    }


def _charged_payload(subscription_id: str, plan_price: int, occurred: datetime) -> dict[str, Any]:
    """Real subscription.charged shape — the stop-on-charge story, CONTROL arm."""
    return {
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": subscription_id,
                    "entity": "subscription",
                    "status": "active",
                    "plan_id": f"plan_DEMO{plan_price // 100}",
                    "customer_id": f"cust_DEMO{subscription_id[-3:]}",
                    "notes": {"source": "vaapsi-demo-seed"},
                }
            }
        },
        "created_at": int(occurred.timestamp()),
    }


def _seed_cohorts(conn: sqlite3.Connection, episodes_n: int, base: datetime) -> int:
    """30/30 TREATMENT/CONTROL — customer_id stays NULL (no PII, ever)."""
    rows: list[tuple[str, str, int, str, str]] = []
    for slot in range(COHORTS_PER_ARM):
        halted = slot < episodes_n
        rows.append(
            (
                _treatment_sub(slot),
                "TREATMENT",
                slot,
                "halted" if halted else "active",
                _iso(base - timedelta(days=30 - slot)),
            )
        )
    for slot in range(COHORTS_PER_ARM):
        rows.append(
            (
                _control_sub(slot),
                "CONTROL",
                COHORTS_PER_ARM + slot,
                "active",
                _iso(base - timedelta(days=30 - slot)),
            )
        )
    conn.executemany(
        "INSERT INTO cohorts (subscription_id, cohort, slot, customer_id, "
        "rzp_status, short_url, created_utc) VALUES (?, ?, ?, NULL, ?, NULL, ?)",
        [(sub, cohort, slot, status, created) for sub, cohort, slot, status, created in rows],
    )
    return len(rows)


def _seed_episodes(
    conn: sqlite3.Connection, episodes_n: int, base: datetime
) -> tuple[int, int, int]:
    """Open the demo recovery cycles via the REAL episode state machine.

    Every create/transition appends its own hash-chained ledger row on the
    caller's connection, so the seeded chain is exactly what production
    would have written for these fake halts. Returns
    (episodes, ledger_rows, verified_sub_slot).
    """
    created = 0
    verified_slot = -1
    for slot in range(episodes_n):
        state = EPISODE_STATE_PLAN[slot % len(EPISODE_STATE_PLAN)]
        halt_hours = HALT_HOURS_AGO[slot % len(HALT_HOURS_AGO)]
        sub = _treatment_sub(slot)
        halt_ts = base - timedelta(hours=halt_hours)
        plan_price = PLAN_PRICE_PAISE if slot % 2 == 0 else ALT_PLAN_PRICE_PAISE

        # Webhook first: a real subscription.halted delivery preceded it.
        _insert_webhook_event(
            conn,
            event="subscription.halted",
            subscription_id=sub,
            occurred=halt_ts,
            payload=_halted_payload(sub, f"cust_DEMO{slot:03d}", plan_price, halt_ts),
            event_seq=created,
        )
        episode = create_episode(
            conn, subscription_id=sub, halt_ts_utc=_iso(halt_ts), cohort="TREATMENT"
        )
        created += 1
        if state == "NEW":
            continue

        # The full cycle beyond NEW: diagnose → score → (dispatch) → verify.
        transition(conn, episode["id"], "DIAGNOSED")
        transition(
            conn,
            episode["id"],
            "SCORED",
            ledger_fields={
                "score": 0.72,
                "features": {
                    "tier": 1,
                    "amount_paise": plan_price,
                    "attempts_so_far": 0,
                    "hours_since_halt": round(halt_hours, 1),
                },
            },
        )
        # The SENT dispatch payload is the EXACT shape the real action
        # client builds (recovery_link.build_recovery_link_payload) —
        # fake entities in, fake entities out.
        dispatch_payload = build_recovery_link_payload(episode)
        sent_episode = transition(
            conn, episode["id"], "SENT", ledger_fields={"rzp_call": dispatch_payload}
        )
        if state == "SENT":
            continue

        # The customer paid: a real payment_link.paid webhook, then the
        # same VERIFIED transition (with recovered_paise from the event
        # payload) the verify consumer performs.
        paid_ts = halt_ts + timedelta(hours=halt_hours - 10)
        _insert_webhook_event(
            conn,
            event="payment_link.paid",
            subscription_id=sub,
            occurred=paid_ts,
            payload=_paid_payload(sub, sent_episode["id"], PLAN_PRICE_PAISE, paid_ts),
            event_seq=created + 90,
        )
        transition(
            conn,
            sent_episode["id"],
            "VERIFIED",
            ledger_fields={
                "trigger_event": "payment_link.paid",
                "recovered_paise": PLAN_PRICE_PAISE,
            },
        )
        verified_slot = slot
    return created, verified_slot


def _seed_control_events(conn: sqlite3.Connection, base: datetime) -> None:
    """CONTROL-arm deliveries: recorded, never consumed (no episodes)."""
    for seq, (slot, hours_ago) in enumerate(((3, 40), (17, 12))):
        sub = _control_sub(slot)
        occurred = base - timedelta(hours=hours_ago)
        _insert_webhook_event(
            conn,
            event="subscription.charged",
            subscription_id=sub,
            occurred=occurred,
            payload=_charged_payload(sub, ALT_PLAN_PRICE_PAISE, occurred),
            event_seq=200 + seq,
        )


def seed_store(db_path: Path, *, episodes: int = DEFAULT_EPISODES) -> dict[str, int]:
    """Build the sanitized demo store at db_path; refuse a broken chain.

    Idempotent by construction: any existing store (and its WAL sidecars)
    is removed first, so both `--db` reruns and seed-on-boot cold starts
    produce the same clean, chain-true demo world.
    """
    db_path = Path(db_path)
    episodes = max(0, int(episodes))
    _remove_store_files(db_path)

    base = datetime.now(timezone.utc)
    conn = _connect(db_path)
    try:
        init_db(conn)
        ensure_default_row(conn)
        cohorts = _seed_cohorts(conn, episodes, base)
        episode_n, _verified_slot = _seed_episodes(conn, episodes, base)
        _seed_control_events(conn, base)
        conn.commit()

        rows = list(audit_ledger.iter_rows(conn))
        ok, detail = verify_chain(rows)
        if not ok:  # fail-closed: never hand back an untrustworthy demo store
            raise RuntimeError(f"seeded demo ledger failed verification: {detail}")
        policies = conn.execute(
            "SELECT COUNT(*) AS n FROM merchant_policies WHERE merchant_id = ?",
            (DEFAULT_MERCHANT_ID,),
        ).fetchone()["n"]
        events = int(
            conn.execute("SELECT COUNT(*) AS n FROM webhook_events").fetchone()["n"]
        )
        return {
            "episodes": episode_n,
            "ledger_rows": len(rows),
            "webhook_events": events,
            "cohorts": cohorts,
            "merchant_policies": int(policies),
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed a sanitized, chain-valid Vaapsi demo store (fake ids only)."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="target SQLite path")
    parser.add_argument(
        "--episodes", type=int, default=DEFAULT_EPISODES, help="demo episodes to open"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="run app.audit.verify_chain after seeding and print the verdict",
    )
    args = parser.parse_args(argv)

    counts = seed_store(args.db, episodes=args.episodes)
    print(f"seeded {args.db}: {counts}")
    if args.verify:
        conn = _connect(args.db)
        try:
            ok, detail = verify_chain(list(audit_ledger.iter_rows(conn)))
        finally:
            conn.close()
        print(("OK: " if ok else "FAIL: ") + detail)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
