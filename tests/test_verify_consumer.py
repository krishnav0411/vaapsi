"""D6 paid→VERIFIED consumer tests.

Offline, per-test tmp data_dir (house pattern — its own SQLite file, its
own archive). Covers the D6 close-the-loop gates: a payment_link.paid
through the REAL receiver path flips a seeded SENT episode to VERIFIED
with exactly one EPISODE_VERIFIED ledger row and an intact hash chain; a
replayed delivery (same idempotency key) adds no second row or
transition; an event with no vaapsi notes / unknown subscription is
accepted but touches zero episodes; and an event for a sub with no open
episode is a no-op. Extra focus: reference_id prefix resolution and the
invoice.paid event family."""

import hashlib
import hmac
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain
from app.core.episodes import create_episode, transition
from app.db import get_conn, init_db
from app.ingest import verify_consumer
from app.ingest.receiver import process_webhook
from app.main import app
from app.settings import get_settings

SECRET = "test_webhook_secret_0123456789abcdef"
TS = "2026-08-28T10:00:00+00:00"
TS_LATER = "2026-08-28T11:00:00+00:00"  # outside the 5-minute idem window


def _signed(payload: dict, secret: str = SECRET) -> tuple[bytes, dict]:
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Razorpay-Signature": sig, "Content-Type": "application/json"}


def _paid_payload(
    episode_id: str | None,
    *,
    event: str = "payment_link.paid",
    with_notes: bool = True,
) -> dict:
    """A paid-event payload shaped like the real Razorpay wire format.

    episode_id=None builds an event with NO vaapsi trace (no notes, no
    vaapsi reference_id) — the unknown-subscription / missing-notes case;
    with_notes=False keeps the reference_id but drops the notes round-trip
    (forces the reference_id prefix resolution path).
    """
    if event == "invoice.paid":
        entity: dict = {"id": "inv_V", "amount": 49900}
        if episode_id is not None:
            entity["reference_id"] = f"vaapsi:{episode_id[:24]}:1"
            if with_notes:
                entity["notes"] = {"vaapsi_episode_id": episode_id}
        families = {"invoice": {"entity": entity}}
    else:
        link: dict = {"id": "plink_V", "amount": 49900}
        if episode_id is not None:
            link["reference_id"] = f"vaapsi:{episode_id[:24]}:1"
            if with_notes:
                link["notes"] = {"vaapsi_episode_id": episode_id}
        families = {
            "payment_link": {"entity": link},
            "payment": {"entity": {"id": "pay_V", "amount": 49900}},
        }
    return {
        "event": event,
        "created_at": int(time.time()),
        "payload": families,
    }


def _paid_row(
    conn: sqlite3.Connection,
    episode_id: str | None,
    sub_id: str,
    *,
    ts: str = TS,
    event: str = "payment_link.paid",
    with_notes: bool = True,
) -> sqlite3.Row:
    """Store one paid delivery directly and return its webhook_events row.

    Distinct timestamps → distinct idempotency keys, so two calls simulate
    two REAL deliveries of the same paid event (the replay case once the
    5-minute window has rolled over)."""
    payload = _paid_payload(episode_id, event=event, with_notes=with_notes)
    key = f"key_{sub_id}_{ts}_{event}"
    conn.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, subscription_id, "
        "event_ts_utc, received_ts_utc, payload_json, raw_path) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
        (key, f"evt_{sub_id}", event, sub_id, ts, ts, json.dumps(payload)),
    )
    return conn.execute(
        "SELECT * FROM webhook_events WHERE idempotency_key = ?", (key,)
    ).fetchone()


def _seed_sent_episode(conn: sqlite3.Connection, sub_id: str) -> dict:
    """One recovery cycle dispatched: NEW → DIAGNOSED → SCORED → SENT."""
    episode = create_episode(conn, sub_id, TS, cohort="TREATMENT")
    for state in ("DIAGNOSED", "SCORED", "SENT"):
        episode = transition(conn, episode["id"], state)
    return episode


