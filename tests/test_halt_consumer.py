"""D6 halt→episode consumer tests.

Offline, per-test tmp data_dir (house pattern — its own SQLite file, its
own archive). Covers the four D6 gates: a TREATMENT halt through the REAL
receiver path opens a NEW episode with a 200-equivalent ingest result; a
CONTROL halt is observed and ignored (no episode row, the gate line on
stdout); a duplicate halt delivery returns the same episode id with still
exactly one episode row; and a failing consumer never fails the webhook
ingest (event row survives, no partial episode, the error on stdout)."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn, init_db
from app.ingest import halt_consumer
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


def _halt_payload(sub_id: str) -> dict:
    return {
        "event": "subscription.halted",
        "created_at": int(time.time()),
        "payload": {"subscription": {"entity": {"id": sub_id, "status": "halted"}}},
    }


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Fresh store + tmp archive per test, with the webhook secret set."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "razorpay_webhook_secret", SECRET)
    with get_conn() as conn:
        init_db(conn)
        yield conn


def _cohort(conn, sub_id: str, cohort: str) -> None:
    conn.execute(
        "INSERT INTO cohorts (subscription_id, cohort, slot, created_utc) "
        "VALUES (?, ?, 1, ?)",
        (sub_id, cohort, TS),
    )


def _halt_row(conn, sub_id: str, *, ts: str = TS, event: str = "subscription.halted"):
    """Store one halt delivery directly and return its webhook_events row.

    Distinct timestamps → distinct idempotency keys, so two calls simulate
    two REAL deliveries of the same halt (Razorpay retries land like this
    once the 5-minute window has rolled over)."""
    payload = {
        "event": event,
        "created_at": 1756375200,
        "payload": {"subscription": {"entity": {"id": sub_id, "status": "halted"}}},
    }
    key = f"key_{sub_id}_{ts}_{event}"
    conn.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, subscription_id, "
        "event_ts_utc, received_ts_utc, payload_json, raw_path) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
        (key, f"evt_{sub_id}", event, sub_id, ts, ts, json.dumps(payload)),
    )
    return conn.execute(
        "SELECT * FROM webhook_events WHERE idempotency_key = ?", (key,)
    ).fetchone()


def _episode_count(conn, sub_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM episodes WHERE subscription_id = ?", (sub_id,)
    ).fetchone()["c"]


class TestTreatmentPath:
    def test_receiver_creates_episode_for_treatment_halt(self, monkeypatch, tmp_path, capsys):
        """The full live path: signed halt → 200-equivalent ingest → NEW episode."""
        s = get_settings()
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "razorpay_webhook_secret", SECRET)
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with get_conn() as conn:
            init_db(conn)
            _cohort(conn, "sub_T6", "TREATMENT")

        with TestClient(app) as client:
            raw, headers = _signed(_halt_payload("sub_T6"))
            r = client.post("/webhooks/razorpay", content=raw, headers=headers)

        assert r.status_code == 200
        assert r.json()["status"] == "accepted"
        with get_conn() as conn:
            row = conn.execute(
                "SELECT e.state, e.halt_ts_utc, w.event_ts_utc FROM episodes e "
                "JOIN webhook_events w ON w.subscription_id = e.subscription_id "
                "WHERE e.subscription_id = 'sub_T6'"
            ).fetchone()
        assert row is not None
        assert row["state"] == "NEW"
        # halt_ts comes from the event, not the wall clock.
        assert row["halt_ts_utc"] == row["event_ts_utc"]
        assert "halt-consumer: TREATMENT sub=sub_T6" in capsys.readouterr().out

    def test_non_halted_event_returns_none(self, db):
        row = _halt_row(db, "sub_T7", event="payment.failed")
        assert halt_consumer.maybe_create_episode(db, row) is None
        assert _episode_count(db, "sub_T7") == 0


class TestControlGate:
    def test_control_halt_observed_and_ignored(self, db, capsys):
        _cohort(db, "sub_C6", "CONTROL")
        row = _halt_row(db, "sub_C6")

        result = halt_consumer.maybe_create_episode(db, row)

        assert result is None
        assert _episode_count(db, "sub_C6") == 0
        out = capsys.readouterr().out
        assert "halt-consumer: CONTROL sub=sub_C6 observed, no action" in out

    def test_unknown_cohort_ignored(self, db):
        row = _halt_row(db, "sub_U6")
        assert halt_consumer.maybe_create_episode(db, row) is None
        assert _episode_count(db, "sub_U6") == 0


class TestDuplicateDelivery:
    def test_duplicate_halt_returns_same_episode_one_row(self, db):
        _cohort(db, "sub_D6", "TREATMENT")
        first = halt_consumer.maybe_create_episode(db, _halt_row(db, "sub_D6", ts=TS))
        again = halt_consumer.maybe_create_episode(db, _halt_row(db, "sub_D6", ts=TS_LATER))

        assert first is not None and again is not None
        assert again["id"] == first["id"]
        assert again["state"] == "NEW"
        assert _episode_count(db, "sub_D6") == 1
        # The replay added no evidence: exactly one creation ledger row.
        outcomes = [
            r["outcome"]
            for r in db.execute(
                "SELECT outcome FROM audit_ledger WHERE subscription_id = 'sub_D6'"
            ).fetchall()
        ]
        assert outcomes == ["EPISODE_CREATED"]


class TestConsumerFailureIsolation:
    def test_consumer_exception_never_fails_webhook(self, db, monkeypatch, capsys):
        _cohort(db, "sub_X6", "TREATMENT")

        def boom(*args, **kwargs):
            raise RuntimeError("episode creation exploded")

        monkeypatch.setattr(halt_consumer, "create_episode", boom)
        raw, headers = _signed(_halt_payload("sub_X6"))

        result = process_webhook(db, headers, raw)

        assert result["status"] == "accepted"  # the 200-equivalent ingest result
        assert (
            db.execute("SELECT COUNT(*) AS c FROM webhook_events WHERE event = "
                       "'subscription.halted'").fetchone()["c"]
            == 1
        )
        # The savepoint rolled the consumer's partial writes back: no episode.
        assert _episode_count(db, "sub_X6") == 0
        assert "halt-consumer: episode creation failed for sub=sub_X6" in capsys.readouterr().out
