"""D8 ledger-explorer API tests — offline, tmp data_dir, house pattern.

Covers the new /api/ledger* contract: the block-explorer list (hashes
truncated SERVER-SIDE, total = COUNT, chain verdict included), the full
row detail (FULL hashes + canonical JSON that actually recomputes the
row_hash), the verify endpoint (happy chain + a real detected tamper),
and the tamper demo — which must detect the edit on its throwaway COPY,
report expected vs found, leave the live store's chain valid and its rows
byte-identical, and be idempotent across calls. Nothing here touches the
real data/ store or .env.
"""

import pytest
from fastapi.testclient import TestClient

from app.audit import ledger
from app.main import app
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Fresh store per test + kill switch off + tmp .env note target."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "kill_switch", False)
    monkeypatch.setattr(s, "env_file_path", tmp_path / "env-note")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(client):
    """Same tmp store the TestClient app initialized — seed helpers COMMIT:
    the API reads through fresh per-request connections (WAL shows only
    committed data)."""
    from app.db import connect

    conn = connect()
    yield conn
    conn.close()


def _seed_rows(conn, n: int) -> list[int]:
    """Chain-true rows via the house append path; returns their seqs."""
    for i in range(n):
        ledger.append(
            conn,
            subscription_id=f"sub_LEDGER_{i % 2}",
            trigger_event="human_gate.approved" if i == n - 1 else (
                "subscription.halted" if i == 0 else "episode.transition"
            ),
            policy_eval={"decision": "verify", "i": i},
            human_gate=i == n - 1,
            outcome="EPISODE_CREATED" if i == 0 else "EPISODE_SENT",
            recovered_paise=100 * i,
            mode="NORMAL",
        )
    conn.commit()
    return [
        int(r["seq"])
        for r in conn.execute("SELECT seq FROM audit_ledger ORDER BY seq ASC")
    ]


def _live_row(conn, seq: int) -> dict:
    return dict(conn.execute("SELECT * FROM audit_ledger WHERE seq = ?", (seq,)).fetchone())


# ── GET /api/ledger ─────────────────────────────────────────────────────


class TestLedgerList:
    def test_happy_chain_true_truncation_and_actor(self, client, db):
        _seed_rows(db, 3)
        r = client.get("/api/ledger")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"rows", "total", "chain_valid"}
        assert body["chain_valid"] is True
        assert body["total"] == 3
        assert len(body["rows"]) == 3
        first = body["rows"][0]
        assert set(first) == {
            "seq", "ts_utc", "trigger_event", "actor", "outcome",
            "subscription_id", "prev_hash", "hash",
        }
        assert len(first["prev_hash"]) == 12
        assert len(first["hash"]) == 16
        assert first["hash"] != first["prev_hash"]
        # actor: the human-gate row is human, agent rows are the agent
        by_seq = {row["seq"]: row for row in body["rows"]}
        assert by_seq[3]["actor"] == "human"  # seeded human_gate=True row
        assert by_seq[1]["actor"] == "agent"
        # chain order: seq ASC, row 1 links to the genesis sentinel
        assert [row["seq"] for row in body["rows"]] == [1, 2, 3]
        assert by_seq[1]["prev_hash"] == ("0" * 64)[:12]

    def test_empty_store_is_valid_not_an_error(self, client, db):
        r = client.get("/api/ledger")
        assert r.status_code == 200
        body = r.json()
        assert body == {"rows": [], "total": 0, "chain_valid": True}

    def test_limit_and_offset_slice(self, client, db):
        _seed_rows(db, 5)
        page = client.get("/api/ledger", params={"limit": 2, "offset": 1})
        assert page.status_code == 200
        rows = page.json()["rows"]
        assert [r["seq"] for r in rows] == [2, 3]
        assert page.json()["total"] == 5  # total stays the full COUNT


# ── GET /api/ledger/{seq} ───────────────────────────────────────────────


