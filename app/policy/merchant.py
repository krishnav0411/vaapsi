"""Per-merchant policy rows — the frozen envelope, now addressable per merchant.

Why a table: recovery outreach touches real customers, so every threshold
the policy engine obeys must be explicit, auditable and per-merchant. The
``merchant_policies`` table holds one row per merchant; the DEFAULT row
always exists and its values are EXACTLY the frozen constants that lived
in app.policy.engine (they still do — engine re-exports them from here,
so a cap that drifts silently stays impossible: these change in code with
review, never via env and never over the API).

Read path: get_policy() is synchronous and cheap — one SELECT, self-healing
(ensure_default_row is INSERT OR IGNORE, so first read on a fresh store
seeds the DEFAULT row), cached in-process for a few seconds keyed by the
store path so the per-request connection pattern never multiplies queries.
A merchant without a row falls back to the DEFAULT row, which makes engine
behavior for default merchants byte-for-byte what it was before this table
existed.

Write path: only the API (app.dashboard.api PUT /api/policy/{merchant_id})
creates or updates CUSTOM rows, through MerchantPolicyIn's range validation.
The DEFAULT row can never be written through the API — fail-closed — because
a browser-reachable edit of the safety envelope is not a safety envelope.
"""

import sqlite3
import time
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.settings import get_settings

# Frozen policy constants — the safety envelope. These change in code (with
# review), never via env: a cap that drifts silently is worse than a fixed
# one. app.policy.engine re-exports them under their historical names, and
# the DEFAULT row below is built from them — one source of truth.
DEFAULT_COOLING_HOURS = 6
DEFAULT_OUTREACH_MIN_INTERVAL_HOURS = 48
DEFAULT_MAX_ATTEMPTS_PER_EPISODE = 3
DEFAULT_QUIET_HOURS_IST = (21, 9)  # (start, end) hour in IST — quiet 21:00 through 09:00
DEFAULT_HUMAN_GATE_THRESHOLD_PAISE = 50000  # ₹500 — outreach above this needs a human (D3+)

# The DEFAULT row's primary key. Reserved: the API refuses PUTs to it.
DEFAULT_MERCHANT_ID = "DEFAULT"

# Column order used by every SELECT/INSERT here — one tuple, no drift.
POLICY_FIELDS: tuple[str, ...] = (
    "cooling_hours",
    "outreach_min_interval_hours",
    "max_attempts_per_episode",
    "quiet_hours_start",
    "quiet_hours_end",
    "human_gate_threshold_paise",
)

_POLICY_COLUMNS_SQL = ", ".join(("merchant_id", *POLICY_FIELDS))

# Short TTL: cheap freshness for API writes from other processes, without a
# query per evaluate(). Cleared eagerly on every write through this module.
_POLICY_TTL_SECONDS = 5.0

# In-process cache: {(db_path, merchant_id): (monotonic_ts, policy_dict)}.
# Keyed by the store path so a fresh tmp store (tests) can never see a
# previous store's rows.
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

# The DEFAULT row as data, derived from the frozen constants — used only as
# a last-resort in-memory fallback (e.g. before a store exists); the live
# read path always goes through the table.
DEFAULT_ROW_DICT: dict[str, Any] = {
    "merchant_id": DEFAULT_MERCHANT_ID,
    "cooling_hours": DEFAULT_COOLING_HOURS,
    "outreach_min_interval_hours": DEFAULT_OUTREACH_MIN_INTERVAL_HOURS,
    "max_attempts_per_episode": DEFAULT_MAX_ATTEMPTS_PER_EPISODE,
    "quiet_hours_start": DEFAULT_QUIET_HOURS_IST[0],
    "quiet_hours_end": DEFAULT_QUIET_HOURS_IST[1],
    "human_gate_threshold_paise": DEFAULT_HUMAN_GATE_THRESHOLD_PAISE,
}


class MerchantPolicyIn(BaseModel):
    """Body of PUT /api/policy/{merchant_id} — every field range-checked.

    The ranges mirror the DB CHECK constraints in app.db SCHEMA exactly, so
    a payload the schema accepts can never trip an IntegrityError, and a
    row that slipped in some other way can never pass the schema. Quiet
    hours must be a real window (start != end); Asia/Kolkata has no DST,
    so a plain 0..23 hour pair is the whole contract.
    """

    cooling_hours: int = Field(ge=1, le=168)
    outreach_min_interval_hours: int = Field(ge=1, le=336)
    max_attempts_per_episode: int = Field(ge=1, le=10)
    quiet_hours_start: int = Field(ge=0, le=23)
    quiet_hours_end: int = Field(ge=0, le=23)
    human_gate_threshold_paise: int = Field(ge=0)

    @model_validator(mode="after")
    def _quiet_window_is_real(self) -> "MerchantPolicyIn":
        if self.quiet_hours_start == self.quiet_hours_end:
            raise ValueError("quiet_hours_start must differ from quiet_hours_end")
        return self


