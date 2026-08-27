"""CLI verifier for the audit ledger — replay the hash chain, exit on verdict.

Exit 0: every row links to its predecessor (row 1 to the genesis sentinel)
and every row_hash recomputes exactly from its stored contents.
Exit 1: the chain is broken somewhere — a stored row was edited, a row was
removed, or a row was inserted, i.e. the audit trail can no longer be
trusted. Run it after any DB restore or before acting on ledger history:

    .venv/Scripts/python.exe -m app.audit.verify_chain
    .venv/Scripts/python.exe app/audit/verify_chain.py   (same verdict)
"""

import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # allow direct execution: python app/audit/verify_chain.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.audit.ledger import GENESIS_HASH, compute_row_hash, iter_rows
from app.db import get_conn, init_db


def verify_chain(rows: Iterator[dict[str, Any]] | Sequence[dict[str, Any]]) -> tuple[bool, str]:
    """Replay the chain; return (ok, detail).

    Two independent checks per row: the link (prev_hash must equal the
    previous row's row_hash) and the content commitment (row_hash must
    recompute from the stored fields). A single flipped byte in any stored
    field breaks the content check; deleting a row breaks the link check.
    """
    prev_hash = GENESIS_HASH
    position = 0
    for position, row in enumerate(rows, start=1):
        if row["prev_hash"] != prev_hash:
            return False, f"row {position}: prev_hash does not link to predecessor"
        expected = compute_row_hash(prev_hash, row)
        if row["row_hash"] != expected:
            return False, f"row {position}: row_hash does not match stored contents"
        prev_hash = row["row_hash"]
    return True, f"chain valid ({position} row{'s' if position != 1 else ''})"


def main() -> int:
    with get_conn() as conn:
        init_db(conn)
        ok, detail = verify_chain(list(iter_rows(conn)))
    print(("OK: " if ok else "FAIL: ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
