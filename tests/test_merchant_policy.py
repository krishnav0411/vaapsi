"""Per-merchant policy table tests (D8) — offline, tmp data_dir, house pattern.

Mirrors tests/test_policy.py and tests/test_api.py: every test gets a fresh
tmp store (its own SQLite file), an explicitly-off kill switch, and a frozen
clock where timing matters — nothing leaks in from the local .env or a
previous test. Covers: the DEFAULT row (seeded on startup AND self-healing
on first read, values byte-for-byte the frozen engine constants), the
engine reading a merchant's row (custom respected, unknown → DEFAULT
fallback), PUT /api/policy/{merchant_id} validation (each field's range,
quiet-hours window, the DEFAULT row's fail-closed 403), the API happy path,
the dashboard Policy card, and one end-to-end evaluation through a custom
merchant row (API write → engine verdict)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.episodes import create_episode, transition
from app.db import get_conn, init_db
from app.main import app
from app.policy import engine, merchant
from app.policy.engine import evaluate
from app.settings import get_settings

HALT_TS = "2026-08-28T05:00:00+00:00"
# 10:00 UTC == 15:30 IST — the DEFAULT outreach window is open at this instant.
FROZEN_NOW = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)

FROZEN_DEFAULTS = {
    "merchant_id": "DEFAULT",
    "cooling_hours": 6,
    "outreach_min_interval_hours": 48,
    "max_attempts_per_episode": 3,
    "quiet_hours_start": 21,
    "quiet_hours_end": 9,
    "human_gate_threshold_paise": 50000,
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Fresh store per test + kill switch off — the TestClient lifespan runs
    init_db + ensure_default_row exactly like a real start."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "kill_switch", False)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(client):
    """Same tmp store the TestClient app initialized — seed helpers must
    COMMIT (the API reads through fresh per-request connections)."""
    from app.db import connect

    conn = connect()
    yield conn
    conn.close()


@pytest.fixture()
def db_only(monkeypatch, tmp_path):
    """Engine-level store WITHOUT the app lifespan: init_db only, so the
    DEFAULT row must self-heal on first read (get_policy's INSERT OR IGNORE)."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "kill_switch", False)
    with get_conn() as conn:
        init_db(conn)
        yield conn


@pytest.fixture()
def freeze_clock(monkeypatch):
    monkeypatch.setattr(engine, "_now_utc", lambda: FROZEN_NOW)


def _scored(conn, subscription_id: str, cohort: str = "TREATMENT") -> dict:
    """Clean TREATMENT episode driven NEW → DIAGNOSED → SCORED."""
    ep = create_episode(conn, subscription_id=subscription_id, halt_ts_utc=HALT_TS, cohort=cohort)
    ep = transition(conn, ep["id"], "DIAGNOSED")
    return transition(conn, ep["id"], "SCORED")


def _set_outreach(conn, episode_id: str, hours_ago: float) -> None:
    """Stamp last_action as if an outreach happened `hours_ago` before the frozen now."""
    ts = (FROZEN_NOW - timedelta(hours=hours_ago)).isoformat()
    conn.execute("UPDATE episodes SET last_action_ts_utc = ? WHERE id = ?", (ts, episode_id))


# ── The DEFAULT row: seeded, self-healing, exactly the frozen constants ──


def test_default_row_seeded_on_startup(db):
    """The lifespan's ensure_default_row leaves exactly one DEFAULT row with
    the frozen constants — no env var, no API call, no LLM in the loop."""
    merchant.ensure_default_row(db)  # startup path; idempotent by design
    db.commit()
    merchant.ensure_default_row(db)  # a second start must not duplicate
    rows = db.execute("SELECT * FROM merchant_policies").fetchall()
    assert len(rows) == 1
    assert dict(rows[0]) == FROZEN_DEFAULTS


def test_default_row_self_heals_on_first_read(db_only):
    """init_db alone (no lifespan): the first read seeds the DEFAULT row —
    a wiped/missing row can never wedge the engine."""
    assert db_only.execute("SELECT COUNT(*) AS n FROM merchant_policies").fetchone()["n"] == 0
    assert merchant.get_policy(db_only, None) == FROZEN_DEFAULTS
    assert dict(
        db_only.execute(
            "SELECT * FROM merchant_policies WHERE merchant_id = 'DEFAULT'"
        ).fetchone()
    ) == FROZEN_DEFAULTS


def test_default_row_values_match_frozen_engine_constants(db):
    """The DEFAULT row IS the frozen envelope: equal to the constants engine
    re-exports (scorecard, human_gate and request_retry import these)."""
    row = dict(db.execute("SELECT * FROM merchant_policies WHERE merchant_id = 'DEFAULT'").fetchone())
    assert row["cooling_hours"] == engine.COOLING_HOURS == 6
    assert row["outreach_min_interval_hours"] == engine.OUTREACH_MIN_INTERVAL_HOURS == 48
    assert row["max_attempts_per_episode"] == engine.MAX_ATTEMPTS_PER_EPISODE == 3
    assert (row["quiet_hours_start"], row["quiet_hours_end"]) == engine.QUIET_HOURS_IST == (21, 9)
    assert row["human_gate_threshold_paise"] == engine.HUMAN_GATE_THRESHOLD_PAISE == 50000


# ── Engine reads the merchant's row ─────────────────────────────────────


def test_engine_reads_default_for_unknown_merchant(db_only, freeze_clock):
    """Unknown merchant → DEFAULT row: same verdict, same evidence dict as
    the historical frozen behavior."""
    ep = _scored(db_only, "sub_UNK1")

    known = evaluate(db_only, ep["subscription_id"], ep)
    unknown = evaluate(db_only, ep["subscription_id"], ep, merchant_id="mer_NOSUCH")

    assert known.ok is True and known.action == "SEND"
    assert unknown == known


def test_custom_row_overrides_cooling(db, freeze_clock):
    """cooling_hours=12: outreach 7h ago is still cooling for this merchant
    (DEFAULT's 6h would already have moved on to the 48h interval rule)."""
    ep = _scored(db, "sub_COOLM1")
    _set_outreach(db, ep["id"], hours_ago=7)
    db.execute(
        "INSERT INTO merchant_policies (merchant_id, cooling_hours, "
        "outreach_min_interval_hours, max_attempts_per_episode, quiet_hours_start, "
        "quiet_hours_end, human_gate_threshold_paise) "
        "VALUES ('mer_COOLM', 12, 48, 3, 21, 9, 50000)"
    )
    db.commit()
    merchant.clear_policy_cache()

    decision = evaluate(db, ep["subscription_id"], ep, merchant_id="mer_COOLM")

    assert decision.ok is False
    assert decision.reason == "cooling_off"
    assert decision.details["cooling_hours"] == 12


def test_custom_row_overrides_max_attempts(db, freeze_clock):
    ep = _scored(db, "sub_CAPM1")
    db.execute("UPDATE episodes SET attempt_count = 1 WHERE id = ?", (ep["id"],))
    db.execute(
        "INSERT INTO merchant_policies (merchant_id, cooling_hours, "
        "outreach_min_interval_hours, max_attempts_per_episode, quiet_hours_start, "
        "quiet_hours_end, human_gate_threshold_paise) "
        "VALUES ('mer_CAPM', 6, 48, 1, 21, 9, 50000)"
    )
    db.commit()
    merchant.clear_policy_cache()

    decision = evaluate(db, ep["subscription_id"], ep, merchant_id="mer_CAPM")

    assert decision.ok is False
    assert decision.reason == "max_attempts"
    assert decision.details["max_attempts"] == 1


def test_custom_row_overrides_min_interval(db, freeze_clock):
    ep = _scored(db, "sub_INTM1")
    _set_outreach(db, ep["id"], hours_ago=49)
    db.execute(
        "INSERT INTO merchant_policies (merchant_id, cooling_hours, "
        "outreach_min_interval_hours, max_attempts_per_episode, quiet_hours_start, "
        "quiet_hours_end, human_gate_threshold_paise) "
        "VALUES ('mer_INVM', 6, 72, 3, 21, 9, 50000)"
    )
    db.commit()
    merchant.clear_policy_cache()

    decision = evaluate(db, ep["subscription_id"], ep, merchant_id="mer_INVM")

    assert decision.ok is False
    assert decision.reason == "outreach_cap_48h"  # rule name is stable; value is the merchant's
    assert decision.details["min_interval_hours"] == 72


def test_custom_row_overrides_quiet_hours(db, freeze_clock):
    """quiet 15:00–20:00 IST: 15:30 IST (DEFAULT-open) is quiet for this
    merchant, and the evidence prints the merchant's own window."""
    ep = _scored(db, "sub_QUIETM1")
    db.execute(
        "INSERT INTO merchant_policies (merchant_id, cooling_hours, "
        "outreach_min_interval_hours, max_attempts_per_episode, quiet_hours_start, "
        "quiet_hours_end, human_gate_threshold_paise) "
        "VALUES ('mer_QUIETM', 6, 48, 3, 15, 20, 50000)"
    )
    db.commit()
    merchant.clear_policy_cache()

    decision = evaluate(db, ep["subscription_id"], ep, merchant_id="mer_QUIETM")

    assert decision.ok is False
    assert decision.reason == "quiet_hours"
    assert decision.details["quiet_hours_ist"] == "15:00-20:00"


def test_merchant_without_row_falls_back_while_custom_row_applies(db, freeze_clock):
    """Two merchants, one store: the custom row governs its merchant only —
    the rowless merchant still rides the DEFAULT envelope."""
    ep_custom = _scored(db, "sub_MIX_C")
    ep_plain = _scored(db, "sub_MIX_P")
    _set_outreach(db, ep_custom["id"], hours_ago=7)
    _set_outreach(db, ep_plain["id"], hours_ago=7)
    db.execute(
        "INSERT INTO merchant_policies (merchant_id, cooling_hours, "
        "outreach_min_interval_hours, max_attempts_per_episode, quiet_hours_start, "
        "quiet_hours_end, human_gate_threshold_paise) "
        "VALUES ('mer_MIX', 12, 48, 3, 21, 9, 50000)"
    )
    db.commit()
    merchant.clear_policy_cache()

    custom = evaluate(db, ep_custom["subscription_id"], ep_custom, merchant_id="mer_MIX")
    plain = evaluate(db, ep_plain["subscription_id"], ep_plain)

    assert custom.reason == "cooling_off"  # 7h < 12h custom cooling
    assert plain.reason == "outreach_cap_48h"  # 7h ≥ 6h cooling but < 48h DEFAULT interval


def test_episode_dict_merchant_id_is_honored(db, freeze_clock):
    """Without the kwarg, an episode dict that carries merchant_id resolves
    that merchant's row (future episode rows will carry it natively)."""
    ep = _scored(db, "sub_EPMID1")
    db.execute("UPDATE episodes SET attempt_count = 2 WHERE id = ?", (ep["id"],))
    db.execute(
        "INSERT INTO merchant_policies (merchant_id, cooling_hours, "
        "outreach_min_interval_hours, max_attempts_per_episode, quiet_hours_start, "
        "quiet_hours_end, human_gate_threshold_paise) "
        "VALUES ('mer_EPMID', 6, 48, 2, 21, 9, 50000)"
    )
    db.commit()
    merchant.clear_policy_cache()

    decision = evaluate(db, ep["subscription_id"], {**ep, "merchant_id": "mer_EPMID"})

    assert decision.ok is False
    assert decision.reason == "max_attempts"
    assert decision.details["max_attempts"] == 2


# ── API: GET /api/policy + PUT /api/policy/{merchant_id} ────────────────


def test_get_policy_api_lists_default_on_empty_db(client):
    """Zero-safe like every other read: a fresh store answers 200 with the
    DEFAULT row (self-seeded) and no custom rows."""
    r = client.get("/api/policy")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"default", "custom"}
    assert body["default"] == FROZEN_DEFAULTS
    assert body["custom"] == []


