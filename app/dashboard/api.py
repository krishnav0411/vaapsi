"""JSON API layer for the React dashboard (D7.1) — a thin read-only skin.

Why this exists: the React frontend (D7.2+) needs the same numbers the
Jinja dashboard renders, but as JSON. The law for this module is REUSE:
every number and every row comes from the existing, tested functions —
``app.dashboard.metrics`` (M1–M5 + overview aggregates), the episode-row
and ledger-timeline helpers in ``app.dashboard.routes`` (same shapes the
Jinja templates consume — one rendering surface, one definition),
``app.dashboard.killswitch`` for the kill endpoint and
``app.gates.human_gate.decide`` for approvals. NO SQL is rewritten here;
if a query changes, both surfaces change together.

Read-only except two human actions, exactly mirroring the Jinja rule:
the kill switch (same one-way switch, same exact-confirmation ritual)
and the human-gate decide (whose default ActionClient is the offline
RecordingStub — dispatch is logged, never networked). Connection pattern
copied from routes.py: every request opens a short-lived ``get_conn()``
(WAL lets the API read while webhooks land); GET routes never write, so
the ledger grows only when a human actually decides.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.episodes import EPISODE_STATES
from app.dashboard import metrics
from app.dashboard.killswitch import activate as activate_kill_switch
from app.dashboard.routes import (
    _episode_ledger,
    _episode_rows,
    _pending_approval,
    engine_mode,
)
from app.db import get_conn
from app.gates import human_gate
from app.policy.merchant import (
    DEFAULT_MERCHANT_ID,
    MerchantPolicyIn,
    list_policies,
    upsert_policy,
)

api_router = APIRouter(prefix="/api", tags=["api"])

_COHORTS: tuple[str, ...] = ("TREATMENT", "CONTROL")

_DECISIONS: dict[str, bool] = {"approve": True, "reject": False}


def _episode_recovered_paise(conn, episode: dict[str, Any]) -> int:
    """Per-episode recovered total as integer paise (D7 Stage C Task A).

    The episode listing must show a real Amount column, but episodes rows
    carry no amount — the money lives on the ledger. The SUM runs over
    the SAME window ``_episode_ledger`` draws the detail timeline from
    (same subscription_id, stamped at/after the episode's creation), so
    the index and the detail page can never disagree about what an
    episode recovered — a fresh cycle must not inherit a previous one's
    rows, and a row stamped before creation belongs to that previous
    cycle. Zero matching rows → 0; never float.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(recovered_paise), 0) AS total FROM audit_ledger "
        "WHERE subscription_id = ? AND ts_utc >= ?",
        (episode["subscription_id"], episode["created_ts_utc"]),
    ).fetchone()
    return int(row["total"])


def _overview_stats(conn) -> dict[str, Any]:
    """The overview page's stat block, as data (same metrics calls the
    Jinja overview makes — recovered ₹ headline, both M1 rates, open
    episodes). Amounts stay integer paise; rates stay 0..1 or None."""
    rate_t = metrics.recovery_rate(conn, "TREATMENT")
    rate_c = metrics.recovery_rate(conn, "CONTROL")
    recovered_paise, recovered_n, recovered_note = metrics.recovered_paise_total(conn)
    return {
        "recovered_paise": recovered_paise,
        "recovered_paise_n": recovered_n,
        "recovered_paise_note": recovered_note,
        "recovery_rate_treatment": {"value": rate_t[0], "n": rate_t[1], "note": rate_t[2]},
        "recovery_rate_control": {"value": rate_c[0], "n": rate_c[1], "note": rate_c[2]},
        "open_episodes": metrics.open_episode_count(conn),
    }


@api_router.get("/overview")
def overview() -> dict[str, Any]:
    """{stats, cohorts, mode} — the overview hero's whole payload."""
    with get_conn() as conn:
        return {
            "stats": _overview_stats(conn),
            "cohorts": metrics.cohort_counts(conn),
            "mode": engine_mode(conn),
        }


@api_router.get("/episodes")
def episodes(state: str = "", cohort: str = "") -> list[dict[str, Any]]:
    """Episode rows with the pending-approval flag and the per-episode
    recovered total (``recovered_paise``, summed over the same ledger
    window the detail timeline uses), filters like the Jinja index
    (unknown state/cohort values are ignored, not errors — same contract
    the HTML filters follow)."""
    state = state if state in EPISODE_STATES else ""
    cohort = cohort if cohort in _COHORTS else ""
    with get_conn() as conn:
        rows = _episode_rows(conn, state or None, cohort or None)
        for row in rows:
            row["recovered_paise"] = _episode_recovered_paise(conn, row)
        return rows


