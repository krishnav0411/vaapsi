"""D3 scoring layer — the deterministic episode scorer.

app.scoring.scorecard.score_episode() maps one recovery episode to a tier
(1 gentle nudge / 2 standard recovery / 3 escalate to human review) using
only facts read from the episodes row and the subscription's webhook
events: zero LLM, zero network, zero writes. It is the deterministic-first
half of the D3 decision loop — the LLM (app.llm) may refine the choice,
but the tier, the features, and the rationale are always computed here so
the ledger can show a rules-grounded baseline even in DEGRADED mode.
"""

from app.scoring.scorecard import PLAN_PRICE_PAISE, ScoreResult, score_episode

__all__ = ["PLAN_PRICE_PAISE", "ScoreResult", "score_episode"]