def test_get_policy_api_lists_default_and_custom_rows(client, db):
    _put_policy(client, "mer_LIST", _payload(cooling_hours=12))
    r = client.get("/api/policy")
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == FROZEN_DEFAULTS
    assert [row["merchant_id"] for row in body["custom"]] == ["mer_LIST"]
    assert body["custom"][0]["cooling_hours"] == 12
    assert body["default"]["cooling_hours"] == 6


def test_put_policy_happy_path_roundtrip(client, db):
    body = _put_policy(
        client,
        "mer_HAPPY",
        {
            "cooling_hours": 12,
            "outreach_min_interval_hours": 72,
            "max_attempts_per_episode": 5,
            "quiet_hours_start": 22,
            "quiet_hours_end": 8,
            "human_gate_threshold_paise": 100000,
        },
    )
    assert body == {
        "merchant_id": "mer_HAPPY",
        "cooling_hours": 12,
        "outreach_min_interval_hours": 72,
        "max_attempts_per_episode": 5,
        "quiet_hours_start": 22,
        "quiet_hours_end": 8,
        "human_gate_threshold_paise": 100000,
    }
    listed = client.get("/api/policy").json()
    assert listed["custom"] == [body]
    row = dict(
        db.execute("SELECT * FROM merchant_policies WHERE merchant_id = 'mer_HAPPY'").fetchone()
    )
    assert row == body


