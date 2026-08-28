"""Experiment metrics M1–M5 (EXPERIMENT.md, verbatim definitions) — read-only.

Why a dedicated module: the dashboard is the experiment's public
measurement surface, so every number it shows must come from the
pre-registered definitions (EXPERIMENT.md §Metrics) — nothing invented
here, nothing recomputed with a different denominator. Each function is
zero-safe on an empty database (returns None/0 with an explanatory note,
never raises) because the dashboard renders before a single halt exists,
and every function returns the same shape: (value, n, note) where `n` is
the sample the value was computed over — RESULTS.md (D6) republishes the
same numbers, so per-cohort N must travel with the value.

Pure SQL over the existing tables (webhook_events, cohorts, audit_ledger)
on the CALLER's connection — the dashboard never opens its own write path.
Amounts are integer paise throughout; time math uses SQLite's julianday on
the ISO-8601 UTC stamps the store already persists.
"""

import sqlite3
from statistics import median
from typing import Any

# The exact recovery events EXPERIMENT.md M1 counts: the subscription
# itself charging, or a recovery link/invoice being paid.
HALT_EVENT = "subscription.halted"
RECOVERY_EVENTS: tuple[str, ...] = (
    "subscription.charged",
    "payment_link.paid",
    "invoice.paid",
)

# M1 evaluation window: recovery must land within 7 days of the halt.
RECOVERY_WINDOW_DAYS = 7.0

COHORTS: tuple[str, ...] = ("TREATMENT", "CONTROL")

_RECOVERY_EVENT_PLACEHOLDERS = ", ".join("?" for _ in RECOVERY_EVENTS)


def recovery_rate(conn: sqlite3.Connection, cohort: str) -> tuple[float | None, int, str]:
    """M1 (primary): halted subs reaching charged/paid within 7 days ÷ halted.

    Per cohort (EXPERIMENT.md M1, source `audit ledger + webhook_events`;
    the webhook store is the charge/paid truth, the cohorts table the
    pre-registered assignment). The window is computed in SQL with
    julianday so out-of-order arrival never matters — only event TIME
    does. Returns (rate 0..1, total halted, note) with rate None when the
    cohort has no halted subscriptions yet (0/0 is undefined, not zero).
    """
    row = conn.execute(
        f"""
        WITH halts AS (
            SELECT subscription_id,
                   MIN(COALESCE(event_ts_utc, received_ts_utc)) AS halt_ts
            FROM webhook_events
            WHERE event = '{HALT_EVENT}'
            GROUP BY subscription_id
        ),
        recoveries AS (
            SELECT subscription_id, MIN(COALESCE(event_ts_utc, received_ts_utc)) AS recover_ts
            FROM webhook_events
            WHERE event IN ({", ".join("?" for _ in RECOVERY_EVENTS)})
            GROUP BY subscription_id
        )
        SELECT COUNT(*) AS halted,
               COALESCE(SUM(CASE WHEN r.subscription_id IS NOT NULL
                                  AND (julianday(r.recover_ts) - julianday(h.halt_ts))
                                      BETWEEN 0.0 AND {float(RECOVERY_WINDOW_DAYS)}
                                 THEN 1 ELSE 0 END), 0) AS recovered
        FROM halts h
        JOIN cohorts c ON c.subscription_id = h.subscription_id
        LEFT JOIN recoveries r ON r.subscription_id = h.subscription_id
        WHERE c.cohort = ?
        """,
        (*RECOVERY_EVENTS, cohort),
    ).fetchone()
    halted, recovered = int(row["halted"]), int(row["recovered"])
    if halted == 0:
        return None, 0, f"{cohort}: no halted subscriptions yet"
    rate = recovered / halted
    return rate, halted, f"{cohort}: {recovered}/{halted} recovered within {RECOVERY_WINDOW_DAYS}d"


def recovered_paise_total(
    conn: sqlite3.Connection, cohort: str | None = None
) -> tuple[int, int, str]:
    """M2: ₹ recovered — sum of ledger `recovered_paise`, per cohort.

    EXPERIMENT.md M2 verbatim ("sum of successful recovery amounts (paise),
    per cohort — ledger `recovered_paise`"). cohort=None sums across both
    cohorts (the overview headline); a named cohort joins the cohorts table
    so the split stays pre-registered, never post-hoc. Returns
    (paise, contributing rows, note) — (0, 0, ...) on an empty ledger.
    """
    if cohort is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(recovered_paise), 0) AS total, COUNT(*) AS n "
            "FROM audit_ledger WHERE recovered_paise > 0"
        ).fetchone()
        label = "both cohorts"
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(l.recovered_paise), 0) AS total, COUNT(*) AS n "
            "FROM audit_ledger l JOIN cohorts c ON c.subscription_id = l.subscription_id "
            "WHERE l.recovered_paise > 0 AND c.cohort = ?",
            (cohort,),
        ).fetchone()
        label = cohort
    return int(row["total"]), int(row["n"]), f"recovered_paise summed over {label}"