def clear_policy_cache() -> None:
    """Drop every cached row — called after any write (API PUT) so the next
    read in this process observes it immediately, TTL or not."""
    _CACHE.clear()


def ensure_default_row(conn: sqlite3.Connection) -> None:
    """Idempotently seed the DEFAULT row with the frozen constants.

    Runs on startup (app.main lifespan, after init_db) and defensively on
    every cache-miss read, so a fresh or wiped store self-heals instead of
    500ing the engine. INSERT OR IGNORE: an existing row — default or
    custom — is never touched.
    """
    conn.execute(
        f"INSERT OR IGNORE INTO merchant_policies ({_POLICY_COLUMNS_SQL}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            DEFAULT_MERCHANT_ID,
            DEFAULT_COOLING_HOURS,
            DEFAULT_OUTREACH_MIN_INTERVAL_HOURS,
            DEFAULT_MAX_ATTEMPTS_PER_EPISODE,
            DEFAULT_QUIET_HOURS_IST[0],
            DEFAULT_QUIET_HOURS_IST[1],
            DEFAULT_HUMAN_GATE_THRESHOLD_PAISE,
        ),
    )


def _select_policy(conn: sqlite3.Connection, merchant_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {_POLICY_COLUMNS_SQL} FROM merchant_policies WHERE merchant_id = ?",
        (merchant_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_policy(conn: sqlite3.Connection, merchant_id: str | None = None) -> dict[str, Any]:
    """The effective policy for a merchant, as a plain dict (one query).

    Unknown/None merchant → the DEFAULT row, which is always seeded first
    (self-healing). The result is cached per (store path, merchant) for a
    few seconds; writes through upsert_policy clear the cache eagerly.
    """
    mid = merchant_id or DEFAULT_MERCHANT_ID
    cache_key = (str(get_settings().db_path), mid)
    now = time.monotonic()
    hit = _CACHE.get(cache_key)
    if hit is not None and now - hit[0] < _POLICY_TTL_SECONDS:
        return dict(hit[1])  # a copy: callers must never share the cached dict

    ensure_default_row(conn)
    policy = _select_policy(conn, mid)
    if policy is None and mid != DEFAULT_MERCHANT_ID:
        # No custom row → the frozen envelope governs, byte-for-byte.
        policy = _select_policy(conn, DEFAULT_MERCHANT_ID)
    cached = policy if policy is not None else dict(DEFAULT_ROW_DICT)
    _CACHE[cache_key] = (now, cached)
    return cached


def upsert_policy(
    conn: sqlite3.Connection, merchant_id: str, values: dict[str, int]
) -> dict[str, Any]:
    """Create or update a CUSTOM merchant row; returns the fresh row dict.

    Callers (the API) are responsible for refusing the DEFAULT merchant id —
    this function deliberately does not second-guess them, but the DEFAULT
    row is still unreachable through it because api.py fails closed first.
    Cache is cleared so the very next read sees the new values.
    """
    conn.execute(
        f"INSERT INTO merchant_policies ({_POLICY_COLUMNS_SQL}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(merchant_id) DO UPDATE SET "
        "cooling_hours = excluded.cooling_hours, "
        "outreach_min_interval_hours = excluded.outreach_min_interval_hours, "
        "max_attempts_per_episode = excluded.max_attempts_per_episode, "
        "quiet_hours_start = excluded.quiet_hours_start, "
        "quiet_hours_end = excluded.quiet_hours_end, "
        "human_gate_threshold_paise = excluded.human_gate_threshold_paise",
        (
            merchant_id,
            values["cooling_hours"],
            values["outreach_min_interval_hours"],
            values["max_attempts_per_episode"],
            values["quiet_hours_start"],
            values["quiet_hours_end"],
            values["human_gate_threshold_paise"],
        ),
    )
    clear_policy_cache()
    fresh = _select_policy(conn, merchant_id)
    if fresh is None:  # pragma: no cover - the upsert above just wrote it
        raise LookupError(f"upsert did not persist policy row for {merchant_id!r}")
    return fresh


def list_policies(conn: sqlite3.Connection) -> dict[str, Any]:
    """{default, custom} for GET /api/policy — no secrets exist here, but the
    shape stays server-controlled so the dashboard never guesses."""
    default = get_policy(conn, DEFAULT_MERCHANT_ID)
    custom = [
        dict(r)
        for r in conn.execute(
            f"SELECT {_POLICY_COLUMNS_SQL} FROM merchant_policies "
            "WHERE merchant_id != ? ORDER BY merchant_id ASC",
            (DEFAULT_MERCHANT_ID,),
        )
    ]
    return {"default": default, "custom": custom}