def test_put_policy_updates_existing_custom_row(client, db):
    _put_policy(
        client,
        "mer_UPD",
        {
            "cooling_hours": 12,
            "outreach_min_interval_hours": 48,
            "max_attempts_per_episode": 3,
            "quiet_hours_start": 21,
            "quiet_hours_end": 9,
            "human_gate_threshold_paise": 50000,
        },
    )
    body = _put_policy(
        client,
        "mer_UPD",
        {
            "cooling_hours": 24,
            "outreach_min_interval_hours": 96,
            "max_attempts_per_episode": 2,
            "quiet_hours_start": 23,
            "quiet_hours_end": 7,
            "human_gate_threshold_paise": 25000,
        },
    )
    assert body["cooling_hours"] == 24
    n = db.execute(
        "SELECT COUNT(*) AS n FROM merchant_policies WHERE merchant_id = 'mer_UPD'"
    ).fetchone()["n"]
    assert n == 1  # upsert, never a second row
    assert client.get("/api/policy").json()["custom"] == [body]


@pytest.mark.parametrize(
    "field,bad",
    [
        ("cooling_hours", 0),
        ("cooling_hours", 169),
        ("outreach_min_interval_hours", 0),
        ("outreach_min_interval_hours", 337),
        ("max_attempts_per_episode", 0),
        ("max_attempts_per_episode", 11),
        ("quiet_hours_start", 24),
        ("quiet_hours_end", -1),
        ("human_gate_threshold_paise", -1),
    ],
)
def test_put_policy_rejects_out_of_range_fields(client, field, bad):
    payload = _payload(**{field: bad})
    r = client.put("/api/policy/mer_RANGE", json=payload)
    assert r.status_code == 422
    # fail-closed: nothing was written
    assert client.get("/api/policy").json()["custom"] == []