@api_router.get("/episodes/{episode_id}")
def episode_detail(episode_id: str) -> dict[str, Any]:
    """Episode row + its ledger timeline (ordered by seq, this cycle only)
    + the pending approval the decide buttons will target. The episode
    row carries the same per-episode ``recovered_paise`` total the
    listing joins, so the detail header renders the honest amount."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such episode")
        episode = dict(row)
        episode["recovered_paise"] = _episode_recovered_paise(conn, episode)
        return {
            "episode": episode,
            "timeline": _episode_ledger(conn, episode),
            "pending_approval": _pending_approval(conn, episode_id),
        }


@api_router.get("/metrics")
def metrics_all() -> list[dict[str, Any]]:
    """M1–M5 verbatim from metrics.py, each as {name, value, n, note}.

    M1 appears once per cohort (the experiment's primary is the T vs C
    contrast); n travels with every value because RESULTS.md republishes
    the same numbers — the denominator is part of the metric.
    """
    names: tuple[str, ...] = (
        "M1_recovery_rate_TREATMENT",
        "M1_recovery_rate_CONTROL",
        "M2_recovered_paise",
        "M3_time_to_recover_hours_median",
        "M4_outreach_efficiency",
        "M5_false_outreach",
    )
    with get_conn() as conn:
        results = (
            metrics.recovery_rate(conn, "TREATMENT"),
            metrics.recovery_rate(conn, "CONTROL"),
            metrics.recovered_paise_total(conn),
            metrics.time_to_recover_median(conn),
            metrics.outreach_efficiency(conn),
            metrics.false_outreach(conn),
        )
    return [
        {"name": name, "value": value, "n": n, "note": note}
        for name, (value, n, note) in zip(names, results)
    ]


@api_router.get("/mode")
def mode() -> dict[str, str]:
    """Current engine mode for the banner: NORMAL | DEGRADED | KILLED."""
    with get_conn() as conn:
        return {"mode": engine_mode(conn)}


@api_router.get("/policy")
def policy_rows() -> dict[str, Any]:
    """{default, custom} — the DEFAULT policy row (the frozen constants the
    engine falls back to) plus every per-merchant override. Nothing here is
    secret: these are the same thresholds the ledger's policy_eval prints."""
    with get_conn() as conn:
        return list_policies(conn)


@api_router.put("/policy/{merchant_id}")
def put_policy(merchant_id: str, payload: MerchantPolicyIn) -> dict[str, Any]:
    """Create/update ONE merchant's policy row, range-validated by
    MerchantPolicyIn (the DB CHECKs mirror it). The DEFAULT row is the
    frozen safety envelope — it changes in code with review, never over the
    API — so a PUT targeting it fails closed with 403 and writes nothing."""
    mid = merchant_id.strip()
    if not mid:
        raise HTTPException(status_code=422, detail="merchant_id must be non-empty")
    if mid == DEFAULT_MERCHANT_ID:
        raise HTTPException(
            status_code=403,
            detail=(
                "the DEFAULT policy row is frozen (app/policy/merchant.py) and can "
                "never be edited through the API — create a per-merchant row instead"
            ),
        )
    with get_conn() as conn:
        return upsert_policy(conn, mid, payload.model_dump())


class KillRequest(BaseModel):
    confirm: str


@api_router.post("/kill")
def kill(payload: KillRequest) -> dict[str, str]:
    """The kill switch over JSON — same switch, same ritual.

    Uses killswitch.activate() (the one sanctioned .env writer): flips the
    in-memory flag this process obeys immediately, then persists the env
    line. The exact-confirmation text stays mandatory; a wrong confirm is
    a 400 and the switch is untouched — no un-kill endpoint, by design.
    """
    if payload.confirm.strip().upper() != "KILL":
        raise HTTPException(
            status_code=400, detail="kill switch untouched — confirm must be the exact text KILL"
        )
    return {"mode": activate_kill_switch()}


class DecideRequest(BaseModel):
    decision: str


@api_router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, payload: DecideRequest) -> dict[str, Any]:
    """One human decision per approval, via human_gate.decide.

    approve → dispatch through the same RecordingStub path the orchestrator
    uses; reject → GATED → CLOSED. Errors map to status codes (404 unknown
    approval, 409 double decision / stop race / kill-switch outranks) —
    the ledger, not the HTTP layer, remains the record of what happened.
    """
    approved = _DECISIONS.get(payload.decision.strip().lower())
    if approved is None:
        raise HTTPException(
            status_code=422, detail="decision must be 'approve' or 'reject'"
        )
    try:
        with get_conn() as conn:
            return human_gate.decide(conn, approval_id, approved=approved)
    except human_gate.ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="no such approval") from None
    except human_gate.ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
