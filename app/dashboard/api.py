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

Read-only except three human actions, exactly mirroring the Jinja rule:
the kill switch (same one-way switch, same exact-confirmation ritual),
the human-gate decide (whose default ActionClient is the offline
RecordingStub — dispatch is logged, never networked), and the D8 drill
runners (which write ONLY to throwaway stores in per-call temp
directories — the live store is never touched). Connection pattern
copied from routes.py: every request opens a short-lived ``get_conn()``
(WAL lets the API read while webhooks land); GET routes never write, so
the ledger grows only when a human actually decides.
"""

import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.audit import ledger as audit_ledger
from app.audit.verify_chain import verify_chain
from app.core.episodes import EPISODE_STATES
from app.dashboard import drills as drill_runners
from app.dashboard import metrics
from app.dashboard.killswitch import activate as activate_kill_switch
from app.dashboard.routes import (
    _episode_ledger,
    _episode_rows,
    _pending_approval,
    engine_mode,
)
from app.db import get_conn
from app.demo_mode import is_demo_mode
from app.gates import human_gate
from app.policy.engine import HUMAN_GATE_THRESHOLD_PAISE
from app.policy.merchant import (
    DEFAULT_MERCHANT_ID,
    MerchantPolicyIn,
    list_policies,
    upsert_policy,
)
from app.scoring.scorecard import PLAN_PRICE_PAISE
from app.settings import get_settings

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
def mode() -> dict[str, Any]:
    """Current engine mode for the banner: NORMAL | DEGRADED | KILLED,
    plus the public-demo flag (VAAPSI_PUBLIC_DEMO) — the UI badges demo
    mode with a read-only chip and disables the write buttons off this
    field (the frontend only ever READS the flag; the server enforces it)."""
    with get_conn() as conn:
        return {"mode": engine_mode(conn), "demo": is_demo_mode(get_settings())}


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
    # The D8 approvals-inbox reason capture: the human's stated why, threaded
    # into the decision's ledger evidence (human_gate.decide decision_note).
    note: str = ""


@api_router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, payload: DecideRequest) -> dict[str, Any]:
    """One human decision per approval, via human_gate.decide.

    approve → dispatch through the same RecordingStub path the orchestrator
    uses; reject → GATED → CLOSED. Errors map to status codes (404 unknown
    approval, 409 double decision / stop race / kill-switch outranks) —
    the ledger, not the HTTP layer, remains the record of what happened.
    An optional `note` travels into the decision's ledger evidence so the
    operator's stated reason is recorded where the verdict is.
    """
    approved = _DECISIONS.get(payload.decision.strip().lower())
    if approved is None:
        raise HTTPException(
            status_code=422, detail="decision must be 'approve' or 'reject'"
        )
    try:
        with get_conn() as conn:
            return human_gate.decide(
                conn, approval_id, approved=approved, note=payload.note
            )
    except human_gate.ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="no such approval") from None
    except human_gate.ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


# ── D8 ledger explorer ─────────────────────────────────────────────────
#
# The audit ledger's public read surface: a block-explorer list, one full
# row, the chain verdict, and the tamper demo. The tamper demo is the one
# place the word "tamper" is allowed near this API — and even there it
# only ever touches a THROWAWAY COPY of the store (sqlite backup API into
# a per-call temp dir, deleted when the call returns); the live store is
# opened read-only for the copy and never written by any route below.


class LedgerListRow(BaseModel):
    seq: int
    ts_utc: str
    trigger_event: str
    actor: str
    outcome: str
    subscription_id: str
    prev_hash: str  # truncated to 12 chars server-side
    hash: str  # truncated to 16 chars server-side


class LedgerListResponse(BaseModel):
    rows: list[LedgerListRow]
    total: int
    chain_valid: bool


class LedgerRowDetail(BaseModel):
    seq: int
    action_id: str
    ts_utc: str
    subscription_id: str
    trigger_event: str
    policy_eval: Any
    score: float | None
    features: Any
    llm_request_hash: str | None
    llm_output_raw: Any
    llm_model: str | None
    human_gate: bool
    rzp_call: Any
    outcome: str
    recovered_paise: int
    mode: str
    prev_hash: str  # FULL
    row_hash: str  # FULL
    prev_seq: int | None
    # Canonical JSON of the logical row (seq excluded, row_hash excluded) —
    # exactly the material compute_row_hash commits to.
    canonical_json: str


class LedgerVerifyResponse(BaseModel):
    valid: bool
    rows: int
    broken_seq: int | None
    detail: str


class TamperDemoResponse(BaseModel):
    verdict: str  # "tamper_detected" | "empty_ledger"
    broken_seq: int | None
    field: str | None
    expected_value: int | None
    found_value: int | None
    stored_hash: str | None
    recomputed_hash: str | None
    verify_detail: str
    rows: int
    original_store_chain_valid: bool
    original_rows: int


def _actor_of(trigger_event: str) -> str:
    """Human rows are the gate's own events; everything else is the agent."""
    return "human" if trigger_event.startswith("human_gate.") else "agent"


