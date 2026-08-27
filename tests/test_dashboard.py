"""D5 dashboard tests — offline, tmp data_dir, house fixture pattern.

Covers the dashboard contract: pages render 200 with key markers (stat
labels, .tnum present, Stripe tokens in the stylesheet); the kill
endpoint flips the mode banner to KILLED (and a wrong confirmation is a
no-op); approve/reject are thin HTTP skins over app.gates.human_gate.decide
on a PENDING approval (the default ActionClient underneath is the offline
RecordingStub — dispatch is logged, never networked); every metrics
function is zero-safe on an empty DB; and the stylesheet keeps the exact
Stripe shadow formula + #533afd (design-law regression guard). Nothing
here touches the real data/ store or .env: fresh tmp data_dir + tmp
env-file path per test.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.audit import ledger
from app.core.episodes import create_episode, transition
from app.dashboard import metrics
from app.gates import human_gate
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
    """Same tmp store the TestClient app initialized — but seed helpers
    must COMMIT: the dashboard reads through fresh per-request
    connections, and WAL shows them only committed data."""
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


# ── Pages: 200 + key markers ───────────────────────────────────────────


class TestPages:
    def test_overview_renders_stat_labels_tnum_and_banner_controls(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200
        body = r.text
        assert "Recovery rate" in body
        assert "₹ recovered" in body
        assert "Open episodes" in body
        assert "tnum" in body
        assert "updated" in body and "IST" in body
        assert "Cohort A/B" in body
        assert "Recent ledger" in body
        # Kill switch control present while NORMAL (design: ruby outline button).
        assert "/dashboard/kill" in body
        assert "Engine KILLED" not in body

    def test_episodes_page_lists_and_filters(self, client, db):
        ep_t = create_episode(
            db, subscription_id="sub_DASH_T", halt_ts_utc=HALT_TS, cohort="TREATMENT"
        )
        ep_c = create_episode(
            db, subscription_id="sub_DASH_C", halt_ts_utc=HALT_TS, cohort="CONTROL"
        )
        db.commit()
        r = client.get("/dashboard/episodes")
        assert r.status_code == 200
        assert ep_t["id"] in r.text and ep_c["id"] in r.text

        by_cohort = client.get("/dashboard/episodes", params={"cohort": "TREATMENT"})
        assert ep_t["id"] in by_cohort.text
        assert ep_c["id"] not in by_cohort.text

        by_state = client.get("/dashboard/episodes", params={"state": "NEW"})
        assert ep_t["id"] in by_state.text

        empty = client.get("/dashboard/episodes", params={"state": "SENT"})
        assert "No episodes match these filters" in empty.text

    def test_episode_detail_renders_timeline(self, client, db):
        ep = _scored(db, "sub_DASH_TL")
        r = client.get(f"/dashboard/episodes/{ep['id']}")
        assert r.status_code == 200
        assert "Agent timeline" in r.text
        assert "EPISODE_CREATED" in r.text
        assert "subscription.halted" in r.text
        assert "NORMAL" in r.text  # mode badge on timeline nodes

    def test_episode_detail_404(self, client):
        assert client.get("/dashboard/episodes/ep_missing").status_code == 404

    def test_metrics_page_renders_m1_to_m5(self, client):
        r = client.get("/dashboard/metrics")
        assert r.status_code == 200
        assert "M1" in r.text and "M5" in r.text
        assert "0 false outreach" in r.text
        assert "Data freshness" in r.text

    def test_css_served_with_stripe_tokens_and_exact_shadow(self, client):
        r = client.get("/dashboard/static/vaapsi.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["content-type"]
        css = r.text
        assert "#533afd" in css
        assert "#061b31" in css  # headings navy — never #000
        flat = "".join(css.split())
        assert (
            "rgba(50,50,93,0.25)0px30px45px-30px,"
            "rgba(0,0,0,0.1)0px18px36px-18px" in flat
        )
        assert "font-variant-numeric:tabular-nums" in flat


# ── Kill switch ────────────────────────────────────────────────────────


class TestKillSwitch:
    def test_kill_flips_banner_to_killed_and_notes_env(self, client, db, tmp_path):
        r = client.post("/dashboard/kill", data={"confirm": "KILL"})
        assert r.status_code == 200  # followed the 303 back to /dashboard
        assert "KILLED" in r.text
        assert get_settings().kill_switch is True
        note = (tmp_path / "env-note").read_text(encoding="utf-8")
        assert "dashboard kill endpoint" in note
        assert note.count("# VAAPSI_KILL_SWITCH=1") == 1  # idempotent: one note line

    def test_kill_without_confirm_text_is_a_no_op(self, client, db, tmp_path):
        r = client.post("/dashboard/kill", data={"confirm": "resume"})
        assert r.status_code == 200
        assert "Engine KILLED" not in r.text
        assert get_settings().kill_switch is False
        assert not (tmp_path / "env-note").exists()

    def test_reads_stay_available_while_killed(self, client, db):
        client.post("/dashboard/kill", data={"confirm": "KILL"})
        assert client.get("/dashboard/episodes").status_code == 200
        assert client.get("/dashboard/metrics").status_code == 200


# ── Approve / reject: human_gate.decide over HTTP ──────────────────────


class TestApprovalEndpoints:
    def test_approve_calls_decide_and_dispatches_via_stub(self, client, db, monkeypatch):
        episode, approval_id = _gated_with_pending_approval(db, "sub_DASH_APPR")
        calls: list[tuple[str, bool]] = []
        real_decide = human_gate.decide

        def spy(conn, approval_id_, approved, **kwargs):
            calls.append((approval_id_, approved))
            return real_decide(conn, approval_id_, approved, **kwargs)

        monkeypatch.setattr(human_gate, "decide", spy)

        r = client.post(f"/dashboard/approvals/{approval_id}/approve")
        assert r.status_code == 200
        assert calls == [(approval_id, True)]

        assert db.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()["status"] == "APPROVED"
        assert db.execute(
            "SELECT state FROM episodes WHERE id = ?", (episode["id"],)
        ).fetchone()["state"] == "SENT"
        # The dispatch went through the RecordingStub path: the SENT ledger
        # row carries the payment-link payload (logged outreach, no network).
        sent_row = db.execute(
            "SELECT * FROM audit_ledger WHERE trigger_event = 'human_gate.approved' "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        assert sent_row is not None
        # rzp_call persists as canonical JSON text — rehydrate before asserting.
        assert json.loads(sent_row["rzp_call"])["reference_id"] == f"vaapsi:{episode['id'][:24]}:1"

    def test_reject_closes_episode_without_outreach(self, client, db, monkeypatch):
        episode, approval_id = _gated_with_pending_approval(db, "sub_DASH_REJ")
        calls: list[tuple[str, bool]] = []
        real_decide = human_gate.decide

        def spy(conn, approval_id_, approved, **kwargs):
            calls.append((approval_id_, approved))
            return real_decide(conn, approval_id_, approved, **kwargs)

        monkeypatch.setattr(human_gate, "decide", spy)

        r = client.post(f"/dashboard/approvals/{approval_id}/reject")
        assert r.status_code == 200
        assert calls == [(approval_id, False)]

        assert db.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()["status"] == "REJECTED"
        assert db.execute(
            "SELECT state FROM episodes WHERE id = ?", (episode["id"],)
        ).fetchone()["state"] == "CLOSED"
        assert db.execute(
            "SELECT attempt_count FROM episodes WHERE id = ?", (episode["id"],)
        ).fetchone()["attempt_count"] == 0

    def test_double_decide_redirects_without_error(self, client, db):
        episode, approval_id = _gated_with_pending_approval(db, "sub_DASH_DBL")
        assert client.post(f"/dashboard/approvals/{approval_id}/approve").status_code == 200
        second = client.post(f"/dashboard/approvals/{approval_id}/reject")
        assert second.status_code == 200  # graceful redirect, never a 500
        assert (
            db.execute(
                "SELECT state FROM episodes WHERE id = ?", (episode["id"],)
            ).fetchone()["state"]
            == "SENT"
        )

    def test_unknown_approval_404(self, client, db):
        assert client.post("/dashboard/approvals/apr_missing/approve").status_code == 404

    def test_detail_page_shows_pending_approval_actions(self, client, db):
        episode, approval_id = _gated_with_pending_approval(db, "sub_DASH_PEND")
        r = client.get(f"/dashboard/episodes/{episode['id']}")
        assert r.status_code == 200
        assert f"/dashboard/approvals/{approval_id}/approve" in r.text
        assert f"/dashboard/approvals/{approval_id}/reject" in r.text
        assert "Approval required" in r.text

    def test_timeline_shows_policy_eval_and_mode(self, client, db):
        episode, _ = _gated_with_pending_approval(db, "sub_DASH_POL")
        r = client.get(f"/dashboard/episodes/{episode['id']}")
        assert "enqueue_human_gate" in r.text  # policy_eval summary
        assert "NORMAL" in r.text  # mode badges
        assert "human gate" in r.text  # the GATED row carries the gate marker


# ── Metrics: zero-safety + real definitions ────────────────────────────


class TestMetrics:
    def test_all_metrics_zero_safe_on_empty_db(self, db):
        value, n, note = metrics.recovery_rate(db, "TREATMENT")
        assert (value, n) == (None, 0) and "TREATMENT" in note
        assert metrics.recovered_paise_total(db, "CONTROL") == (0, 0, "recovered_paise summed over CONTROL")
        assert metrics.time_to_recover_median(db) == (None, 0, "no recovery events in the ledger yet")
        eff, sent, note = metrics.outreach_efficiency(db)
        assert eff is None and sent == 0 and "no outreach" in note
        count, voids, note = metrics.false_outreach(db)
        assert (count, voids) == (0, 0) and "0 outreach rows" in note
        assert metrics.cohort_counts(db) == {}
        assert metrics.open_episode_count(db) == 0
        assert metrics.recent_ledger(db) == []
        assert metrics.ledger_count(db) == 0

    def test_m1_window_and_cohort_split(self, db):
        rows = [
            ("sub_M1_T1", "TREATMENT", "2026-08-20T10:00:00+00:00", "2026-08-22T10:00:00+00:00"),
            ("sub_M1_T2", "TREATMENT", "2026-08-20T10:00:00+00:00", None),
            # 9 days out — outside the 7-day window, must not count.
            ("sub_M1_T3", "TREATMENT", "2026-08-10T10:00:00+00:00", "2026-08-19T10:00:00+00:00"),
            ("sub_M1_C1", "CONTROL", "2026-08-20T10:00:00+00:00", None),
        ]
        for sub, cohort, halt, recover in rows:
            _cohort(db, sub, cohort)
            _webhook_event(db, "subscription.halted", sub, halt)
            if recover is not None:
                _webhook_event(db, "payment_link.paid", sub, recover)

        rate_t, n_t, note_t = metrics.recovery_rate(db, "TREATMENT")
        # 1 of 3 within the 7-day window (the 08-10→08-19 halt is 9 days out).
        assert (rate_t, n_t) == (pytest.approx(1 / 3), 3)
        assert "1/3" in note_t

        rate_c, n_c, note_c = metrics.recovery_rate(db, "CONTROL")
        assert (rate_c, n_c) == (0.0, 1)
        assert "0/1" in note_c

    def test_m2_m3_m4_from_ledger_rows(self, db):
        _cohort(db, "sub_M3", "TREATMENT")  # M2's cohort split joins the cohorts table
        ep = create_episode(db, subscription_id="sub_M3", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        transition(db, ep["id"], "DIAGNOSED")
        transition(db, ep["id"], "SCORED")
        transition(db, ep["id"], "SENT")
        ledger.append(
            db,
            subscription_id="sub_M3",
            trigger_event="payment_link.paid",
            policy_eval={"decision": "verify"},
            outcome="RECOVERY_VERIFIED",
            recovered_paise=49900,
            mode="NORMAL",
        )

        total, n_rows, _note = metrics.recovered_paise_total(db, "TREATMENT")
        assert (total, n_rows) == (49900, 1)
        hours, n_pairs, _ = metrics.time_to_recover_median(db)
        assert n_pairs == 1 and hours is not None and hours >= 0.0
        eff, sent, note = metrics.outreach_efficiency(db)
        assert sent == 1 and eff == 1.0 and "1 recoveries / 1" in note
        assert metrics.false_outreach(db)[0] == 0

    def test_m5_flags_only_outreach_fired_after_a_stop(self, db):
        # Violation shape: stop event lands FIRST, outreach after it.
        ledger.append(
            db,
            subscription_id="sub_M5_BAD",
            trigger_event="subscription.charged",
            policy_eval={"decision": "void"},
            outcome="EPISODE_VOIDED",
            mode="NORMAL",
        )
        ledger.append(
            db,
            subscription_id="sub_M5_BAD",
            trigger_event="human_gate.approved",
            policy_eval={"decision": "x"},
            human_gate=True,
            outcome="EPISODE_SENT",
            mode="NORMAL",
        )
        # Correct shape: outreach first, stop event after — NOT false outreach.
        ledger.append(
            db,
            subscription_id="sub_M5_OK",
            trigger_event="episode.transition",
            policy_eval={"decision": "x"},
            outcome="EPISODE_SENT",
            mode="NORMAL",
        )
        ledger.append(
            db,
            subscription_id="sub_M5_GOOD",
            trigger_event="subscription.charged",
            policy_eval={"decision": "void"},
            outcome="EPISODE_VOIDED",
            mode="NORMAL",
        )

        count, voids, note = metrics.false_outreach(db)
        assert (count, voids) == (1, 2)
        assert "1 outreach rows" in note and "2 stop voids" in note