def test_put_policy_rejects_equal_quiet_hours(client):
    payload = {
        "cooling_hours": 6,
        "outreach_min_interval_hours": 48,
        "max_attempts_per_episode": 3,
        "quiet_hours_start": 9,
        "quiet_hours_end": 9,
        "human_gate_threshold_paise": 50000,
    }
    r = client.put("/api/policy/mer_QE", json=payload)
    assert r.status_code == 422
    assert client.get("/api/policy").json()["custom"] == []


def test_put_policy_on_default_row_rejected_403(client, db):
    r = client.put(
        "/api/policy/DEFAULT",
        json={
            "cooling_hours": 1,
            "outreach_min_interval_hours": 1,
            "max_attempts_per_episode": 1,
            "quiet_hours_start": 1,
            "quiet_hours_end": 2,
            "human_gate_threshold_paise": 1,
        },
    )
    assert r.status_code == 403
    assert "frozen" in r.json()["detail"]
    # byte-for-byte: the DEFAULT row survived the attempt untouched
    assert dict(
        db.execute("SELECT * FROM merchant_policies WHERE merchant_id = 'DEFAULT'").fetchone()
    ) == FROZEN_DEFAULTS


# ── Dashboard: the Policy card ──────────────────────────────────────────


def test_dashboard_policy_card_renders_default(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    text = r.text
    assert "Policy" in text
    assert "48 h" in text  # DEFAULT min outreach interval
    assert "21:00-09:00" in text  # DEFAULT quiet-hours window
    assert "₹500.00" in text  # DEFAULT human-gate threshold
    assert "Per-merchant overrides" not in text  # no custom rows yet


def test_dashboard_policy_card_shows_override_note(client, db):
    _put_policy(
        client,
        "mer_CARD",
        {
            "cooling_hours": 12,
            "outreach_min_interval_hours": 48,
            "max_attempts_per_episode": 3,
            "quiet_hours_start": 21,
            "quiet_hours_end": 9,
            "human_gate_threshold_paise": 50000,
        },
    )
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Per-merchant overrides" in r.text
    assert "mer_CARD" in r.text
    assert "12 h" in r.text


# ── End-to-end: API write → engine verdict through a custom row ─────────


def test_end_to_end_policy_evaluation_with_custom_merchant_row(client, db, freeze_clock):
    """PUT a custom row over the API, then run the real engine on a real
    episode of that merchant: the custom max_attempts cap BLOCKS what the
    DEFAULT row would have SENT — one store, two governed merchants."""
    assert (
        client.put(
            "/api/policy/mer_E2E",
            json={
                "cooling_hours": 6,
                "outreach_min_interval_hours": 48,
                "max_attempts_per_episode": 1,
                "quiet_hours_start": 21,
                "quiet_hours_end": 9,
                "human_gate_threshold_paise": 50000,
            },
        ).status_code
        == 200
    )

    ep_custom = _scored(db, "sub_E2E_C")
    db.execute("UPDATE episodes SET attempt_count = 1 WHERE id = ?", (ep_custom["id"],))
    db.commit()

    ep_default = _scored(db, "sub_E2E_D")

    blocked = evaluate(db, ep_custom["subscription_id"], ep_custom, merchant_id="mer_E2E")
    sent = evaluate(db, ep_default["subscription_id"], ep_default)

    assert blocked.ok is False
    assert blocked.reason == "max_attempts"
    assert blocked.details["max_attempts"] == 1
    assert sent.ok is True and sent.action == "SEND"


# ── helpers ─────────────────────────────────────────────────────────────


def _payload(**overrides) -> dict:
    """A valid custom-row body, with FROZEN_DEFAULTS as the baseline."""
    return {
        key: value
        for key, value in {**FROZEN_DEFAULTS, **overrides}.items()
        if key != "merchant_id"
    }


def _put_policy(client, merchant_id: str, payload: dict):
    r = client.put(f"/api/policy/{merchant_id}", json=payload)
    assert r.status_code == 200, r.text
    return r.json()
