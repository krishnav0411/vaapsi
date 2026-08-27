"""Append-only, hash-chained audit ledger on SQLite.

Why a hash chain: Vaapsi takes real actions against customer subscriptions
(payment links, invoice notifies), so the audit trail must be tamper-evident.
Each row commits to the previous row's hash — editing, removing, or inserting
any historical row invalidates every hash after it, and `verify_chain.py`
detects that with a linear replay and no external trust.

Row fields are frozen in app/db.py's schema.
`score`/`features` and the `llm_*` fields are nullable until the scorer
and LLM adapter wire in. Amounts are integer paise, never float. The API is
append-only by construction: `append()` and `iter_rows()` are the whole
surface — there is no update or delete path in code.
"""

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

# Column order here mirrors the schema in app/db.py; the canonical hash
# payload is built by name, so column order never affects hashes.
LEDGER_FIELDS: tuple[str, ...] = (
    "action_id",
    "ts_utc",
    "subscription_id",
    "trigger_event",
    "policy_eval",
    "score",
    "features",
    "llm_request_hash",
    "llm_output_raw",
    "llm_model",
    "human_gate",
    "rzp_call",
    "outcome",
    "recovered_paise",
    "mode",
)

# The chain starts here; row 1's prev_hash must equal this sentinel.
GENESIS_HASH = "0" * 64

# Fields persisted as canonical JSON text in SQLite but hashed as structured
# values, so the hash material is stable across storage round-trips.
# llm_output_raw joins them in D3: the model's parsed output is dict-valued
# evidence and must round-trip exactly like policy_eval/features.
JSON_FIELDS: tuple[str, ...] = ("policy_eval", "features", "rzp_call", "llm_output_raw")


def canonical_json(obj: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace — byte-stable everywhere."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def compute_row_hash(prev_hash: str, row: dict[str, Any]) -> str:
    """row_hash = sha256(prev_hash + canonical_json(row without row_hash)).

    Takes the *logical* row (dicts for policy_eval/features/rzp_call, bool
    for human_gate) — the same shape append() hashed and iter_rows()
    rehydrates, so verification recomputes the identical material.
    """
    payload = {k: v for k, v in row.items() if k != "row_hash"}
    material = prev_hash + canonical_json(payload)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _insert_sql() -> str:
    cols = ", ".join(LEDGER_FIELDS)
    placeholders = ", ".join("?" for _ in LEDGER_FIELDS)
    return (
        f"INSERT INTO audit_ledger ({cols}, prev_hash, row_hash) "
        f"VALUES ({placeholders}, ?, ?)"
    )


def append(conn: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
    """Append one ledger row, chained onto the current head.

    Reads the previous head and writes the new row on the caller's
    connection, so the append participates in the caller's transaction —
    either the episode state change and its ledger row land together, or
    neither does. Unknown/missing fields raise rather than silently default.
    """
    unknown = set(fields) - set(LEDGER_FIELDS)
    if unknown:
        raise ValueError(f"unknown ledger fields: {sorted(unknown)}")

    row: dict[str, Any] = {name: None for name in LEDGER_FIELDS}
    row.update(fields)
    if not row["action_id"]:
        row["action_id"] = uuid.uuid4().hex
    if not row["ts_utc"]:
        row["ts_utc"] = datetime.now(timezone.utc).isoformat()
    row["recovered_paise"] = int(row["recovered_paise"] or 0)
    row["human_gate"] = bool(row["human_gate"])
    for required in ("subscription_id", "trigger_event", "policy_eval", "outcome", "mode"):
        if row[required] is None:
            raise ValueError(f"ledger field '{required}' is required")

    head = conn.execute(
        "SELECT row_hash FROM audit_ledger ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = head["row_hash"] if head is not None else GENESIS_HASH
    row["prev_hash"] = prev_hash
    row["row_hash"] = compute_row_hash(prev_hash, row)

    values = [_to_db_value(row[f], f) for f in LEDGER_FIELDS]
    conn.execute(_insert_sql(), [*values, prev_hash, row["row_hash"]])
    return row


def _to_db_value(value: Any, name: str) -> Any:
    """Dicts/lists persist as canonical JSON text; bools as SQLite ints."""
    if name in JSON_FIELDS and value is not None:
        return canonical_json(value)
    if name == "human_gate":
        return int(value)
    return value


def _from_db(db_row: sqlite3.Row) -> dict[str, Any]:
    """Normalize a stored row back to the exact shape append() hashed."""
    row = {name: db_row[name] for name in (*LEDGER_FIELDS, "prev_hash", "row_hash")}
    for name in JSON_FIELDS:
        if row[name] is not None:
            row[name] = json.loads(row[name])
    row["human_gate"] = bool(row["human_gate"])
    row["recovered_paise"] = int(row["recovered_paise"])
    return row


def iter_rows(conn: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    """Yield every ledger row in append order (seq), fully normalized.

    Selects only the logical row fields — the surrogate `seq` column orders
    the replay but is deliberately excluded from the rehydrated dict, so the
    hashed material matches what append() committed byte for byte.
    """
    cols = ", ".join(("seq", *LEDGER_FIELDS, "prev_hash", "row_hash"))
    cursor = conn.execute(f"SELECT {cols} FROM audit_ledger ORDER BY seq ASC")
    for db_row in cursor:
        yield _from_db(db_row)