def _episode_state(conn: sqlite3.Connection, episode_id: str) -> str:
    return conn.execute(
        "SELECT state FROM episodes WHERE id = ?", (episode_id,)
    ).fetchone()["state"]


def _verified_rows(conn: sqlite3.Connection, sub_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT trigger_event, recovered_paise FROM audit_ledger "
        "WHERE outcome = 'EPISODE_VERIFIED' AND subscription_id = ?",
        (sub_id,),
    ).fetchall()


def _ledger_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM audit_ledger").fetchone()["c"]


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Fresh store + tmp archive per test, with the webhook secret set."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "razorpay_webhook_secret", SECRET)
    with get_conn() as conn:
        init_db(conn)
        yield conn


class TestReceiverVerifyPath:
    def test_payment_link_paid_verifies_sent_episode(self, monkeypatch, tmp_path, capsys):
        """The full live path: signed paid event → 200 → SENT episode VERIFIED."""
        s = get_settings()
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "razorpay_webhook_secret", SECRET)
        s.data_dir.mkdir(parents=True, exist_ok=True)
        sub_id = "sub_V1"
        with get_conn() as conn:
            init_db(conn)
            episode = _seed_sent_episode(conn, sub_id)

        with TestClient(app) as client:
            raw, headers = _signed(_paid_payload(episode["id"]))
            r = client.post("/webhooks/razorpay", content=raw, headers=headers)

        assert r.status_code == 200
        assert r.json()["status"] == "accepted"
        with get_conn() as conn:
            assert _episode_state(conn, episode["id"]) == "VERIFIED"
            verified = _verified_rows(conn, sub_id)
            ok, detail = verify_chain(list(iter_rows(conn)))
        assert len(verified) == 1
        assert verified[0]["trigger_event"] == "payment_link.paid"
        assert verified[0]["recovered_paise"] == 49900
        assert ok, detail
        assert "verify-consumer" in capsys.readouterr().out

    def test_replayed_event_no_second_row_or_transition(self, monkeypatch, tmp_path, capsys):
        """Same delivery twice: dedupe at ingest, idempotent no-op at consumer."""
        s = get_settings()
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "razorpay_webhook_secret", SECRET)
        s.data_dir.mkdir(parents=True, exist_ok=True)
        sub_id = "sub_V2"
        with get_conn() as conn:
            init_db(conn)
            episode = _seed_sent_episode(conn, sub_id)

        with TestClient(app) as client:
            raw, headers = _signed(_paid_payload(episode["id"]))
            first = client.post("/webhooks/razorpay", content=raw, headers=headers)
            replay = client.post("/webhooks/razorpay", content=raw, headers=headers)

        assert first.status_code == 200
        assert first.json()["status"] == "accepted"
        assert replay.json()["status"] == "duplicate"
        with get_conn() as conn:
            assert _episode_state(conn, episode["id"]) == "VERIFIED"
            assert len(_verified_rows(conn, sub_id)) == 1
        # A re-delivery past the idem window is STILL a no-op: the episode
        # is already VERIFIED, so the consumer adds no second ledger row.
        with get_conn() as conn:
            replay_row = _paid_row(
                conn, episode["id"], sub_id, ts=TS_LATER
            )
            assert verify_consumer.maybe_verify_episode(conn, replay_row) is None
            assert len(_verified_rows(conn, sub_id)) == 1
        assert "already VERIFIED" in capsys.readouterr().out


class TestNonPaidEvent:
    def test_non_paid_event_returns_none(self, db):
        episode = _seed_sent_episode(db, "sub_V3")
        before = _ledger_count(db)
        row = _paid_row(db, episode["id"], "sub_V3", event="payment.captured")

        assert verify_consumer.maybe_verify_episode(db, row) is None

        assert _episode_state(db, episode["id"]) == "SENT"
        assert _ledger_count(db) == before


