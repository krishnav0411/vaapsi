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

Every threshold comes from the episode merchant's row in the
merchant_policies table (app.policy.merchant): the DEFAULT row is seeded
with the frozen constants below, and a merchant without a row falls back
to it — so behavior for default merchants is byte-for-byte what it has
always been. Callers may pass merchant_id explicitly (the API/dashboard
do); otherwise the episode dict's merchant_id (when present) or DEFAULT
governs.

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

from app.policy.merchant import (
    DEFAULT_COOLING_HOURS,
    DEFAULT_HUMAN_GATE_THRESHOLD_PAISE,
    DEFAULT_MAX_ATTEMPTS_PER_EPISODE,
    DEFAULT_OUTREACH_MIN_INTERVAL_HOURS,
    DEFAULT_QUIET_HOURS_IST,
    get_policy,
)
from app.settings import get_settings

# Frozen policy constants — the safety envelope. These change in code (with
# review), never via env: a cap that drifts silently is worse than a fixed
# one. The kill switch is the sole env-driven input (it must be flippable
# mid-incident without a deploy) and lives in app.settings. The values are
# OWNED by app.policy.merchant — the DEFAULT row of the merchant_policies
# table is built from them — and re-exported here under their historical
# names so every existing importer sees exactly the same integers.
COOLING_HOURS = DEFAULT_COOLING_HOURS
OUTREACH_MIN_INTERVAL_HOURS = DEFAULT_OUTREACH_MIN_INTERVAL_HOURS
MAX_ATTEMPTS_PER_EPISODE = DEFAULT_MAX_ATTEMPTS_PER_EPISODE
QUIET_HOURS_IST = DEFAULT_QUIET_HOURS_IST  # (start, end) hour in IST — quiet 21:00 through 09:00
HUMAN_GATE_THRESHOLD_PAISE = DEFAULT_HUMAN_GATE_THRESHOLD_PAISE
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


def _is_quiet_hours(ist_hour: int, start: int, end: int) -> bool:
    """Quiet window across midnight (e.g. 21:00–09:00); `end` exactly is open."""
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
    *,
    merchant_id: str | None = None,
) -> PolicyDecision:
    """Decide whether outreach may leave now for this episode's subscription.

    `episode` is an episodes-row dict (as returned by app.core.episodes);
    its `id` locates the row, whose columns are re-read so the caps always
    see current attempt_count / last_action_ts_utc. The thresholds come from
    the merchant's merchant_policies row — `merchant_id` if given, else the
    episode dict's merchant_id when present, else the always-seeded DEFAULT
    row (identical to the historical frozen constants). Returns the FIRST
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

    policy = get_policy(
        conn,
        merchant_id if merchant_id is not None else episode.get("merchant_id"),
    )
    max_attempts = int(policy["max_attempts_per_episode"])
    cooling_hours = int(policy["cooling_hours"])
    min_interval_hours = int(policy["outreach_min_interval_hours"])
    quiet_start = int(policy["quiet_hours_start"])
    quiet_end = int(policy["quiet_hours_end"])

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
    # max_attempts times per halt.
    if state != "SCORED" or attempt_count >= max_attempts:
        return _blocked(
            "max_attempts",
            subscription_id,
            state=state,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
        )

    now = _now_utc()
    last_action = _parse_utc(last_action_ts)
    if last_action is not None:
        hours_since = (now - last_action).total_seconds() / 3600
        if hours_since < cooling_hours:
            return _blocked(
                "cooling_off",
                subscription_id,
                hours_since_last_outreach=round(hours_since, 2),
                cooling_hours=cooling_hours,
            )
        if hours_since < min_interval_hours:
            return _blocked(
                "outreach_cap_48h",
                subscription_id,
                hours_since_last_outreach=round(hours_since, 2),
                min_interval_hours=min_interval_hours,
            )

    ist_hour = now.astimezone(IST).hour
    if _is_quiet_hours(ist_hour, quiet_start, quiet_end):
        return _blocked(
            "quiet_hours",
            subscription_id,
            ist_hour=ist_hour,
            quiet_hours_ist=f"{quiet_start:02d}:00-{quiet_end:02d}:00",
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
