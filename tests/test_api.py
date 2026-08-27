"""D7.1 JSON API tests — offline, tmp data_dir, house fixture pattern.

Mirrors tests/test_dashboard.py: fresh tmp store + kill switch off + tmp
env-file path per test (nothing here touches the real data/ store or
.env). Covers the API contract: every read route 200 with a sane
shape on seeded data (and zero-safe on an empty one); filters match the
Jinja index's contract; episode detail 404s on unknown ids; the kill
endpoint flips the mode to KILLED through the SAME killswitch module (a
wrong confirm is a 400 no-op, and re-firing stays idempotent); the decide
endpoint is a thin JSON skin over app.gates.human_gate.decide (spy-verified,
default ActionClient underneath is the offline RecordingStub); and GET
routes never write — the ledger is byte-for-byte unchanged across them.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.audit import ledger
from app.core.episodes import create_episode, transition
from app.dashboard import killswitch
from app.gates import human_gate
from app.main import app
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Fresh store per test + kill switch off + tmp .env kill target."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "kill_switch", False)
    monkeypatch.setattr(s, "env_file_path", tmp_path / "env-note")
    monkeypatch.setattr(killswitch, "ENV_PATH", tmp_path / "env-note")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(client):
    """Same tmp store the TestClient app initialized — but seed helpers
    must COMMIT: the API reads through fresh per-request connections,
    and WAL shows them only committed data."""
    from app.db import connect

    conn = connect()
    yield conn
    conn.close()


# ── Seed helpers (house paths, so seeded evidence is chain-true) ───────


def _scored(conn, subscription_id: str, cohort: str = "TREATMENT") -> dict:
    ep = create_episode(conn, subscription_id=subscription_id, halt_ts_utc=HALT_TS, cohort=cohort)
    ep = transition(conn, ep["id"], "DIAGNOSED")
    ep = transition(conn, ep["id"], "SCORED")
    conn.commit()
    return ep


def _gated_with_pending_approval(conn, subscription_id: str) -> tuple[dict, str]:
    episode = _scored(conn, subscription_id)
    approval_id = human_gate.enqueue_for_approval(conn, episode, "tier3_escalation")
    conn.commit()
    return episode, approval_id


def _cohort(conn, subscription_id: str, cohort: str) -> None:
    conn.execute(
        "INSERT INTO cohorts (subscription_id, cohort, slot, customer_id, rzp_status, "
        "short_url, created_utc) VALUES (?, ?, 0, NULL, NULL, NULL, ?)",
        (subscription_id, cohort, HALT_TS),
    )
    conn.commit()


def _webhook_event(conn, event: str, subscription_id: str, ts_utc: str) -> None:
    conn.execute(
        "INSERT INTO webhook_events (idempotency_key, event_id, event, subscription_id, "
        "event_ts_utc, received_ts_utc, payload_json, raw_path) "
        "VALUES (?, NULL, ?, ?, ?, ?, '{}', NULL)",
        (f"idem-{uuid.uuid4().hex}", event, subscription_id, ts_utc, ts_utc),
    )
    conn.commit()


# ── Read routes: 200 + sane shapes ─────────────────────────────────────


class TestOverview:
    def test_shape_on_seeded_db(self, client, db):
        _cohort(db, "sub_API_OV", "TREATMENT")
        ep = _scored(db, "sub_API_OV")
        r = client.get("/api/overview")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"stats", "cohorts", "mode"}
        assert body["mode"] == "NORMAL"
        assert body["cohorts"] == {"TREATMENT": 1}
        stats = body["stats"]
        assert stats["recovered_paise"] == 0  # integer paise, never float
        assert stats["open_episodes"] == 1
        assert stats["recovery_rate_treatment"]["value"] is None  # 0/0 is undefined
        assert stats["recovery_rate_treatment"]["n"] == 0
        assert ep["id"]

    def test_zero_safe_on_empty_db(self, client):
        r = client.get("/api/overview")
        assert r.status_code == 200
        body = r.json()
        assert body["stats"]["recovered_paise"] == 0
        assert body["cohorts"] == {}
        assert body["mode"] == "NORMAL"

    def test_recovered_paise_from_ledger_is_integer_paise(self, client, db):
        _cohort(db, "sub_API_M2", "TREATMENT")
        ep = _scored(db, "sub_API_M2")
        transition(db, ep["id"], "SENT")
        ledger.append(
            db,
            subscription_id="sub_API_M2",
            trigger_event="payment_link.paid",
            policy_eval={"decision": "verify"},
            outcome="RECOVERY_VERIFIED",
            recovered_paise=49900,
            mode="NORMAL",
        )
        db.commit()  # the API reads committed data only (WAL)
        body = client.get("/api/overview").json()
        assert body["stats"]["recovered_paise"] == 49900
        assert isinstance(body["stats"]["recovered_paise"], int)


class TestEpisodes:
    def test_list_and_filters(self, client, db):
        ep_t = create_episode(
            db, subscription_id="sub_API_T", halt_ts_utc=HALT_TS, cohort="TREATMENT"
        )
        ep_c = create_episode(
            db, subscription_id="sub_API_C", halt_ts_utc=HALT_TS, cohort="CONTROL"
        )
        db.commit()

        rows = client.get("/api/episodes").json()
        assert {r["id"] for r in rows} == {ep_t["id"], ep_c["id"]}
        # Same shape the Jinja templates consume: full episode row + flag
        # + the Stage C per-episode recovered total (0 before any recovery).
        assert all(
            {"pending_approval", "recovered_paise"} <= set(r) and r["recovered_paise"] == 0
            for r in rows
        )

        by_cohort = client.get("/api/episodes", params={"cohort": "TREATMENT"}).json()
        assert {r["id"] for r in by_cohort} == {ep_t["id"]}

        by_state = client.get("/api/episodes", params={"state": "NEW"}).json()
        assert {r["id"] for r in by_state} == {ep_t["id"], ep_c["id"]}

        empty = client.get("/api/episodes", params={"state": "SENT"}).json()
        assert empty == []

        # Unknown filter values are ignored (Jinja contract), never errors.
        junk = client.get("/api/episodes", params={"state": "NOT_A_STATE"}).json()
        assert len(junk) == 2

    def test_rows_carry_per_episode_recovered_paise(self, client, db):
        """Stage C Task A: the listing joins a real Amount per episode —
        SUM(recovered_paise) over the SAME window _episode_ledger uses
        (same subscription, stamped at/after creation). Rows before
        creation belong to an older cycle and must not count; an episode
        with no matching ledger rows reports an honest 0."""
        ep_paid = _scored(db, "sub_API_AMT")
        transition(db, ep_paid["id"], "SENT")
        ledger.append(
            db,
            subscription_id="sub_API_AMT",
            trigger_event="payment_link.paid",
            policy_eval={"decision": "verify"},
            outcome="RECOVERY_VERIFIED",
            recovered_paise=49900,
            mode="NORMAL",
        )
        ledger.append(
            db,
            subscription_id="sub_API_AMT",
            trigger_event="episode.transition",
            policy_eval={"decision": "transition", "from_state": "SENT", "to_state": "VERIFIED"},
            outcome="EPISODE_VERIFIED",
            recovered_paise=2500,
            mode="NORMAL",
        )
        ep_zero = create_episode(db, subscription_id="sub_API_ZERO", halt_ts_utc=HALT_TS)
        ledger.append(
            db,
            subscription_id="sub_API_ZERO",
            trigger_event="payment_link.paid",
            policy_eval={"decision": "verify"},
            outcome="RECOVERY_VERIFIED",
            recovered_paise=99000,
            mode="NORMAL",
            ts_utc="2026-08-27T00:00:00+00:00",  # before ep_zero's creation
        )
        db.commit()  # the API reads committed data only (WAL)

        rows = {r["id"]: r for r in client.get("/api/episodes").json()}
        assert rows[ep_paid["id"]]["recovered_paise"] == 52400  # integer paise
        assert rows[ep_zero["id"]]["recovered_paise"] == 0

    def test_detail_episode_row_carries_recovered_paise(self, client, db):
        ep = _scored(db, "sub_API_AMTD")
        transition(db, ep["id"], "SENT")
        ledger.append(
            db,
            subscription_id="sub_API_AMTD",
            trigger_event="payment_link.paid",
            policy_eval={"decision": "verify"},
            outcome="RECOVERY_VERIFIED",
            recovered_paise=49900,
            mode="NORMAL",
        )
        db.commit()
        body = client.get(f"/api/episodes/{ep['id']}").json()
        assert body["episode"]["recovered_paise"] == 49900
        assert isinstance(body["episode"]["recovered_paise"], int)

    def test_detail_returns_ordered_timeline(self, client, db):
        ep = _scored(db, "sub_API_TL")
        r = client.get(f"/api/episodes/{ep['id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["episode"]["id"] == ep["id"]
        timeline = body["timeline"]
        assert [row["outcome"] for row in timeline] == [
            "EPISODE_CREATED",
            "EPISODE_DIAGNOSED",
            "EPISODE_SCORED",
        ]
        assert [row["seq"] for row in timeline] == sorted(row["seq"] for row in timeline)
        assert timeline[0]["trigger_event"] == "subscription.halted"
        assert body["pending_approval"] is None  # SCORED, nothing queued

    def test_detail_shows_pending_approval_for_gated(self, client, db):
        episode, approval_id = _gated_with_pending_approval(db, "sub_API_PEND")
        body = client.get(f"/api/episodes/{episode['id']}").json()
        assert body["pending_approval"]["id"] == approval_id
        assert body["pending_approval"]["status"] == "PENDING"

    def test_detail_404_on_unknown_id(self, client):
        assert client.get("/api/episodes/ep_missing").status_code == 404


class TestMetricsAndMode:
    def test_metrics_shape_m1_to_m5_zero_safe(self, client):
        r = client.get("/api/metrics")
        assert r.status_code == 200
        rows = r.json()
        names = [row["name"] for row in rows]
        assert names == [
            "M1_recovery_rate_TREATMENT",
            "M1_recovery_rate_CONTROL",
            "M2_recovered_paise",
            "M3_time_to_recover_hours_median",
            "M4_outreach_efficiency",
            "M5_false_outreach",
        ]
        for row in rows:
            assert set(row) == {"name", "value", "n", "note"}
            assert isinstance(row["note"], str) and row["note"]
            assert isinstance(row["n"], int)
        # Zero-safety, verbatim from the metrics module's contract:
        m1_t, m2, m3, m4, m5 = rows[0], rows[2], rows[3], rows[4], rows[5]
        assert m1_t["value"] is None and m1_t["n"] == 0
        assert m2["value"] == 0 and m2["n"] == 0
        assert m3["value"] is None and m3["n"] == 0
        assert m4["value"] is None and m4["n"] == 0
        assert m5["value"] == 0 and m5["n"] == 0

    def test_metrics_reuse_pre_registered_definitions(self, client, db):
        rows = [
            ("sub_API_M1a", "TREATMENT", "2026-08-20T10:00:00+00:00", "2026-08-22T10:00:00+00:00"),
            ("sub_API_M1b", "TREATMENT", "2026-08-20T10:00:00+00:00", None),
        ]
        for sub, cohort, halt, recover in rows:
            _cohort(db, sub, cohort)
            _webhook_event(db, "subscription.halted", sub, halt)
            if recover is not None:
                _webhook_event(db, "payment_link.paid", sub, recover)
        rows = client.get("/api/metrics").json()
        m1_t = rows[0]
        assert m1_t["value"] == pytest.approx(1 / 2) and m1_t["n"] == 2
        assert "1/2" in m1_t["note"]

    def test_mode_endpoint(self, client):
        assert client.get("/api/mode").status_code == 200
        assert client.get("/api/mode").json() == {"mode": "NORMAL"}


class TestReadOnlyGuarantee:
    def test_get_routes_write_no_ledger_rows(self, client, db):
        ep = _scored(db, "sub_API_RO")
        before = db.execute("SELECT COUNT(*) AS n FROM audit_ledger").fetchone()["n"]
        assert client.get("/api/overview").status_code == 200
        assert client.get("/api/episodes").status_code == 200
        assert client.get(f"/api/episodes/{ep['id']}").status_code == 200
        assert client.get("/api/metrics").status_code == 200
        assert client.get("/api/mode").status_code == 200
        after = db.execute("SELECT COUNT(*) AS n FROM audit_ledger").fetchone()["n"]
        assert before == after


# ── Kill endpoint: same switch, same ritual ────────────────────────────


class TestKillEndpoint:
    def test_kill_flips_mode_to_killed_and_persists(self, client, db, tmp_path):
        r = client.post("/api/kill", json={"confirm": "KILL"})
        assert r.status_code == 200
        assert r.json() == {"mode": "KILLED"}
        assert client.get("/api/mode").json() == {"mode": "KILLED"}
        assert get_settings().kill_switch is True
        env_note = (tmp_path / "env-note").read_text(encoding="utf-8")
        assert "VAAPSI_KILL_SWITCH=true" in env_note  # same killswitch module

    def test_wrong_confirm_is_400_no_op(self, client, tmp_path):
        r = client.post("/api/kill", json={"confirm": "resume"})
        assert r.status_code == 400
        assert get_settings().kill_switch is False
        assert client.get("/api/mode").json() == {"mode": "NORMAL"}
        assert not (tmp_path / "env-note").exists()

    def test_kill_is_idempotent(self, client, tmp_path):
        assert client.post("/api/kill", json={"confirm": "KILL"}).status_code == 200
        assert client.post("/api/kill", json={"confirm": "KILL"}).status_code == 200
        note = (tmp_path / "env-note").read_text(encoding="utf-8")
        assert note.count("VAAPSI_KILL_SWITCH=true") == 1

    def test_reads_stay_available_while_killed(self, client):
        client.post("/api/kill", json={"confirm": "KILL"})
        assert client.get("/api/overview").status_code == 200
        assert client.get("/api/episodes").status_code == 200


# ── Decide endpoint: human_gate.decide over JSON ───────────────────────


class TestDecideEndpoint:
    def test_approve_calls_decide_and_dispatches_via_stub(self, client, db, monkeypatch):
        episode, approval_id = _gated_with_pending_approval(db, "sub_API_APPR")
        calls: list[tuple[str, bool]] = []
        real_decide = human_gate.decide

        def spy(conn, approval_id_, approved, **kwargs):
            calls.append((approval_id_, approved))
            return real_decide(conn, approval_id_, approved, **kwargs)

        monkeypatch.setattr(human_gate, "decide", spy)

        r = client.post(
            f"/api/approvals/{approval_id}/decide", json={"decision": "approve"}
        )
        assert r.status_code == 200
        assert calls == [(approval_id, True)]
        body = r.json()
        assert body["status"] == "APPROVED"
        assert body["episode_state_after"] == "SENT"
        assert db.execute(
            "SELECT state FROM episodes WHERE id = ?", (episode["id"],)
        ).fetchone()["state"] == "SENT"
        sent_row = db.execute(
            "SELECT * FROM audit_ledger WHERE trigger_event = 'human_gate.approved' "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        assert sent_row is not None
        # The offline RecordingStub path: the SENT row carries the payload.
        assert json.loads(sent_row["rzp_call"])["reference_id"].startswith("vaapsi:")

    def test_reject_closes_episode_without_outreach(self, client, db, monkeypatch):
        episode, approval_id = _gated_with_pending_approval(db, "sub_API_REJ")
        calls: list[tuple[str, bool]] = []
        real_decide = human_gate.decide

        def spy(conn, approval_id_, approved, **kwargs):
            calls.append((approval_id_, approved))
            return real_decide(conn, approval_id_, approved, **kwargs)

        monkeypatch.setattr(human_gate, "decide", spy)

        r = client.post(
            f"/api/approvals/{approval_id}/decide", json={"decision": "reject"}
        )
        assert r.status_code == 200
        assert calls == [(approval_id, False)]
        body = r.json()
        assert body["status"] == "REJECTED"
        assert body["episode_state_after"] == "CLOSED"
        assert db.execute(
            "SELECT attempt_count FROM episodes WHERE id = ?", (episode["id"],)
        ).fetchone()["attempt_count"] == 0

    def test_double_decide_is_409(self, client, db):
        _, approval_id = _gated_with_pending_approval(db, "sub_API_DBL")
        assert (
            client.post(
                f"/api/approvals/{approval_id}/decide", json={"decision": "approve"}
            ).status_code
            == 200
        )
        second = client.post(
            f"/api/approvals/{approval_id}/decide", json={"decision": "reject"}
        )
        assert second.status_code == 409

    def test_unknown_approval_404(self, client):
        r = client.post("/api/approvals/apr_missing/decide", json={"decision": "approve"})
        assert r.status_code == 404

    def test_invalid_decision_is_422(self, client):
        r = client.post("/api/approvals/apr_x/decide", json={"decision": "maybe"})
        assert r.status_code == 422
