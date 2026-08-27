"""Core domain logic — the recovery-episode state machine.

An episode is one bounded recovery cycle for a halted subscription:
NEW → DIAGNOSED → SCORED → GATED → SENT → VERIFIED → CLOSED, with VOIDED
reached from any open state via a stop event (charge/cancel). Every
transition lands with a hash-chained ledger row (app.audit) in the same
transaction, so state and its audit evidence can never diverge.
"""

from app.core.episodes import (
    ALLOWED_TRANSITIONS,
    DEFAULT_MODE,
    EPISODE_STATES,
    OPEN_STATES,
    TERMINAL_STATES,
    VOID_REASONS,
    EpisodeNotFoundError,
    TransitionError,
    create_episode,
    get_episode,
    get_open_episodes,
    transition,
    void_open_episodes,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "DEFAULT_MODE",
    "EPISODE_STATES",
    "OPEN_STATES",
    "TERMINAL_STATES",
    "VOID_REASONS",
    "EpisodeNotFoundError",
    "TransitionError",
    "create_episode",
    "get_episode",
    "get_open_episodes",
    "transition",
    "void_open_episodes",
]