class TestReferenceResolution:
    def test_reference_id_resolves_episode_without_notes(self, db):
        """Razorpay caps reference_id at 40 chars: the 24-char id prefix resolves."""
        episode = _seed_sent_episode(db, "sub_V4")
        row = _paid_row(db, episode["id"], "sub_V4", with_notes=False)

        verified = verify_consumer.maybe_verify_episode(db, row)

        assert verified is not None
        assert verified["id"] == episode["id"]
        assert verified["state"] == "VERIFIED"
        assert len(_verified_rows(db, "sub_V4")) == 1


class TestInvoicePaid:
    def test_invoice_paid_verifies_sent_episode(self, db):
        episode = _seed_sent_episode(db, "sub_V5")
        row = _paid_row(db, episode["id"], "sub_V5", event="invoice.paid")

        verified = verify_consumer.maybe_verify_episode(db, row)

        assert verified is not None
        assert verified["state"] == "VERIFIED"
        rows = _verified_rows(db, "sub_V5")
        assert len(rows) == 1
        assert rows[0]["trigger_event"] == "invoice.paid"
        assert rows[0]["recovered_paise"] == 49900


class TestNoMatch:
    def test_unknown_subscription_missing_notes_accepted_no_episodes(
        self, monkeypatch, tmp_path, capsys
    ):
        """No vaapsi notes, unknown sub → accepted 200, zero episodes touched."""
        s = get_settings()
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "razorpay_webhook_secret", SECRET)
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with get_conn() as conn:
            init_db(conn)

        with TestClient(app) as client:
            raw, headers = _signed(_paid_payload(None))
            r = client.post("/webhooks/razorpay", content=raw, headers=headers)

        assert r.status_code == 200
        assert r.json()["status"] == "accepted"
        with get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()["c"] == 0
            assert _ledger_count(conn) == 0
        assert "matched no episode" in capsys.readouterr().out


class TestNoOpenEpisode:
    def test_event_for_sub_without_open_episode_noop(self, db, capsys):
        """A VOIDED cycle is not open: the paid event touches nothing."""
        episode = create_episode(db, "sub_V6", TS, cohort="TREATMENT")
        transition(
            db,
            episode["id"],
            "VOIDED",
            ledger_fields={"trigger_event": "subscription.charged"},
            void_reason="charged",
        )
        before = _ledger_count(db)
        # No notes/reference on the entities: resolution falls to the
        # subscription_id column, which has no open (SENT) episode left.
        row = _paid_row(db, None, "sub_V6")

        assert verify_consumer.maybe_verify_episode(db, row) is None

        assert _episode_state(db, episode["id"]) == "VOIDED"
        assert _ledger_count(db) == before
        assert len(_verified_rows(db, "sub_V6")) == 0
        assert "matched no episode" in capsys.readouterr().out


class TestConsumerFailureIsolation:
    def test_consumer_exception_never_fails_webhook(self, db, monkeypatch, capsys):
        episode = _seed_sent_episode(db, "sub_V7")

        def boom(*args, **kwargs):
            raise RuntimeError("verification exploded")

        monkeypatch.setattr(verify_consumer, "transition", boom)
        raw, headers = _signed(_paid_payload(episode["id"]))

        result = process_webhook(db, headers, raw)

        assert result["status"] == "accepted"  # the 200-equivalent ingest result
        assert (
            db.execute(
                "SELECT COUNT(*) AS c FROM webhook_events WHERE event = 'payment_link.paid'"
            ).fetchone()["c"]
            == 1
        )
        # The savepoint rolled the consumer's partial writes back: still SENT.
        assert _episode_state(db, episode["id"]) == "SENT"
        assert _ledger_count(db) == 4  # CREATED + DIAGNOSED + SCORED + SENT only
        assert "verify-consumer: verification failed for sub=" in capsys.readouterr().out
