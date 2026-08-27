"""Day-0 tests: signature verification, tamper rejection, idempotent replay,
and the 503 gate while the webhook secret is unset.

Each test gets its own tmp data_dir — no cross-run state, ever."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings

SECRET = "test_webhook_secret_0123456789abcdef"


def _signed(payload: dict, secret: str = SECRET) -> tuple[bytes, dict]:
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Razorpay-Signature": sig, "Content-Type": "application/json"}


def _halt_payload(sub_id: str = "sub_K0001") -> dict:
    return {
        "event": "subscription.halted",
        "created_at": int(time.time()),
        "payload": {"subscription": {"entity": {"id": sub_id, "status": "halted"}}},
    }


@pytest.fixture()
def client(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_webhook_secret", SECRET)
    monkeypatch.setattr(s, "data_dir", tmp_path)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client_no_secret(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_webhook_secret", "")
    monkeypatch.setattr(s, "data_dir", tmp_path)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        yield c


class TestSignature:
    def test_valid_signature_accepted(self, client):
        raw, headers = _signed(_halt_payload())
        r = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

    def test_tampered_body_rejected_401(self, client):
        raw, headers = _signed(_halt_payload())
        tampered = raw.replace(b"halted", b"active")
        r = client.post("/webhooks/razorpay", content=tampered, headers=headers)
        assert r.status_code == 401

    def test_missing_signature_rejected_401(self, client):
        raw = json.dumps(_halt_payload()).encode()
        r = client.post("/webhooks/razorpay", content=raw)
        assert r.status_code == 401

    def test_wrong_secret_rejected_401(self, client):
        raw, headers = _signed(_halt_payload(), secret="not-the-secret")
        r = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert r.status_code == 401


class TestSecretGate:
    def test_unset_secret_returns_503(self, client_no_secret):
        raw, headers = _signed(_halt_payload())
        r = client_no_secret.post("/webhooks/razorpay", content=raw, headers=headers)
        assert r.status_code == 503


class TestIdempotency:
    def test_exact_replay_is_duplicate(self, client):
        raw, headers = _signed(_halt_payload("sub_IDEM1"))
        r1 = client.post("/webhooks/razorpay", content=raw, headers=headers)
        r2 = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert r1.json()["status"] == "accepted"
        assert r2.json()["status"] == "duplicate"

    def test_replay_with_shuffled_json_is_duplicate(self, client):
        payload = _halt_payload("sub_IDEM2")
        raw1, h1 = _signed(payload)
        reshuffled = dict(reversed(list(payload.items())))
        raw2, h2 = _signed(reshuffled)
        r1 = client.post("/webhooks/razorpay", content=raw1, headers=h1)
        r2 = client.post("/webhooks/razorpay", content=raw2, headers=h2)
        assert r1.json()["status"] == "accepted"
        assert r2.json()["status"] == "duplicate"


class TestNonSubscriptionEntities:
    def test_payment_failed_extracts_payment_id(self, client):
        payload = {
            "event": "payment.failed",
            "created_at": int(time.time()),
            "payload": {"payment": {"entity": {"id": "pay_TEST1", "error_code": "GATEWAY_ERROR"}}},
        }
        raw, headers = _signed(payload)
        r = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert r.status_code == 200
        assert r.json()["subscription_id"] == "pay_TEST1"

    def test_two_different_payments_same_window_both_accepted(self, client):
        t = int(time.time())
        for pid in ("pay_A1", "pay_B2"):
            payload = {
                "event": "payment.failed",
                "created_at": t,
                "payload": {"payment": {"entity": {"id": pid}}},
            }
            raw, headers = _signed(payload)
            r = client.post("/webhooks/razorpay", content=raw, headers=headers)
            assert r.json()["status"] == "accepted"


class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["webhook_secret_set"] is True
