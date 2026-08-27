"""D4 Drill 1 tests — webhook replay storm, proven through the pure seam.

Fire the same delivery 25 times plus 5 shuffled-key variants (30
signature-valid deliveries, jittered timestamps inside one 5-minute
idempotency window) straight through app.ingest.receiver.process_webhook
— no HTTP, no server — and assert ingest idempotency: exactly 1
webhook_events row, one archive per delivery (duplicates are archived,
never dropped), zero recovery episodes. The second storm after the
window rolls over is a genuinely NEW delivery: it gets its own row.
Each test gets its own tmp data_dir — no cross-run state, ever."""

import time

import pytest

from app.chaos.replay import fire_replay_storm
from app.db import get_conn, init_db
from app.settings import get_settings

SECRET = "test_webhook_secret_0123456789abcdef"


def _halt_payload(sub_id: str, ts: int) -> dict:
    return {
        "event": "subscription.halted",
        "created_at": ts,
        "payload": {"subscription": {"entity": {"id": sub_id, "status": "halted"}}},
    }


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Fresh store + tmp archive per test, with the drill's secret set."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "razorpay_webhook_secret", SECRET)
    with get_conn() as conn:
        init_db(conn)
        yield conn
    conn.close()


def test_storm_of_30_yields_one_row_and_all_archived(db):
    """25 identical + 5 shuffled deliveries → 1 DB row, 30 archive files."""
    base = _halt_payload("sub_STORM1", int(time.time()))

    result = fire_replay_storm(db, base)

    assert result["deliveries"] == 30  # 25 identical + 5 shuffled variants
    assert result["webhook_rows"] == 1
    assert result["accepted"] == 1
    assert result["duplicates"] == 29
    assert result["archived"] == 30
    assert db.execute("SELECT COUNT(*) AS c FROM webhook_events").fetchone()["c"] == 1
    files = list(get_settings().archive_dir.rglob("*.json"))
    assert len(files) == 30


def test_second_storm_after_window_expiry_creates_new_row(db):
    """A storm past the 5-minute window is a NEW delivery: new idempotency
    key, new row — window rollover works, dedupe never outlives the window."""
    ts = int(time.time())

    first = fire_replay_storm(db, _halt_payload("sub_ROLL1", ts))
    # exactly two full windows later → a different bucket, guaranteed
    second = fire_replay_storm(db, _halt_payload("sub_ROLL1", ts + 600))

    assert first["webhook_rows"] == 1
    assert second["webhook_rows"] == 1
    assert second["accepted"] == 1  # the new window's first delivery lands
    assert db.execute("SELECT COUNT(*) AS c FROM webhook_events").fetchone()["c"] == 2


def test_storm_creates_zero_duplicate_episodes(db):
    """Ingest stays inert: no episode rows appear for the stormed sub —
    episode creation belongs to the event layer, never the webhook path."""
    base = _halt_payload("sub_EP1", int(time.time()))

    result = fire_replay_storm(db, base)

    assert result["episodes_for_subscription"] == 0
    assert (
        db.execute(
            "SELECT COUNT(*) AS c FROM episodes WHERE subscription_id = 'sub_EP1'"
        ).fetchone()["c"]
        == 0
    )
