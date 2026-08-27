"""Audit ledger package — the tamper-evident record of everything Vaapsi does.

Every policy decision and outbound Razorpay call is appended here before it
matters; the per-row hash chain makes any later edit, deletion, or insertion
of a historical row detectable by a cheap linear replay (verify_chain.py).
"""

from app.audit.ledger import (
    GENESIS_HASH,
    append,
    canonical_json,
    compute_row_hash,
    iter_rows,
)

__all__ = [
    "GENESIS_HASH",
    "append",
    "canonical_json",
    "compute_row_hash",
    "iter_rows",
]