class TestLedgerRowDetail:
    def test_full_row_with_recomputable_canonical_json(self, client, db):
        (seq,) = _seed_rows(db, 1)
        r = client.get(f"/api/ledger/{seq}")
        assert r.status_code == 200
        body = r.json()
        assert body["seq"] == seq
        assert len(body["prev_hash"]) == 64
        assert len(body["row_hash"]) == 64
        assert body["prev_seq"] is None  # row 1 has no predecessor
        assert body["policy_eval"] == {"decision": "verify", "i": 0}
        assert body["human_gate"] is True  # the single seeded row is the gate row
        assert body["outcome"] == "EPISODE_CREATED"
        assert body["recovered_paise"] == 0
        # canonical JSON is EXACTLY the verifier's hash material
        import hashlib

        material = body["prev_hash"] + body["canonical_json"]
        assert hashlib.sha256(material.encode("utf-8")).hexdigest() == body["row_hash"]

    def test_prev_seq_links_to_predecessor(self, client, db):
        seqs = _seed_rows(db, 2)
        r = client.get(f"/api/ledger/{seqs[1]}")
        assert r.status_code == 200
        assert r.json()["prev_seq"] == seqs[0]

    def test_unknown_seq_404(self, client, db):
        assert client.get("/api/ledger/999").status_code == 404

    def test_non_numeric_seq_is_not_a_detail_lookup(self, client, db):
        # the literal verify/tamper routes must not be swallowed by {seq}
        assert client.get("/api/ledger/verify").status_code == 200


# ── GET /api/ledger/verify ──────────────────────────────────────────────


class TestLedgerVerify:
    def test_empty_chain_is_valid(self, client, db):
        r = client.get("/api/ledger/verify")
        assert r.status_code == 200
        assert r.json() == {"valid": True, "rows": 0, "broken_seq": None, "detail": "chain valid (0 rows)"}

    def test_happy_chain(self, client, db):
        _seed_rows(db, 4)
        body = client.get("/api/ledger/verify").json()
        assert body["valid"] is True
        assert body["rows"] == 4
        assert body["broken_seq"] is None

    def test_detects_a_real_tamper_with_broken_seq(self, client, db):
        seqs = _seed_rows(db, 3)
        tampered_seq = seqs[1]
        db.execute(
            "UPDATE audit_ledger SET recovered_paise = recovered_paise + 1 WHERE seq = ?",
            (tampered_seq,),
        )
        db.commit()
        body = client.get("/api/ledger/verify").json()
        assert body["valid"] is False
        assert body["broken_seq"] == tampered_seq
        assert "row_hash" in body["detail"]


# ── POST /api/ledger/tamper-demo ────────────────────────────────────────


class TestTamperDemo:
    def test_detects_on_copy_and_leaves_live_store_intact(self, client, db):
        seqs = _seed_rows(db, 3)
        live_before = {r["seq"]: r for r in db.execute("SELECT * FROM audit_ledger")}

        r = client.post("/api/ledger/tamper-demo")
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "tamper_detected"
        assert body["field"] == "recovered_paise"
        assert body["broken_seq"] in seqs
        assert body["found_value"] == body["expected_value"] + 1
        assert len(body["stored_hash"]) == 64
        assert len(body["recomputed_hash"]) == 64
        assert body["stored_hash"] != body["recomputed_hash"]
        assert "row_hash" in body["verify_detail"]
        assert body["rows"] == 3
        assert body["original_store_chain_valid"] is True
        assert body["original_rows"] == 3

        # the live store: same rows, same hashes, chain still verifies
        live_after = {r["seq"]: r for r in db.execute("SELECT * FROM audit_ledger")}
        assert live_after == live_before
        assert client.get("/api/ledger/verify").json()["valid"] is True

    def test_idempotent_fresh_copy_per_call(self, client, db):
        _seed_rows(db, 2)
        first = client.post("/api/ledger/tamper-demo").json()
        second = client.post("/api/ledger/tamper-demo").json()
        assert first["verdict"] == second["verdict"] == "tamper_detected"
        assert first["broken_seq"] == second["broken_seq"]
        assert first["expected_value"] == second["expected_value"]

    def test_empty_ledger_is_reported_not_raised(self, client, db):
        r = client.post("/api/ledger/tamper-demo")
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "empty_ledger"
        assert body["broken_seq"] is None
        assert body["original_store_chain_valid"] is True
        # and the live store still verifies afterwards
        assert client.get("/api/ledger/verify").json()["valid"] is True
