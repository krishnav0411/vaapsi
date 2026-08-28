"""Webhook receiver — Day 0 slice (PLAN.md §2 step 2).

Contract:
- verify HMAC-SHA256 signature on the raw body (never re-serialized JSON)
- archive the raw event under data/webhook_archive/YYYY/MM/DD/
- dedupe on idempotency key (event, subscription_id, 5-minute ts window)
- 401 on bad/tampered signature; 503 while the webhook secret is unset
  (Day-0 protocol: nothing is accepted until Krishnav registers the
  webhook and the secret lands in .env)

Days 2+ hooked recovery-episode creation in here (`subscription.halted`
and Nth `subscription.pending`); `subscription.charged` voids open
episodes (stop-on-charge). Since D6 BOTH loop halves are LIVE:
accepted `subscription.halted` rows are handed to the halt→episode
consumer (app.ingest.halt_consumer), and accepted paid events
(`payment_link.paid` / `invoice.paid`) are handed to the paid→VERIFIED
consumer (app.ingest.verify_consumer) — each on the same connection,
inside its own savepoint, so a consumer failure can never fail the
webhook itself nor leave a half-written state change behind.
"""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from app.db import get_conn
from app.ingest.halt_consumer import HALT_EVENT, maybe_create_episode
from app.ingest.signature import verify_signature
from app.ingest.verify_consumer import VERIFY_EVENTS, maybe_verify_episode
from app.settings import get_settings

log = logging.getLogger("vaapsi.ingest")

router = APIRouter(prefix="/webhooks", tags=["ingest"])

# A delivery re-arriving within this window for the same
# (event, subscription) pair counts as the same delivery (replay/storm).
TS_WINDOW_MINUTES = 5


class WebhookRejection(Exception):
    """Transport-agnostic rejection raised by the pure ingest seam.

    The HTTP layer (the FastAPI routes below) maps status_code/detail onto
    HTTPException; direct callers — the D4 replay-storm drill and its
    tests — get a plain exception instead of importing FastAPI into
    offline code. Nothing else about the ingest contract changes.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"webhook rejected {status_code}: {detail}")


def _event_time(ts: int | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _bucket(ts_utc: datetime) -> str:
    minute_bucket = (ts_utc.minute // TS_WINDOW_MINUTES) * TS_WINDOW_MINUTES
    return ts_utc.replace(minute=minute_bucket, second=0, microsecond=0).isoformat()


def _idempotency_key(event: str, subscription_id: str, ts_window: str) -> str:
    return hashlib.sha256(
        f"{event}|{subscription_id}|{ts_window}".encode()
    ).hexdigest()


def _archive_raw(raw: bytes, occurred_at: datetime) -> str:
    settings = get_settings()
    day_dir = settings.archive_dir / occurred_at.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()[:12]
    name = f"{occurred_at.strftime('%H%M%S')}-{occurred_at.microsecond:06d}-{digest}.json"
    (day_dir / name).write_bytes(raw)
    return str((day_dir / name).relative_to(settings.data_dir).as_posix())


def process_webhook(
    conn: sqlite3.Connection,
    headers: Mapping[str, str | None],
    body: bytes,
    *,
    via: str = "direct",
) -> dict:
    """Pure ingest seam: verify → archive → dedupe → record, no HTTP.

    Why this seam: the receiver logic used to live only inside the async
    FastAPI handler, so the D4 replay-storm drill had to speak HTTP to
    prove ingest idempotency — a network-bound test of a database
    property. process_webhook(conn, headers, body) carries the whole
    contract (signature gate, raw-byte archive, 5-minute idempotency
    window) with the connection injected; the routes below and
    app.chaos.replay.fire_replay_storm both call exactly this. Rejections
    raise WebhookRejection (the routes translate to HTTPException), and
    the caller owns the transaction: this function never commits, so
    many deliveries can share one connection/transaction in the drill.

    headers is case-insensitively probed for X-Razorpay-Signature and
    X-Razorpay-Event-Id; body must be the RAW bytes exactly as received
    (the signature covers the wire bytes, never re-serialized JSON).
    """
    settings = get_settings()

    if not settings.razorpay_webhook_secret:
        # Fail loud, accept nothing: the secret must come from the
        # Dashboard registration first (Day-0 webhook protocol).
        raise WebhookRejection(503, "webhook secret not configured")

    signature = _header(headers, "x-razorpay-signature") or ""
    if not verify_signature(body, signature, settings.razorpay_webhook_secret):
        raise WebhookRejection(401, "invalid signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WebhookRejection(400, "invalid JSON body") from None

    # A signature proves the bytes came from Razorpay, not that they carry a
    # well-formed event. A shape-adversarial body (a bare string, a list, an
    # entity that is not an object) must be rejected as a bad request, never
    # crash the receiver into a 500.
    if not isinstance(payload, dict):
        raise WebhookRejection(400, "event payload must be a JSON object")

    event = str(payload.get("event", "unknown"))
    # Event payloads nest the entity under different keys depending on the
    # event family (subscription.* / payment.* / payment_link.* / invoice.*).
    # Key idempotency on the *entity* id — never a blanket "unknown" — or
    # distinct events in the same window would wrongly collapse into one.
    subscription_id = "unknown"
    family_container = payload.get("payload", {})
    if not isinstance(family_container, dict):
        # A Razorpay event always nests entities under payload.<family> —
        # anything else is not an event we can idempotently key or audit.
        raise WebhookRejection(400, "event payload missing entity nesting")
    for family in ("subscription", "payment", "payment_link", "invoice"):
        branch = family_container.get(family, {})
        entity = branch.get("entity") if isinstance(branch, dict) else None
        if isinstance(entity, dict) and entity.get("id"):
            subscription_id = str(entity["id"])
            break
        if isinstance(branch, dict) and "entity" in branch and not isinstance(branch["entity"], dict):
            # shape-adversarial: entity present but not an object
            raise WebhookRejection(400, "event entity must be a JSON object")
    occurred_at = _event_time(payload.get("created_at"))
    received_at = datetime.now(timezone.utc).isoformat()

    idem = _idempotency_key(event, subscription_id, _bucket(occurred_at))
    raw_rel = _archive_raw(body, occurred_at)

    try:
        conn.execute(
            """
            INSERT INTO webhook_events
                (idempotency_key, event_id, event, subscription_id,
                 event_ts_utc, received_ts_utc, payload_json, raw_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idem,
                _header(headers, "x-razorpay-event-id"),
                event,
                subscription_id,
                occurred_at.isoformat(),
                received_at,
                body.decode("utf-8"),
                raw_rel,
            ),
        )
    except sqlite3.IntegrityError:
        return {"status": "duplicate", "idempotency_key": idem, "via": via}

    if event == HALT_EVENT:
        # D6: a freshly accepted halt opens its recovery episode on the same
        # connection — the episode, its ledger row and the event row commit
        # together when the caller commits. Best-effort (see below).
        _run_halt_consumer(conn, idem)
    elif event in VERIFY_EVENTS:
        # D6: a freshly accepted paid event closes the recovery loop — the
        # SENT episode flips to VERIFIED on the same connection (its ledger
        # row commits with the event row). Same best-effort contract.
        _run_verify_consumer(conn, idem)

    return {
        "status": "accepted",
        "event": event,
        "subscription_id": subscription_id,
        "idempotency_key": idem,
        "via": via,
    }