def _chain_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """verify_chain over the caller's store, plus the broken seq.

    verify_chain reports the failing POSITION (1-based replay order); the
    store's seq column is the same ordering (append-only, no deletes in
    app code), so position maps 1:1 onto the seq list.
    """
    rows = list(audit_ledger.iter_rows(conn))
    ok, detail = verify_chain(rows)
    broken_seq: int | None = None
    if not ok:
        seqs = [
            int(r["seq"])
            for r in conn.execute("SELECT seq FROM audit_ledger ORDER BY seq ASC")
        ]
        match = re.search(r"row (\d+)", detail)
        if match is not None and seqs:
            position = int(match.group(1))
            if 1 <= position <= len(seqs):
                broken_seq = seqs[position - 1]
    return {"valid": ok, "rows": len(rows), "broken_seq": broken_seq, "detail": detail}


@api_router.get("/ledger/verify", response_model=LedgerVerifyResponse)
def ledger_verify() -> LedgerVerifyResponse:
    """The chain verdict, live: verify_chain over every stored row."""
    with get_conn() as conn:
        status = _chain_status(conn)
    return LedgerVerifyResponse(**status)


@api_router.post("/ledger/tamper-demo", response_model=TamperDemoResponse)
def ledger_tamper_demo() -> TamperDemoResponse:
    """Prove the chain catches tampering — on a COPY, never the live store.

    Per call: copy the SQLite store into a fresh temp dir (sqlite backup
    API, source opened READ-ONLY — WAL-safe), flip ONE row's
    recovered_paise by exactly one paise on the copy, run the SAME
    verifier on the copy, report the verdict with expected vs found, then
    let the temp dir (and the tampered copy with it) be deleted. The live
    store is never opened for write and never mutated; rerunning the demo
    re-copies from the pristine live store, so it is idempotent.
    """
    with get_conn() as conn:
        original = _chain_status(conn)
    if original["rows"] == 0:
        return TamperDemoResponse(
            verdict="empty_ledger",
            broken_seq=None,
            field=None,
            expected_value=None,
            found_value=None,
            stored_hash=None,
            recomputed_hash=None,
            verify_detail="nothing to tamper — the ledger has no rows yet",
            rows=0,
            original_store_chain_valid=original["valid"],
            original_rows=0,
        )

    settings_guard = None
    with tempfile.TemporaryDirectory(prefix="vaapsi-tamper-") as td:
        copy_path = Path(td) / "vaapsi-tamper-copy.sqlite3"
        # READ-ONLY source (WAL-safe: the backup API replays the live WAL
        # into the copy without ever writing to the original).
        src = sqlite3.connect(
            f"file:{get_settings().db_path.as_posix()}?mode=ro", uri=True
        )
        try:
            dst = sqlite3.connect(copy_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        copy_conn = sqlite3.connect(copy_path)
        copy_conn.row_factory = sqlite3.Row
        try:
            target = copy_conn.execute(
                "SELECT seq, recovered_paise FROM audit_ledger ORDER BY seq ASC LIMIT 1"
            ).fetchone()
            seq = int(target["seq"])
            expected_value = int(target["recovered_paise"])
            found_value = expected_value + 1  # a one-paise lie, integer math
            copy_conn.execute(
                "UPDATE audit_ledger SET recovered_paise = ? WHERE seq = ?",
                (found_value, seq),
            )
            copy_conn.commit()

            status = _chain_status(copy_conn)
            # The detection detail: the stored hash (the honest commitment)
            # vs what the tampered contents now recompute to.
            tampered_db_row = copy_conn.execute(
                "SELECT * FROM audit_ledger WHERE seq = ?", (seq,)
            ).fetchone()
            logical = audit_ledger._from_db(tampered_db_row)
            recomputed = audit_ledger.compute_row_hash(logical["prev_hash"], logical)
            settings_guard = {
                "broken_seq": status["broken_seq"],
                "stored_hash": str(logical["row_hash"]),
                "recomputed_hash": recomputed,
                "verify_detail": status["detail"],
                "rows": status["rows"],
            }
        finally:
            copy_conn.close()
    # temp dir (and the tampered copy) is deleted here — per-call lifetime

    assert settings_guard is not None  # the empty-store branch returned above
    return TamperDemoResponse(
        verdict="tamper_detected",
        broken_seq=settings_guard["broken_seq"],
        field="recovered_paise",
        expected_value=expected_value,
        found_value=found_value,
        stored_hash=settings_guard["stored_hash"],
        recomputed_hash=settings_guard["recomputed_hash"],
        verify_detail=settings_guard["verify_detail"],
        rows=settings_guard["rows"],
        original_store_chain_valid=original["valid"],
        original_rows=original["rows"],
    )


@api_router.get("/ledger", response_model=LedgerListResponse)
def ledger_list(limit: int = 100, offset: int = 0) -> LedgerListResponse:
    """Block-explorer rows in chain order (seq ASC), hashes truncated
    SERVER-SIDE (prev to 12, row to 16 — the frontend never sees a full
    hash it does not need), `total` as the full COUNT, and the current
    chain verdict so the header chip never has to guess."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    with get_conn() as conn:
        total = int(conn.execute("SELECT COUNT(*) AS n FROM audit_ledger").fetchone()["n"])
        chain_valid = _chain_status(conn)["valid"]
        db_rows = conn.execute(
            "SELECT seq, ts_utc, trigger_event, outcome, subscription_id, "
            "prev_hash, row_hash FROM audit_ledger ORDER BY seq ASC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        rows = [
            LedgerListRow(
                seq=int(r["seq"]),
                ts_utc=str(r["ts_utc"]),
                trigger_event=str(r["trigger_event"]),
                actor=_actor_of(str(r["trigger_event"])),
                outcome=str(r["outcome"]),
                subscription_id=str(r["subscription_id"]),
                prev_hash=str(r["prev_hash"])[:12],
                hash=str(r["row_hash"])[:16],
            )
            for r in db_rows
        ]
    return LedgerListResponse(rows=rows, total=total, chain_valid=chain_valid)


@api_router.get("/ledger/{seq}", response_model=LedgerRowDetail)
def ledger_row(seq: int) -> LedgerRowDetail:
    """One full ledger row: every column, FULL hashes, the parsed payload
    JSON fields, and the canonical JSON the chain verifier hashes — the
    exact material an auditor replays."""
    with get_conn() as conn:
        db_row = conn.execute("SELECT * FROM audit_ledger WHERE seq = ?", (seq,)).fetchone()
        if db_row is None:
            raise HTTPException(status_code=404, detail="no such ledger row")
        prev = conn.execute(
            "SELECT seq FROM audit_ledger WHERE seq < ? ORDER BY seq DESC LIMIT 1",
            (seq,),
        ).fetchone()
    logical = audit_ledger._from_db(db_row)
    canonical = audit_ledger.canonical_json(
        {k: v for k, v in logical.items() if k != "row_hash"}
    )
    return LedgerRowDetail(
        seq=int(db_row["seq"]),
        action_id=str(logical["action_id"]),
        ts_utc=str(logical["ts_utc"]),
        subscription_id=str(logical["subscription_id"]),
        trigger_event=str(logical["trigger_event"]),
        policy_eval=logical["policy_eval"],
        score=logical["score"],
        features=logical["features"],
        llm_request_hash=logical["llm_request_hash"],
        llm_output_raw=logical["llm_output_raw"],
        llm_model=logical["llm_model"],
        human_gate=bool(logical["human_gate"]),
        rzp_call=logical["rzp_call"],
        outcome=str(logical["outcome"]),
        recovered_paise=int(logical["recovered_paise"]),
        mode=str(logical["mode"]),
        prev_hash=str(logical["prev_hash"]),
        row_hash=str(logical["row_hash"]),
        prev_seq=int(prev["seq"]) if prev is not None else None,
        canonical_json=canonical,
    )


# ── D8 approvals inbox ─────────────────────────────────────────────────


class ApprovalSummary(BaseModel):
    id: str
    episode_id: str
    subscription_id: str
    reason: str
    status: str
    created_ts_utc: str
    episode_state: str
    attempt_count: int
    tier: int | None
    amount_paise: int
    threshold_paise: int
    exceeds_threshold: bool
    over_by_paise: int
    proposed_action: str


class ApprovalsPendingResponse(BaseModel):
    approvals: list[ApprovalSummary]


class ApprovalDetailResponse(BaseModel):
    approval: ApprovalSummary
    # The SAME shapes episode_detail returns — one rendering definition.
    episode: dict[str, Any]
    timeline: list[dict[str, Any]]


def _proposed_action_summary(
    tier: int | None, choice: dict[str, Any] | None, reason: str
) -> str:
    """One-line proposed action, from the SCORED row's recorded choice when
    the ledger carries one, else the deterministic fallback story."""
    if choice is not None:
        action = str(choice.get("action", "send_payment_link"))
        channel = str(choice.get("channel", "payment_link"))
        variant = str(choice.get("message_variant", "standard"))
        return f"{action} via {channel} ({variant} nudge)"
    if tier == 3 or reason == human_gate.GATE_REASON_TIER3:
        return "tier-3 escalation — held for human judgment before any outreach"
    return "rules-only payment-link outreach, gated pending human approval"


def _approval_summary(conn: sqlite3.Connection, approval: dict[str, Any]) -> ApprovalSummary:
    """The queue-card/detail context for one approval row: episode state,
    the scorer's tier and proposed action (from the episode's SCORED ledger
    row), and the threshold math in integer paise — the same single-source
    constants the engine gates with (app.policy.engine / app.scoring)."""
    episode = conn.execute(
        "SELECT id, subscription_id, state, attempt_count, created_ts_utc "
        "FROM episodes WHERE id = ?",
        (approval["episode_id"],),
    ).fetchone()
    tier: int | None = None
    choice: dict[str, Any] | None = None
    if episode is not None:
        for row in _episode_ledger(conn, dict(episode)):
            if row["outcome"] != "EPISODE_SCORED" or not isinstance(
                row["policy_eval"], dict
            ):
                continue
            pe = row["policy_eval"]
            if isinstance(pe.get("tier"), int):
                tier = int(pe["tier"])
            if isinstance(pe.get("choice"), dict):
                choice = dict(pe["choice"])
            break
    amount = PLAN_PRICE_PAISE
    exceeds = amount > HUMAN_GATE_THRESHOLD_PAISE
    return ApprovalSummary(
        id=str(approval["id"]),
        episode_id=str(approval["episode_id"]),
        subscription_id=str(approval["subscription_id"]),
        reason=str(approval["reason"]),
        status=str(approval["status"]),
        created_ts_utc=str(approval["created_ts_utc"]),
        episode_state=str(episode["state"]) if episode is not None else "UNKNOWN",
        attempt_count=int(episode["attempt_count"]) if episode is not None else 0,
        tier=tier,
        amount_paise=amount,
        threshold_paise=HUMAN_GATE_THRESHOLD_PAISE,
        exceeds_threshold=exceeds,
        over_by_paise=max(0, amount - HUMAN_GATE_THRESHOLD_PAISE),
        proposed_action=_proposed_action_summary(tier, choice, str(approval["reason"])),
    )


@api_router.get("/approvals/pending", response_model=ApprovalsPendingResponse)
def approvals_pending() -> ApprovalsPendingResponse:
    """Every PENDING human-gate decision, oldest first, with the threshold
    math and the proposed action the human is being asked to judge."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, episode_id, subscription_id, reason, status, created_ts_utc "
            "FROM approvals WHERE status = 'PENDING' ORDER BY created_ts_utc ASC"
        ).fetchall()
        approvals = [_approval_summary(conn, dict(r)) for r in rows]
    return ApprovalsPendingResponse(approvals=approvals)


