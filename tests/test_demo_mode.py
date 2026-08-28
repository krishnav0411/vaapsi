"""Phase D public-demo mode tests — fail-closed read-only demo deployments.

House fixture pattern (fresh tmp store per test via the cached settings
singleton — nothing touches the real data/ store or .env). Covers the
contract the demo promises a judge: writes 404 with the exact demo body,
reads stay fully live, the ingest surface is gone, /api/mode flags the
demo, seed-on-boot builds a store whose hash chain verifies, and — the
fail-closed core — a demo deployment that also carries real credentials
refuses to boot AT ALL, naming the offending settings without their
values. Demo mode must be invisible when VAAPSI_PUBLIC_DEMO is unset.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audit import ledger as audit_ledger
from app.audit.verify_chain import verify_chain
from app.db import connect, init_db
from app.demo_mode import assert_demo_safe, is_demo_blocked, is_demo_mode
from app.main import app
from app.settings import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]

DEMO_DETAIL = {"detail": "disabled in public demo"}


def _patch_demo_settings(monkeypatch, tmp_path, **overrides):
    """Point the cached settings singleton at a fresh tmp demo deployment."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "public_demo", True)
    monkeypatch.setattr(s, "kill_switch", False)
    monkeypatch.setattr(s, "env_file_path", tmp_path / "env-note")
    for name in ("razorpay_key_id", "razorpay_key_secret", "razorpay_webhook_secret", "llm_api_key"):
        monkeypatch.setattr(s, name, "")
    for name, value in overrides.items():
        monkeypatch.setattr(s, name, value)
    return s


@pytest.fixture()
def demo_client(monkeypatch, tmp_path):
    """A credential-free public-demo boot on a fresh tmp store."""
    _patch_demo_settings(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


# ── Writes are refused, exactly ─────────────────────────────────────────


class TestWritesBlocked:
    def test_api_write_routes_404_with_demo_body(self, demo_client):
        policy = {
            "cooling_hours": 6,
            "outreach_min_interval_hours": 48,
            "max_attempts_per_episode": 3,
            "quiet_hours_start": 21,
            "quiet_hours_end": 9,
            "human_gate_threshold_paise": 50000,
        }
        blocked = [
            demo_client.put("/api/policy/sub_DEMOCUSTOM", json=policy),
            demo_client.post("/api/kill", json={"confirm": "KILL"}),
            demo_client.post(
                "/api/approvals/apr_DEMO/decide", json={"decision": "approve"}
            ),
            demo_client.post("/api/ledger/tamper-demo"),
            demo_client.post("/api/drills/replay_storm/run"),
            demo_client.post("/api/drills/no_such_drill/run"),
        ]
        for r in blocked:
            assert r.status_code == 404, r.text
            assert r.json() == DEMO_DETAIL

    def test_jinja_write_routes_404_too(self, demo_client):
        assert demo_client.post("/dashboard/kill", data={"confirm": "KILL"}).json() == DEMO_DETAIL
        assert demo_client.post("/dashboard/approvals/apr_DEMO/approve").json() == DEMO_DETAIL
        assert demo_client.post("/dashboard/approvals/apr_DEMO/reject").json() == DEMO_DETAIL

    def test_ingest_surface_is_gone(self, demo_client):
        # The webhook receiver (and the root tolerance route) answer 404 —
        # a public demo has no ingest endpoint to probe.
        assert demo_client.post("/webhooks/razorpay", json={}).json() == DEMO_DETAIL
        assert demo_client.post("/webhooks/razorpay/extra/tail", json={}).json() == DEMO_DETAIL
        assert demo_client.post("/", json={}).json() == DEMO_DETAIL

    def test_block_list_is_exact_not_a_blanket_block(self):
        # Fail-closed means writes are refused — not that unknown paths
        # vanish: only the exact write method+path pairs are blocked.
        assert is_demo_blocked("PUT", "/api/policy/sub_1")
        assert is_demo_blocked("POST", "/api/kill")
        assert not is_demo_blocked("GET", "/api/kill")  # method mismatch
        assert not is_demo_blocked("DELETE", "/api/kill")
        assert not is_demo_blocked("POST", "/api/episodes")  # unknown pair
        assert not is_demo_blocked("POST", "/api/kill/extra")
        assert not is_demo_blocked("GET", "/api/overview")


# ── Reads stay fully live ───────────────────────────────────────────────


class TestReadsLive:
    def test_read_routes_stay_200_in_demo(self, demo_client):
        assert demo_client.get("/api/overview").status_code == 200
        assert demo_client.get("/api/episodes").status_code == 200
        assert demo_client.get("/api/metrics").status_code == 200
        assert demo_client.get("/api/ledger").status_code == 200
        assert demo_client.get("/api/ledger/verify").status_code == 200
        assert demo_client.get("/api/drills").status_code == 200
        assert demo_client.get("/api/approvals/pending").status_code == 200
        assert demo_client.get("/api/policy").status_code == 200
        assert demo_client.get("/dashboard").status_code == 200
        assert demo_client.get("/health").status_code == 200

    def test_mode_endpoint_flags_the_demo(self, demo_client):
        body = demo_client.get("/api/mode").json()
        assert body["mode"] == "NORMAL"
        assert body["demo"] is True


# ── Seed-on-boot ────────────────────────────────────────────────────────


class TestSeedOnBoot:
    def test_boot_seeds_a_chain_valid_sanitized_store(self, demo_client):
        settings = get_settings()
        assert settings.db_path.exists()
        conn = connect()
        try:
            rows = list(audit_ledger.iter_rows(conn))
            ok, detail = verify_chain(rows)
            assert ok, detail
            # ~a dozen ledger rows; at least one real recovery for the hero.
            assert 10 <= len(rows) <= 24
            assert conn.execute("SELECT COUNT(*) AS n FROM episodes").fetchone()["n"] == 6
            states = {
                r["state"] for r in conn.execute("SELECT DISTINCT state FROM episodes")
            }
            assert {"NEW", "SENT", "VERIFIED"} <= states
            recovered = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_ledger "
                "WHERE outcome = 'EPISODE_VERIFIED' AND recovered_paise > 0"
            ).fetchone()["n"]
            assert recovered >= 1
            # 30/30 experiment, DEFAULT policy row present.
            cohorts = {
                r["cohort"]: r["n"]
                for r in conn.execute(
                    "SELECT cohort, COUNT(*) AS n FROM cohorts GROUP BY cohort"
                )
            }
            assert cohorts == {"TREATMENT": 30, "CONTROL": 30}
            assert (
                conn.execute(
                    "SELECT COUNT(*) AS n FROM merchant_policies WHERE merchant_id = 'DEFAULT'"
                ).fetchone()["n"]
                == 1
            )
            # Sanitization: fake ids only, no real entities anywhere.
            assert (
                conn.execute(
                    "SELECT COUNT(*) AS n FROM episodes WHERE subscription_id NOT LIKE 'sub_DEMO%'"
                ).fetchone()["n"]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) AS n FROM audit_ledger WHERE subscription_id NOT LIKE 'sub_DEMO%'"
                ).fetchone()["n"]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) AS n FROM webhook_events WHERE event_id NOT LIKE 'evt_DEMO%'"
                ).fetchone()["n"]
                == 0
            )
            events = {
                r["event"]
                for r in conn.execute("SELECT DISTINCT event FROM webhook_events")
            }
            assert "subscription.halted" in events
            assert "payment_link.paid" in events
        finally:
            conn.close()

    def test_boot_with_existing_store_does_not_reseed(self, monkeypatch, tmp_path):
        _patch_demo_settings(monkeypatch, tmp_path)
        conn = connect()
        try:
            init_db(conn)
            audit_ledger.append(
                conn,
                subscription_id="sub_DEMOSENTINEL",
                trigger_event="subscription.halted",
                policy_eval={"decision": "create_episode"},
                outcome="EPISODE_CREATED",
                mode="NORMAL",
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app) as client:
            assert client.get("/api/overview").status_code == 200

        conn = connect()
        try:
            # Exactly the sentinel row: an existing store boots as-is.
            assert conn.execute("SELECT COUNT(*) AS n FROM audit_ledger").fetchone()["n"] == 1
            assert conn.execute("SELECT COUNT(*) AS n FROM episodes").fetchone()["n"] == 0
        finally:
            conn.close()

    def test_seeder_cli_verify_flag(self, tmp_path):
        db = tmp_path / "cli-seed.sqlite3"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.seed_demo",
                "--db",
                str(db),
                "--episodes",
                "6",
                "--verify",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert "chain valid" in proc.stdout


