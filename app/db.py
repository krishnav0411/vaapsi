"""SQLite connection management.

Day 0 establishes the connection factory and pragmas (WAL, FK, busy
timeout). SCHEMA below is created idempotently by init_db: webhook event
store (D0), cohorts (D1), audit ledger + episodes (D2), approvals (D3).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.settings import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_id TEXT,
    event TEXT NOT NULL,
    subscription_id TEXT,
    event_ts_utc TEXT,
    received_ts_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    raw_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_event
    ON webhook_events(event);
CREATE TABLE IF NOT EXISTS cohorts (
    subscription_id TEXT PRIMARY KEY,
    cohort TEXT NOT NULL CHECK (cohort IN ('CONTROL', 'TREATMENT')),
    slot INTEGER NOT NULL,
    customer_id TEXT,
    rzp_status TEXT,
    short_url TEXT,
    created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id TEXT NOT NULL UNIQUE,
    ts_utc TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    trigger_event TEXT NOT NULL,
    policy_eval TEXT NOT NULL,
    score REAL,
    features TEXT,
    llm_request_hash TEXT,
    llm_output_raw TEXT,
    llm_model TEXT,
    human_gate INTEGER NOT NULL CHECK (human_gate IN (0, 1)),
    rzp_call TEXT,
    outcome TEXT NOT NULL,
    recovered_paise INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_audit_ledger_subscription
    ON audit_ledger(subscription_id);
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL,
    cohort TEXT,
    state TEXT NOT NULL CHECK (state IN (
        'NEW', 'DIAGNOSED', 'SCORED', 'GATED', 'SENT', 'VERIFIED', 'CLOSED', 'VOIDED'
    )),
    halt_ts_utc TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_action_ts_utc TEXT,
    void_reason TEXT CHECK (void_reason IN ('charged', 'cancelled')),
    created_ts_utc TEXT NOT NULL,
    updated_ts_utc TEXT NOT NULL,
    -- void_reason is stamped exactly when the episode is VOIDED (the two
    -- sides of this equality must match: 1=1 when voided, 0=0 otherwise).
    CHECK ((state = 'VOIDED') = (void_reason IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_episodes_subscription ON episodes(subscription_id);
-- At most one OPEN episode per subscription, enforced at the DB level so a
-- replayed/concurrent halt delivery can never spawn a second cycle.
CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_open_subscription
    ON episodes(subscription_id) WHERE state IN (
        'NEW', 'DIAGNOSED', 'SCORED', 'GATED', 'SENT'
    );
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    created_ts_utc TEXT NOT NULL,
    decided_ts_utc TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_episode ON approvals(episode_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
-- D4 drill 2: dead-letter queue for outreach that kept 5xx-ing through
-- every retry. The episode still transitions to SENT (the outreach IS
-- dispatched from Vaapsi's perspective — delivery is async), and the
-- PENDING row is what drain_dlq re-dispatches once the transport heals.
CREATE TABLE IF NOT EXISTS dlq (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    payload_json TEXT NOT NULL,
    error TEXT NOT NULL,
    failed_ts_utc TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'DRAINED', 'DROPPED'))
);
CREATE INDEX IF NOT EXISTS idx_dlq_status ON dlq(status);
-- Per-merchant policy overrides (one row per merchant). The DEFAULT row is
-- seeded with the frozen engine constants (app.policy.merchant) and is
-- never editable through the API; the CHECKs mirror the API schema's
-- ranges exactly, so the store fails closed even if a row slips past it.
CREATE TABLE IF NOT EXISTS merchant_policies (
    merchant_id TEXT PRIMARY KEY,
    cooling_hours INTEGER NOT NULL CHECK (cooling_hours BETWEEN 1 AND 168),
    outreach_min_interval_hours INTEGER NOT NULL
        CHECK (outreach_min_interval_hours BETWEEN 1 AND 336),
    max_attempts_per_episode INTEGER NOT NULL
        CHECK (max_attempts_per_episode BETWEEN 1 AND 10),
    quiet_hours_start INTEGER NOT NULL CHECK (quiet_hours_start BETWEEN 0 AND 23),
    quiet_hours_end INTEGER NOT NULL CHECK (quiet_hours_end BETWEEN 0 AND 23),
    human_gate_threshold_paise INTEGER NOT NULL CHECK (human_gate_threshold_paise >= 0),
    CHECK (quiet_hours_start != quiet_hours_end)
);
"""


def connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