def _run_halt_consumer(conn: sqlite3.Connection, idempotency_key: str) -> None:
    """Best-effort halt→episode creation for a just-stored event row.

    Why a savepoint: the consumer writes on the caller's transaction, so a
    crash mid-episode-creation would otherwise leave a half-written episode
    to commit with the event row — exactly the state-without-evidence gap
    an auditor hunts for. The savepoint isolates the consumer's writes: any
    exception rolls them back cleanly, logs one stdout line with the error,
    and the ingest still returns accepted — the webhook is the durable
    record, and Razorpay's next delivery retries creation — the retry-queue
    flush during the dispatcher stall proved exactly this path.
    """
    row = conn.execute(
        "SELECT * FROM webhook_events WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if row is None:  # pragma: no cover - the insert above just stored it
        return
    conn.execute("SAVEPOINT halt_consumer")
    try:
        maybe_create_episode(conn, row)
    except Exception as exc:  # noqa: BLE001 - ingest contract outranks the consumer
        conn.execute("ROLLBACK TO halt_consumer")
        print(
            f"halt-consumer: episode creation failed for sub={row['subscription_id']}: {exc!r}"
        )
    finally:
        conn.execute("RELEASE halt_consumer")


def _run_verify_consumer(conn: sqlite3.Connection, idempotency_key: str) -> None:
    """Best-effort paid→VERIFIED transition for a just-stored event row.

    Same savepoint contract as _run_halt_consumer: the consumer writes on
    the caller's transaction, so a crash mid-transition would otherwise
    leave a half-written state change to commit with the event row —
    exactly the state-without-evidence gap an auditor hunts for. Any
    exception rolls the savepoint back, prints one stdout line with the
    error, and the ingest still returns accepted — the webhook row is the
    durable record, and the next delivery (or a replay drill) retries the
    verification.
    """
    row = conn.execute(
        "SELECT * FROM webhook_events WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if row is None:  # pragma: no cover - the insert above just stored it
        return
    conn.execute("SAVEPOINT verify_consumer")
    try:
        maybe_verify_episode(conn, row)
    except Exception as exc:  # noqa: BLE001 - ingest contract outranks the consumer
        conn.execute("ROLLBACK TO verify_consumer")
        print(
            f"verify-consumer: verification failed for sub={row['subscription_id']}: {exc!r}"
        )
    finally:
        conn.execute("RELEASE verify_consumer")


def _header(headers: Mapping[str, str | None], name: str) -> str | None:
    """Case-insensitive header probe (callers pass dicts, HTTP passes Title-Case)."""
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
    x_razorpay_event_id: str | None = Header(default=None),
) -> dict:
    return await _handle(request, x_razorpay_signature, x_razorpay_event_id, via="path")


async def root_webhook_handler(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
    x_razorpay_event_id: str | None = Header(default=None),
) -> dict:
    # Tolerance path, registered on the APP root (main.py): a webhook saved
    # without the /webhooks/razorpay suffix still delivers to `/` (observed
    # live — Razorpay POSTs to `/` and retries). Same HMAC verification,
    # same idempotency; marked in the response.
    return await _handle(request, x_razorpay_signature, x_razorpay_event_id, via="root-fallback")


async def _handle(
    request: Request,
    x_razorpay_signature: str,
    x_razorpay_event_id: str | None,
    via: str,
) -> dict:
    raw = await request.body()
    headers = {
        "X-Razorpay-Signature": x_razorpay_signature,
        "X-Razorpay-Event-Id": x_razorpay_event_id,
    }
    try:
        with get_conn() as conn:
            return process_webhook(conn, headers, raw, via=via)
    except WebhookRejection as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