def time_to_recover_median(conn: sqlite3.Connection) -> tuple[float | None, int, str]:
    """M3: median hours from the halted event to the recovery event.

    EXPERIMENT.md M3, source `ledger timestamps`: for each subscription,
    the halt reference is its EPISODE_CREATED row (the halt enters the
    ledger there) and the recovery reference is its first row carrying
    recovered_paise > 0 (the D6 verify stamp); the metric is the median of
    the pairwise gaps in hours, over subscriptions that have BOTH — n is
    that pair count, and (None, 0, ...) when no recovery has landed yet.
    """
    rows = conn.execute(
        """
        SELECT h.subscription_id AS subscription_id,
               (julianday(r.recover_ts) - julianday(h.halt_ts)) * 24.0 AS hours
        FROM (
            SELECT subscription_id, MIN(ts_utc) AS halt_ts
            FROM audit_ledger WHERE outcome = 'EPISODE_CREATED'
            GROUP BY subscription_id
        ) h
        JOIN (
            SELECT subscription_id, MIN(ts_utc) AS recover_ts
            FROM audit_ledger WHERE recovered_paise > 0
            GROUP BY subscription_id
        ) r ON r.subscription_id = h.subscription_id
        """
    ).fetchall()
    gaps = [row["hours"] for row in rows if row["hours"] is not None]
    if not gaps:
        return None, 0, "no recovery events in the ledger yet"
    return float(median(gaps)), len(gaps), "halt ledger row → recovered ledger row"


def outreach_efficiency(conn: sqlite3.Connection) -> tuple[float | None, int, str]:
    """M4: recoveries ÷ outreach actions sent.

    EXPERIMENT.md M4, source `ledger`: every dispatch of an outreach marks
    exactly one EPISODE_SENT ledger row (the GATED→SENT and SCORED→SENT
    transitions both write it), so `sent` counts those rows and
    `recoveries` counts distinct subscriptions with a recovered_paise > 0
    row. Returns (ratio, sent, note); None while nothing has been sent
    (0 ÷ 0 is undefined — the dashboard renders '—', never a fake 0).
    """
    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_ledger WHERE outcome = 'EPISODE_SENT'"
    ).fetchone()["n"]
    recoveries = conn.execute(
        "SELECT COUNT(DISTINCT subscription_id) AS n FROM audit_ledger "
        "WHERE recovered_paise > 0"
    ).fetchone()["n"]
    if sent == 0:
        return None, 0, "no outreach sent yet"
    return recoveries / sent, int(sent), f"{recoveries} recoveries / {sent} outreach sends"


def false_outreach(conn: sqlite3.Connection) -> tuple[int, int, str]:
    """M5: false outreach — must be 0, ledger-proven.

    EXPERIMENT.md M5 verbatim: "any outreach to a subscription that
    charged/cancelled/completed before the action fired". Ledger-proven
    here means: an EPISODE_SENT row whose subscription ALREADY has an
    EPISODE_VOIDED row (stop-on-charge / stop-on-cancel) stamped no later
    than the send. Zero-safe and cheap; n is the number of stop voids
    observed, so the banner can say how many stop events were checked
    against, not just assert emptiness.
    """
    sent_after_stop = conn.execute(
        """
        SELECT COUNT(*) AS n FROM audit_ledger s
        WHERE s.outcome = 'EPISODE_SENT' AND EXISTS (
            SELECT 1 FROM audit_ledger v
            WHERE v.subscription_id = s.subscription_id
              AND v.outcome = 'EPISODE_VOIDED'
              AND v.ts_utc <= s.ts_utc
        )
        """
    ).fetchone()["n"]
    voids = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_ledger WHERE outcome = 'EPISODE_VOIDED'"
    ).fetchone()["n"]
    note = (
        f"{sent_after_stop} outreach rows fired at/after a stop event "
        f"({voids} stop voids in ledger)"
    )
    return int(sent_after_stop), int(voids), note


def cohort_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Pre-registered cohort sizes (the A/B panel's 30/30), zero-safe."""
    return {
        row["cohort"]: int(row["n"])
        for row in conn.execute(
            "SELECT cohort, COUNT(*) AS n FROM cohorts GROUP BY cohort"
        )
    }


def open_episode_count(conn: sqlite3.Connection) -> int:
    """Episodes that may still originate outreach (the open-state set)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM episodes WHERE state IN "
        "('NEW', 'DIAGNOSED', 'SCORED', 'GATED', 'SENT')"
    ).fetchone()
    return int(row["n"])


def recent_ledger(conn: sqlite3.Connection, limit: int = 12) -> list[dict[str, Any]]:
    """The last `limit` ledger rows, newest last-seq first for the table."""
    rows = conn.execute(
        "SELECT seq, ts_utc, subscription_id, trigger_event, outcome, mode, "
        "recovered_paise, human_gate FROM audit_ledger "
        "ORDER BY seq DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def ledger_count(conn: sqlite3.Connection) -> int:
    """Total ledger rows — the data-freshness note's volume signal."""
    row = conn.execute("SELECT COUNT(*) AS n FROM audit_ledger").fetchone()
    return int(row["n"])
