"""Pure-rules policy engine — the single gate every outbound action passes.

Why rules-only: recovery outreach touches real customers, so the decision
to act must be deterministic and auditable — the same episode state always
yields the same verdict, and the full rule evaluation lands in the ledger
as evidence. There is no LLM, no network, and no I/O beyond reading the
episode's own row: evaluate() is a pure function of (fresh episodes row,
settings, clock hook).

Rules run IN ORDER and the FIRST failure wins as (ok=False,
action='BLOCKED'): kill switch → attempt cap / state gate → cooling-off →
48h outreach interval → quiet hours (IST) → cohort gate. All pass →
(ok=True, action='SEND').

Attempt and last-action state are read from the episodes row columns
(attempt_count, last_action_ts_utc) — never by counting ledger rows, which
would make the caps depend on audit volume instead of outreach reality.
The row is re-read inside evaluate() so a caller holding a stale episode
dict can never act on outdated counts.

Quiet hours: 21:00–09:00 IST (Asia/Kolkata, aware datetimes — the hour is
examined after converting the UTC instant, never by shifting the clock).
Windows are [21:00, 24:00) and [00:00, 09:00); 09:00 exactly opens the
outreach window, 21:00 exactly closes it. Asia/Kolkata has no DST, so the
window is stable year-round.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.settings import get_settings

# Frozen policy constants — the safety envelope. These change in code (with
# review), never via env: a cap that drifts silently is worse than a fixed
# one. The kill switch is the sole env-driven input (it must be flippable
# mid-incident without a deploy) and lives in app.settings.
COOLING_HOURS = 6
OUTREACH_MIN_INTERVAL_HOURS = 48
MAX_ATTEMPTS_PER_EPISODE = 3
QUIET_HOURS_IST = (21, 9)  # (start, end) hour in IST — quiet 21:00 through 09:00
HUMAN_GATE_THRESHOLD_PAISE = 50000  # ₹500 — outreach above this needs a human (D3+)
DEFAULT_KILL_SWITCH = False

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class PolicyDecision:
    """One engine verdict; `details` is the structured rule evidence for the ledger."""

    ok: bool
    action: str
    reason: str
    details: dict


def _now_utc() -> datetime:
    """Clock hook — the only time source evaluate() uses, so tests freeze it."""
    return datetime.now(tz=timezone.utc)


def _parse_utc(ts: str | None) -> datetime | None:
    """Parse a stored UTC ISO timestamp; naive strings are read as UTC."""
    if ts is None:
        return None
    parsed = datetime.fromisoformat(ts)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _is_quiet_hours(ist_hour: int) -> bool:
    """21:00–09:00 IST across midnight; 09:00 exactly is open, 21:00 closed."""
    start, end = QUIET_HOURS_IST
    return ist_hour >= start or ist_hour < end


def _blocked(reason: str, subscription_id: str, **rule_details: object) -> PolicyDecision:
    return PolicyDecision(
        ok=False,
        action="BLOCKED",
        reason=reason,
        details={"subscription_id": subscription_id, **rule_details},
    )


def evaluate(
    conn: sqlite3.Connection,
    subscription_id: str,
    episode: dict[str, Any],
) -> PolicyDecision:
    """Decide whether outreach may leave now for this episode's subscription.

    `episode` is an episodes-row dict (as returned by app.core.episodes);
    its `id` locates the row, whose columns are re-read so the caps always
    see current attempt_count / last_action_ts_utc. Returns the FIRST
    failing rule as (ok=False, action='BLOCKED'), or (ok=True, 'SEND')
    when every rule passes — one PolicyDecision whose details go into the
    ledger row verbatim.
    """
    if get_settings().kill_switch:
        return _blocked(
            "kill_switch",
            subscription_id,
            kill_switch=True,
        )

    row = conn.execute(
        "SELECT state, attempt_count, last_action_ts_utc, cohort "
        "FROM episodes WHERE id = ?",
        (episode["id"],),
    ).fetchone()
    state = row["state"] if row is not None else episode["state"]
    attempt_count = row["attempt_count"] if row is not None else episode["attempt_count"]
    last_action_ts = row["last_action_ts_utc"] if row is not None else episode["last_action_ts_utc"]
    cohort = row["cohort"] if row is not None else episode["cohort"]

    # Outreach may only leave a SCORED episode, and at most
    # MAX_ATTEMPTS_PER_EPISODE times per halt.
    if state != "SCORED" or attempt_count >= MAX_ATTEMPTS_PER_EPISODE:
        return _blocked(
            "max_attempts",
            subscription_id,
            state=state,
            attempt_count=attempt_count,
            max_attempts=MAX_ATTEMPTS_PER_EPISODE,
        )

    now = _now_utc()
    last_action = _parse_utc(last_action_ts)
    if last_action is not None:
        hours_since = (now - last_action).total_seconds() / 3600
        if hours_since < COOLING_HOURS:
            return _blocked(
                "cooling_off",
                subscription_id,
                hours_since_last_outreach=round(hours_since, 2),
                cooling_hours=COOLING_HOURS,
            )
        if hours_since < OUTREACH_MIN_INTERVAL_HOURS:
            return _blocked(
                "outreach_cap_48h",
                subscription_id,
                hours_since_last_outreach=round(hours_since, 2),
                min_interval_hours=OUTREACH_MIN_INTERVAL_HOURS,
            )

    ist_hour = now.astimezone(IST).hour
    if _is_quiet_hours(ist_hour):
        start, end = QUIET_HOURS_IST
        return _blocked(
            "quiet_hours",
            subscription_id,
            ist_hour=ist_hour,
            quiet_hours_ist=f"{start:02d}:00-{end:02d}:00",
        )

    if cohort != "TREATMENT":
        return _blocked(
            "cohort_gate",
            subscription_id,
            cohort=cohort,
        )

    return PolicyDecision(
        ok=True,
        action="SEND",
        reason="all_rules_pass",
        details={
            "subscription_id": subscription_id,
            "state": state,
            "attempt_count": attempt_count,
            "cohort": cohort,
            "ist_hour": ist_hour,
        },
    )
