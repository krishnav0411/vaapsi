"""D3 gates layer — consumers for episodes the deterministic pipeline holds back.

app.gates.human_gate is the GATED state's counterpart: it enqueues a SCORED
episode for human review (approvals table + SCORED→GATED transition, one
transaction) and turns a human's decision into the episode's next state —
approve → dispatch via the same action+transition path the orchestrator's
SEND branch uses; reject → CLOSED with outcome 'human_rejected'. Every
decision lands exactly one hash-chained ledger row, so "a human looked at
this" is auditable evidence, never an untracked side channel.
"""

from app.gates.human_gate import (
    ApprovalError,
    ApprovalNotFoundError,
    DoubleDecisionError,
    decide,
    enqueue_for_approval,
    requires_human_gate,
)

__all__ = [
    "ApprovalError",
    "ApprovalNotFoundError",
    "DoubleDecisionError",
    "decide",
    "enqueue_for_approval",
    "requires_human_gate",
]