# ── Fail-closed: demo + credentials refuse to boot ──────────────────────


class TestFailClosedCredentials:
    @pytest.mark.parametrize(
        "field",
        ("razorpay_key_id", "razorpay_key_secret", "razorpay_webhook_secret", "llm_api_key"),
    )
    def test_assert_demo_safe_names_the_field_not_the_value(self, monkeypatch, field):
        s = get_settings()
        monkeypatch.setattr(s, "public_demo", True)
        monkeypatch.setattr(s, field, "rzp_test_SUPERSECRETVALUE")
        with pytest.raises(RuntimeError) as exc:
            assert_demo_safe(s)
        assert field in str(exc.value)
        assert "SUPERSECRETVALUE" not in str(exc.value)

    def test_credentials_without_demo_do_not_raise(self, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s, "public_demo", False)
        monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_SUPERSECRETVALUE")
        assert assert_demo_safe(s) is None  # normal deployments keep secrets

    def test_demo_boot_with_a_key_id_raises(self, monkeypatch, tmp_path):
        _patch_demo_settings(
            monkeypatch, tmp_path, razorpay_key_id="rzp_test_FAKEKEYID"
        )
        with pytest.raises(RuntimeError, match="razorpay_key_id"), TestClient(app):
            pass  # lifespan must refuse before the app serves anything


# ── Default off: demo mode is invisible when the env var is unset ───────


class TestDefaultOff:
    def test_unset_demo_leaves_every_route_live(self, monkeypatch, tmp_path):
        s = get_settings()
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "public_demo", False)
        monkeypatch.setattr(s, "kill_switch", False)
        monkeypatch.setattr(s, "env_file_path", tmp_path / "env-note")
        with TestClient(app) as client:
            assert client.get("/api/mode").json() == {"mode": "NORMAL", "demo": False}
            assert client.get("/api/overview").status_code == 200
            # Writes reachable again: the empty-store tamper demo answers 200.
            body = client.post("/api/ledger/tamper-demo").json()
            assert body["verdict"] == "empty_ledger"

    def test_env_alias_parses(self, monkeypatch):
        monkeypatch.setenv("VAAPSI_PUBLIC_DEMO", "1")
        assert Settings(_env_file=None).public_demo is True
        monkeypatch.setenv("VAAPSI_PUBLIC_DEMO", "0")
        assert Settings(_env_file=None).public_demo is False

    def test_is_demo_mode_defaults_false_without_the_field(self):
        class Legacy:
            pass

        assert is_demo_mode(Legacy()) is False
