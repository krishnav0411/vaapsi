"""Policy layer — the pure-rules gate every outbound recovery action passes.

app.policy.engine.evaluate() is the single decision point for outreach:
deterministic, rules-only (zero LLM, zero network), returning a
PolicyDecision whose full rule evidence goes into the ledger row.
"""

from app.policy.engine import (
    COOLING_HOURS,
    DEFAULT_KILL_SWITCH,
    HUMAN_GATE_THRESHOLD_PAISE,
    MAX_ATTEMPTS_PER_EPISODE,
    OUTREACH_MIN_INTERVAL_HOURS,
    QUIET_HOURS_IST,
    PolicyDecision,
    evaluate,
)

__all__ = [
    "COOLING_HOURS",
    "DEFAULT_KILL_SWITCH",
    "HUMAN_GATE_THRESHOLD_PAISE",
    "MAX_ATTEMPTS_PER_EPISODE",
    "OUTREACH_MIN_INTERVAL_HOURS",
    "QUIET_HOURS_IST",
    "PolicyDecision",
    "evaluate",
]
