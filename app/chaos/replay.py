"""D4 Drill 1 — webhook replay storm: prove ingest idempotency under fire.

Why a storm drill: D0's idempotency was proven with two hand-rolled
deliveries; real Razorpay retries arrive as a STORM — the same delivery
re-firing dozens of times, each with different bytes (shuffled JSON key
order) and jittered timestamps, all inside one 5-minute idempotency
window. fire_replay_storm fires exactly that through the pure
process_webhook(conn, headers, body) seam — every delivery individually
signature-valid (re-signed over its own bytes, because the HMAC covers
the wire bytes and different key order changes them) — then asserts the
invariants that make the storm harmless: exactly ONE webhook_events row,
one archive file per delivery (nothing is dropped, even duplicates),
and zero duplicate recovery episodes for the subscription.

Determinism: jitter and key shuffles are index-derived (no randomness),
so the drill is byte-repeatable — same inputs, same storm, same verdict,
every run.
"""

import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.ingest.receiver import process_webhook
from app.settings import get_settings

# Storm shape: N identical deliveries + exactly 5 shuffled-key
# variants (same event, different byte order); SHUFFLED_VARIANTS pins
# the count.
SHUFFLED_VARIANTS = 5

# Jitter must never cross the 5-minute idempotency bucket: every delivery
# lands in [bucket_start + 5, bucket_start + 294] of the base event's
# window. 300 == receiver.TS_WINDOW_MINUTES * 60.
TS_WINDOW_SECONDS = 300
JITTER_STEP = 53  # coprime with 295 → distinct offsets across many fires
JITTER_GUARD = 10  # keep offsets off both bucket edges


def _sign(raw: bytes, secret: str) -> str:
    """HMAC-SHA256 hex digest — the exact scheme app.ingest.signature uses."""
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _jittered_ts(base_ts: int, fire_index: int) -> int:
    """Timestamp deep inside the base event's 5-minute idempotency bucket.

    Index-derived (i * JITTER_STEP mod window), never random: the storm is
    repeatable, and no jitter can spill into the next bucket and mint a
    spurious second idempotency key.
    """
    bucket_start = base_ts - (base_ts % TS_WINDOW_SECONDS)
    offset = (fire_index * JITTER_STEP) % (TS_WINDOW_SECONDS - JITTER_GUARD)
    return bucket_start + (JITTER_GUARD // 2) + offset


def _shuffled_keys(payload: dict[str, Any], round_index: int) -> dict[str, Any]:
    """Same event, different byte order: rotate top-level keys and reverse
    the nested payload mapping — JSON equality survives, bytes (and the
    signature over them) change, which is exactly the Razorpay-retry shape."""
    items = list(payload.items())
    rot = round_index % len(items)
    rotated = items[rot:] + items[:rot]
    shuffled: dict[str, Any] = {}
    for key, value in rotated:
        shuffled[key] = (
            dict(reversed(list(value.items()))) if isinstance(value, dict) else value
        )
    return shuffled


def _entity_id(payload: dict[str, Any]) -> str:
    """Mirror the receiver's entity extraction (the idempotency half-key)."""
    for family in ("subscription", "payment", "payment_link", "invoice"):
        ent = payload.get("payload", {}).get(family, {}).get("entity", {})
        if ent.get("id"):
            return str(ent["id"])
    return "unknown"


def _count_files(archive_dir: Path) -> int:
    if not archive_dir.exists():
        return 0
    return sum(1 for p in archive_dir.rglob("*") if p.is_file())


def fire_replay_storm(
    conn: sqlite3.Connection,
    base_event: dict[str, Any],
    deliveries: int = 25,
) -> dict[str, int]:
    """Fire the SAME webhook delivery `deliveries` + 5 shuffled times.

    Every delivery is signature-valid over its own bytes and carries a
    jittered timestamp inside the base event's 5-minute idempotency
    window; the trailing 5 are shuffled-key variants. All go through
    app.ingest.receiver.process_webhook on the caller's connection (the
    caller commits). Asserts, per drill contract: exactly one NEW
    webhook_events row, one archive file per delivery, and no duplicate
    episode rows created for the subscription — then returns the counts
    for the demo table/tests to quote.

    Raises ValueError if the webhook secret is unset (the drill fires
    signature-VALID deliveries; without the secret there is no drill).
    """
    settings = get_settings()
    secret = settings.razorpay_webhook_secret
    if not secret:
        raise ValueError("webhook secret unset — the storm needs signature-valid deliveries")

    base_ts = int(base_event["created_at"])
    event = str(base_event["event"])
    subscription_id = _entity_id(base_event)
    event_id = (
        "evt_storm_"
        + hashlib.sha256(f"{event}|{subscription_id}|{base_ts}".encode()).hexdigest()[:12]
    )

    rows_before = conn.execute(
        "SELECT COUNT(*) AS c FROM webhook_events WHERE event = ? AND subscription_id = ?",
        (event, subscription_id),
    ).fetchone()["c"]
    archives_before = _count_files(settings.archive_dir)
    episodes_before = conn.execute(
        "SELECT COUNT(*) AS c FROM episodes WHERE subscription_id = ?",
        (subscription_id,),
    ).fetchone()["c"]

    total = deliveries + SHUFFLED_VARIANTS
    accepted = duplicates = 0
    for i in range(total):
        payload = {**base_event, "created_at": _jittered_ts(base_ts, i)}
        if i >= deliveries:
            payload = _shuffled_keys(payload, i - deliveries)
        raw = json.dumps(payload).encode("utf-8")
        headers = {"X-Razorpay-Signature": _sign(raw, secret), "X-Razorpay-Event-Id": event_id}
        result = process_webhook(conn, headers, raw)
        if result["status"] == "accepted":
            accepted += 1
        elif result["status"] == "duplicate":
            duplicates += 1
        else:  # pragma: no cover — process_webhook returns only these two
            raise AssertionError(f"unexpected ingest status: {result['status']}")

    rows_after = conn.execute(
        "SELECT COUNT(*) AS c FROM webhook_events WHERE event = ? AND subscription_id = ?",
        (event, subscription_id),
    ).fetchone()["c"]
    archives_after = _count_files(settings.archive_dir)
    episodes_after = conn.execute(
        "SELECT COUNT(*) AS c FROM episodes WHERE subscription_id = ?",
        (subscription_id,),
    ).fetchone()["c"]

    # ── drill invariants (the whole point of the storm) ────────────────
    assert rows_after - rows_before == 1, (
        f"replay storm minted {rows_after - rows_before} webhook_events rows, expected 1"
    )
    assert archives_after - archives_before == total, (
        f"expected {total} archive files (every delivery archived), "
        f"got {archives_after - archives_before}"
    )
    assert episodes_after - episodes_before == 0, (
        "the storm created recovery episodes — ingest must stay inert"
    )
    assert episodes_after <= 1, (
        f"{episodes_after} episodes for {subscription_id} — duplicate episode cycle"
    )

    return {
        "deliveries": total,
        "identical": deliveries,
        "shuffled_variants": SHUFFLED_VARIANTS,
        "accepted": accepted,
        "duplicates": duplicates,
        "archived": archives_after - archives_before,
        "webhook_rows": rows_after - rows_before,
        "episodes_for_subscription": episodes_after,
    }