@api_router.get("/approvals/{approval_id}/detail", response_model=ApprovalDetailResponse)
def approval_detail(approval_id: str) -> ApprovalDetailResponse:
    """Full context for the diff view: the approval summary plus the SAME
    episode + timeline payloads /api/episodes/{id} renders."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, episode_id, subscription_id, reason, status, created_ts_utc "
            "FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such approval")
        approval = dict(row)
        summary = _approval_summary(conn, approval)
        episode_row = conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (approval["episode_id"],)
        ).fetchone()
        if episode_row is None:
            raise HTTPException(status_code=404, detail="no such episode")
        episode = dict(episode_row)
        episode["recovered_paise"] = _episode_recovered_paise(conn, episode)
        return ApprovalDetailResponse(
            approval=summary,
            episode=episode,
            timeline=_episode_ledger(conn, episode),
        )


# ── D8 drills console ──────────────────────────────────────────────────


class DrillRunResult(BaseModel):
    drill_id: str
    passed: bool
    summary: str
    evidence: dict[str, Any]
    ran_ts_utc: str
    duration_ms: int


class DrillInfo(BaseModel):
    drill_id: str
    title: str
    description: str
    last_run: DrillRunResult | None


class DrillsResponse(BaseModel):
    drills: list[DrillInfo]


@api_router.get("/drills", response_model=DrillsResponse)
def drills() -> DrillsResponse:
    """The three drill cards with their last-run records (None before the
    first run in this server process — the drills are stateless)."""
    return DrillsResponse(drills=[DrillInfo(**d) for d in drill_runners.catalog()])


@api_router.post("/drills/{drill_id}/run", response_model=DrillRunResult)
def run_drill(drill_id: str) -> DrillRunResult:
    """Run one drill synchronously against an ISOLATED temp store (bounded,
    offline, <30s) — the live store is never touched. Unknown ids 404."""
    try:
        result = drill_runners.run_drill(drill_id)
    except drill_runners.UnknownDrillError:
        raise HTTPException(status_code=404, detail="no such drill") from None
    return DrillRunResult(**result)
